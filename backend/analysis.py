"""
The scenario battery for the required analysis (docs/DESIGN.md section 6):
energy-aware planner vs. (a) naive/no-energy-awareness and (b) full
replan-from-scratch, across a handful of small test scenarios, each run
with a few random seeds (the seed controls which actions randomly "fail").
"""

import csv
import statistics
from dataclasses import asdict

from backend.executor import Scenario
from backend.baseline import run_scenario, ScriptedTrigger

SEEDS = [1, 2, 3]

SCENARIOS = {
    "quiet-morning": dict(
        scenario=Scenario(dirty_rooms=["Kitchen"], robot_start="LivingRoom",
                           loads=["Load1"], dishes_dirty=False, start_minute=7 * 60),
        triggers=[],
    ),
    "typical-day": dict(
        scenario=Scenario(dirty_rooms=["Kitchen", "Bedroom"], robot_start="LivingRoom",
                           loads=["Load1"], dishes_dirty=True, start_minute=9 * 60),
        triggers=[ScriptedTrigger(at_step=3, kind="fault")],
    ),
    "busy-day-new-load": dict(
        scenario=Scenario(dirty_rooms=["Kitchen", "Bedroom"], robot_start="LivingRoom",
                           loads=["Load1"], dishes_dirty=True, start_minute=9 * 60),
        triggers=[ScriptedTrigger(at_step=3, kind="fault"), ScriptedTrigger(at_step=5, kind="new_load", arg="Load2")],
    ),
    "price-spike": dict(
        scenario=Scenario(dirty_rooms=["Kitchen"], robot_start="LivingRoom",
                           loads=["Load1"], dishes_dirty=True, start_minute=9 * 60),
        triggers=[ScriptedTrigger(at_step=2, kind="price_spike", arg=500.0)],
    ),
}

STRATEGY_LABELS = {
    "energy_aware": "Energy-aware (ours)",
    "naive": "Naive (no energy-awareness)",
    "scratch_replan": "Replan-from-scratch baseline",
}


def run_battery():
    """Returns a list of row-dicts, one per (scenario, strategy), averaged
    over SEEDS. Each row has the fields needed for the report table/charts."""
    rows = []
    for scen_name, cfg in SCENARIOS.items():
        for strategy in STRATEGY_LABELS:
            trials = [run_scenario(cfg["scenario"], strategy, cfg["triggers"], seed=s) for s in SEEDS]
            rows.append({
                "scenario": scen_name,
                "strategy": strategy,
                "strategy_label": STRATEGY_LABELS[strategy],
                "avg_energy_used": statistics.mean(t.total_energy_used for t in trials),
                "avg_replan_count": statistics.mean(t.replan_count for t in trials),
                "avg_replan_extra_energy": statistics.mean(t.replan_extra_energy for t in trials),
                "avg_total_minutes": statistics.mean(t.total_minutes for t in trials),
                "completed_fraction": statistics.mean(1.0 if t.completed else 0.0 for t in trials),
                "budget_per_window": trials[0].budget_per_window,
                "trials": len(trials),
            })
    return rows


def write_csv(rows, path):
    fieldnames = ["scenario", "strategy", "strategy_label", "avg_energy_used", "avg_replan_count",
                  "avg_replan_extra_energy", "avg_total_minutes", "completed_fraction",
                  "budget_per_window", "trials"]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_markdown_table(rows, path):
    lines = ["| Scenario | Strategy | Avg energy used | Avg replans | Extra energy from replanning | Completed |",
             "|---|---|---|---|---|---|"]
    for row in rows:
        lines.append(
            f"| {row['scenario']} | {row['strategy_label']} | {row['avg_energy_used']:.1f} | "
            f"{row['avg_replan_count']:.1f} | {row['avg_replan_extra_energy']:.1f} | "
            f"{row['completed_fraction'] * 100:.0f}% |"
        )
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
