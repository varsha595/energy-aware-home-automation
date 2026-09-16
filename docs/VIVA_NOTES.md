# Viva Defense Notes

Quick-reference answers for the questions this project is likely to draw.
Longer reasoning is in `DESIGN.md`; this file is the condensed version.

## "Why STRIPS and not something fancier (PDDL/HTN/POMDP)?"

The domain is genuinely flat: every action's preconditions and effects are
known and deterministic, there's no partial observability, and the only
"hierarchy" is really just three independent per-device action chains that
get interleaved. STRIPS (predicates as sets, actions as precondition/effect
pairs) is the minimum machinery that can express that faithfully, and it's
the formalism the course unit covers. A full PDDL engine or HTN planner
would add expressiveness (numeric fluents natively, task decomposition)
the domain doesn't need, at the cost of being much harder to hand-roll and
explain line-by-line.

## "Why weighted A* instead of plain A* or plain greedy best-first?"

- Plain **greedy best-first** (f = h only) ignores sunk cost entirely, so it
  can walk into an expensive dead end it never recovers from -- bad when
  the entire point is minimizing energy cost.
- Plain **A\*** (W = 1) is optimal but explores every tied-priority node;
  early prototyping showed it's slower than necessary for a domain this
  size once the heuristic is already informative.
- **Weighted A\*** (`f = g + W·h`, `W` close to 1 here -- see below) gets
  near-optimal plans while pruning much more aggressively than plain A*,
  which matters because every trigger causes a fresh search.

## "Why is the weight so close to 1 (1.1), isn't that basically plain A*?"

Yes, deliberately. Once the heuristic was upgraded from plain goal-counting
to an h_add relaxed-planning-graph estimate (see next question), it became
informative enough that a larger weight (we tried 1.4) made the search
over-trust it and settle for the *first* goal-reaching path it found --
which is usually "run every device immediately," not the actually cheaper
off-peak-deferred plan. Dropping the weight back down recovered the
cheaper plans at an acceptable speed cost. This is a real,
measured trade-off in this codebase (`backend/planner.py`), not a
textbook default -- good material for "walk me through a decision you
tuned."

## "Why h_add and not just goal-counting?"

Goal-counting only decrements when a *goal* predicate becomes true. But
none of the intermediate steps in a device's cycle (`LoadClothes ->
AddDetergent -> RunWashCycle -> RunDryCycle`) are goals themselves -- only
`dry-done` is. So goal-counting is blind across 3 of every 4 steps in a
wash cycle, giving the search almost no gradient to follow and blowing up
combinatorially once there's more than one agent's chain to interleave.
h_add (delete-relaxation, ignore delete effects, propagate cheapest cost
to each predicate to a fixed point) sees the whole chain and gives a
gradient at every step. This was an actual bug we hit and fixed during
development (worth mentioning if asked "what didn't work").

## "Why does the shared budget replenish exactly at every window boundary, including mid-action?"

Simplification, stated explicitly in `DESIGN.md` section 1: an action that
straddles a window boundary is billed against the window it *started* in,
and the new window's budget is fully available immediately after. The
honest alternative (pro-rating a long action's cost across the windows it
spans) adds real complexity for a small accuracy gain in a domain where the
longest action (60 min) is exactly one window anyway. Flag this
proactively if asked to defend budget realism.

## "Why centralized planning instead of decentralized/negotiating agents?"

The three devices don't have competing preferences worth negotiating over
-- they all just want to finish their own job as cheaply as possible, and
the only real coupling between them is the shared budget. A centralized
planner that sees every agent's action set can *guarantee* the budget
constraint is respected globally; a decentralized/auction-based scheme
would need an extra negotiation protocol to get the same guarantee, for a
domain too small to need the added realism. This is the standard "why did
you pick the simple option" trade-off answer: centralized coordination is
enough multi-agent-ness to be a legitimate coordination problem (see
`DESIGN.md` section 5 for exactly where the multi-agent/single-planner line
is drawn in the code) without decentralized negotiation's complexity.

## "Why these two (three) replanning triggers specifically?"

The spec asked for two: action failure, and either a new goal or a budget
drop. We implemented all three (failure, new goal, budget drop) because
they're cheap to add once the replan-from-current-state mechanism exists,
and together they cover the two qualitatively different causes of plan
invalidation: **the world didn't cooperate** (a device got stuck) vs. **the
goal itself changed** (new laundry load, or less budget than assumed). A
demo that can only show one flavor is a weaker demo.

## "Why replan from the current state instead of literally patching/repairing the old plan?"

Plan repair (patching just the broken part of the old plan) is a genuine
alternative and would be faster per replan, but it's a meaningfully harder
algorithm to implement correctly and explain than "just re-run the same
search you already trust, from wherever you are now." Given the domain is
small enough that a fresh search from the current state is fast (well
under a second for most triggers), the simpler, more defensible approach
wins. The comparison against the *replan-from-scratch* baseline
(section 6) is specifically there to demonstrate that "from current state"
still matters a lot even without full repair -- discarding progress is
expensive, discarding the *search's own bookkeeping* is comparatively
cheap.

## "What does the naive baseline actually get wrong?"

Two things, both in `backend/planner.py`'s `energy_aware=False` path: (1)
its heuristic drops the energy-pressure term, so nothing pushes it toward
cheap orderings, and (2) it's never offered the "jump to next price
change" wait transition, so it can only wait when physically forced to by
an empty budget, never merely because waiting would be cheaper. It's still
a real, goal-directed, budget-respecting planner -- just blind to price,
which is exactly the "no energy-awareness" contrast the assignment asks
for.

## "What does the replan-from-scratch baseline actually get wrong?"

On every trigger it re-solves the *entire original* goal set against the
*true initial state*, including facts genuinely made true by devices that
already finished (a `dirty(Kitchen)` predicate the current run already
turned into `clean(Kitchen)`). It then re-executes that plan, so already
-completed physical work gets redone. `Engine.replan_extra_energy` in
`backend/executor.py` isolates exactly the wasted portion for the
analysis. One subtlety worth mentioning if pressed: it does keep facts
injected by real external events (a new laundry load's "ready" predicate)
-- forgetting *those* would make new goals unreachable, which would be a
bug, not a fair baseline.

## "What would you add with more time?"

- Pro-rated budget billing across window boundaries (see above).
- Plan repair instead of full re-search, to make replanning even faster.
- A deadline/urgency concept per goal, so the planner isn't always willing
  to wait until 20:00 for the cheapest possible plan -- right now nothing
  stops it from being *very* patient if left alone (see the "quiet
  morning" scenario in the analysis, where it deferred nothing only
  because the day already started off-peak).
