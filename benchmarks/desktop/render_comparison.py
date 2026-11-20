"""Render the published benchmark comparison from the checked-in measurements."""
from pathlib import Path
import json
from statistics import median

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator


RESULTS = Path(__file__).resolve().parent / "results"
CHATGPT = RESULTS / "chatgpt-26.1111.21308.json"
CODELITHE = RESULTS / "codelithe-ea8c53c.json"
CODELITHE_CODING = RESULTS / "codelithe-ea8c53c-gpt-6.2-sol-medium.json"

BACKGROUND = "#16181d"
FOREGROUND = "#eef2f7"
MUTED = "#c1cad8"
GRID = "#343c49"
APP_COLORS = ("#9aa9bf", "#4bcbb2")


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def phase_median(runs, phase, metric):
    return median(run["phases"][phase][metric] for run in runs if phase in run["phases"])


def render_panel(axis, title, description, values, unit, labels):
    axis.set_facecolor(BACKGROUND)
    axis.text(0, 1.14, title, transform=axis.transAxes, color=FOREGROUND,
              fontsize=16, fontweight="bold")
    axis.text(0, 1.06, description, transform=axis.transAxes, color=MUTED, fontsize=10)
    axis.set_axisbelow(True)
    axis.grid(axis="y", color=GRID, linewidth=0.8)
    axis.yaxis.set_major_locator(MaxNLocator(nbins=5))
    axis.set_ylim(0, max(values) * 1.20)
    axis.set_xlim(-0.6, 1.6)
    axis.tick_params(colors=MUTED, length=0, labelsize=10, pad=7)
    axis.set_xticks((0, 1), ("ChatGPT", "CodeLithe"))
    for spine in axis.spines.values():
        spine.set_visible(False)
    bars = axis.bar((0, 1), values, color=APP_COLORS, width=0.54)
    axis.bar_label(bars, labels=[f"{label}{unit}" for label in labels],
                   color=FOREGROUND, fontsize=11, fontweight="bold", padding=5)


def render_comparison(title, panels, note, filename):
    fig, axes = plt.subplots(1, 3, figsize=(11.8, 4.7), facecolor=BACKGROUND)
    fig.subplots_adjust(left=0.065, right=0.975, bottom=0.20, top=0.66, wspace=0.36)
    fig.text(0.05, 0.90, title, color=FOREGROUND, fontsize=24, fontweight="bold")
    fig.text(0.05, 0.82, "Native Linux windows  /  Lower is better", color=MUTED, fontsize=12)
    for axis, (heading, description, values, unit, precision) in zip(axes, panels, strict=True):
        render_panel(axis, heading, description, values, unit,
                     [format(value, precision) for value in values])
    fig.text(0.05, 0.065, note, color=MUTED, fontsize=9)
    fig.savefig(RESULTS / filename, dpi=150, facecolor=BACKGROUND)
    plt.close(fig)


def main():
    chatgpt, codelithe = load(CHATGPT), load(CODELITHE)
    startup = (
        phase_median(chatgpt, "startup_manual", "seconds"),
        phase_median(codelithe, "startup_automatic", "seconds"),
    )
    ram = tuple(phase_median(app, "idle", "pss_median_mib") for app in (chatgpt, codelithe))
    cpu = tuple(phase_median(app, "idle", "cpu_pct_one_core") for app in (chatgpt, codelithe))
    render_comparison(
        "Desktop benchmark",
        [
            ("Startup", "Different ready markers", startup, " s", ".2f"),
            ("Idle RAM", "Median PSS over 60 s", ram, " MiB", ",.1f"),
            ("Idle CPU", "Percentage of one logical core", cpu, "%", ".2f"),
        ],
        "ChatGPT startup used a manual marker. CodeLithe used an automatic marker and a scripted provider.",
        "desktop-comparison.png",
    )

    chatgpt_coding = next(run["phases"]["single"] for run in chatgpt if "single" in run["phases"])
    coding = (chatgpt_coding, load(CODELITHE_CODING)["phases"]["single"])
    render_comparison(
        "One active coding chat",
        [
            ("Completion time", "Different timing methods",
             tuple(run["seconds"] for run in coding), " s", ".2f"),
            ("Active RAM", "Median PSS", tuple(run["pss_median_mib"] for run in coding), " MiB", ",.1f"),
            ("Total CPU time", "CPU seconds across the app",
             tuple(run["cpu_seconds"] for run in coding), " s", ".2f"),
        ],
        "Both apps used GPT-6.2 Sol with Medium reasoning effort.",
        "coding-comparison.png",
    )


if __name__ == "__main__":
    main()
