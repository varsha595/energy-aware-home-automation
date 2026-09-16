"""
The "Acting" half of the project: executes a plan action by action and
replans from the *current* state (not from scratch) whenever a trigger
fires. See docs/DESIGN.md section 4.

This Engine is deliberately the single implementation used by both the live
dashboard and the analysis battery (backend/baseline.py just flips its
`energy_aware` / `replan_from_scratch` flags) so the two are guaranteed to
behave identically except for the one variable being compared.
"""

import random
import threading
from dataclasses import dataclass, field

from backend.domain import all_actions, actual_cost, window_index, BUDGET_PER_WINDOW
from backend.scheduler import AgentGoalRequest, merge_goals, schedule

# "Gets stuck" probabilities per device, used for the random action-failure
# trigger. The vacuum is the most failure-prone -- it's the one that
# literally gets physically stuck under furniture.
FAILURE_PROB = {
    "Vacuum": 0.09,
    "Washer": 0.04,
    "Dishwasher": 0.04,
    "System": 0.0,
}


@dataclass
class LogEntry:
    seq: int
    sim_minute: int
    kind: str          # "action" | "wait" | "replan" | "failure" | "new-goal" | "price-spike" | "done"
    message: str
    detail: dict = field(default_factory=dict)


@dataclass
class Scenario:
    dirty_rooms: list
    robot_start: str
    loads: list
    dishes_dirty: bool
    start_minute: int


class Engine:
    def __init__(self, scenario: Scenario, energy_aware: bool = True,
                 replan_from_scratch: bool = False, rng: random.Random | None = None):
        from backend.state import initial_world_state

        self.energy_aware = energy_aware
        self.replan_from_scratch = replan_from_scratch
        self.rng = rng or random.Random()

        self.initial_predicates = initial_world_state(
            dirty_rooms=scenario.dirty_rooms, robot_start=scenario.robot_start,
            laundry_loads_ready=scenario.loads, dishes_dirty=scenario.dishes_dirty)
        self.initial_minute = scenario.start_minute
        self.initial_requests = [AgentGoalRequest("Vacuum", frozenset(("clean", r) for r in scenario.dirty_rooms))] if scenario.dirty_rooms else []
        if scenario.loads:
            self.initial_requests.append(AgentGoalRequest("Washer", frozenset(("dry-done", l) for l in scenario.loads)))
        if scenario.dishes_dirty:
            self.initial_requests.append(AgentGoalRequest("Dishwasher", frozenset({("dishes-clean",)})))

        self.predicates = self.initial_predicates
        self.sim_minute = scenario.start_minute
        self.budget_remaining = BUDGET_PER_WINDOW
        self.loads = list(scenario.loads)
        self.goal_requests = list(self.initial_requests)
        # Facts injected by real external events (new-goal triggers), as
        # opposed to facts made true by the planner's own completed
        # actions. The scratch-replan baseline needs to keep these when it
        # "forgets" progress -- a new laundry load being ready is a fact
        # about the world, not planning progress to discard.
        self.injected_predicates: set = set()

        self.plan_steps: list[dict] = []
        self.plan_cursor = 0
        self.events: list[LogEntry] = []
        self._seq = 0

        self.total_energy_used = 0.0
        self.replan_count = 0
        self.replan_extra_energy = 0.0     # energy attributable to redone/wasted work from replanning
        self.replan_extra_minutes = 0.0
        self.force_fault = False
        self.done = False
        self.failed = False
        self.lock = threading.RLock()

        self._replan(reason="initial plan")

    # ------------------------------------------------------------------
    # Goal / event bookkeeping
    # ------------------------------------------------------------------

    def goals(self) -> frozenset:
        return merge_goals(self.goal_requests)

    def _log(self, kind: str, message: str, detail: dict | None = None):
        self._seq += 1
        self.events.append(LogEntry(self._seq, self.sim_minute, kind, message, detail or {}))

    def _current_actions(self):
        return all_actions(self.loads)

    def _find_action(self, name: str):
        for a in self._current_actions():
            if a.name == name:
                return a
        return None

    # ------------------------------------------------------------------
    # Replanning
    # ------------------------------------------------------------------

    def _replan(self, reason: str):
        old_tail = self.plan_steps[self.plan_cursor:] if self.plan_steps else []

        if self.replan_from_scratch and self.plan_steps:
            # Baseline strawman: forget all progress and re-solve the
            # *entire original* problem from the true initial state, even
            # though most of it is already done. Any work already executed
            # gets planned (and, in the executor loop, re-executed) again.
            base_predicates = self.initial_predicates | frozenset(self.injected_predicates)
            base_minute = self.initial_minute
            base_budget = BUDGET_PER_WINDOW
            base_requests = self.initial_requests + [
                r for r in self.goal_requests if r not in self.initial_requests
            ]
            steps, _ = schedule(base_predicates, base_requests, self.loads, base_minute,
                                 base_budget, energy_aware=self.energy_aware)
        else:
            steps, _ = schedule(self.predicates, self.goal_requests, self.loads, self.sim_minute,
                                 self.budget_remaining, energy_aware=self.energy_aware)

        self.replan_count += 1
        if steps is None:
            self._log("replan", f"Replan FAILED ({reason}): no plan found", {"reason": reason})
            self.plan_steps, self.plan_cursor = [], 0
            self.failed = True
            return

        if self.replan_from_scratch and self.plan_steps:
            new_energy = sum(s["cost"] for s in steps)
            old_energy = sum(s["cost"] for s in old_tail)
            self.replan_extra_energy += max(0.0, new_energy - old_energy)
            self.plan_steps = steps
            self.plan_cursor = 0
        else:
            self.plan_steps = steps
            self.plan_cursor = 0

        self._log("replan", f"Replanned ({reason}): old tail had {len(old_tail)} step(s), new plan has {len(self.plan_steps)} step(s)", {
            "reason": reason,
            "old_tail": [s["name"] for s in old_tail],
            "new_plan": [s["name"] for s in self.plan_steps],
        })

    # ------------------------------------------------------------------
    # Trigger 1: manual / random action failure
    # ------------------------------------------------------------------

    def trigger_fault(self):
        with self.lock:
            self.force_fault = True
            self._log("fault-armed", "Fault armed manually: the next action will fail")

    # ------------------------------------------------------------------
    # Trigger 2: new goal mid-plan
    # ------------------------------------------------------------------

    def add_laundry_load(self, load_name: str):
        with self.lock:
            self.loads.append(load_name)
            new_fact = ("laundry-ready", load_name)
            self.predicates = self.predicates | {new_fact}
            self.injected_predicates.add(new_fact)
            self.goal_requests.append(AgentGoalRequest("Washer", frozenset({("dry-done", load_name)})))
            self._log("new-goal", f"New goal arrived: laundry load '{load_name}' needs washing and drying")
            self._replan(reason=f"new goal: dry-done({load_name})")

    def add_dirty_room(self, room: str):
        with self.lock:
            new_fact = ("dirty", room)
            self.predicates = self.predicates | {new_fact}
            self.injected_predicates.add(new_fact)
            merged = self.goals()
            reqs = [r for r in self.goal_requests if r.agent != "Vacuum"]
            vac = next((r for r in self.goal_requests if r.agent == "Vacuum"), None)
            new_goals = set(vac.goals) if vac else set()
            new_goals.add(("clean", room))
            reqs.append(AgentGoalRequest("Vacuum", frozenset(new_goals)))
            self.goal_requests = reqs
            self._log("new-goal", f"New goal arrived: {room} just got dirty again")
            self._replan(reason=f"new goal: clean({room})")

    # ------------------------------------------------------------------
    # Trigger 3: energy price spike / surprise load on the circuit
    # ------------------------------------------------------------------

    def price_spike(self, amount: float = 300.0):
        with self.lock:
            self.budget_remaining = max(0.0, self.budget_remaining - amount)
            self._log("price-spike", f"Unexpected device switched on: shared budget dropped by {amount:.0f} units this window")
            self._replan(reason="energy budget dropped mid-execution")

    # ------------------------------------------------------------------
    # Execution loop -- one plan step at a time
    # ------------------------------------------------------------------

    def step(self) -> bool:
        """Executes exactly one step of the current plan. Returns False once
        the plan is exhausted (done) or unrecoverable (failed)."""
        with self.lock:
            if self.done or self.failed:
                return False
            if self.plan_cursor >= len(self.plan_steps):
                if self.goals().issubset(self.predicates):
                    self.done = True
                    self._log("done", "All goals satisfied")
                else:
                    self.failed = True
                    self._log("replan", "Plan exhausted without satisfying all goals")
                return False

            planned = self.plan_steps[self.plan_cursor]

            if planned["name"].startswith("Wait"):
                self.sim_minute = planned["start_minute"] + planned["duration"]
                self.budget_remaining = BUDGET_PER_WINDOW
                self.plan_cursor += 1
                self._log("wait", f"Idling until t={self.sim_minute} min (budget window / off-peak boundary)")
                return True

            action = self._find_action(planned["name"])
            fail_prob = FAILURE_PROB.get(planned["agent"], 0.03)
            fails = self.force_fault or (self.rng.random() < fail_prob)
            self.force_fault = False

            if fails:
                self._log("failure", f"{planned['agent']} FAILED during {planned['name']} (device got stuck)", {"action": planned["name"]})
                self._replan(reason=f"action failure: {planned['name']}")
                return True

            real_cost = actual_cost(action, self.sim_minute)
            prev_minute = self.sim_minute
            self.predicates = action.apply(self.predicates)
            self.sim_minute += action.duration_min
            if window_index(self.sim_minute) != window_index(prev_minute):
                self.budget_remaining = BUDGET_PER_WINDOW
            else:
                self.budget_remaining -= real_cost
            self.total_energy_used += real_cost
            self.plan_cursor += 1
            self._log("action", f"{planned['agent']} executed {planned['name']} (cost {real_cost:.1f})", {
                "action": planned["name"], "agent": planned["agent"], "cost": real_cost,
            })

            if self.goals().issubset(self.predicates):
                self.done = True
                self._log("done", "All goals satisfied")
            return True

    def run_to_completion(self, max_steps: int = 2000):
        steps_taken = 0
        while steps_taken < max_steps and self.step():
            steps_taken += 1
        return self.done
