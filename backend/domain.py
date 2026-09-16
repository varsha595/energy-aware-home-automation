"""
STRIPS domain definition for the energy-aware home automation project.

A predicate is a plain tuple, e.g. ("clean", "Kitchen") or ("dishes-dirty",).
A State is a frozenset of predicates (see state.py).

Each Action is grounded (no free variables left) so the planner can work with
plain set operations: applicable iff preconditions subset of state; applying
it means (state - del_effects) | add_effects.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Action:
    name: str
    agent: str                 # which device/agent owns this action ("Vacuum", "Washer", "Dishwasher")
    preconditions: frozenset
    add_effects: frozenset
    del_effects: frozenset
    base_energy_cost: float    # energy units before time-of-day price multiplier
    duration_min: int          # simulated minutes this action takes to execute

    def is_applicable(self, state: frozenset) -> bool:
        return self.preconditions.issubset(state)

    def apply(self, state: frozenset) -> frozenset:
        return (state - self.del_effects) | self.add_effects


# --------------------------------------------------------------------------
# House layout
# --------------------------------------------------------------------------

ROOMS = ["LivingRoom", "Kitchen", "Bedroom", "LaundryRoom", "Bathroom"]

ADJACENCY = [
    ("LivingRoom", "Kitchen"),
    ("LivingRoom", "Bedroom"),
    ("Kitchen", "Bathroom"),
    ("Kitchen", "LaundryRoom"),
]


def connected_predicates() -> frozenset:
    preds = set()
    for a, b in ADJACENCY:
        preds.add(("connected", a, b))
        preds.add(("connected", b, a))
    return frozenset(preds)


# --------------------------------------------------------------------------
# Agent A: Vacuum robot
# --------------------------------------------------------------------------

def vacuum_actions() -> list[Action]:
    actions = []
    for a, b in ADJACENCY:
        for src, dst in ((a, b), (b, a)):
            actions.append(Action(
                name=f"Move({src}->{dst})",
                agent="Vacuum",
                preconditions=frozenset({("robot-at", src), ("connected", src, dst)}),
                add_effects=frozenset({("robot-at", dst)}),
                del_effects=frozenset({("robot-at", src)}),
                base_energy_cost=5,
                duration_min=2,
            ))
    for r in ROOMS:
        actions.append(Action(
            name=f"Clean({r})",
            agent="Vacuum",
            preconditions=frozenset({("robot-at", r), ("dirty", r)}),
            add_effects=frozenset({("clean", r)}),
            del_effects=frozenset({("dirty", r)}),
            base_energy_cost=12,
            duration_min=6,
        ))
    return actions


# --------------------------------------------------------------------------
# Agent B: Washing machine (parametrized by load id, e.g. "Load1")
# --------------------------------------------------------------------------

def washer_actions(load: str) -> list[Action]:
    return [
        Action(
            name=f"LoadClothes({load})",
            agent="Washer",
            preconditions=frozenset({("laundry-ready", load), ("machine-empty",)}),
            add_effects=frozenset({("laundry-loaded", load)}),
            del_effects=frozenset({("machine-empty",)}),
            base_energy_cost=2,
            duration_min=3,
        ),
        Action(
            name=f"AddDetergent({load})",
            agent="Washer",
            preconditions=frozenset({("laundry-loaded", load)}),
            add_effects=frozenset({("detergent-added", load)}),
            del_effects=frozenset(),
            base_energy_cost=1,
            duration_min=1,
        ),
        Action(
            name=f"RunWashCycle({load})",
            agent="Washer",
            preconditions=frozenset({("laundry-loaded", load), ("detergent-added", load)}),
            add_effects=frozenset({("wash-done", load)}),
            del_effects=frozenset(),
            base_energy_cost=40,
            duration_min=45,
        ),
        Action(
            name=f"RunDryCycle({load})",
            agent="Washer",
            preconditions=frozenset({("wash-done", load)}),
            add_effects=frozenset({("dry-done", load), ("machine-empty",)}),
            del_effects=frozenset({("laundry-loaded", load), ("detergent-added", load)}),
            base_energy_cost=35,
            duration_min=40,
        ),
    ]


# --------------------------------------------------------------------------
# Agent C: Dishwasher
# --------------------------------------------------------------------------

def dishwasher_actions() -> list[Action]:
    return [
        Action(
            name="LoadDishes",
            agent="Dishwasher",
            preconditions=frozenset({("dishes-dirty",), ("dishwasher-empty",)}),
            add_effects=frozenset({("dishes-loaded",)}),
            del_effects=frozenset({("dishwasher-empty",)}),
            base_energy_cost=2,
            duration_min=3,
        ),
        Action(
            name="AddDetergentDW",
            agent="Dishwasher",
            preconditions=frozenset({("dishes-loaded",)}),
            add_effects=frozenset({("dw-detergent-added",)}),
            del_effects=frozenset(),
            base_energy_cost=1,
            duration_min=1,
        ),
        Action(
            name="RunWashCycleDW",
            agent="Dishwasher",
            preconditions=frozenset({("dishes-loaded",), ("dw-detergent-added",)}),
            add_effects=frozenset({("dishes-clean",)}),
            del_effects=frozenset({("dishes-dirty",), ("dishes-loaded",), ("dw-detergent-added",)}),
            base_energy_cost=50,
            duration_min=60,
        ),
    ]


def all_actions(loads: list[str]) -> list[Action]:
    actions = vacuum_actions() + dishwasher_actions()
    for load in loads:
        actions.extend(washer_actions(load))
    return actions


# --------------------------------------------------------------------------
# Time-of-day energy pricing
# --------------------------------------------------------------------------

PEAK_START_MIN = 8 * 60     # 08:00
PEAK_END_MIN = 20 * 60      # 20:00
PEAK_MULTIPLIER = 1.5
OFFPEAK_MULTIPLIER = 0.6

WINDOW_MINUTES = 60
BUDGET_PER_WINDOW = 800


def price_multiplier(sim_minute: int) -> float:
    """sim_minute is minutes since simulated midnight, wraps at 1440."""
    tod = sim_minute % (24 * 60)
    if PEAK_START_MIN <= tod < PEAK_END_MIN:
        return PEAK_MULTIPLIER
    return OFFPEAK_MULTIPLIER


def actual_cost(action: Action, sim_minute: int) -> float:
    return action.base_energy_cost * price_multiplier(sim_minute)


def window_index(sim_minute: int) -> int:
    return sim_minute // WINDOW_MINUTES
