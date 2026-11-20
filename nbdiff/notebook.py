"""Loading and normalizing Jupyter notebooks (nbformat v3 and v4), stdlib only.

Everything downstream works on the small :class:`Notebook` / :class:`Cell`
model defined here, so the rest of nbdiff never has to care whether a
notebook stored ``source`` as a list of strings or a single string, or
whether it is an old v3 notebook with worksheets.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class NotebookError(Exception):
    """A file could not be read or is not a Jupyter notebook."""


# nbformat v3 used short keys for output mimetypes.
V3_MIME = {
    "text": "text/plain",
    "html": "text/html",
    "svg": "image/svg+xml",
    "png": "image/png",
    "jpeg": "image/jpeg",
    "latex": "text/latex",
    "json": "application/json",
    "javascript": "application/javascript",
    "markdown": "text/markdown",
    "pdf": "application/pdf",
}


def join_text(value: Any) -> str:
    """nbformat 'multiline strings' may be a str or a list of str."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "".join(str(v) for v in value)
    return str(value)


def is_json_mime(mime: str) -> bool:
    return mime == "application/json" or mime.endswith("+json")


@dataclass
class Cell:
    index: int  # 0-based position in the notebook
    cell_type: str
    source: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    id: Optional[str] = None
    execution_count: Optional[int] = None
    outputs: List[Dict[str, Any]] = field(default_factory=list)  # v4-style, text joined
    attachments: Optional[Dict[str, Any]] = None

    @property
    def number(self) -> int:
        """1-based position, as shown to users."""
        return self.index + 1

    def to_v4(self, cell_id: Optional[str] = None) -> Dict[str, Any]:
        """Return a valid nbformat 4.5 cell dict for this cell."""
        d: Dict[str, Any] = {
            "cell_type": self.cell_type,
            "id": cell_id or self.id or "cell-%d" % self.number,
            "metadata": json.loads(json.dumps(self.metadata)),
            "source": self.source,
        }
        if self.cell_type == "code":
            d["execution_count"] = self.execution_count
            d["outputs"] = [output_to_v4(o) for o in self.outputs]
        elif self.attachments:
            d["attachments"] = self.attachments
        if self.cell_type not in ("code", "markdown", "raw"):
            d["cell_type"] = "raw"
        return d


@dataclass
class Notebook:
    path: str  # path as given on the command line ('-' for stdin)
    label: str  # name shown in output (path, --label, or git rev:path)
    cells: List[Cell] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    nbformat: int = 4
    nbformat_minor: int = 5
    mtime_ns: Optional[int] = None
    exists: bool = True  # False for an absent file treated as empty (-N, git add/delete)

    @property
    def kernel_name(self) -> Optional[str]:
        ks = self.metadata.get("kernelspec") or {}
        return ks.get("display_name") or ks.get("name")

    @property
    def language(self) -> Optional[str]:
        li = self.metadata.get("language_info") or {}
        ks = self.metadata.get("kernelspec") or {}
        return li.get("name") or ks.get("language")

    @property
    def has_ids(self) -> bool:
        return bool(self.cells) and all(c.id for c in self.cells)

    @property
    def format_version(self) -> str:
        return "%s.%s" % (self.nbformat, self.nbformat_minor)

    def timestamp(self) -> str:
        """GNU diff style timestamp, e.g. '2026-11-20 15:08:01.123456789 +0100'."""
        if self.mtime_ns is None:
            if not self.exists:
                return "1970-01-01 00:00:00.000000000 +0000"
            dt = _dt.datetime.now().astimezone()
            return dt.strftime("%Y-%m-%d %H:%M:%S.") + "%09d" % (dt.microsecond * 1000) + dt.strftime(" %z")
        secs, ns = divmod(self.mtime_ns, 1_000_000_000)
        dt = _dt.datetime.fromtimestamp(secs).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M:%S.") + "%09d" % ns + dt.strftime(" %z")


def output_to_v4(o: Dict[str, Any]) -> Dict[str, Any]:
    """Make a normalized output dict schema-valid for nbformat 4."""
    t = o.get("output_type")
    if t == "stream":
        return {"output_type": "stream", "name": o.get("name", "stdout"), "text": o.get("text", "")}
    if t == "error":
        return {
            "output_type": "error",
            "ename": o.get("ename", ""),
            "evalue": o.get("evalue", ""),
            "traceback": list(o.get("traceback", [])),
        }
    if t in ("execute_result", "display_data"):
        d = {"output_type": t, "data": dict(o.get("data", {})), "metadata": dict(o.get("metadata", {}))}
        if t == "execute_result":
            d["execution_count"] = o.get("execution_count")
        return d
    # unknown output type: degrade to a text display so the notebook stays valid
    return {
        "output_type": "display_data",
        "data": {"text/plain": json.dumps(o, indent=1, sort_keys=True)},
        "metadata": {},
    }


def _norm_output_v4(out: Any) -> Dict[str, Any]:
    if not isinstance(out, dict):
        return {"output_type": "display_data", "data": {"text/plain": str(out)}, "metadata": {}}
    o = dict(out)
    t = o.get("output_type")
    if t == "stream":
        o["name"] = o.get("name") or "stdout"
        o["text"] = join_text(o.get("text"))
    elif t in ("execute_result", "display_data", "update_display_data"):
        data = {}
        for mime, val in (o.get("data") or {}).items():
            data[mime] = val if is_json_mime(mime) else join_text(val)
        o["data"] = data
        o["metadata"] = o.get("metadata") or {}
        if t == "update_display_data":
            o["output_type"] = "display_data"
    elif t == "error":
        o["ename"] = str(o.get("ename", ""))
        o["evalue"] = str(o.get("evalue", ""))
        o["traceback"] = [str(x) for x in (o.get("traceback") or [])]
    return o


def _convert_output_v3(out: Any) -> Dict[str, Any]:
    if not isinstance(out, dict):
        return _norm_output_v4(out)
    t = out.get("output_type")
    if t == "pyout":
        t = "execute_result"
    elif t == "pyerr":
        t = "error"
    if t == "stream":
        return {"output_type": "stream", "name": out.get("stream", "stdout"), "text": join_text(out.get("text"))}
    if t == "error":
        return _norm_output_v4(
            {"output_type": "error", "ename": out.get("ename"), "evalue": out.get("evalue"),
             "traceback": out.get("traceback")}
        )
    data = {}
    for k, v in out.items():
        if k in V3_MIME:
            mime = V3_MIME[k]
        elif "/" in k:
            mime = k
        else:
            continue
        data[mime] = v if is_json_mime(mime) else join_text(v)
    res = {"output_type": t if t in ("execute_result", "display_data") else "display_data",
           "data": data, "metadata": out.get("metadata") or {}}
    if res["output_type"] == "execute_result":
        res["execution_count"] = out.get("prompt_number")
    return res


def empty_notebook(path: str, label: Optional[str] = None, exists: bool = False) -> Notebook:
    return Notebook(path=path, label=label or path, exists=exists)


def parse_notebook(nb: Any, path: str, label: Optional[str] = None,
                   mtime_ns: Optional[int] = None) -> Notebook:
    """Build a :class:`Notebook` from decoded notebook JSON."""
    label = label or path
    if not isinstance(nb, dict):
        raise NotebookError("%s: not a Jupyter notebook (top level is not a JSON object)" % path)
    if "cells" in nb:
        raw_cells = nb.get("cells") or []
        v3 = False
    elif "worksheets" in nb:
        raw_cells = [c for ws in (nb.get("worksheets") or []) for c in (ws.get("cells") or [])]
        v3 = True
    else:
        raise NotebookError('%s: not a Jupyter notebook (no "cells" found)' % path)
    if not isinstance(raw_cells, list):
        raise NotebookError('%s: not a Jupyter notebook ("cells" is not a list)' % path)

    cells: List[Cell] = []
    for i, rc in enumerate(raw_cells):
        if not isinstance(rc, dict):
            raise NotebookError("%s: cell %d is not a JSON object" % (path, i + 1))
        ctype = rc.get("cell_type", "code")
        meta = rc.get("metadata") or {}
        if v3:
            if ctype == "heading":
                level = int(rc.get("level", 1) or 1)
                src = "#" * level + " " + join_text(rc.get("source"))
                cells.append(Cell(i, "markdown", src, meta))
            elif ctype == "code":
                cells.append(Cell(i, "code", join_text(rc.get("input")), meta,
                                  execution_count=rc.get("prompt_number"),
                                  outputs=[_convert_output_v3(o) for o in rc.get("outputs") or []]))
            else:
                cells.append(Cell(i, ctype, join_text(rc.get("source")), meta))
        else:
            cell = Cell(i, ctype, join_text(rc.get("source")), meta, id=rc.get("id"),
                        attachments=rc.get("attachments") or None)
            if ctype == "code":
                cell.execution_count = rc.get("execution_count")
                cell.outputs = [_norm_output_v4(o) for o in rc.get("outputs") or []]
            cells.append(cell)

    major = nb.get("nbformat", 3 if v3 else 4)
    minor = nb.get("nbformat_minor", 0)
    return Notebook(path=path, label=label, cells=cells, metadata=nb.get("metadata") or {},
                    nbformat=major, nbformat_minor=minor, mtime_ns=mtime_ns)


def parse_notebook_bytes(data: bytes, path: str, label: Optional[str] = None,
                         mtime_ns: Optional[int] = None) -> Notebook:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise NotebookError("%s: not a Jupyter notebook (not UTF-8 text: %s)" % (path, e))
    if not text.strip():
        # An empty file (or /dev/null) is treated as a notebook with no cells.
        nb = empty_notebook(path, label, exists=True)
        nb.mtime_ns = mtime_ns
        return nb
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError as e:
        raise NotebookError("%s: not a Jupyter notebook (invalid JSON: %s)" % (path, e))
    return parse_notebook(decoded, path, label, mtime_ns)


def read_notebook(path: str, label: Optional[str] = None, missing_ok: bool = False) -> Notebook:
    """Read a notebook from ``path`` ('-' means stdin).

    With ``missing_ok`` an absent file is treated as an empty notebook,
    which is how ``diff -N`` treats absent files.
    """
    if path == "-":
        data = sys.stdin.buffer.read()
        return parse_notebook_bytes(data, path, label or "-")
    try:
        st = os.stat(path)
    except FileNotFoundError:
        if missing_ok:
            return empty_notebook(path, label, exists=False)
        raise NotebookError("%s: No such file or directory" % path)
    except OSError as e:
        raise NotebookError("%s: %s" % (path, e.strerror or e))
    if os.path.isdir(path):
        raise NotebookError("%s: Is a directory" % path)
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise NotebookError("%s: %s" % (path, e.strerror or e))
    return parse_notebook_bytes(data, path, label, st.st_mtime_ns)
