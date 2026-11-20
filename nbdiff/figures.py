"""Plotly-aware normalization of figure outputs.

A plotly figure in a notebook is stored as an ``application/vnd.plotly.v1+json``
bundle and/or as ``text/html`` that embeds the same figure in a
``Plotly.newPlot(...)`` call.  Compared naively, both are useless:

* the HTML carries a random ``<div id>`` that changes on every run, and the
  whole figure sits on one enormous line;
* pretty-printed JSON puts every data point on its own line, and plotly >= 6
  stores numpy arrays as base64 ``{"dtype", "bdata"}`` blobs.

This module turns a figure into one line per setting, with data arrays
summarized (count, min/max/mean and a content hash), so a diff reads like::

    -layout.title.text: "Daily rides per station"
    +layout.title.text: "Daily rides per station, 7-day rolling mean"
    -data[0].y: 365 values, min 167, max 653, mean 412.6  #3fa2c1d0
    +data[0].y: 365 values, min 236.6, max 568.3, mean 412.4  #9e01b7aa

Arrays are summarized by value, so the same data stored as a JSON list or as
a typed array compares equal.  Only the standard library is used.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
import struct
import sys
from array import array
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

PLOTLY_MIME = "application/vnd.plotly.v1+json"

INLINE_ITEMS = 12  # arrays this short (and narrow) are shown in full
INLINE_WIDTH = 72
LONG_STRING = 240

# plotly.js typed-array dtypes -> (array typecode, item size)
_DTYPES = {"i1": ("b", 1), "u1": ("B", 1), "u1c": ("B", 1), "i2": ("h", 2), "u2": ("H", 2),
           "i4": ("i", 4), "u4": ("I", 4), "f4": ("f", 4), "f8": ("d", 8)}
_STRUCT = {"b": "b", "B": "B", "h": "h", "H": "H", "i": "i", "I": "I", "f": "f", "d": "d"}

_NEWPLOT = re.compile(r"Plotly\.newPlot\(\s*")
_JSON = json.JSONDecoder()
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


# --------------------------------------------------------------------------- arrays

def _is_typed_array(v: Any) -> bool:
    return isinstance(v, dict) and "bdata" in v and "dtype" in v and set(v) <= {"bdata", "dtype", "shape"}


def _decode_typed(v: Dict[str, Any]) -> Optional[Tuple[List[float], Optional[Tuple[int, ...]]]]:
    spec = _DTYPES.get(str(v.get("dtype")))
    if spec is None:
        return None
    code, size = spec
    try:
        raw = base64.b64decode(str(v.get("bdata", "")), validate=False)
    except (binascii.Error, ValueError):
        return None
    if len(raw) % size:
        return None
    arr = array(code)
    if arr.itemsize == size:
        arr.frombytes(raw)
        if sys.byteorder == "big":
            arr.byteswap()
        values = list(arr)
    else:  # unusual platform item sizes: fall back to struct
        values = [t[0] for t in struct.iter_unpack("<" + _STRUCT[code], raw)]
    shape = None
    s = v.get("shape")
    if s is not None:
        try:
            dims = [int(x) for x in (s.split(",") if isinstance(s, str) else s)]
            shape = tuple(dims) if len(dims) > 1 else None
        except (TypeError, ValueError):
            shape = None
    return values, shape


def _is_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _numeric_values(v: Any) -> Optional[Tuple[List[Any], Optional[Tuple[int, ...]]]]:
    """Values and shape of a numeric array (list, list of lists, or typed array)."""
    if _is_typed_array(v):
        return _decode_typed(v)
    if not isinstance(v, list) or not v:
        return None
    if all(x is None or _is_number(x) for x in v):
        return (v, None) if any(_is_number(x) for x in v) else None
    rows: List[List[Any]] = []
    for row in v:
        r = _numeric_values(row)
        if r is None or r[1] is not None:
            return None
        rows.append(r[0])
    widths = {len(r) for r in rows}
    shape = (len(rows), widths.pop()) if len(widths) == 1 else (len(rows),)
    return [x for r in rows for x in r], shape


def fmt_num(x: float) -> str:
    if isinstance(x, int):
        return str(x)
    if math.isfinite(x) and x == int(x) and abs(x) < 1e15:
        return str(int(x))
    return format(x, ".6g")


def _short_hash(data: bytes) -> str:
    return "#" + hashlib.sha1(data).hexdigest()[:8]


def _numeric_summary(values: List[Any], shape: Optional[Tuple[int, ...]]) -> str:
    finite = [float(x) for x in values if x is not None and math.isfinite(x)]
    canon = array("d", [float(x) if x is not None and not math.isnan(x) else math.nan for x in values])
    if shape and len(shape) >= 2:
        size = "×".join(str(d) for d in shape) + " values"
    elif shape:
        size = "%d rows, %d values" % (shape[0], len(values))
    else:
        size = "%d value%s" % (len(values), "" if len(values) == 1 else "s")
    s = size
    if finite:
        lo, hi = min(finite), max(finite)
        s += ", min %s, max %s" % (fmt_num(lo), fmt_num(hi))
        if lo != hi:
            s += ", mean %s" % fmt_num(round(sum(finite) / len(finite), 10))
    missing = len(values) - len(finite)
    if missing:
        s += ", %d missing" % missing
    return s + "  " + _short_hash(canon.tobytes())


def _compact(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(", ", ": "), sort_keys=True)


def format_array(v: Any) -> str:
    """One-line description of a data array (or short arrays in full)."""
    num = _numeric_values(v)
    if num is not None:
        values, shape = num
        if shape is None and len(values) <= INLINE_ITEMS:
            inline = "[" + ", ".join("null" if x is None else fmt_num(x) for x in values) + "]"
            if len(inline) <= INLINE_WIDTH:
                return inline
        return _numeric_summary(values, shape)
    if isinstance(v, list):
        text = _compact(v)
        if len(v) <= INLINE_ITEMS and len(text) <= INLINE_WIDTH:
            return text
        digest = _short_hash(text.encode("utf-8"))
        if all(isinstance(x, list) and len(x) == 2 and _is_number(x[0]) and isinstance(x[1], str) for x in v):
            # a colorscale: [[0, "rgb(...)"], ..., [1, "rgb(...)"]]
            return "%d stops, %s … %s  %s" % (len(v), json.dumps(v[0][1]), json.dumps(v[-1][1]), digest)
        if all(isinstance(x, str) for x in v):
            uniq = len(set(v))
            s = "%d strings" % len(v)
            if uniq < len(v):
                s += " (%d distinct)" % uniq
            return "%s, %s … %s  %s" % (s, _clip(json.dumps(v[0], ensure_ascii=False), 40),
                                         _clip(json.dumps(v[-1], ensure_ascii=False), 40), digest)
        return "%d items  %s" % (len(v), digest)
    if _is_typed_array(v):  # undecodable dtype
        return "typed array %s, %d base64 chars  %s" % (v.get("dtype"), len(str(v.get("bdata", ""))),
                                                       _short_hash(_compact(v).encode("utf-8")))
    return _compact(v)


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


# --------------------------------------------------------------------------- flattening

def _scalar(v: Any) -> str:
    if isinstance(v, str):
        s = json.dumps(v, ensure_ascii=False)
        if len(s) > LONG_STRING:
            return "%s (%d chars)  %s" % (s[:120] + "…", len(v), _short_hash(v.encode("utf-8")))
        return s
    if _is_number(v):
        return fmt_num(v)
    return json.dumps(v)


def _leaf_count(v: Any) -> int:
    if isinstance(v, dict):
        return sum(_leaf_count(x) for x in v.values()) or 1
    if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
        return sum(_leaf_count(x) for x in v)
    return 1


def _flatten(value: Any, path: str, out: List[str]) -> None:
    if _is_typed_array(value):
        out.append("%s: %s" % (path, format_array(value)))
    elif isinstance(value, dict):
        if not value:
            out.append("%s: {}" % path)
        for k in sorted(value):
            _flatten(value[k], "%s.%s" % (path, k) if path else str(k), out)
    elif isinstance(value, list):
        if value and all(isinstance(x, dict) and not _is_typed_array(x) for x in value):
            for i, x in enumerate(value):
                _flatten(x, "%s[%d]" % (path, i), out)
        else:
            out.append("%s: %s" % (path, format_array(value) if value else "[]"))
    else:
        out.append("%s: %s" % (path, _scalar(value)))


def _trace_label(trace: Dict[str, Any]) -> str:
    label = str(trace.get("type", "scatter"))
    if trace.get("name") not in (None, ""):
        label += " " + json.dumps(str(trace["name"]), ensure_ascii=False)
    return label


def figure_lines(fig: Dict[str, Any]) -> List[str]:
    """The figure as one line per setting (data arrays summarized)."""
    out: List[str] = []
    data = fig.get("data")
    traces = data if isinstance(data, list) else []
    for i, trace in enumerate(traces):
        if not isinstance(trace, dict):
            out.append("data[%d]: %s" % (i, _compact(trace)))
            continue
        out.append("data[%d]: %s" % (i, _trace_label(trace)))
        for k in sorted(trace):
            if k not in ("type", "name"):
                _flatten(trace[k], "data[%d].%s" % (i, k), out)
    if data is not None and not isinstance(data, list):
        _flatten(data, "data", out)
    layout = fig.get("layout")
    if isinstance(layout, dict):
        for k in sorted(layout):
            if k == "template":
                tpl = layout[k]
                out.append("layout.template: %d settings  %s" % (
                    _leaf_count(tpl), _short_hash(_compact(tpl).encode("utf-8"))))
            else:
                _flatten(layout[k], "layout." + k, out)
    elif layout is not None:
        _flatten(layout, "layout", out)
    frames = fig.get("frames")
    if frames:
        names = [f.get("name") for f in frames if isinstance(f, dict) and f.get("name") is not None]
        s = "frames: %d animation frame%s" % (len(frames), "" if len(frames) == 1 else "s")
        if names:
            s += ", %s … %s" % (_clip(_scalar(names[0]), 30), _clip(_scalar(names[-1]), 30))
        out.append(s + "  " + _short_hash(_compact(frames).encode("utf-8")))
    for k in sorted(fig):
        if k not in ("data", "layout", "frames"):
            _flatten(fig[k], k, out)
    return out


def figure_text(fig: Dict[str, Any]) -> str:
    return "\n".join(figure_lines(fig))


def figure_summary(fig: Dict[str, Any]) -> str:
    """'plotly figure: 5 traces (scattergl ×5), "Daily rides per station"'."""
    data = fig.get("data") if isinstance(fig.get("data"), list) else []
    types = Counter(str(t.get("type", "scatter")) if isinstance(t, dict) else "?" for t in data)
    kinds = ", ".join(k if n == 1 else "%s ×%d" % (k, n) for k, n in types.items())
    s = "plotly figure: %d trace%s" % (len(data), "" if len(data) == 1 else "s")
    if kinds:
        s += " (%s)" % kinds
    title = (fig.get("layout") or {}).get("title") if isinstance(fig.get("layout"), dict) else None
    if isinstance(title, dict):
        title = title.get("text")
    if isinstance(title, str) and title:
        s += ", " + _clip(json.dumps(title, ensure_ascii=False), 60)
    return s


# --------------------------------------------------------------------------- HTML

def is_plotly_html(html: str) -> bool:
    return "Plotly.newPlot" in html or "plotly-graph-div" in html


def figure_from_html(html: str) -> Optional[Dict[str, Any]]:
    """Extract the figure from plotly's HTML (one ``Plotly.newPlot`` call), else None."""
    calls = list(_NEWPLOT.finditer(html))
    if len(calls) != 1:
        return None
    pos = calls[0].end()
    args: List[Any] = []
    try:
        for _ in range(4):
            value, pos = _JSON.raw_decode(html, pos)
            args.append(value)
            while pos < len(html) and html[pos].isspace():
                pos += 1
            if pos >= len(html) or html[pos] != ",":
                break
            pos += 1
            while pos < len(html) and html[pos].isspace():
                pos += 1
    except ValueError:
        return None
    if len(args) < 2 or not isinstance(args[1], list):
        return None
    fig: Dict[str, Any] = {"data": args[1]}
    if len(args) > 2:
        fig["layout"] = args[2]
    if len(args) > 3:
        fig["config"] = args[3]
    return fig


def loader_text(html: str) -> Optional[str]:
    """Summary of plotly's 'load plotly.js' HTML output, else None.

    The ``notebook_connected`` renderer emits a small script that loads
    plotly.js from a CDN; the ``notebook`` renderer inlines all of plotly.js
    (several MB).  Neither is interesting to diff line by line.
    """
    if "Plotly.newPlot" in html or not ("PlotlyConfig" in html or "plotly" in html.lower()):
        return None
    if len(html) > 100_000:
        m = re.search(r"plotly\.js v(\d+\.\d+\.\d+)", html)
        if not m:
            return None
        return "plotly.js loader: inline plotly.js v%s (%d chars)" % (m.group(1), len(html))
    if "PlotlyConfig" not in html:
        return None
    urls = sorted(set(re.findall(r"""(?:src=|import\s+)["']([^"']*plotly[^"']*)["']""", html)))
    return "plotly.js loader: " + (", ".join(urls) if urls else "(no URL)")


def strip_plotly_ids(html: str) -> str:
    """Replace the random element ids plotly puts into its HTML."""
    return _UUID.sub("<id>", html)
