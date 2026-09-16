# Design Document — Energy-Aware Multi-Agent Home Automation

## 1. Environment

**Layout.** 5 rooms in a small graph (not a full grid — a house floor plan is naturally a
sparse graph of connected rooms, which is simpler to reason about than a grid while still
requiring `Move` actions with real preconditions):

```
Living Room -- Kitchen -- Laundry Room
     |             |
 Bedroom       Bathroom
```

Adjacency (`connected(a, b)`, symmetric):
`LivingRoom-Kitchen`, `LivingRoom-Bedroom`, `Kitchen-Bathroom`, `Kitchen-LaundryRoom`.

**Room state.** Each room has a `dirty(room)` / `clean(room)` predicate. Kitchen additionally
tracks dish state; Laundry Room tracks the washing machine.

**Shared energy budget.** `800 units` per 60-minute window. Time is simulated in minutes.
A time-of-day multiplier makes the budget's *effective* cost of an action vary:

| Window | Hours (sim clock) | Price multiplier |
|---|---|---|
| Peak | 08:00–20:00 | 1.5× |
| Off-peak | 20:00–08:00 | 0.6× |

An action's actual draw on the shared budget = `base_energy_cost × price_multiplier(now)`.
This is what makes the planner "energy-aware": two plans that do the same work can draw
very different amounts from the shared budget depending on *when* the expensive actions
(wash/dry/dishwasher cycles) are scheduled.

The budget replenishes to 800 at every new 60-minute window boundary during execution.
The planner must never schedule an action whose cost would push the *current* window's
cumulative draw over 800 — if nothing affordable is left, it accounts for an implicit
wait until the next window (modeled as an `AdvanceWindow` transition), which is the
mechanism that lets the shared budget be enforced "at any point in time," per the spec.

## 2. Agents and STRIPS actions

Three agents, each a separate actor with its own action set. All three actions sets are
grounded into one predicate vocabulary and merged by **one centralized planner** — this is
the multi-agent/single-planner distinction explained in §5.

### Predicates (vocabulary)

`robot-at(r)`, `connected(a,b)`, `dirty(r)`, `clean(r)`,
`laundry-ready(load)`, `machine-empty`, `laundry-loaded(load)`, `detergent-added(load)`,
`wash-done(load)`, `dry-done(load)`,
`dishes-dirty`, `dishwasher-empty`, `dishes-loaded`, `dw-detergent-added`, `dishes-clean`

### Agent A — Vacuum Robot

| Action | Preconditions | Effects (add / delete) | Base energy | Duration |
|---|---|---|---|---|
| `Move(from, to)` | `robot-at(from)`, `connected(from,to)` | +`robot-at(to)` / −`robot-at(from)` | 5 | 2 min |
| `Clean(r)` | `robot-at(r)`, `dirty(r)` | +`clean(r)` / −`dirty(r)` | 12 | 6 min |

### Agent B — Washing Machine

| Action | Preconditions | Effects | Base energy | Duration |
|---|---|---|---|---|
| `LoadClothes(load)` | `laundry-ready(load)`, `machine-empty` | +`laundry-loaded(load)` / −`machine-empty` | 2 | 3 min |
| `AddDetergent(load)` | `laundry-loaded(load)` | +`detergent-added(load)` | 1 | 1 min |
| `RunWashCycle(load)` | `laundry-loaded(load)`, `detergent-added(load)` | +`wash-done(load)` | 40 | 45 min |
| `RunDryCycle(load)` | `wash-done(load)` | +`dry-done(load)` / −`laundry-loaded(load)`, −`detergent-added(load)`, +`machine-empty` | 35 | 40 min |

### Agent C — Dishwasher

| Action | Preconditions | Effects | Base energy | Duration |
|---|---|---|---|---|
| `LoadDishes` | `dishes-dirty`, `dishwasher-empty` | +`dishes-loaded` / −`dishwasher-empty` | 2 | 3 min |
| `AddDetergentDW` | `dishes-loaded` | +`dw-detergent-added` | 1 | 1 min |
| `RunWashCycleDW` | `dishes-loaded`, `dw-detergent-added` | +`dishes-clean` / −`dishes-dirty`, −`dishes-loaded`, −`dw-detergent-added` | 50 | 60 min |

`RunWashCycle`, `RunDryCycle`, and `RunWashCycleDW` are the "peak-sensitive" actions: at
1.5× peak price they cost 60/52.5/75 units respectively — more than half the hourly budget
for a single action — so scheduling them off-peak is a real, demonstrable win.

## 3. Heuristic

State `n` carries: predicate set, elapsed simulated time, and budget remaining in the
current window.

```
h(n) = w_goal * unsatisfied_goal_count(n)
     + w_energy * max(0, cheapest_remaining_cost_estimate(n) - budget_remaining(n))
```

- `unsatisfied_goal_count` — number of goal predicates not yet true (classic STRIPS
  goal-distance component).
- `cheapest_remaining_cost_estimate` — sum, over each unsatisfied goal, of the minimum
  `base_energy_cost` action known to be able to produce it (a relaxed, admissible-ish
  lower bound on remaining energy spend, ignoring price for the estimate).
- The second term is 0 while the remaining budget in the window comfortably covers the
  cheapest path; it grows (penalizing the state) once the planner is "boxed in" and would
  be forced into an expensive reorder or a wait. This is what pushes the search toward
  orderings that front-load cheap actions and defer expensive ones to off-peak windows.

`h` itself is computed with an **h_add** delete-relaxation: ignore every action's delete
effects and find, via fixed-point propagation, the cheapest sum-of-action-cost chain
that derives each unsatisfied goal predicate from what's already true. This matters
because plain goal-counting can't see multi-step device cycles (`LoadClothes ->
AddDetergent -> RunWashCycle -> RunDryCycle`) — only the last predicate in the chain is
a goal, so goal-counting is blind across most of the search tree. h_add sees the whole
chain and gives usable gradient at every step.

Search is **weighted A\*** (`f = g + W·h`, `W = 1.1` by default) over grounded states,
with `g` = actual cumulative price-weighted energy spent so far. Weighted A* is used
instead of plain greedy best-first because `g` (real energy cost) is exactly what we
want to keep low, not just approximated by `h` — using both keeps the search from ever
being purely greedy about goal-distance while ignoring cost already sunk. `W` is kept
close to 1 deliberately: h_add is informative enough that a larger weight makes the
search over-trust it and settle for the first goal-reaching path found (usually "do
everything immediately") instead of the genuinely cheaper off-peak-deferred plan.

## 4. Replanning strategy ("Acting")

The execution loop runs the current plan action by action. Two triggers force a replan
(both implemented; a third is included since it's cheap and strengthens the demo):

1. **Action failure.** Each action has a small failure probability (default 6%, vacuum
   `Clean` slightly higher to model "getting stuck"). A manual "Trigger Fault" UI button
   forces the *next* action to fail for a live demo.
2. **New goal mid-plan.** The UI's "Add Laundry Load" / "Add Goal" button injects a new
   goal predicate (e.g. a second laundry load) into the live goal set.
3. **Energy price spike / surprise load.** A manual button (or scripted event) can shrink
   the current window's remaining budget mid-execution, simulating another device
   switching on unexpectedly.

On any trigger, the executor **replans from the current state** — not from the initial
state — reusing whatever progress has already been made (rooms already cleaned, loads
already washed stay satisfied and are simply not re-derived). It re-invokes the same
weighted-A* planner with: current predicate state, current elapsed time/budget, and the
updated goal set. The new plan replaces the remaining tail of the old plan; execution
continues immediately. Every trigger is logged with: timestamp, trigger type, the state
delta that caused it, and a diff between the old plan's remaining tail and the new plan.

The "replan from scratch" baseline used for comparison in the analysis (§6) instead
discards progress bookkeeping and re-derives the *entire* goal set from the true initial
state, wasting the energy/time already spent — this is intentionally a strawman to make
the benefit of current-state replanning measurable.

## 5. Multi-agent vs. centralized planner — the distinction for the viva

- **"Multiple agents"** = the three *devices* (vacuum, washer, dishwasher). Each owns its
  own STRIPS action schema, its own preconditions/effects, and its own local goals
  (submitted as a goal-predicate set to the planner). In the code, each lives in
  `backend/domain.py` as an independent action-generator function and is visualized in
  the UI as an independent card with its own status.
- **"One planner"** = `backend/planner.py` + `backend/scheduler.py`. There is exactly one
  search process. It grounds *all three* agents' actions into a single joint action set,
  and produces **one interleaved, totally-ordered plan** across all of them. This is what
  makes the shared energy budget enforceable: only a single scheduler that sees every
  agent's action can guarantee the sum of concurrently-drawn energy never exceeds 800/hr.
- This is a **centralized multi-agent planning** setup (one planner, many actors), not a
  decentralized/negotiation-based one (no agent bids for budget or messages another
  agent). That's a deliberate simplification, justified in `docs/VIVA_NOTES.md`.

## 6. Analysis plan

`run_analysis.py` runs a fixed battery of scenarios (baseline dirty rooms + 1 laundry load
+ dirty dishes, with an injected fault and an injected new goal) across three planner
configurations:

1. **Energy-aware planner** (this project): weighted A* with the heuristic above.
2. **Naive planner**: same search, but `h`'s energy term is dropped (`w_energy = 0`) and
   actions are otherwise picked in a fixed default order — goal-distance only, blind to
   price.
3. **Replan-from-scratch**: same energy-aware planner, but on every trigger it replans
   against the true initial state instead of the current state.

For each, it records: total energy used vs. 800/hr budget, number of replanning events,
extra energy/time cost attributable to replanning, and total plan completion time. Output
is a CSV/markdown table plus bar/line charts (`docs/analysis_*.png`).
