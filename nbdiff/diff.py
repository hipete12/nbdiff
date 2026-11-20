"""The notebook diff model: compares two :class:`Notebook` objects cell by cell.

Every aligned cell gets a :class:`CellDiff` with a status

* ``unchanged`` - no difference in the compared aspects
* ``modified``  - same cell, but something compared differs
* ``added``     - exists only in the second notebook
* ``deleted``   - exists only in the first notebook
* ``moved``     - same cell at a different position (possibly also modified)
* ``moved-away`` - placeholder at the old position of a moved cell

The *scope* (which aspects count as differences) is chosen by the caller:
cell type and source are always compared; outputs, metadata (cell and
notebook level, incl. attachments) and execution counts are opt-in.
Differences in aspects outside the scope are still detected and recorded
as *hints* so the user can be told that, e.g., "outputs differ (use -o)".
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .align import align_cells, similarity, _Keys
from .notebook import Cell, Notebook
from .outputs import OutputsDiff, diff_outputs, outputs_equal
from .textdiff import CompareOptions, LineDiff, split_lines

STATUSES = ("unchanged", "modified", "added", "deleted", "moved")


@dataclass
class DiffOptions:
    compare: CompareOptions = field(default_factory=CompareOptions)
    outputs: bool = False
    metadata: bool = False
    execution_count: bool = False
    match: str = "auto"  # auto | id | content
    threshold: float = 0.5
    moves: bool = True
    ignore_metadata: List[str] = field(default_factory=list)  # fnmatch patterns on dotted keys
    ignore_mimes: List[str] = field(default_factory=list)


@dataclass
class MetaChange:
    path: str  # dotted key path, e.g. 'kernelspec.display_name'
    kind: str  # 'added', 'removed', 'changed'
    old: Any = None
    new: Any = None


def diff_json(a: Any, b: Any, path: str = "", ignore: Sequence[str] = ()) -> List[MetaChange]:
    """Recursive key-level diff of two JSON values."""
    if path and any(fnmatch.fnmatchcase(path, pat) for pat in ignore):
        return []
    if isinstance(a, dict) and isinstance(b, dict):
        out: List[MetaChange] = []
        for k in list(a) + [k for k in b if k not in a]:
            p = "%s.%s" % (path, k) if path else str(k)
            if any(fnmatch.fnmatchcase(p, pat) for pat in ignore):
                continue
            if k not in b:
                out.append(MetaChange(p, "removed", a[k], None))
            elif k not in a:
                out.append(MetaChange(p, "added", None, b[k]))
            else:
                out.extend(diff_json(a[k], b[k], p, ignore))
        return out
    if a != b or type(a) is not type(b):
        return [MetaChange(path or "(value)", "changed", a, b)]
    return []


@dataclass
class CellDiff:
    status: str
    a: Optional[Cell] = None
    b: Optional[Cell] = None
    source: Optional[LineDiff] = None
    type_changed: bool = False
    outputs: Optional[OutputsDiff] = None
    metadata: List[MetaChange] = field(default_factory=list)
    attachments: List[MetaChange] = field(default_factory=list)
    exec_counts: Optional[Tuple[Optional[int], Optional[int]]] = None  # set when they differ
    changed: List[str] = field(default_factory=list)  # in-scope aspects that differ
    hidden: List[str] = field(default_factory=list)  # out-of-scope aspects that differ
    similarity: Optional[float] = None

    @property
    def cell(self) -> Cell:
        """The most relevant cell (B if present, else A)."""
        return self.b if self.b is not None else self.a  # type: ignore[return-value]

    @property
    def cell_type(self) -> str:
        if self.type_changed and self.a and self.b:
            return "%s→%s" % (self.a.cell_type, self.b.cell_type)
        return self.cell.cell_type

    @property
    def is_change(self) -> bool:
        return self.status not in ("unchanged", "moved-away")


@dataclass
class NotebookDiff:
    a: Notebook
    b: Notebook
    cells: List[CellDiff]
    metadata: List[MetaChange]  # notebook-level metadata (+ nbformat version)
    metadata_hidden: bool  # notebook metadata differs but is out of scope
    strategy: str
    options: DiffOptions

    @property
    def identical(self) -> bool:
        return not self.metadata and not any(c.is_change for c in self.cells)

    def counts(self) -> Dict[str, int]:
        d = {s: 0 for s in STATUSES}
        for c in self.cells:
            if c.status in d:
                d[c.status] += 1
        return d

    def aspect_counts(self) -> Dict[str, int]:
        """Number of cells in which each aspect changed (in scope only)."""
        d: Dict[str, int] = {}
        for c in self.cells:
            if c.status in ("added", "deleted", "moved-away"):
                continue
            for asp in c.changed:
                d[asp] = d.get(asp, 0) + 1
        return d

    def hidden_aspects(self) -> List[str]:
        seen: List[str] = []
        for c in self.cells:
            for h in c.hidden:
                if h not in seen:
                    seen.append(h)
        if self.metadata_hidden and "metadata" not in seen:
            seen.append("metadata")
        return seen


def _cell_pair(a: Cell, b: Cell, opts: DiffOptions, status: str = "modified") -> CellDiff:
    co = opts.compare
    cd = CellDiff(status, a, b)
    cd.type_changed = a.cell_type != b.cell_type
    cd.source = LineDiff(split_lines(a.source), split_lines(b.source), co)
    if cd.type_changed:
        cd.changed.append("type")
    if cd.source.changed:
        cd.changed.append("source")

    if a.cell_type == "code" or b.cell_type == "code":
        if opts.outputs:
            od = diff_outputs(a.outputs, b.outputs, co, opts.metadata, opts.ignore_mimes)
            cd.outputs = od
            if od.changed:
                cd.changed.append("outputs")
        elif not outputs_equal(a.outputs, b.outputs, co, False, opts.ignore_mimes):
            cd.hidden.append("outputs")
        if a.execution_count != b.execution_count:
            cd.exec_counts = (a.execution_count, b.execution_count)
            if opts.execution_count:
                cd.changed.append("execution count")

    meta = diff_json(a.metadata, b.metadata, "", opts.ignore_metadata)
    att = diff_json(a.attachments or {}, b.attachments or {}, "", ())
    if opts.metadata:
        cd.metadata, cd.attachments = meta, att
        if meta:
            cd.changed.append("metadata")
        if att:
            cd.changed.append("attachments")
    elif meta or att:
        cd.hidden.append("metadata")

    if status != "moved":
        cd.status = "modified" if cd.changed else "unchanged"
    return cd


def _cell_single(cell: Cell, status: str, opts: DiffOptions) -> CellDiff:
    co = opts.compare
    lines = split_lines(cell.source)
    if status == "added":
        cd = CellDiff(status, None, cell, source=LineDiff([], lines, co))
        if opts.outputs and cell.outputs:
            cd.outputs = diff_outputs([], cell.outputs, co, opts.metadata, opts.ignore_mimes)
    else:
        cd = CellDiff(status, cell, None, source=LineDiff(lines, [], co))
        if opts.outputs and cell.outputs:
            cd.outputs = diff_outputs(cell.outputs, [], co, opts.metadata, opts.ignore_mimes)
    return cd


def diff_notebooks(a: Notebook, b: Notebook, opts: Optional[DiffOptions] = None) -> NotebookDiff:
    opts = opts or DiffOptions()
    entries, strategy = align_cells(a.cells, b.cells, opts.compare, opts.match, opts.threshold, opts.moves)
    ka = kb = None
    cells: List[CellDiff] = []
    for kind, i, j in entries:
        if kind == "pair":
            cd = _cell_pair(a.cells[i], b.cells[j], opts)
        elif kind == "moved":
            cd = _cell_pair(a.cells[i], b.cells[j], opts, status="moved")
        elif kind == "moved-away":
            cd = CellDiff("moved-away", a.cells[i], b.cells[j])
        elif kind == "added":
            cd = _cell_single(b.cells[j], "added", opts)
        else:
            cd = _cell_single(a.cells[i], "deleted", opts)
        if kind in ("pair", "moved") and cd.status != "unchanged":
            if ka is None:
                ka, kb = _Keys(a.cells, opts.compare), _Keys(b.cells, opts.compare)
            cd.similarity = similarity(ka, i, kb, j)
        cells.append(cd)

    nb_meta: List[MetaChange] = []
    if a.exists and b.exists:
        nb_meta = diff_json(a.metadata, b.metadata, "", opts.ignore_metadata)
        if a.format_version != b.format_version:
            nb_meta.insert(0, MetaChange("nbformat", "changed", a.format_version, b.format_version))
    hidden = False
    if not opts.metadata:
        hidden = bool(nb_meta)
        nb_meta = []
    return NotebookDiff(a, b, cells, nb_meta, hidden, strategy, opts)


def short_json(value: Any, limit: int = 80) -> str:
    """Compact JSON rendering of a metadata value, truncated to ``limit`` chars."""
    try:
        s = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        s = repr(value)
    if limit and len(s) > limit:
        s = s[: limit - 1] + "…"
    return s
