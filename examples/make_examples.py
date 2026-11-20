#!/usr/bin/env python3
"""Generate examples/before.ipynb and examples/after.ipynb.

The pair exercises most of what nbdiff reports: edited sources, changed
text/HTML/PNG outputs, an error that disappears, carriage-return progress
output that only differs in intermediate redraws, added/deleted/moved
cells, cell tags and notebook metadata changes.  Run it with no arguments;
it uses only the standard library (PNG plots are drawn by hand).
"""

import base64
import json
import os
import struct
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))


def png_bar_chart(values, width=200, height=110, color=(31, 119, 180)):
    """A tiny bar chart as PNG bytes (no plotting library needed)."""
    vmax = max(values)
    n = len(values)
    slot = width // n
    rows = []
    for y in range(height):
        row = bytearray([0])  # filter type 0
        for x in range(width):
            k = min(x // slot, n - 1)
            bar_h = int((height - 10) * values[k] / vmax)
            in_bar = (slot * 0.15 <= x - k * slot <= slot * 0.85) and (height - 5 - bar_h <= y < height - 5)
            axis = y == height - 5
            px = color if in_bar else ((60, 60, 60) if axis else (255, 255, 255))
            row.extend(px)
        rows.append(bytes(row))

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def lines(text):
    """nbformat stores multi-line strings as lists of lines."""
    parts = text.split("\n")
    return [p + "\n" for p in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def md(cid, text, **meta):
    return {"cell_type": "markdown", "id": cid, "metadata": meta, "source": lines(text)}


def code(cid, text, count=None, outputs=(), **meta):
    return {"cell_type": "code", "id": cid, "metadata": meta, "execution_count": count,
            "outputs": list(outputs), "source": lines(text)}


def stream(text, name="stdout"):
    return {"output_type": "stream", "name": name, "text": lines(text)}


def result(count, plain, html=None):
    data = {"text/plain": lines(plain)}
    if html:
        data["text/html"] = lines(html)
    return {"output_type": "execute_result", "execution_count": count, "data": data, "metadata": {}}


def image(values, width):
    b64 = base64.b64encode(png_bar_chart(values, width=width)).decode()
    return {"output_type": "display_data", "metadata": {},
            "data": {"image/png": b64, "text/plain": ["<Figure size 640x480 with 1 Axes>"]}}


def error(ename, evalue, frames):
    red, green, reset = "\x1b[0;31m", "\x1b[0;32m", "\x1b[0m"
    tb = [red + "-" * 75 + reset,
          red + ename + reset + "                         Traceback (most recent call last)"]
    tb += [green + f + reset for f in frames]
    tb.append(red + ename + reset + ": " + evalue)
    return {"output_type": "error", "ename": ename, "evalue": evalue, "traceback": tb}


def table(rows):
    head = "<table border=\"1\" class=\"dataframe\">\n  <thead>\n    <tr><th></th><th>quarter</th><th>sales</th></tr>\n  </thead>\n  <tbody>\n"
    body = "".join("    <tr><th>%d</th><td>%s</td><td>%d</td></tr>\n" % (i, q, s) for i, (q, s) in enumerate(rows))
    plain = "  quarter  sales\n" + "\n".join("%d %7s %6d" % (i, q, s) for i, (q, s) in enumerate(rows))
    return plain, head + body + "  </tbody>\n</table>"


def notebook(cells, kernel_display, py_version):
    return {
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": kernel_display, "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": py_version, "mimetype": "text/x-python",
                              "file_extension": ".py"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def build():
    q4 = [("Q1", 3), ("Q2", 5), ("Q3", 2), ("Q4", 6)]
    q5 = [("Q1", 3), ("Q2", 5), ("Q3", 4), ("Q4", 6), ("Q5", 5)]
    plain4, html4 = table(q4)
    plain5, html5 = table(q5)
    progress_a = "\r  0%|          | 0/3\r 33%|###       | 1/3\r 67%|######    | 2/3\r100%|##########| 3/3\n"
    progress_b = "\r  0%|          | 0/3\r 50%|#####     | 1/3\r100%|##########| 3/3\n"

    before = notebook([
        md("intro", "# Sales analysis\n\nQuarterly sales exploration."),
        code("imports", "import pandas as pd\nimport numpy as np", 1),
        code("load", "rows = [(\"Q1\", 3), (\"Q2\", 5), (\"Q3\", 2), (\"Q4\", 6)]\n"
                     "df = pd.DataFrame(rows, columns=[\"quarter\", \"sales\"])\ndf.head()",
             2, [result(2, plain4, html4)]),
        code("total", "total = df[\"sales\"].sum()\nprint(\"total sales:\", total)", 3,
             [stream("total sales: 16\n")]),
        md("plot-title", "## Plot"),
        code("plot", "ax = df.plot.bar(x=\"quarter\", y=\"sales\", legend=False)", 4,
             [image([v for _, v in q4], 160)]),
        code("growth", "previous = 0\ngrowth = total / previous\ngrowth", 5,
             [error("ZeroDivisionError", "division by zero",
                    ["Cell In[5], line 2", "      1 previous = 0", "----> 2 growth = total / previous"])]),
        code("progress", "for _ in tqdm(range(3)):\n    time.sleep(0.1)", 6, [stream(progress_a, "stderr")]),
        md("old-notes", "## Old notes\n\nThese notes are out of date."),
        code("helper", "def summarize(frame):\n    \"\"\"Describe the numeric columns.\"\"\"\n"
                       "    return frame.describe()", 7),
    ], "Python 3", "3.11.4")

    after = notebook([
        md("intro", "# Sales analysis\n\nQuarterly sales exploration (now with Q5)."),
        code("imports", "import pandas as pd\nimport numpy as np\nimport matplotlib.pyplot as plt", 1),
        code("helper", "def summarize(frame):\n    \"\"\"Describe the numeric columns.\"\"\"\n"
                       "    return frame.describe()", 2),
        code("load", "rows = [(\"Q1\", 3), (\"Q2\", 5), (\"Q3\", 4), (\"Q4\", 6), (\"Q5\", 5)]\n"
                     "df = pd.DataFrame(rows, columns=[\"quarter\", \"sales\"])\ndf.head()",
             3, [result(3, plain5, html5)], tags=["data"]),
        code("total", "total = df[\"sales\"].sum()\nprint(\"total sales:\", total)", 4,
             [stream("total sales: 23\n")]),
        md("quality", "## Data quality\n\nNo missing values were found."),
        code("missing", "df.isna().sum()", 5, [result(5, "quarter    0\nsales      0\ndtype: int64")]),
        md("plot-title", "## Plot"),
        code("plot", "ax = df.plot.bar(x=\"quarter\", y=\"sales\", legend=False)", 6,
             [image([v for _, v in q5], 200)]),
        code("growth", "previous = 16\ngrowth = (total - previous) / previous\nprint(f\"growth: {growth:.1%}\")",
             7, [stream("growth: 43.8%\n")]),
        code("progress", "for _ in tqdm(range(3)):\n    time.sleep(0.1)", 8, [stream(progress_b, "stderr")]),
    ], "Python 3 (ipykernel)", "3.12.3")
    return before, after


def main():
    before, after = build()
    for name, nb in (("before.ipynb", before), ("after.ipynb", after)):
        with open(os.path.join(HERE, name), "w", encoding="utf-8") as f:
            json.dump(nb, f, indent=1, ensure_ascii=False)
            f.write("\n")
        print("wrote", os.path.join(HERE, name))


if __name__ == "__main__":
    main()
