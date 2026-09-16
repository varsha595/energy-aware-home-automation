"""
The three planner configurations compared in the analysis (docs/DESIGN.md
section 6). All three run through the exact same Engine (backend/executor.py)
and the exact same scripted scenario/trigger sequence -- only the flags
differ -- so the comparison isolates the one variable each is meant to test.
"""

import random
from dataclasses import dataclass

from backend.executor import Engine, Scenario

STRATEGIES = {
    "energy_aware": dict(energy_aware=True, replan_from_scratch=False),
    "naive": dict(energy_aware=False, replan_from_scratch=False),
    "scratch_replan": dict(energy_aware=True, replan_from_scratch=True),
}


@dataclass
class ScriptedTrigger:
    at_step: int
    kind: str            # "fault" | "new_load" | "new_room" | "price_spike"
    arg: str | float | None = None


@dataclass
class Metrics:
    strategy: str
    total_energy_used: float
    budget_per_window: float
    replan_count: int
    replan_extra_energy: float
    total_minutes: int
    completed: bool
    events: int


def run_scenario(scenario: Scenario, strategy: str, triggers: list[ScriptedTrigger],
                  seed: int = 0, max_steps: int = 2000) -> Metrics:
    from backend.domain import BUDGET_PER_WINDOW

    flags = STRATEGIES[strategy]
    eng = Engine(scenario, rng=random.Random(seed), **flags)

    pending = sorted(triggers, key=lambda t: t.at_step)
    step_count = 0
    while step_count < max_steps and not eng.done and not eng.failed:
        while pending and pending[0].at_step == step_count:
            t = pending.pop(0)
            if t.kind == "fault":
                eng.trigger_fault()
            elif t.kind == "new_load":
                eng.add_laundry_load(t.arg)
            elif t.kind == "new_room":
                eng.add_dirty_room(t.arg)
            elif t.kind == "price_spike":
                eng.price_spike(t.arg or 300.0)
        if not eng.step():
            break
        step_count += 1

    return Metrics(
        strategy=strategy,
        total_energy_used=eng.total_energy_used,
        budget_per_window=BUDGET_PER_WINDOW,
        replan_count=eng.replan_count,
        replan_extra_energy=eng.replan_extra_energy,
        total_minutes=eng.sim_minute - scenario.start_minute,
        completed=eng.done,
        events=len(eng.events),
    )
