#!/usr/bin/env python3
"""Build and execute examples/plotly/before.ipynb and after.ipynb.

A realistic plotly analysis notebook (synthetic bike-share data) in two
versions.  Unlike examples/make_examples.py this one runs a real kernel,
so it needs:  pip install plotly pandas numpy nbformat nbclient ipykernel

The executed notebooks are committed, so you only need this script to
regenerate them.  What changes between the versions:

* the daily-rides figure switches to a 7-day rolling mean (data + layout change)
* the scatter plot gets a different colour scale and marginal histograms
* the heatmap cell moves up, unchanged (its output differs only in the
  random <div> id plotly writes into the HTML)
* a cell that raised a KeyError is fixed
* a subplot figure (stacked bars + pie) is added, df.describe() is deleted
* markdown edits
"""

import os
import sys

import nbformat
from nbclient import NotebookClient

HERE = os.path.dirname(os.path.abspath(__file__))

TITLE_A = """\
# Bike-share demand: exploratory analysis

Synthetic daily ride counts for five stations during 2025, with temperature and rain."""

TITLE_B = """\
# Bike-share demand: exploratory analysis (v2)

Synthetic daily ride counts for five stations during 2025, with temperature and rain.
Daily series are smoothed with a 7-day rolling mean."""

IMPORTS = """\
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots

pio.renderers.default = "plotly_mimetype+notebook_connected"
pio.templates.default = "plotly_white\""""

DATA = """\
rng = np.random.default_rng(42)
days = pd.date_range("2025-01-01", "2025-12-31", freq="D")
stations = {"Downtown": 420, "University": 310, "Harbor": 260, "Old Town": 200, "Airport": 150}

doy = days.day_of_year.to_numpy()
temp = 12 + 10 * np.sin(2 * np.pi * (doy - 110) / 365) + rng.normal(0, 2.5, len(days))
rain = rng.gamma(0.6, 3.0, len(days)) * (rng.random(len(days)) < 0.35)

frames = []
for name, base in stations.items():
    weekend = 1.25 if name == "Harbor" else 0.75
    level = base * np.where(days.day_of_week < 5, 1.0, weekend)
    expected = level * (1 + 0.025 * (temp - 12)) * np.exp(-0.08 * rain)
    frames.append(pd.DataFrame({
        "date": days, "station": name,
        "rides": rng.poisson(np.clip(expected, 5, None)),
        "temp_c": temp.round(1), "rain_mm": rain.round(1),
    }))
df = pd.concat(frames, ignore_index=True)
df.shape"""

LINE_A = """\
fig = px.line(df, x="date", y="rides", color="station", title="Daily rides per station")
fig.show()"""

LINE_B = """\
smooth = df.assign(rides=df.groupby("station")["rides"].transform(lambda s: s.rolling(7, min_periods=1).mean()))
fig = px.line(smooth, x="date", y="rides", color="station",
              title="Daily rides per station, 7-day rolling mean")
fig.update_layout(hovermode="x unified", legend_title_text="")
fig.show()"""

SCATTER_A = """\
daily = df.groupby("date", as_index=False).agg(
    rides=("rides", "sum"), temp_c=("temp_c", "first"), rain_mm=("rain_mm", "first"))
fig = px.scatter(daily, x="temp_c", y="rides", color="rain_mm", color_continuous_scale="Blues",
                 title="Total rides vs. temperature",
                 labels={"temp_c": "temperature (°C)", "rain_mm": "rain (mm)"})
fig.show()"""

SCATTER_B = """\
daily = df.groupby("date", as_index=False).agg(
    rides=("rides", "sum"), temp_c=("temp_c", "first"), rain_mm=("rain_mm", "first"))
fig = px.scatter(daily, x="temp_c", y="rides", color="rain_mm", color_continuous_scale=px.colors.sequential.Tealgrn,
                 marginal_x="histogram", marginal_y="box",
                 title="Total rides vs. temperature",
                 labels={"temp_c": "temperature (°C)", "rain_mm": "rain (mm)"})
fig.show()"""

HEATMAP = """\
hours = np.arange(24)
commute = np.exp(-((hours - 8) ** 2) / 3) + np.exp(-((hours - 17.5) ** 2) / 4)
leisure = np.exp(-((hours - 14) ** 2) / 12)
profile = np.array([0.8 * commute + 0.3 * leisure if d < 5 else leisure for d in range(7)])
fig = go.Figure(go.Heatmap(z=profile.round(3), x=hours, y=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
                           colorscale="Viridis"))
fig.update_layout(title="Relative demand by hour and weekday", xaxis_title="hour of day")
fig.show()"""

TOP_A = """\
top = df.groupby("station")["ride"].sum().sort_values(ascending=False)
top"""

TOP_B = """\
top = df.groupby("station")["rides"].sum().sort_values(ascending=False)
top"""

MONTHLY = """\
month = df["date"].dt.to_period("M").dt.to_timestamp()
monthly = df.groupby([month, "station"])["rides"].sum().unstack()
fig = make_subplots(rows=1, cols=2, column_widths=[0.65, 0.35],
                    subplot_titles=("Monthly totals", "Share by station"),
                    specs=[[{"type": "xy"}, {"type": "domain"}]])
for name in monthly.columns:
    fig.add_trace(go.Bar(x=monthly.index, y=monthly[name], name=name), row=1, col=1)
fig.add_trace(go.Pie(labels=monthly.columns, values=monthly.sum(), hole=0.45, showlegend=False), row=1, col=2)
fig.update_layout(barmode="stack", title="Monthly volume")
fig.show()"""

NOTES_A = """\
## Notes

- Weekday commuting dominates Downtown and University.
- Rain suppresses demand at every station."""

NOTES_B = """\
## Notes

- Weekday commuting dominates Downtown and University.
- Harbor is busier at weekends (leisure trips).
- Rain suppresses demand at every station; warm days add roughly 2.5% per °C."""


def build(version):
    md, code = nbformat.v4.new_markdown_cell, nbformat.v4.new_code_cell
    a = version == "before"
    cells = [
        ("title", md(TITLE_A if a else TITLE_B)),
        ("imports", code(IMPORTS)),
        ("data", code(DATA)),
        ("head", code("df.head()")),
        ("h-daily", md("## Daily rides per station" if a else "## Daily rides per station (7-day rolling mean)")),
        ("line", code(LINE_A if a else LINE_B)),
        ("scatter", code(SCATTER_A if a else SCATTER_B)),
        ("heatmap", code(HEATMAP)),
        ("top", code(TOP_A if a else TOP_B)),
        ("describe", code("df.describe()")),
        ("monthly", code(MONTHLY)),
        ("notes", md(NOTES_A if a else NOTES_B)),
    ]
    if a:
        cells = [c for c in cells if c[0] != "monthly"]
    else:
        cells = [c for c in cells if c[0] != "describe"]
        order = [k for k, _ in cells]
        order.remove("heatmap")
        order.insert(order.index("line"), "heatmap")  # heatmap moves above the line chart
        lookup = dict(cells)
        cells = [(k, lookup[k]) for k in order]
    for key, cell in cells:
        cell["id"] = key  # stable ids, as Jupyter would keep them across edits
    nb = nbformat.v4.new_notebook(cells=[c for _, c in cells])
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3 (ipykernel)", "language": "python"}
    return nb


def scrub(nb):
    """Replace the local environment's path in tracebacks with a neutral one."""
    prefix = sys.prefix.rstrip("/")
    for cell in nb.cells:
        for out in cell.get("outputs", []):
            if "traceback" in out:
                out["traceback"] = [line.replace(prefix, "/opt/venv") for line in out["traceback"]]
            if isinstance(out.get("text"), str):
                out["text"] = out["text"].replace(prefix, "/opt/venv")


def main():
    for version in ("before", "after"):
        nb = build(version)
        NotebookClient(nb, timeout=120, kernel_name="python3", allow_errors=True,
                       resources={"metadata": {"path": HERE}}).execute()
        scrub(nb)
        path = os.path.join(HERE, version + ".ipynb")
        nbformat.write(nb, path)
        print("wrote", os.path.relpath(path), file=sys.stderr)


if __name__ == "__main__":
    main()
