"""Normalizing and diffing cell outputs.

Outputs are reduced to *parts* (one per mimetype, or text/traceback for
streams and errors) that can be compared meaningfully:

* stream outputs that Jupyter split into several chunks are merged,
  and carriage returns are applied the way a frontend would (so progress
  bars such as tqdm compare by what is visible, not by every redraw);
* ANSI escape sequences (coloured tracebacks) are stripped;
* JSON mimetypes are pretty-printed so they diff line by line;
* binary mimetypes (PNG, JPEG, PDF, ...) are compared by content and
  summarised by size and, for images, pixel dimensions.
"""

from __future__ import annotations

import base64
import binascii
import fnmatch
import json
import re
import struct
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import figures
from .notebook import is_json_mime
from .textdiff import CompareOptions, LineDiff, split_lines, text_key

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def apply_carriage_returns(text: str) -> str:
    """Render '\\r' the way a terminal/Jupyter frontend would (overwrite the line)."""
    text = text.replace("\r\n", "\n")
    if "\r" not in text:
        return text
    lines = []
    for line in text.split("\n"):
        if "\r" in line:
            cur = ""
            for seg in line.split("\r"):
                cur = seg + cur[len(seg):]
            line = cur
        lines.append(line)
    return "\n".join(lines)


def is_binary_mime(mime: str) -> bool:
    if mime == "image/svg+xml":
        return False
    return mime.startswith(("image/", "audio/", "video/")) or mime in (
        "application/pdf", "application/octet-stream", "application/zip")


def human_size(n: int) -> str:
    if n < 1024:
        return "%d B" % n
    for unit in ("KiB", "MiB", "GiB"):
        n /= 1024.0
        if n < 1024:
            return "%.1f %s" % (n, unit)
    return "%.1f TiB" % n


def image_dims(data: bytes) -> Optional[Tuple[int, int]]:
    """(width, height) of a PNG, GIF or JPEG, or None."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
            return struct.unpack(">II", data[16:24])
        if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
            return struct.unpack("<HH", data[6:10])
        if data[:2] == b"\xff\xd8":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    return None
                marker = data[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return w, h
                seglen = struct.unpack(">H", data[i + 2:i + 4])[0]
                i += 2 + seglen
    except struct.error:
        return None
    return None


@dataclass
class Part:
    """A comparable piece of an output (one mimetype, a stream's text, ...)."""
    name: str  # mimetype, or 'text' / 'error' / 'traceback' / 'metadata'
    binary: bool
    text: str = ""  # display text (for text parts)
    data: str = ""  # whitespace-free base64 (for binary parts)
    nbytes: int = 0
    dims: Optional[Tuple[int, int]] = None
    summary: Optional[str] = None  # overrides describe(), e.g. for plotly figures

    def describe(self) -> str:
        """One-line summary, e.g. 'image/png 23.4 KiB, 640×480 px'."""
        if self.summary:
            return self.summary
        if self.binary:
            s = human_size(self.nbytes)
            if self.dims:
                s += ", %d×%d px" % self.dims
            return s
        n = len(split_lines(self.text))
        return "%d line%s" % (n, "" if n == 1 else "s")


@dataclass
class NormOutput:
    index: int  # 1-based position in the (merged) output list
    output_type: str
    name: Optional[str]  # stream name
    parts: Dict[str, Part]
    raw: Dict[str, Any]  # renderable v4 output dict

    @property
    def label(self) -> str:
        if self.output_type == "stream":
            return "stream:%s" % self.name
        return self.output_type

    @property
    def signature(self) -> Tuple[str, Optional[str]]:
        return (self.output_type, self.name)

    def describe(self) -> str:
        """Short human summary of what this output contains."""
        if self.output_type == "stream":
            return "%s, %s" % (self.label, self.parts["text"].describe())
        if self.output_type == "error":
            return "error %s" % self.parts["error"].text
        mimes = [p for p in self.parts if p != "metadata"]
        main = _preferred_mime(mimes)
        extra = len(mimes) - 1
        s = "%s %s" % (self.output_type, main or "(empty)")
        if main:
            s += " (%s)" % self.parts[main].describe()
        if extra > 0:
            s += " +%d other mimetype%s" % (extra, "" if extra == 1 else "s")
        return s


MIME_PRIORITY = [
    figures.PLOTLY_MIME, "application/vnd.jupyter.widget-view+json", "application/javascript", "text/html",
    "text/markdown", "image/svg+xml", "text/latex", "image/png", "image/jpeg", "image/gif",
    "application/pdf", "application/json", "text/plain",
]


def _preferred_mime(mimes: Sequence[str]) -> Optional[str]:
    for m in MIME_PRIORITY:
        if m in mimes:
            return m
    return mimes[0] if mimes else None


def _plotly_part(mime: str, value: Any) -> Optional[Part]:
    """Figure-aware parts for plotly JSON and plotly-generated HTML (see figures.py)."""
    if mime == figures.PLOTLY_MIME:
        fig = value
        if isinstance(fig, str):
            try:
                fig = json.loads(fig)
            except ValueError:
                return None
        if isinstance(fig, dict):
            return Part(mime, False, text=figures.figure_text(fig), summary=figures.figure_summary(fig))
    elif mime == "text/html" and isinstance(value, str) and "lotly" in value:
        fig = figures.figure_from_html(value) if figures.is_plotly_html(value) else None
        if fig is not None:
            return Part(mime, False, text=figures.figure_text(fig),
                        summary=figures.figure_summary(fig) + " (HTML)")
        loader = figures.loader_text(value)
        if loader is not None:
            return Part(mime, False, text=loader, summary=loader)
        if figures.is_plotly_html(value):
            return Part(mime, False, text=figures.strip_plotly_ids(value))
    return None


def _make_part(mime: str, value: Any) -> Part:
    special = _plotly_part(mime, value)
    if special is not None:
        return special
    if is_json_mime(mime) and not isinstance(value, str):
        return Part(mime, False, text=json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False))
    if is_binary_mime(mime) and isinstance(value, str):
        b64 = re.sub(r"\s+", "", value)
        try:
            raw = base64.b64decode(b64, validate=False)
        except (binascii.Error, ValueError):
            raw = b""
        return Part(mime, True, data=b64, nbytes=len(raw), dims=image_dims(raw))
    return Part(mime, False, text=str(value))


def normalize_outputs(outputs: Sequence[Dict[str, Any]], include_metadata: bool = False,
                      ignore_mimes: Sequence[str] = ()) -> List[NormOutput]:
    """Merge stream chunks and convert outputs into comparable :class:`NormOutput`s."""
    merged: List[Dict[str, Any]] = []
    for o in outputs:
        if (o.get("output_type") == "stream" and merged and merged[-1].get("output_type") == "stream"
                and merged[-1].get("name") == o.get("name")):
            merged[-1] = dict(merged[-1], text=merged[-1].get("text", "") + o.get("text", ""))
        else:
            merged.append(o)

    result: List[NormOutput] = []
    for idx, o in enumerate(merged, 1):
        t = o.get("output_type", "unknown")
        parts: Dict[str, Part] = {}
        name = None
        if t == "stream":
            name = o.get("name", "stdout")
            parts["text"] = Part("text", False, text=apply_carriage_returns(strip_ansi(o.get("text", ""))))
        elif t == "error":
            parts["error"] = Part("error", False, text="%s: %s" % (o.get("ename", ""), o.get("evalue", "")))
            tb = strip_ansi("\n".join(o.get("traceback", [])))
            parts["traceback"] = Part("traceback", False, text=tb)
        elif t in ("execute_result", "display_data"):
            data = {m: v for m, v in (o.get("data") or {}).items()
                    if not any(fnmatch.fnmatchcase(m, pat) for pat in ignore_mimes)}
            for mime, value in data.items():
                if (mime == "text/html" and figures.PLOTLY_MIME in data and isinstance(value, str)
                        and figures.is_plotly_html(value)):
                    continue  # an HTML rendering of the plotly figure next to it (with a random div id)
                parts[mime] = _make_part(mime, value)
            if include_metadata and o.get("metadata"):
                parts["metadata"] = Part("metadata", False, text=json.dumps(
                    o["metadata"], indent=2, sort_keys=True, ensure_ascii=False))
        else:
            parts["raw"] = Part("raw", False, text=json.dumps(o, indent=2, sort_keys=True, ensure_ascii=False))
        result.append(NormOutput(idx, t, name, parts, o))
    return result


def output_key(o: NormOutput, opts: CompareOptions) -> Tuple:
    """Hashable identity of an output under the comparison options."""
    items = []
    for k, p in o.parts.items():
        items.append((k, p.data if p.binary else text_key(p.text, opts)))
    return (o.output_type, o.name, tuple(items))


@dataclass
class PartChange:
    name: str
    status: str  # 'modified', 'added', 'removed'
    a: Optional[Part] = None
    b: Optional[Part] = None
    diff: Optional[LineDiff] = None  # for text parts

    def note(self) -> str:
        """Summary for binary parts: '20.1 KiB → 22.3 KiB, 432×288 px'."""
        a, b = self.a, self.b
        if a and b and a.binary:
            s = "%s → %s" % (human_size(a.nbytes), human_size(b.nbytes))
            if a.dims or b.dims:
                da = "%d×%d" % a.dims if a.dims else "?"
                db = "%d×%d" % b.dims if b.dims else "?"
                s += ", %s px" % (da if da == db else "%s → %s" % (da, db))
            return s
        p = a or b
        return p.describe() if p else ""


@dataclass
class OutputChange:
    status: str  # 'unchanged', 'modified', 'added', 'deleted'
    a: Optional[NormOutput] = None
    b: Optional[NormOutput] = None
    parts: List[PartChange] = field(default_factory=list)

    @property
    def label(self) -> str:
        o = self.b or self.a
        return o.label if o else "?"


@dataclass
class OutputsDiff:
    changes: List[OutputChange]
    a: List[NormOutput]
    b: List[NormOutput]

    @property
    def changed(self) -> bool:
        return any(c.status != "unchanged" for c in self.changes)

    def counts(self) -> Dict[str, int]:
        d: Dict[str, int] = {}
        for c in self.changes:
            d[c.status] = d.get(c.status, 0) + 1
        return d


def compare_pair(a: NormOutput, b: NormOutput, opts: CompareOptions) -> OutputChange:
    changes: List[PartChange] = []
    names = list(a.parts) + [n for n in b.parts if n not in a.parts]
    for n in names:
        pa, pb = a.parts.get(n), b.parts.get(n)
        if pa is None:
            changes.append(PartChange(n, "added", None, pb))
        elif pb is None:
            changes.append(PartChange(n, "removed", pa, None))
        elif pa.binary or pb.binary:
            if pa.binary != pb.binary or pa.data != pb.data:
                changes.append(PartChange(n, "modified", pa, pb))
        else:
            ld = LineDiff.from_text(pa.text, pb.text, opts)
            if ld.changed:
                changes.append(PartChange(n, "modified", pa, pb, ld))
    return OutputChange("modified" if changes else "unchanged", a, b, changes)


def diff_outputs(raw_a: Sequence[Dict[str, Any]], raw_b: Sequence[Dict[str, Any]], opts: CompareOptions,
                 include_metadata: bool = False, ignore_mimes: Sequence[str] = ()) -> OutputsDiff:
    A = normalize_outputs(raw_a, include_metadata, ignore_mimes)
    B = normalize_outputs(raw_b, include_metadata, ignore_mimes)
    ka = [output_key(o, opts) for o in A]
    kb = [output_key(o, opts) for o in B]
    changes: List[OutputChange] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, ka, kb, autojunk=False).get_opcodes():
        if tag == "equal":
            changes.extend(OutputChange("unchanged", A[i1 + k], B[j1 + k]) for k in range(i2 - i1))
            continue
        # Within a changed region, pair outputs of the same kind (stream name /
        # output type) in order; anything left over was added or deleted.
        _pair_region(A[i1:i2], B[j1:j2], opts, changes, strict=True)
    return OutputsDiff(changes, A, B)


def _pair_region(sa: List[NormOutput], sb: List[NormOutput], opts: CompareOptions,
                 changes: List[OutputChange], strict: bool) -> None:
    """Pair changed outputs in order: first those of the same kind *and* the same
    mimetypes (so a figure is not paired with a loader script that appeared
    before it), then the rest by kind; leftovers were added or deleted."""
    def key(o: NormOutput) -> Tuple:
        return (o.signature, tuple(sorted(o.parts))) if strict else o.signature

    sub = SequenceMatcher(None, [key(o) for o in sa], [key(o) for o in sb], autojunk=False)
    for tag, p1, p2, q1, q2 in sub.get_opcodes():
        if tag == "equal":
            changes.extend(compare_pair(sa[p1 + k], sb[q1 + k], opts) for k in range(p2 - p1))
        elif strict:
            _pair_region(sa[p1:p2], sb[q1:q2], opts, changes, strict=False)
        else:
            changes.extend(OutputChange("deleted", o, None) for o in sa[p1:p2])
            changes.extend(OutputChange("added", None, o) for o in sb[q1:q2])


def outputs_equal(raw_a: Sequence[Dict[str, Any]], raw_b: Sequence[Dict[str, Any]], opts: CompareOptions,
                  include_metadata: bool = False, ignore_mimes: Sequence[str] = ()) -> bool:
    """Cheap equality test used for 'outputs differ' hints."""
    A = normalize_outputs(raw_a, include_metadata, ignore_mimes)
    B = normalize_outputs(raw_b, include_metadata, ignore_mimes)
    return [output_key(o, opts) for o in A] == [output_key(o, opts) for o in B]
