"""
The "multiple agents submit requests, one planner merges them" layer.

Each device (Vacuum, Washer, Dishwasher) is an independent agent with its
own goal predicates it wants achieved -- that's the multi-agent part. This
module just merges those requests into the single joint goal set that
backend/planner.py solves as one centralized scheduling problem -- that's
the "one planner coordinates them" part. See docs/DESIGN.md section 5.
"""

from dataclasses import dataclass

from backend.domain import all_actions
from backend.planner import plan as astar_plan


@dataclass(frozen=True)
class AgentGoalRequest:
    agent: str          # "Vacuum" | "Washer" | "Dishwasher"
    goals: frozenset     # goal predicates this agent wants true


def merge_goals(requests: list[AgentGoalRequest]) -> frozenset:
    merged = set()
    for r in requests:
        merged |= set(r.goals)
    return frozenset(merged)


def schedule(predicates: frozenset, requests: list[AgentGoalRequest], loads: list[str],
             sim_minute: int, budget_remaining: float, energy_aware: bool = True):
    """Grounds every agent's actions into one action set, merges every
    agent's goal request into one goal set, and hands the whole thing to
    the single centralized planner. Returns (plan_steps, final_state)."""
    actions = all_actions(loads)
    goals = merge_goals(requests)
    return astar_plan(predicates, goals, actions, sim_minute, budget_remaining, energy_aware=energy_aware)


def default_requests(dirty_rooms: list[str], loads: list[str], dishes_dirty: bool) -> list[AgentGoalRequest]:
    """Builds the standard 'clean everything, finish every load, wash the
    dishes' goal set used by the demo scenario and the analysis battery."""
    requests = []
    if dirty_rooms:
        requests.append(AgentGoalRequest("Vacuum", frozenset(("clean", r) for r in dirty_rooms)))
    if loads:
        requests.append(AgentGoalRequest("Washer", frozenset(("dry-done", l) for l in loads)))
    if dishes_dirty:
        requests.append(AgentGoalRequest("Dishwasher", frozenset({("dishes-clean",)})))
    return requests
