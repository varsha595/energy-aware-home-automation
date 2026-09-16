"""
Weighted A* STRIPS planner.

f(n) = g(n) + W * h(n)

  g(n) = cumulative *actual* (price-weighted) energy spent to reach n
  h(n) = relaxed_cost(n) + w_energy * max(0, relaxed_cost(n) - budget_remaining(n))

relaxed_cost(n) is an "h_add"-style delete-relaxation estimate: ignore every
action's delete effects, and compute the cheapest sum-of-action-costs chain
that derives each unsatisfied goal predicate from what's already true in n,
then sum that over all unsatisfied goals. Plain goal-counting was tried
first and discarded: it can't see multi-step device cycles (LoadClothes ->
AddDetergent -> RunWashCycle -> RunDryCycle) because only the *last*
predicate in each chain is a goal, so it gives identical, uninformative
heuristic values across huge parts of the search tree. h_add sees the whole
chain and is standard in the STRIPS-planning literature (see docs/DESIGN.md
section 3).

A special "wait for next window" transition lets the planner defer expensive
actions to an off-peak window instead of paying peak price, which is the
mechanism that makes the plan "energy-aware" rather than just goal-directed.
"""

import heapq
import itertools
from dataclasses import dataclass

from backend.domain import Action, actual_cost, window_index, BUDGET_PER_WINDOW
from backend.state import SearchState

W_ENERGY = 1.0
DEFAULT_WEIGHT = 1.1
UNREACHABLE_PENALTY = 1000.0
MAX_EXPANSIONS = 150000
WAIT_STEP_MIN = 10   # granularity for the "wait" transition, in minutes

# Waiting is "free" on the shared energy budget, but it isn't free in the
# search: without some cost it out-competes every real action forever (an
# endless chain of zero-cost waits always beats a 5-unit Move on f = g+W*h),
# so the planner would never actually act. A tiny per-minute cost keeps
# waiting available (worth it when it avoids a much larger peak surcharge)
# without letting it dominate the search.
WAIT_COST_PER_MIN = 0.05


@dataclass
class Node:
    state: SearchState
    g: float                       # search-optimization cost (what f=g+Wh minimizes)
    real_g: float                  # actual price-weighted energy spent (for reporting)
    parent: "Node | None"
    action: "Action | None"       # None means this was a "wait" transition
    scheduled_minute: int          # sim minute the action/wait started at
    is_wait: bool = False


def _relaxed_costs(predicates: frozenset, actions: list[Action]) -> dict:
    """h_add: cost[p] = cheapest sum-of-precondition-costs + action-cost
    chain that derives p, ignoring delete effects (the standard STRIPS
    delete-relaxation). 0 for predicates already true in `predicates`.
    Computed fresh per search node -- the domain here is small enough
    (dozens of actions/predicates) that the fixed-point loop is cheap."""
    cost = {p: 0.0 for p in predicates}
    changed = True
    while changed:
        changed = False
        for a in actions:
            total = 0.0
            reachable = True
            for p in a.preconditions:
                if p not in cost:
                    reachable = False
                    break
                total += cost[p]
            if not reachable:
                continue
            total += a.base_energy_cost
            for eff in a.add_effects:
                if eff not in cost or total < cost[eff] - 1e-9:
                    cost[eff] = total
                    changed = True
    return cost


def _heuristic(state: SearchState, goals: frozenset, actions: list[Action], energy_aware: bool) -> float:
    unsatisfied = [g for g in goals if g not in state.predicates]
    if not unsatisfied:
        return 0.0
    relaxed = _relaxed_costs(state.predicates, actions)
    goal_term = sum(relaxed.get(g, UNREACHABLE_PENALTY) for g in unsatisfied)
    if not energy_aware:
        return goal_term
    energy_term = W_ENERGY * max(0.0, goal_term - state.budget_remaining)
    return goal_term + energy_term


def _successors(node: Node, actions: list[Action], energy_aware: bool):
    """Yields (new_state, search_cost, real_cost, action, start_minute, is_wait).

    search_cost is what the A* frontier optimizes (f = g + W*h); real_cost is
    the actual price-weighted energy drawn from the shared budget. They're
    the same number for the energy-aware planner. The naive baseline (used
    only for the comparison in the analysis, see baseline.py) instead
    optimizes search_cost = time elapsed, i.e. it is blind to price -- but
    real_cost still enforces the hard budget cap and is still what gets
    reported as energy used, since the budget itself is real physics, not
    an optimization choice.
    """
    st = node.state
    succs = []

    for action in actions:
        if not action.is_applicable(st.predicates):
            continue
        real_cost = actual_cost(action, st.sim_minute)
        if real_cost > st.budget_remaining + 1e-9:
            continue  # can't afford right now; must wait or reorder
        search_cost = real_cost if energy_aware else action.duration_min
        new_minute = st.sim_minute + action.duration_min
        if window_index(new_minute) != window_index(st.sim_minute):
            new_budget = BUDGET_PER_WINDOW
        else:
            new_budget = st.budget_remaining - real_cost
        new_state = SearchState(action.apply(st.predicates), new_minute, new_budget)
        succs.append((new_state, search_cost, real_cost, action, st.sim_minute, False))

    # "Wait" transitions: advance the clock without acting, eventually
    # crossing into a new (replenished, and possibly off-peak) window. This
    # is what lets the planner choose to defer an expensive action.
    #
    # The energy-aware planner is offered two flavors: a one-window hop
    # (needed when the blocker is simply "not enough budget left *this*
    # window") and a direct jump to the next peak/off-peak price change
    # (needed when the blocker is "price is bad right now"). The naive
    # baseline only ever gets the one-window hop -- it will wait when it is
    # physically forced to (budget exhausted), but never merely because
    # waiting would be cheaper, which is exactly the behavior "no energy
    # awareness" is meant to capture.
    next_window = (window_index(st.sim_minute) + 1) * 60
    targets = {next_window}
    if energy_aware:
        targets.add(_next_price_change_minute(st.sim_minute))

    for target in targets:
        if target > st.sim_minute:
            wait_state = SearchState(st.predicates, target, BUDGET_PER_WINDOW)
            wait_cost = WAIT_COST_PER_MIN * (target - st.sim_minute)
            succs.append((wait_state, wait_cost, 0.0, None, st.sim_minute, True))

    return succs


def _next_price_change_minute(sim_minute: int) -> int:
    """The next simulated minute at which the peak/off-peak price flips."""
    from backend.domain import PEAK_START_MIN, PEAK_END_MIN

    day = sim_minute // (24 * 60)
    tod = sim_minute % (24 * 60)
    if tod < PEAK_START_MIN:
        return day * 24 * 60 + PEAK_START_MIN
    if tod < PEAK_END_MIN:
        return day * 24 * 60 + PEAK_END_MIN
    return (day + 1) * 24 * 60 + PEAK_START_MIN


def plan(initial_predicates: frozenset, goals: frozenset, actions: list[Action],
         start_minute: int, start_budget: float, weight: float = DEFAULT_WEIGHT,
         energy_aware: bool = True):
    """Returns (ordered_action_list, final_state) or (None, None) if no plan
    is found within the expansion budget. ordered_action_list is a list of
    dicts: {name, agent, start_minute, cost, duration} (a "Wait" pseudo-step
    is included when the plan needed to idle for the budget window or an
    off-peak window). Set energy_aware=False to get the naive baseline used
    in the analysis comparison (see docs/DESIGN.md section 6)."""

    start_state = SearchState(initial_predicates, start_minute, start_budget)
    root = Node(state=start_state, g=0.0, real_g=0.0, parent=None, action=None,
                scheduled_minute=start_minute)

    if goals.issubset(start_state.predicates):
        return [], start_state

    counter = itertools.count()
    h0 = _heuristic(start_state, goals, actions, energy_aware)
    frontier = [(root.g + weight * h0, next(counter), root)]
    best_g = {start_state.key(): 0.0}
    expansions = 0

    while frontier and expansions < MAX_EXPANSIONS:
        _, _, node = heapq.heappop(frontier)
        expansions += 1

        if goals.issubset(node.state.predicates):
            return _reconstruct(node), node.state

        if node.g > best_g.get(node.state.key(), float("inf")) + 1e-9:
            continue  # stale queue entry

        for new_state, cost, real_cost, action, start_min, is_wait in _successors(node, actions, energy_aware):
            new_g = node.g + cost
            key = new_state.key()
            if new_g >= best_g.get(key, float("inf")) - 1e-9:
                continue
            best_g[key] = new_g
            child = Node(state=new_state, g=new_g, real_g=node.real_g + real_cost, parent=node,
                         action=action, scheduled_minute=start_min, is_wait=is_wait)
            h = _heuristic(new_state, goals, actions, energy_aware)
            heapq.heappush(frontier, (new_g + weight * h, next(counter), child))

    return None, None


def _reconstruct(node: Node):
    steps = []
    cur = node
    while cur.parent is not None:
        if cur.is_wait:
            steps.append({
                "name": "Wait(next-window)",
                "agent": "System",
                "start_minute": cur.scheduled_minute,
                "cost": 0.0,
                "duration": cur.state.sim_minute - cur.scheduled_minute,
            })
        else:
            steps.append({
                "name": cur.action.name,
                "agent": cur.action.agent,
                "start_minute": cur.scheduled_minute,
                "cost": cur.real_g - cur.parent.real_g,
                "duration": cur.action.duration_min,
            })
        cur = cur.parent
    steps.reverse()
    return steps
