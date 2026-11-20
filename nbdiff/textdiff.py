"""Line-oriented diffing with GNU diff comparison options and output formats.

This module knows nothing about notebooks: it diffs two lists of lines,
honouring the whitespace/case/blank-line/regex options of GNU diff, and can
render the result in unified, context, normal or side-by-side format.
Rendered lines are returned as :class:`Line` records (kind + prefix + text +
intraline emphasis spans) so the caller decides on indentation and colour.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Iterator, List, Optional, Pattern, Sequence, Tuple


@dataclass
class CompareOptions:
    ignore_case: bool = False  # -i
    ignore_tab_expansion: bool = False  # -E
    ignore_trailing_space: bool = False  # -Z
    ignore_space_change: bool = False  # -b
    ignore_all_space: bool = False  # -w
    ignore_blank_lines: bool = False  # -B
    ignore_matching: List[Pattern] = field(default_factory=list)  # -I RE
    strip_trailing_cr: bool = False  # --strip-trailing-cr
    minimal: bool = False  # -d
    speed_large_files: bool = False  # --speed-large-files
    tabsize: int = 8


_WS = re.compile(r"\s+")


def normalize_line(line: str, o: CompareOptions) -> str:
    """The comparison key of a line under the given options."""
    if o.strip_trailing_cr and line.endswith("\r"):
        line = line[:-1]
    if o.ignore_all_space:
        line = _WS.sub("", line)
    elif o.ignore_space_change:
        line = _WS.sub(" ", line).rstrip()
    else:
        if o.ignore_tab_expansion:
            line = line.expandtabs(o.tabsize)
        if o.ignore_trailing_space:
            line = line.rstrip()
    if o.ignore_case:
        line = line.casefold()
    return line


def is_ignorable_line(line: str, o: CompareOptions) -> bool:
    """True if a change consisting only of such lines is ignored (-B / -I)."""
    if o.ignore_blank_lines and not line.strip():
        return True
    return any(rx.search(line) for rx in o.ignore_matching)


def split_lines(text: str) -> List[str]:
    """Split on '\\n' only (unlike str.splitlines, which also splits on \\x0c etc.)."""
    if not text:
        return []
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


def text_key(text: str, o: CompareOptions) -> str:
    """Normalized form of a whole text, used to match cells/outputs quickly."""
    return "\n".join(normalize_line(ln, o) for ln in split_lines(text) if not is_ignorable_line(ln, o))


@dataclass(frozen=True)
class Op:
    tag: str  # 'equal', 'replace', 'delete', 'insert'
    i1: int
    i2: int
    j1: int
    j2: int
    ignorable: bool = False  # change consists only of -B/-I lines


@dataclass
class Line:
    kind: str  # 'hunk', 'ctx', 'del', 'add', 'sep', 'note', 'chg' (side-by-side), 'hdr'
    prefix: str
    text: str
    spans: Optional[List[Tuple[int, int]]] = None  # intraline emphasis ranges in text


class LineDiff:
    """Diff between two sequences of lines."""

    def __init__(self, a: Sequence[str], b: Sequence[str], opts: Optional[CompareOptions] = None):
        self.a = list(a)
        self.b = list(b)
        self.opts = opts or CompareOptions()
        ka = [normalize_line(x, self.opts) for x in self.a]
        kb = [normalize_line(x, self.opts) for x in self.b]
        big = len(ka) + len(kb) > 4000
        autojunk = self.opts.speed_large_files or (big and not self.opts.minimal)
        check_ignorable = self.opts.ignore_blank_lines or bool(self.opts.ignore_matching)
        ops: List[Op] = []
        if ka == kb:
            if self.a:
                ops.append(Op("equal", 0, len(self.a), 0, len(self.b)))
        else:
            sm = SequenceMatcher(None, ka, kb, autojunk=autojunk)
            for tag, i1, i2, j1, j2 in sm.get_opcodes():
                ign = False
                if tag != "equal" and check_ignorable:
                    ign = all(is_ignorable_line(x, self.opts) for x in self.a[i1:i2]) and all(
                        is_ignorable_line(x, self.opts) for x in self.b[j1:j2]
                    )
                ops.append(Op(tag, i1, i2, j1, j2, ign))
        self.ops = ops

    @classmethod
    def from_text(cls, a: str, b: str, opts: Optional[CompareOptions] = None) -> "LineDiff":
        return cls(split_lines(a), split_lines(b), opts)

    @property
    def changed(self) -> bool:
        return any(op.tag != "equal" and not op.ignorable for op in self.ops)

    def stats(self) -> Tuple[int, int]:
        """(lines added, lines removed), counting only significant changes."""
        added = removed = 0
        for op in self.ops:
            if op.tag != "equal" and not op.ignorable:
                removed += op.i2 - op.i1
                added += op.j2 - op.j1
        return added, removed

    def hunks(self, n: int = 3) -> List[List[Op]]:
        """Group ops into hunks with ``n`` lines of context.

        Like GNU diff, hunks are formed including ignorable changes and then
        dropped if *every* change in them is ignorable.
        """
        return [g for g in _group_ops(self.ops, n) if any(op.tag != "equal" and not op.ignorable for op in g)]


def _group_ops(ops: List[Op], n: int) -> Iterator[List[Op]]:
    """difflib.SequenceMatcher.get_grouped_opcodes, adapted to :class:`Op`."""
    codes = list(ops)
    if not codes:
        return
    if codes[0].tag == "equal":
        c = codes[0]
        codes[0] = Op("equal", max(c.i1, c.i2 - n), c.i2, max(c.j1, c.j2 - n), c.j2)
    if codes[-1].tag == "equal":
        c = codes[-1]
        codes[-1] = Op("equal", c.i1, min(c.i2, c.i1 + n), c.j1, min(c.j2, c.j1 + n))
    nn = n + n
    group: List[Op] = []
    for c in codes:
        if c.tag == "equal" and c.i2 - c.i1 > nn:
            group.append(Op("equal", c.i1, min(c.i2, c.i1 + n), c.j1, min(c.j2, c.j1 + n)))
            yield group
            group = []
            c = Op("equal", max(c.i1, c.i2 - n), c.i2, max(c.j1, c.j2 - n), c.j2)
        group.append(c)
    if group and not (len(group) == 1 and group[0].tag == "equal"):
        yield group


# ---------------------------------------------------------------------------
# intraline (word/character) emphasis


def _intraline(dels: List[str], adds: List[str]) -> Tuple[List[Optional[list]], List[Optional[list]]]:
    """Emphasis spans for paired removed/added lines of a 'replace' op."""
    sd: List[Optional[list]] = [None] * len(dels)
    sa: List[Optional[list]] = [None] * len(adds)
    for k in range(min(len(dels), len(adds))):
        x, y = dels[k], adds[k]
        if len(x) > 500 or len(y) > 500 or x == y:
            continue
        sm = SequenceMatcher(None, x, y, autojunk=False)
        if sm.ratio() < 0.35:
            continue  # lines are too different for emphasis to be useful
        a_sp, b_sp = [], []
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                continue
            if i2 > i1:
                a_sp.append((i1, i2))
            if j2 > j1:
                b_sp.append((j1, j2))
        sd[k], sa[k] = a_sp, b_sp
    return sd, sa


# ---------------------------------------------------------------------------
# output formats


def _range_unified(start: int, stop: int) -> str:
    beginning = start + 1
    length = stop - start
    if length == 1:
        return "%d" % beginning
    if not length:
        beginning -= 1
    return "%d,%d" % (beginning, length)


def _range_context(start: int, stop: int) -> str:
    beginning = start + 1
    length = stop - start
    if not length:
        beginning -= 1
    if length <= 1:
        return "%d" % beginning
    return "%d,%d" % (beginning, beginning + length - 1)


def _range_normal(start: int, stop: int) -> str:
    return "%d" % (start + 1) if stop - start == 1 else "%d,%d" % (start + 1, stop)


def format_unified(ld: LineDiff, n: int = 3, intraline: bool = True) -> List[Line]:
    out: List[Line] = []
    for group in ld.hunks(n):
        first, last = group[0], group[-1]
        out.append(Line("hunk", "", "@@ -%s +%s @@" % (_range_unified(first.i1, last.i2),
                                                      _range_unified(first.j1, last.j2))))
        for op in group:
            if op.tag == "equal":
                out.extend(Line("ctx", " ", x) for x in ld.a[op.i1:op.i2])
                continue
            dels, adds = ld.a[op.i1:op.i2], ld.b[op.j1:op.j2]
            sd, sa = _intraline(dels, adds) if (intraline and op.tag == "replace") else (
                [None] * len(dels), [None] * len(adds))
            out.extend(Line("del", "-", x, s) for x, s in zip(dels, sd))
            out.extend(Line("add", "+", x, s) for x, s in zip(adds, sa))
    return out


def format_context(ld: LineDiff, n: int = 3, intraline: bool = True) -> List[Line]:
    prefix = {"insert": "+ ", "delete": "- ", "replace": "! ", "equal": "  "}
    out: List[Line] = []
    for group in ld.hunks(n):
        first, last = group[0], group[-1]
        out.append(Line("sep", "", "***************"))
        out.append(Line("hunk", "", "*** %s ****" % _range_context(first.i1, last.i2)))
        if any(op.tag in ("replace", "delete") for op in group):
            for op in group:
                if op.tag == "insert":
                    continue
                kind = "del" if op.tag in ("replace", "delete") else "ctx"
                out.extend(Line(kind, prefix[op.tag], x) for x in ld.a[op.i1:op.i2])
        out.append(Line("hunk", "", "--- %s ----" % _range_context(first.j1, last.j2)))
        if any(op.tag in ("replace", "insert") for op in group):
            for op in group:
                if op.tag == "delete":
                    continue
                kind = "add" if op.tag in ("replace", "insert") else "ctx"
                out.extend(Line(kind, prefix[op.tag], x) for x in ld.b[op.j1:op.j2])
    return out


def format_normal(ld: LineDiff) -> List[Line]:
    out: List[Line] = []
    for op in ld.ops:
        if op.tag == "equal" or op.ignorable:
            continue
        if op.tag == "delete":
            cmd = "%sd%d" % (_range_normal(op.i1, op.i2), op.j1)
        elif op.tag == "insert":
            cmd = "%da%s" % (op.i1, _range_normal(op.j1, op.j2))
        else:
            cmd = "%sc%s" % (_range_normal(op.i1, op.i2), _range_normal(op.j1, op.j2))
        out.append(Line("hunk", "", cmd))
        out.extend(Line("del", "< ", x) for x in ld.a[op.i1:op.i2])
        if op.tag == "replace":
            out.append(Line("sep", "", "---"))
        out.extend(Line("add", "> ", x) for x in ld.b[op.j1:op.j2])
    return out


_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _fit(s: str, width: int, tabsize: int) -> str:
    s = _CTRL.sub("?", s.expandtabs(tabsize))
    return s[:width] if len(s) > width else s


def format_side_by_side(ld: LineDiff, width: int = 130, left_column: bool = False,
                        suppress_common: bool = False, tabsize: int = 8) -> List[Line]:
    """GNU ``diff -y`` style: ``left | right``, ``<`` deleted, ``>`` added."""
    half = max(4, (width - 3) // 2)
    out: List[Line] = []

    def row(left: str, mark: str, right: Optional[str], kind: str) -> Line:
        lt = _fit(left, half, tabsize).ljust(half)
        if right is None:
            return Line(kind, "", (lt + " " + mark).rstrip())
        return Line(kind, "", (lt + " " + mark + " " + _fit(right, half, tabsize)).rstrip())

    for op in ld.ops:
        if op.tag == "equal":
            if suppress_common:
                continue
            for i, j in zip(range(op.i1, op.i2), range(op.j1, op.j2)):
                if left_column:
                    out.append(row(ld.a[i], "(", None, "ctx"))
                else:
                    out.append(row(ld.a[i], " ", ld.b[j], "ctx"))
            continue
        na, nb = op.i2 - op.i1, op.j2 - op.j1
        for k in range(max(na, nb)):
            if k < na and k < nb:
                out.append(row(ld.a[op.i1 + k], "|", ld.b[op.j1 + k], "chg"))
            elif k < na:
                out.append(row(ld.a[op.i1 + k], "<", None, "del"))
            else:
                out.append(row("", ">", ld.b[op.j1 + k], "add"))
    return out
