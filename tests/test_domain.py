from backend.domain import (
    vacuum_actions, washer_actions, dishwasher_actions, all_actions,
    actual_cost, price_multiplier, window_index,
    PEAK_START_MIN, PEAK_END_MIN, PEAK_MULTIPLIER, OFFPEAK_MULTIPLIER,
)
from backend.state import initial_world_state


def test_vacuum_move_preconditions_and_effects():
    move = next(a for a in vacuum_actions() if a.name == "Move(LivingRoom->Kitchen)")
    state = frozenset({("robot-at", "LivingRoom"), ("connected", "LivingRoom", "Kitchen")})
    assert move.is_applicable(state)
    new_state = move.apply(state)
    assert ("robot-at", "Kitchen") in new_state
    assert ("robot-at", "LivingRoom") not in new_state


def test_vacuum_clean_requires_dirty_and_presence():
    clean = next(a for a in vacuum_actions() if a.name == "Clean(Kitchen)")
    assert not clean.is_applicable(frozenset({("robot-at", "Kitchen")}))  # not dirty
    assert not clean.is_applicable(frozenset({("dirty", "Kitchen")}))     # robot not there
    state = frozenset({("robot-at", "Kitchen"), ("dirty", "Kitchen")})
    assert clean.is_applicable(state)
    new_state = clean.apply(state)
    assert ("clean", "Kitchen") in new_state
    assert ("dirty", "Kitchen") not in new_state


def test_washer_chain_order():
    actions = {a.name: a for a in washer_actions("Load1")}
    state = frozenset({("laundry-ready", "Load1"), ("machine-empty",)})

    load = actions["LoadClothes(Load1)"]
    assert load.is_applicable(state)
    state = load.apply(state)
    assert ("machine-empty",) not in state

    detergent = actions["AddDetergent(Load1)"]
    assert detergent.is_applicable(state)
    state = detergent.apply(state)

    wash = actions["RunWashCycle(Load1)"]
    assert wash.is_applicable(state)
    state = wash.apply(state)
    assert ("wash-done", "Load1") in state

    dry = actions["RunDryCycle(Load1)"]
    assert dry.is_applicable(state)
    state = dry.apply(state)
    assert ("dry-done", "Load1") in state
    assert ("machine-empty",) in state  # machine freed up after drying


def test_dishwasher_chain():
    actions = {a.name: a for a in dishwasher_actions()}
    state = frozenset({("dishes-dirty",), ("dishwasher-empty",)})
    state = actions["LoadDishes"].apply(state)
    state = actions["AddDetergentDW"].apply(state)
    state = actions["RunWashCycleDW"].apply(state)
    assert ("dishes-clean",) in state
    assert ("dishes-dirty",) not in state


def test_price_multiplier_peak_vs_offpeak():
    assert price_multiplier(PEAK_START_MIN) == PEAK_MULTIPLIER
    assert price_multiplier(PEAK_END_MIN - 1) == PEAK_MULTIPLIER
    assert price_multiplier(PEAK_END_MIN) == OFFPEAK_MULTIPLIER
    assert price_multiplier(0) == OFFPEAK_MULTIPLIER


def test_actual_cost_scales_with_price():
    action = next(a for a in vacuum_actions() if a.name == "Clean(Kitchen)")
    peak_cost = actual_cost(action, PEAK_START_MIN)
    offpeak_cost = actual_cost(action, PEAK_END_MIN)
    assert peak_cost > offpeak_cost
    assert peak_cost == action.base_energy_cost * PEAK_MULTIPLIER


def test_window_index_hourly():
    assert window_index(0) == 0
    assert window_index(59) == 0
    assert window_index(60) == 1
    assert window_index(125) == 2


def test_initial_world_state_reflects_scenario():
    state = initial_world_state(dirty_rooms=["Kitchen"], robot_start="LivingRoom",
                                 laundry_loads_ready=["Load1"], dishes_dirty=True)
    assert ("dirty", "Kitchen") in state
    assert ("robot-at", "LivingRoom") in state
    assert ("laundry-ready", "Load1") in state
    assert ("dishes-dirty",) in state
    assert ("dirty", "Bedroom") not in state


def test_all_actions_includes_every_agent():
    actions = all_actions(["Load1"])
    agents = {a.agent for a in actions}
    assert agents == {"Vacuum", "Washer", "Dishwasher"}
