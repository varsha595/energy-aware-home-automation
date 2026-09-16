"""
Runs the full scenario battery (backend/analysis.py), writes
docs/analysis_results.csv and docs/analysis_summary.md, and renders two
comparison charts into docs/. This is the "supporting analysis" deliverable
for the case study writeup.

Usage: python3 run_analysis.py
"""

import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from backend.analysis import run_battery, write_csv, write_markdown_table, SCENARIOS, STRATEGY_LABELS

DOCS_DIR = os.path.join(os.path.dirname(__file__), "docs")

# Brand-neutral, colorblind-safe palette, consistent across both charts.
COLORS = {
    "energy_aware": "#2563EB",     # blue
    "naive": "#F59E0B",            # amber
    "scratch_replan": "#DC2626",   # red
}


def energy_bar_chart(rows, path):
    scenarios = list(SCENARIOS.keys())
    strategies = list(STRATEGY_LABELS.keys())
    x = range(len(scenarios))
    width = 0.25

    fig, ax = plt.subplots(figsize=(9, 5))
    for i, strat in enumerate(strategies):
        values = [next(r["avg_energy_used"] for r in rows if r["scenario"] == s and r["strategy"] == strat) for s in scenarios]
        offset = (i - 1) * width
        ax.bar([xi + offset for xi in x], values, width, label=STRATEGY_LABELS[strat], color=COLORS[strat])

    budget = rows[0]["budget_per_window"]
    ax.axhline(budget, color="#6B7280", linestyle="--", linewidth=1, label=f"Budget/window ({budget:.0f})")
    ax.set_xticks(list(x))
    ax.set_xticklabels(scenarios, rotation=15, ha="right")
    ax.set_ylabel("Average total energy used (units)")
    ax.set_title("Total energy used: energy-aware vs. naive vs. replan-from-scratch")
    ax.legend(fontsize=8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def replan_overhead_chart(rows, path):
    scenarios = [s for s in SCENARIOS if SCENARIOS[s]["triggers"]]  # only scenarios with triggers have replans
    strategies = list(STRATEGY_LABELS.keys())
    x = range(len(scenarios))
    width = 0.25

    fig, ax = plt.subplots(figsize=(9, 5))
    for i, strat in enumerate(strategies):
        values = [next(r["avg_replan_extra_energy"] for r in rows if r["scenario"] == s and r["strategy"] == strat) for s in scenarios]
        offset = (i - 1) * width
        ax.bar([xi + offset for xi in x], values, width, label=STRATEGY_LABELS[strat], color=COLORS[strat])

    ax.set_xticks(list(x))
    ax.set_xticklabels(scenarios, rotation=15, ha="right")
    ax.set_ylabel("Extra energy wasted by replanning (units)")
    ax.set_title("Cost of replanning strategy: current-state replanning vs. from-scratch")
    ax.legend(fontsize=8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main():
    os.makedirs(DOCS_DIR, exist_ok=True)
    print("Running scenario battery (this takes ~30-60s)...")
    rows = run_battery()

    csv_path = os.path.join(DOCS_DIR, "analysis_results.csv")
    md_path = os.path.join(DOCS_DIR, "analysis_summary.md")
    write_csv(rows, csv_path)
    write_markdown_table(rows, md_path)
    print(f"Wrote {csv_path}")
    print(f"Wrote {md_path}")

    energy_bar_chart(rows, os.path.join(DOCS_DIR, "analysis_energy.png"))
    replan_overhead_chart(rows, os.path.join(DOCS_DIR, "analysis_replan_overhead.png"))
    print(f"Wrote {os.path.join(DOCS_DIR, 'analysis_energy.png')}")
    print(f"Wrote {os.path.join(DOCS_DIR, 'analysis_replan_overhead.png')}")

    print()
    with open(md_path) as f:
        print(f.read())


if __name__ == "__main__":
    main()
