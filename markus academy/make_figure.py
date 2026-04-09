"""
Figure: US homeownership rates by age of householder, 1994-2025.

Design follows Kieran Healy's principles (Data Visualization, Princeton UP):
  - Show the data; minimize non-data ink
  - Left + bottom spines only; light horizontal gridlines
  - Direct line labels instead of a legend
  - Sequential colorblind-safe palette (viridis) — age is ordinal
  - US overall shown as dashed gray reference line
  - NBER recession shading for substantive context
  - Source note in caption position
"""

import matplotlib
matplotlib.use("Agg")   # headless rendering; switch to "TkAgg" etc. if you want a window

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------

df = pd.read_csv("data/hvs_homeownership_by_age.csv")

# Quarter mid-month -> decimal year  (Q1=Feb, Q2=May, Q3=Aug, Q4=Nov)
Q_TO_FRAC = {"Q1": 2/12, "Q2": 5/12, "Q3": 8/12, "Q4": 11/12}
df["year_frac"] = df["year"] + df["quarter"].map(Q_TO_FRAC)

# ---------------------------------------------------------------------------
# Split US overall vs age groups
# ---------------------------------------------------------------------------

age_order = [
    "Under 35 years",
    "35 to 44 years",
    "45 to 54 years",
    "55 to 64 years",
    "65 years and over",
]
age_labels = {          # shorter display labels
    "Under 35 years":    "Under 35",
    "35 to 44 years":    "35–44",
    "45 to 54 years":    "45–54",
    "55 to 64 years":    "55–64",
    "65 years and over": "65+",
}

us_df  = df[df["age_group"] == "U.S."].sort_values("year_frac")
age_df = df[df["age_group"].isin(age_order)].copy()

# ---------------------------------------------------------------------------
# Colors — viridis sampled at 5 evenly spaced points (colorblind-safe)
# ---------------------------------------------------------------------------

cmap   = plt.cm.viridis
colors = [cmap(v) for v in np.linspace(0.15, 0.85, len(age_order))]

# ---------------------------------------------------------------------------
# NBER recession bands (start, end) as decimal years
# ---------------------------------------------------------------------------

RECESSIONS = [
    (2001 + 2/12,  2001 + 10/12),   # Mar 2001 – Nov 2001
    (2007 + 11/12, 2009 + 5/12),    # Dec 2007 – Jun 2009
    (2020 + 1/12,  2020 + 3/12),    # Feb 2020 – Apr 2020
]

# ---------------------------------------------------------------------------
# Figure setup
# ---------------------------------------------------------------------------

fig, ax = plt.subplots(figsize=(10, 6))

# --- recession shading (draw first so lines sit on top) ---
for r_start, r_end in RECESSIONS:
    ax.axvspan(r_start, r_end, color="#d9d9d9", alpha=0.6, zorder=0, linewidth=0)

# --- US overall reference line ---
ax.plot(
    us_df["year_frac"], us_df["homeownership_rate"],
    color="#aaaaaa", linewidth=1.2, linestyle="--", zorder=2
)
# label at right edge
last_us = us_df.iloc[-1]
ax.text(
    last_us["year_frac"] + 0.2, last_us["homeownership_rate"],
    "U.S.", color="#888888", fontsize=9, va="center"
)

# --- age-group lines ---
for color, age in zip(colors, age_order):
    sub = age_df[age_df["age_group"] == age].sort_values("year_frac")
    ax.plot(sub["year_frac"], sub["homeownership_rate"],
            color=color, linewidth=1.8, zorder=3)
    # direct label at the right end
    last = sub.iloc[-1]
    ax.text(
        last["year_frac"] + 0.2, last["homeownership_rate"],
        age_labels[age], color=color, fontsize=9, va="center", fontweight="bold"
    )

# ---------------------------------------------------------------------------
# Axes styling — Healy / Tufte minimal
# ---------------------------------------------------------------------------

# Remove top and right spines
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)

# Light horizontal gridlines only
ax.yaxis.grid(True, color="#e0e0e0", linewidth=0.8, zorder=1)
ax.xaxis.grid(False)
ax.set_axisbelow(True)

# X-axis: ticks every 5 years, no extra padding on right (labels need room)
x_min = 1993.5
x_max = 2028.5      # extra room for direct labels
ax.set_xlim(x_min, x_max)
x_ticks = range(1995, 2026, 5)
ax.set_xticks(list(x_ticks))
ax.set_xticklabels([str(y) for y in x_ticks], fontsize=9)

# Y-axis
ax.set_ylim(25, 85)
ax.yaxis.set_major_locator(mticker.MultipleLocator(10))
ax.tick_params(axis="y", labelsize=9)
ax.tick_params(axis="both", length=3, color="#aaaaaa")

# Axis labels
ax.set_ylabel("Homeownership rate (%)", fontsize=10, labelpad=8)

# ---------------------------------------------------------------------------
# Titles and caption
# ---------------------------------------------------------------------------

fig.text(
    0.07, 0.96,
    "Homeownership Rates by Age of Householder, 1994–2025",
    fontsize=13, fontweight="bold", ha="left", va="top"
)
fig.text(
    0.07, 0.90,
    "Quarterly homeownership rate (%) by age group. Gray bands mark NBER recessions.",
    fontsize=9.5, color="#555555", ha="left", va="top"
)
fig.text(
    0.07, 0.02,
    "Source: U.S. Census Bureau, Housing Vacancies and Homeownership Survey (CPS/HVS), Table 19.",
    fontsize=8, color="#888888", ha="left", va="bottom"
)

# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

out_path = "figures/homeownership_by_age.png"
import os; os.makedirs("figures", exist_ok=True)
fig.savefig(out_path, dpi=150, bbox_inches="tight", facecolor="white")
print(f"Saved -> {out_path}")
