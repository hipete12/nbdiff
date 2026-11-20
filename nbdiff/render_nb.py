"""``--visualize``: render one or more :class:`NotebookDiff` objects as a notebook.

The generated notebook is plain nbformat 4.5 and renders in JupyterLab,
Jupyter Notebook, VS Code, nbviewer and GitHub without any extension:

* an overview cell (files, cell counts, kernels, status totals and a
  "changes at a glance" table);
* for every changed cell a markdown cell with a coloured status heading
  (🟡 modified, 🟢 added, 🔴 deleted, 🔵 moved) and the source diff as a
  fenced ```` ```diff ```` block (syntax highlighted by every frontend);
* when outputs differ, two real code cells, **Before** and **After**,
  carrying the original outputs of each side - so plots, tables, HTML and
  errors are rendered natively next to each other (input collapsed);
* unchanged cells collapsed into ``<details>`` blocks (or copied verbatim
  with ``--unchanged=show``).

Every generated cell carries ``metadata.nbdiff`` (status, cell indices,
role) and an ``nbdiff-<status>`` tag, so the result is also machine-readable.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from . import __version__
from .diff import CellDiff, MetaChange, NotebookDiff, short_json, similarity_pct
from .notebook import Cell, Notebook, output_to_v4
from .outputs import OutputChange, OutputsDiff
from .textdiff import LineDiff, format_unified, split_lines

EMOJI = {"modified": "🟡", "added": "🟢", "deleted": "🔴", "moved": "🔵", "unchanged": "⚪",
         "moved-away": "🔵"}
TEXT_PRIORITY = ["text", "traceback", "error", "application/vnd.plotly.v1+json", "text/plain",
                 "text/markdown", "application/json", "text/latex", "text/html", "image/svg+xml", "application/javascript", "metadata", "raw"]


@dataclass
class VisualizeOptions:
    context: Optional[int] = None  # None: show whole cells; N: unified hunks with N context lines
    unchanged: str = "collapse"  # collapse | list | hide | show
    command: str = ""  # command line that produced the diff (for provenance)
    title: str = "Notebook diff"
    subtitle: str = ""  # e.g. "main → working tree" for --diff mode


@dataclass
class FileSection:
    diff: NotebookDiff
    heading: str = ""  # e.g. "analysis.ipynb" (multi-file mode)
    note: str = ""  # e.g. "renamed from old.ipynb (95% similar)"


# ---------------------------------------------------------------------------
# small markdown helpers


def fence(text: str, lang: str = "") -> str:
    """Fenced code block that is safe for any content (fence longer than any backtick run)."""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    body = text if text.endswith("\n") or not text else text + "\n"
    return "%s%s\n%s%s" % (ticks, lang, body, ticks)


def code_span(text: str) -> str:
    """Inline code that tolerates backticks in the text."""
    text = text.replace("\n", " ")
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    ticks = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return "%s%s%s%s%s" % (ticks, pad, text, pad, ticks)


def cell_escape(text: str) -> str:
    """Escape text for a markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ")


def html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _lines_keepends(text: str) -> List[str]:
    parts = text.split("\n")
    return [p + "\n" for p in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def _plural(n: int, word: str) -> str:
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def _lang(nb: Notebook) -> str:
    return (nb.language or "python").lower()


def _fence_lang(cell: Cell, nb: Notebook) -> str:
    if cell.cell_type == "code":
        return _lang(nb)
    return "markdown" if cell.cell_type == "markdown" else "text"


def diff_block(ld: LineDiff, context: Optional[int]) -> str:
    if context is None:
        lines = [ln for ln in format_unified(ld, 10 ** 9, intraline=False) if ln.kind != "hunk"]
    else:
        lines = format_unified(ld, context, intraline=False)
    return "\n".join(ln.prefix + ln.text for ln in lines)


def meta_block(changes: Sequence[MetaChange]) -> str:
    out = []
    for mc in changes:
        if mc.kind in ("removed", "changed"):
            out.append("- %s: %s" % (mc.path, short_json(mc.old, 200)))
        if mc.kind in ("added", "changed"):
            out.append("+ %s: %s" % (mc.path, short_json(mc.new, 200)))
    return "\n".join(out)


def _ts(nb: Notebook) -> str:
    if not nb.exists:
        return "—"
    return nb.timestamp()[:19]


# ---------------------------------------------------------------------------


class NotebookBuilder:
    def __init__(self, vo: VisualizeOptions):
        self.vo = vo
        self.cells: List[Dict[str, Any]] = []
        self._n = 0

    def _id(self) -> str:
        self._n += 1
        return "nbdiff-%04d" % self._n

    @staticmethod
    def _meta(status: Optional[str], role: str, cd: Optional[CellDiff] = None, **extra: Any) -> Dict[str, Any]:
        info: Dict[str, Any] = {"role": role}
        if status:
            info["status"] = status
        if cd is not None:
            info["a_index"] = cd.a.number if cd.a is not None else None
            info["b_index"] = cd.b.number if cd.b is not None else None
            if cd.changed:
                info["changed"] = list(cd.changed)
        info.update(extra)
        meta: Dict[str, Any] = {"nbdiff": info}
        if status:
            meta["tags"] = ["nbdiff-%s" % status]
        return meta

    def markdown(self, text: str, meta: Dict[str, Any]) -> None:
        self.cells.append({"cell_type": "markdown", "id": self._id(), "metadata": meta,
                           "source": _lines_keepends(text)})

    def code(self, source: str, outputs: List[Dict[str, Any]], meta: Dict[str, Any],
             execution_count: Optional[int] = None, hide_source: bool = True) -> None:
        if hide_source:
            meta = dict(meta, jupyter={"source_hidden": True}, collapsed=False)
        self.cells.append({"cell_type": "code", "id": self._id(), "metadata": meta,
                           "execution_count": execution_count, "outputs": outputs,
                           "source": _lines_keepends(source)})

    def raw_cell(self, cell: Cell, meta: Dict[str, Any]) -> None:
        d = cell.to_v4(self._id())
        d["metadata"] = dict(d.get("metadata") or {}, **meta)
        self.cells.append(d)

    # -- sections --------------------------------------------------------

    def overview(self, sections: Sequence[FileSection]) -> None:
        vo = self.vo
        md = ["# %s" % vo.title, ""]
        if vo.subtitle:
            md += ["**%s**" % vo.subtitle, ""]
        if not sections:
            md += ["✅ **No notebook differences found.**", ""]
        elif len(sections) == 1:
            md += self._file_table(sections[0].diff)
        else:
            md += ["| | Notebook | Cells | Result |", "|---|---|---|---|"]
            for sec in sections:
                nd = sec.diff
                md.append("| %s | %s | %s | %s |" % (
                    self._file_emoji(nd), cell_escape(code_span(sec.heading)),
                    self._cell_counts(nd), cell_escape(self._result_line(nd) + (" — " + sec.note if sec.note else ""))))
            md.append("")
        md.append("")
        md.append("<sub>Generated by nbdiff %s on %s%s</sub>" % (
            __version__, _dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z"),
            (" · " + code_span(vo.command)) if vo.command else ""))
        md += ["", "<details><summary>Legend</summary>", "",
               "| | Meaning |", "|---|---|",
               "| 🟡 | **modified** — the cell exists in both notebooks but differs |",
               "| 🟢 | **added** — the cell exists only in the second (B) notebook |",
               "| 🔴 | **deleted** — the cell exists only in the first (A) notebook |",
               "| 🔵 | **moved** — the cell changed position (and may also be modified) |",
               "| ⚪ | **unchanged** — no difference in the compared aspects |",
               "",
               "Diff blocks use `-` for lines of A and `+` for lines of B. When outputs differ, "
               "**Before** and **After** cells show the original outputs of each side "
               "(their code is collapsed — click the ellipsis to expand).",
               "", "</details>"]
        self.markdown("\n".join(md), self._meta(None, "overview"))

    @staticmethod
    def _file_emoji(nd: NotebookDiff) -> str:
        if not nd.a.exists:
            return "🟢"
        if not nd.b.exists:
            return "🔴"
        return "⚪" if nd.identical else "🟡"

    @staticmethod
    def _cell_counts(nd: NotebookDiff) -> str:
        na = str(len(nd.a.cells)) if nd.a.exists else "—"
        nb = str(len(nd.b.cells)) if nd.b.exists else "—"
        return "%s → %s" % (na, nb)

    @staticmethod
    def _result_line(nd: NotebookDiff) -> str:
        if nd.identical:
            return "identical"
        c = nd.counts()
        bits = ["%s %d %s" % (EMOJI[k], c[k], k) for k in ("modified", "added", "deleted", "moved", "unchanged")
                if c[k]]
        if nd.metadata:
            bits.append("notebook metadata changed")
        return " · ".join(bits)

    def _file_table(self, nd: NotebookDiff) -> List[str]:
        a, b = nd.a, nd.b
        md = ["| | Notebook | Cells | Kernel | nbformat | Modified |", "|---|---|---|---|---|---|"]
        for tag, nb in (("**A**", a), ("**B**", b)):
            md.append("| %s | %s | %s | %s | %s | %s |" % (
                tag, cell_escape(code_span(nb.label)), len(nb.cells) if nb.exists else "absent",
                cell_escape(nb.kernel_name or "—"), nb.format_version if nb.exists else "—", _ts(nb)))
        md.append("")
        md.append("**Result:** " + self._result_line(nd) + "  ")
        o = nd.options
        scope = ["source"] + [n for n, on in (("outputs", o.outputs), ("metadata", o.metadata),
                                             ("execution counts", o.execution_count)) if on]
        md.append("**Compared:** %s · cells matched by %s  " % (
            ", ".join(scope), "cell id" if nd.strategy == "id" else "content"))
        asp = nd.aspect_counts()
        extra = ["%s changed in %s" % (k, _plural(asp[k], "cell")) for k in ("source", "outputs", "metadata",
                                                                            "execution count") if asp.get(k)]
        if extra:
            md.append("**Details:** " + " · ".join(extra) + "  ")
        hidden = nd.hidden_aspects()
        if hidden:
            md.append("**Not compared but different:** " + ", ".join(hidden) + "  ")
        return md

    def glance(self, nd: NotebookDiff) -> None:
        rows = [cd for cd in nd.cells if cd.is_change]
        if not rows:
            return
        md = ["### Changes at a glance", "", "| | A | B | Type | Status | Changed | Preview |",
              "|---|---|---|---|---|---|---|"]
        for cd in rows:
            first = next((ln.strip() for ln in split_lines(cd.cell.source) if ln.strip()), "")
            if len(first) > 50:
                first = first[:49] + "…"
            md.append("| %s | %s | %s | %s | %s | %s | %s |" % (
                EMOJI[cd.status], cd.a.number if cd.a else "—", cd.b.number if cd.b else "—",
                cd.cell_type, cd.status, cell_escape(", ".join(cd.changed) or "—"),
                cell_escape(code_span(first)) if first else "*(empty)*"))
        unchanged = sum(1 for cd in nd.cells if cd.status == "unchanged")
        if unchanged:
            md += ["", "*%s not listed.*" % _plural(unchanged, "unchanged cell")]
        self.markdown("\n".join(md), self._meta(None, "glance"))

    def notebook_metadata(self, nd: NotebookDiff) -> None:
        if not nd.metadata:
            return
        md = ["### 🟡 Notebook metadata · modified", "", fence(meta_block(nd.metadata), "diff")]
        self.markdown("\n".join(md), self._meta("modified", "notebook-metadata"))

    def unchanged_run(self, nd: NotebookDiff, run: List[CellDiff]) -> None:
        mode = self.vo.unchanged
        if not run or mode == "hide":
            return
        if mode == "show":
            for cd in run:
                self.raw_cell(cd.b, self._meta("unchanged", "unchanged-copy", cd))
            return
        a1, a2 = run[0].a.number, run[-1].a.number
        b1, b2 = run[0].b.number, run[-1].b.number
        if len(run) == 1:
            rng = "Cell %d" % a1 + ("" if a1 == b1 else " → %d" % b1)
        else:
            rng = "Cells %d–%d" % (a1, a2) + ("" if (a1, a2) == (b1, b2) else " → %d–%d" % (b1, b2))
        summary = "⚪ %s unchanged%s" % (rng, "" if len(run) == 1 else " (%d cells)" % len(run))
        if mode == "list":
            lines = ["⚪ **%s** unchanged" % rng, ""]
            for cd in run:
                first = next((ln.strip() for ln in split_lines(cd.b.source) if ln.strip()), "")
                lines.append("- cell %d · %s · %s" % (cd.b.number, cd.b.cell_type,
                                                    code_span(first[:60]) if first else "*(empty)*"))
            self.markdown("\n".join(lines), self._meta("unchanged", "unchanged"))
            return
        body = ["<details><summary>%s</summary>" % html_escape(summary), ""]
        for cd in run:
            hint = " · *%s differ (not compared)*" % " and ".join(cd.hidden) if cd.hidden else ""
            body.append("**Cell %d** · %s%s" % (cd.b.number, cd.b.cell_type, hint))
            body.append("")
            body.append(fence(cd.b.source, _fence_lang(cd.b, nd.b)) if cd.b.source.strip() else "*(empty)*")
            body.append("")
        body.append("</details>")
        self.markdown("\n".join(body), self._meta("unchanged", "unchanged"))

    def cell_ref(self, cd: CellDiff) -> str:
        if cd.a is not None and cd.b is not None:
            if cd.a.number == cd.b.number:
                return "Cell %d" % cd.a.number
            return "Cell %d → %d" % (cd.a.number, cd.b.number)
        return "Cell %d" % cd.cell.number

    def changed_cell(self, nd: NotebookDiff, cd: CellDiff, moved_pos: Dict[int, int], here: int) -> None:
        status = cd.status
        if status == "moved-away":
            where = "below" if moved_pos.get(id(cd.b), here) > here else "above"
            self.markdown("🔵 *Cell %d of A was moved to cell %d of B (shown %s).*" % (
                cd.a.number, cd.b.number, where), self._meta("moved-away", "moved-away", cd))
            return
        a_lbl, b_lbl = nd.a.label, nd.b.label
        heading = "### %s %s · %s · " % (EMOJI[status], self.cell_ref(cd), code_span(cd.cell_type))
        if status == "added":
            heading += "added (only in %s)" % code_span(b_lbl)
        elif status == "deleted":
            heading += "deleted (only in %s)" % code_span(a_lbl)
        elif status == "moved":
            heading += "moved" + (" and modified" if cd.changed else "")
        else:
            heading += "modified"
        md = [heading, ""]

        if status in ("modified", "moved") and cd.changed:
            facts = []
            for asp in cd.changed:
                if asp == "execution count" and cd.exec_counts:
                    facts.append("execution count (%s → %s)" % cd.exec_counts)
                elif asp == "type":
                    facts.append("type (%s → %s)" % (cd.a.cell_type, cd.b.cell_type))
                else:
                    facts.append(asp)
            line = "**Changed:** " + ", ".join(facts)
            if cd.similarity is not None and ("source" in cd.changed or "type" in cd.changed):
                line += " · %d%% similar" % similarity_pct(cd.similarity)
            md += [line, ""]
        if cd.hidden:
            md += ["*Not compared but different: %s.*" % ", ".join(cd.hidden), ""]

        # source
        if status in ("added", "deleted"):
            if cd.cell.source.strip():
                md.append(fence(diff_block(cd.source, None), "diff"))
            else:
                md.append("*(empty cell)*")
            md.append("")
        elif "source" in cd.changed and cd.source is not None:
            md += [fence(diff_block(cd.source, self.vo.context), "diff"), ""]
        elif status == "moved" and cd.b.source.strip():
            md += ["<details><summary>Source (unchanged)</summary>", "",
                   fence(cd.b.source, _fence_lang(cd.b, nd.b)), "", "</details>", ""]

        # outputs (text-level summary; rendered outputs follow in Before/After cells)
        if cd.outputs is not None and cd.outputs.changed and status not in ("added", "deleted"):
            md += self.outputs_md(cd.outputs, a_lbl, b_lbl)
        if cd.metadata:
            md += ["**Metadata:**", "", fence(meta_block(cd.metadata), "diff"), ""]
        if cd.attachments:
            md += ["**Attachments:** " + ", ".join("%s %s" % (code_span(m.path), m.kind) for m in cd.attachments), ""]
        self.markdown("\n".join(md).rstrip() + "\n", self._meta(status, "header", cd))

        # rendered outputs
        show_outputs = cd.outputs is not None and (
            cd.outputs.changed if status in ("modified", "moved") else bool(cd.cell.outputs))
        if show_outputs:
            if cd.a is not None and status != "added":
                self.side_cell(cd, cd.a, nd.a, "before")
            if cd.b is not None and status != "deleted":
                self.side_cell(cd, cd.b, nd.b, "after")

    def outputs_md(self, od: OutputsDiff, a_lbl: str, b_lbl: str) -> List[str]:
        c = od.counts()
        md = ["**Outputs:** " + ", ".join("%d %s" % (c[k], k) for k in ("modified", "added", "deleted", "unchanged")
                                          if c.get(k)), ""]
        for oc in od.changes:
            if oc.status == "unchanged":
                continue
            md += self.output_change_md(oc, a_lbl, b_lbl)
        return md

    def output_change_md(self, oc: OutputChange, a_lbl: str, b_lbl: str) -> List[str]:
        if oc.status in ("added", "deleted"):
            o = oc.b if oc.status == "added" else oc.a
            where = b_lbl if oc.status == "added" else a_lbl
            return ["- %s output %d %s %s (only in %s): %s" % (
                EMOJI[oc.status], o.index, code_span(o.label), oc.status, code_span(where),
                cell_escape(o.describe())), ""]
        ia, ib = oc.a.index, oc.b.index
        ref = "output %d" % ia if ia == ib else "output %d → %d" % (ia, ib)
        md = ["- 🟡 %s %s modified" % (ref, code_span(oc.label))]
        text_mods = [pc for pc in oc.parts if pc.status == "modified" and pc.diff is not None]
        main = next((n for n in TEXT_PRIORITY if n in [pc.name for pc in text_mods]), None)
        blocks = []
        for pc in oc.parts:
            if pc.status == "modified" and pc.diff is not None:
                if pc.name == main:
                    blocks.append((pc.name, diff_block(pc.diff, 3)))
                else:
                    add, rem = pc.diff.stats()
                    md.append("  - %s modified (+%d −%d lines)" % (code_span(pc.name), add, rem))
            elif pc.status == "modified":
                md.append("  - %s changed: %s" % (code_span(pc.name), pc.note()))
            else:
                md.append("  - %s %s: %s" % (code_span(pc.name), pc.status, pc.note()))
        md.append("")
        for name, text in blocks:
            if name != "text":
                md.append("%s:" % code_span(name))
                md.append("")
            md += [fence(text, "diff"), ""]
        return md

    def side_cell(self, cd: CellDiff, cell: Cell, nb: Notebook, side: str) -> None:
        if side == "before":
            title = "◀ Before" if cd.status != "deleted" else "🔴 Deleted"
        else:
            title = "▶ After" if cd.status != "added" else "🟢 Added"
        where = "%s · cell %d" % (nb.label, cell.number)
        if cell.execution_count is not None:
            where += " · In [%s]" % cell.execution_count
        color = "#cf222e" if side == "before" else "#1a7f37"
        label = {
            "output_type": "display_data",
            "metadata": {},
            "data": {
                "text/html": ['<div style="border-left:4px solid %s;padding:2px 8px;font-family:sans-serif">'
                              '<b>%s</b> <span style="opacity:.7">— %s</span></div>' % (
                                  color, html_escape(title), html_escape(where))],
                "text/plain": ["%s — %s" % (title, where)],
            },
        }
        outputs = [label] + [output_to_v4(o) for o in cell.outputs]
        if not cell.outputs:
            outputs.append({"output_type": "display_data", "metadata": {},
                            "data": {"text/html": ["<i>(no output)</i>"], "text/plain": ["(no output)"]}})
        meta = self._meta(cd.status, side, cd)
        self.code(cell.source, outputs, meta, execution_count=cell.execution_count, hide_source=True)

    def file_body(self, nd: NotebookDiff) -> None:
        if nd.identical:
            hidden = nd.hidden_aspects()
            msg = "✅ **No differences** in the compared aspects."
            if hidden:
                msg += " (Not compared but different: %s.)" % ", ".join(hidden)
            self.markdown(msg, self._meta("unchanged", "identical"))
            return
        self.glance(nd)
        self.notebook_metadata(nd)
        moved_pos = {id(cd.b): k for k, cd in enumerate(nd.cells) if cd.status == "moved"}
        run: List[CellDiff] = []
        for k, cd in enumerate(nd.cells):
            if cd.status == "unchanged":
                run.append(cd)
                continue
            self.unchanged_run(nd, run)
            run = []
            self.changed_cell(nd, cd, moved_pos, k)
        self.unchanged_run(nd, run)


def render_notebook(sections: Sequence[FileSection], vo: Optional[VisualizeOptions] = None) -> Dict[str, Any]:
    """Build the diff notebook (as a JSON-serialisable dict)."""
    vo = vo or VisualizeOptions()
    nbb = NotebookBuilder(vo)
    nbb.overview(sections)
    multi = len(sections) > 1
    for sec in sections:
        if multi:
            nd = sec.diff
            md = ["## 📄 %s" % sec.heading, ""]
            if sec.note:
                md += ["*%s*" % sec.note, ""]
            md += nbb._file_table(nd)
            nbb.markdown("\n".join(md), nbb._meta(None, "file", file=sec.heading))
        nbb.file_body(sec.diff)

    # Use the kernel of the newest notebook so code cells get the right highlighting.
    meta: Dict[str, Any] = {}
    for sec in sections:
        for nb in (sec.diff.b, sec.diff.a):
            if nb.exists and nb.metadata.get("kernelspec") and "kernelspec" not in meta:
                meta["kernelspec"] = nb.metadata["kernelspec"]
                if nb.metadata.get("language_info"):
                    meta["language_info"] = nb.metadata["language_info"]
    meta["nbdiff"] = {
        "version": __version__,
        "generated": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "command": vo.command,
        "comparisons": [{"a": s.diff.a.label, "b": s.diff.b.label, "identical": s.diff.identical,
                         "counts": s.diff.counts()} for s in sections],
    }
    return {"cells": nbb.cells, "metadata": meta, "nbformat": 4, "nbformat_minor": 5}


def write_notebook(nb: Dict[str, Any], path: str) -> None:
    import sys
    text = json.dumps(nb, indent=1, ensure_ascii=False) + "\n"
    if path == "-":
        sys.stdout.write(text)
        sys.stdout.flush()
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
