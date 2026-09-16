"""
Search-node state used by the planner: predicates + simulated clock + the
budget remaining in the *current* hour window. Kept separate from the plain
predicate frozenset (the "world state") because the planner needs the extra
bookkeeping to reason about the shared energy budget over time.
"""

from dataclasses import dataclass
from backend.domain import window_index, BUDGET_PER_WINDOW


@dataclass(frozen=True)
class SearchState:
    predicates: frozenset
    sim_minute: int
    budget_remaining: float   # remaining units in the *current* window

    def key(self):
        """Hashable key for the closed-set / visited-state check.

        Budget is bucketed to a coarse granularity (5 units) to keep the
        search space from exploding into near-duplicate states that differ
        only by a fraction of a unit of remaining budget -- what matters
        for planning decisions is roughly how much budget is left, not the
        exact cent, and this bucketing collapses a large number of
        otherwise-distinct states without changing which actions are
        affordable in practice.
        """
        return (self.predicates, window_index(self.sim_minute), round(self.budget_remaining / 20) * 20)


def initial_world_state(dirty_rooms, robot_start, laundry_loads_ready, dishes_dirty):
    from backend.domain import connected_predicates

    preds = set(connected_predicates())
    preds.add(("robot-at", robot_start))
    for r in dirty_rooms:
        preds.add(("dirty", r))
    preds.add(("machine-empty",))
    for load in laundry_loads_ready:
        preds.add(("laundry-ready", load))
    if dishes_dirty:
        preds.add(("dishes-dirty",))
    preds.add(("dishwasher-empty",))
    return frozenset(preds)
