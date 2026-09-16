import random

from backend.domain import all_actions
from backend.state import initial_world_state
from backend.planner import plan
from backend.executor import Engine, Scenario
from backend.baseline import run_scenario, ScriptedTrigger


def test_plan_returns_empty_list_when_goals_already_true():
    init = initial_world_state(dirty_rooms=[], robot_start="LivingRoom",
                                laundry_loads_ready=[], dishes_dirty=False)
    steps, final = plan(init, frozenset({("robot-at", "LivingRoom")}), all_actions([]),
                         start_minute=9 * 60, start_budget=800)
    assert steps == []


def test_plan_solves_single_room_clean():
    init = initial_world_state(dirty_rooms=["Kitchen"], robot_start="LivingRoom",
                                laundry_loads_ready=[], dishes_dirty=False)
    goals = frozenset({("clean", "Kitchen")})
    steps, final = plan(init, goals, all_actions([]), start_minute=9 * 60, start_budget=800)
    assert steps is not None
    assert goals.issubset(final.predicates)
    # every step's preconditions must have held immediately before it ran (plan validity)
    actions_by_name = {a.name: a for a in all_actions([])}
    state = init
    for s in steps:
        if s["name"].startswith("Wait"):
            continue
        action = actions_by_name[s["name"]]
        assert action.is_applicable(state), f"{s['name']} not applicable in state it was scheduled in"
        state = action.apply(state)


def test_plan_never_overspends_budget_within_a_window():
    from backend.domain import window_index, actual_cost

    init = initial_world_state(dirty_rooms=["Kitchen", "Bedroom"], robot_start="LivingRoom",
                                laundry_loads_ready=["Load1"], dishes_dirty=True)
    goals = frozenset({("clean", "Kitchen"), ("clean", "Bedroom"), ("dry-done", "Load1"), ("dishes-clean",)})
    steps, final = plan(init, goals, all_actions(["Load1"]), start_minute=9 * 60, start_budget=800)
    assert steps is not None

    spend_by_window = {}
    for s in steps:
        w = window_index(s["start_minute"])
        spend_by_window[w] = spend_by_window.get(w, 0) + s["cost"]
    for w, spent in spend_by_window.items():
        assert spent <= 800 + 1e-6, f"window {w} overspent: {spent}"


def test_energy_aware_uses_no_more_energy_than_naive():
    init = initial_world_state(dirty_rooms=["Kitchen"], robot_start="LivingRoom",
                                laundry_loads_ready=["Load1"], dishes_dirty=False)
    goals = frozenset({("clean", "Kitchen"), ("dry-done", "Load1")})
    actions = all_actions(["Load1"])

    ea_steps, _ = plan(init, goals, actions, start_minute=9 * 60, start_budget=800, energy_aware=True)
    naive_steps, _ = plan(init, goals, actions, start_minute=9 * 60, start_budget=800, energy_aware=False)

    ea_cost = sum(s["cost"] for s in ea_steps)
    naive_cost = sum(s["cost"] for s in naive_steps)
    assert ea_cost <= naive_cost


def test_executor_replans_on_manual_fault_and_still_finishes():
    scenario = Scenario(dirty_rooms=["Kitchen"], robot_start="LivingRoom",
                         loads=["Load1"], dishes_dirty=False, start_minute=9 * 60)
    eng = Engine(scenario, rng=random.Random(1))
    eng.trigger_fault()
    finished = eng.run_to_completion()
    assert finished
    assert eng.replan_count >= 2  # initial plan + at least one from the forced failure
    assert any(e.kind == "failure" for e in eng.events)
    assert any(e.kind == "replan" for e in eng.events)


def test_executor_replans_on_new_goal_mid_plan():
    scenario = Scenario(dirty_rooms=[], robot_start="LivingRoom",
                         loads=["Load1"], dishes_dirty=False, start_minute=9 * 60)
    eng = Engine(scenario, rng=random.Random(2))
    eng.step()
    eng.add_laundry_load("Load2")
    assert ("dry-done", "Load2") in eng.goals()
    finished = eng.run_to_completion()
    assert finished
    assert ("dry-done", "Load2") in eng.predicates


def test_baseline_comparison_energy_aware_beats_naive():
    scenario = Scenario(dirty_rooms=["Kitchen", "Bedroom"], robot_start="LivingRoom",
                         loads=["Load1"], dishes_dirty=True, start_minute=9 * 60)
    ea = run_scenario(scenario, "energy_aware", [], seed=5)
    naive = run_scenario(scenario, "naive", [], seed=5)
    assert ea.total_energy_used <= naive.total_energy_used
