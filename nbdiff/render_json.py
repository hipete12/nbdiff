"""``--json``: machine-readable rendering of notebook diffs."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from . import __version__
from .diff import CellDiff, MetaChange, NotebookDiff
from .outputs import OutputChange, OutputsDiff
from .textdiff import LineDiff, format_unified


def _unified(ld: Optional[LineDiff], n: int) -> Optional[List[str]]:
    if ld is None or not ld.changed:
        return None
    return [ln.prefix + ln.text for ln in format_unified(ld, n, intraline=False)]


def _meta(changes: Sequence[MetaChange]) -> List[Dict[str, Any]]:
    out = []
    for mc in changes:
        d: Dict[str, Any] = {"path": mc.path, "change": mc.kind}
        if mc.kind != "added":
            d["old"] = mc.old
        if mc.kind != "removed":
            d["new"] = mc.new
        out.append(d)
    return out


def _output_change(oc: OutputChange, n: int) -> Dict[str, Any]:
    d: Dict[str, Any] = {"status": oc.status, "output_type": (oc.b or oc.a).output_type,
                         "a_index": oc.a.index if oc.a else None, "b_index": oc.b.index if oc.b else None}
    o = oc.b or oc.a
    if o.name:
        d["name"] = o.name
    if oc.status in ("added", "deleted"):
        d["summary"] = o.describe()
        d["mimetypes"] = list(o.parts)
    elif oc.status == "modified":
        parts = []
        for pc in oc.parts:
            p: Dict[str, Any] = {"name": pc.name, "change": pc.status}
            if pc.diff is not None:
                p["diff"] = _unified(pc.diff, n)
            else:
                p["summary"] = pc.note()
            parts.append(p)
        d["parts"] = parts
    return d


def _outputs(od: Optional[OutputsDiff], n: int) -> Optional[Dict[str, Any]]:
    if od is None:
        return None
    return {"counts": od.counts(),
            "changes": [_output_change(oc, n) for oc in od.changes if oc.status != "unchanged"]}


def _cell(cd: CellDiff, n: int) -> Dict[str, Any]:
    d: Dict[str, Any] = {
        "status": cd.status,
        "a_index": cd.a.number if cd.a else None,
        "b_index": cd.b.number if cd.b else None,
        "cell_type": cd.cell.cell_type,
        "id": cd.cell.id,
    }
    if cd.status == "moved-away":
        return d
    if cd.changed:
        d["changed"] = list(cd.changed)
    if cd.hidden:
        d["not_compared_but_different"] = list(cd.hidden)
    if cd.similarity is not None:
        d["similarity"] = round(cd.similarity, 3)
    if cd.type_changed:
        d["type_change"] = [cd.a.cell_type, cd.b.cell_type]
    if cd.status in ("added", "deleted"):
        d["source"] = cd.cell.source
    elif cd.source is not None and "source" in cd.changed:
        d["source_diff"] = _unified(cd.source, n)
        add, rem = cd.source.stats()
        d["source_stats"] = {"added": add, "removed": rem}
    if cd.exec_counts and "execution count" in cd.changed:
        d["execution_count"] = list(cd.exec_counts)
    od = _outputs(cd.outputs, n)
    if od is not None and (od["changes"] or cd.status in ("added", "deleted")):
        d["outputs"] = od
    if cd.metadata:
        d["metadata"] = _meta(cd.metadata)
    if cd.attachments:
        d["attachments"] = _meta(cd.attachments)
    return d


def diff_to_dict(nd: NotebookDiff, context: int = 3, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    o = nd.options
    d: Dict[str, Any] = {
        "a": {"path": nd.a.label, "exists": nd.a.exists, "cells": len(nd.a.cells), "kernel": nd.a.kernel_name,
              "nbformat": nd.a.format_version},
        "b": {"path": nd.b.label, "exists": nd.b.exists, "cells": len(nd.b.cells), "kernel": nd.b.kernel_name,
              "nbformat": nd.b.format_version},
        "identical": nd.identical,
        "matched_by": nd.strategy,
        "compared": ["source"] + [k for k, on in (("outputs", o.outputs), ("metadata", o.metadata),
                                                 ("execution_count", o.execution_count)) if on],
        "counts": nd.counts(),
        "notebook_metadata": _meta(nd.metadata),
        "not_compared_but_different": nd.hidden_aspects(),
        "cells": [_cell(cd, context) for cd in nd.cells],
    }
    if extra:
        d.update(extra)
    return d


def render_json(diffs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    return {"nbdiff_version": __version__, "identical": all(d["identical"] for d in diffs), "comparisons": list(diffs)}
