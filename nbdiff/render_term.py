"""Rendering a :class:`NotebookDiff` for the terminal.

Layout (unified format, colours omitted)::

    --- before.ipynb    2026-11-20 15:08:01.000000000 +0100
    +++ after.ipynb     2026-11-20 15:10:22.000000000 +0100
      6 → 7 cells · kernel Python 3 · matched by content · comparing source, outputs

    = cells 1–2 unchanged (2 cells)
    ~ cell 3 [code] modified: source, outputs  (92% similar)
      @@ -1,3 +1,3 @@
       import numpy as np
      -x = np.linspace(0, 1, 10)
      +x = np.linspace(0, 1, 100)
      outputs: 1 modified
        ~ output 1 [execute_result] modified
            @@ -1 +1 @@
            -0.5
            +0.505
    - cell 4 [markdown] deleted (only in before.ipynb)
      -## Old notes
    + cell 5 [code] added (only in after.ipynb)
      +print("hello")

    Summary: 6 → 7 cells: 2 unchanged, 1 modified, 1 added, 1 deleted
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from .diff import CellDiff, MetaChange, NotebookDiff, short_json, similarity_pct
from .outputs import OutputChange, OutputsDiff, Part, PartChange
from .textdiff import (Line, LineDiff, format_context, format_normal, format_side_by_side,
                       format_unified, split_lines)

TERM_MIME_PRIORITY = ["text", "error", "traceback", "text/plain", "text/markdown", "application/json",
                      "text/latex", "text/html", "image/svg+xml", "application/javascript", "metadata", "raw"]


@dataclass
class RenderOptions:
    format: str = "unified"  # unified | context | normal | side-by-side | stat
    context: int = 3
    width: int = 130
    left_column: bool = False
    suppress_common_lines: bool = False
    unchanged: str = "collapse"  # collapse | list | hide | show
    max_lines: int = 50  # per output part when dumping whole outputs (0 = unlimited)
    summary: bool = True
    header: bool = True
    intraline: bool = True
    color: bool = False
    unicode: bool = True
    initial_tab: bool = False  # -T
    expand_tabs: bool = False  # -t
    tabsize: int = 8


class Style:
    CODES = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33", "blue": "34",
             "magenta": "35", "cyan": "36", "reverse": "7"}

    def __init__(self, enabled: bool):
        self.enabled = enabled

    def __call__(self, text: str, *names: str) -> str:
        if not self.enabled or not names or not text:
            return text
        return "\x1b[%sm%s\x1b[0m" % (";".join(self.CODES[n] for n in names), text)


STATUS_STYLE = {
    "unchanged": ("=", ("dim",)),
    "modified": ("~", ("yellow", "bold")),
    "added": ("+", ("green", "bold")),
    "deleted": ("-", ("red", "bold")),
    "moved": (">", ("blue", "bold")),
    "moved-away": ("<", ("blue",)),
}

LINE_STYLE = {"del": ("red",), "add": ("green",), "hunk": ("cyan",), "sep": ("cyan",),
              "chg": ("yellow",), "note": ("dim",), "ctx": ()}


def _plural(n: int, word: str) -> str:
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def _pick_part(names: List[str]) -> Optional[str]:
    for n in TERM_MIME_PRIORITY:
        if n in names:
            return n
    return names[0] if names else None


class TerminalRenderer:
    def __init__(self, ro: RenderOptions):
        self.ro = ro
        self.a_label = self.b_label = ""
        self.s = Style(ro.color)
        u = ro.unicode
        self.arrow = "→" if u else "->"
        self.dash = "–" if u else "-"
        self.dot = "·" if u else "|"
        self.ell = "…" if u else "..."
        self.minus = "−" if u else "-"

    # -- helpers -----------------------------------------------------------

    def paint(self, line: Line, indent: str = "") -> str:
        ro, s = self.ro, self.s
        text = line.text
        spans = line.spans
        if ro.expand_tabs and "\t" in text:
            text = text.expandtabs(ro.tabsize)
            spans = None
        prefix = line.prefix
        if ro.initial_tab and line.kind in ("ctx", "del", "add", "chg") and prefix:
            prefix = prefix.rstrip() + "\t"
        style = LINE_STYLE.get(line.kind, ())
        if not s.enabled or not style:
            return indent + prefix + text
        if not spans:
            return indent + s(prefix + text, *style)
        pieces = [s(prefix, *style)]
        pos = 0
        for start, end in spans:
            if start > pos:
                pieces.append(s(text[pos:start], *style))
            pieces.append(s(text[start:end], *style, "reverse"))
            pos = end
        if pos < len(text):
            pieces.append(s(text[pos:], *style))
        return indent + "".join(pieces)

    def format_ld(self, ld: LineDiff, indent: str) -> List[str]:
        ro = self.ro
        f = ro.format
        if f == "context":
            lines = format_context(ld, ro.context)
        elif f == "normal":
            lines = format_normal(ld)
        elif f == "side-by-side":
            lines = format_side_by_side(ld, max(20, ro.width - len(indent)), ro.left_column,
                                        ro.suppress_common_lines, ro.tabsize)
        else:
            lines = format_unified(ld, ro.context, intraline=ro.intraline and ro.color)
        return [self.paint(ln, indent) for ln in lines]

    def dump(self, text: str, sign: str, indent: str, limit: Optional[int] = None) -> List[str]:
        """Show a whole text as added (+) or removed (-) lines."""
        ro = self.ro
        lines = split_lines(text)
        if not lines:
            return [indent + self.s("(empty)", "dim")]
        if ro.format == "side-by-side":
            ld = LineDiff([], lines) if sign == "+" else LineDiff(lines, [])
            out = self.format_ld(ld, indent)
        else:
            if ro.format == "normal":
                prefix = "> " if sign == "+" else "< "
            elif ro.format == "context":
                prefix = sign + " "
            else:
                prefix = sign
            kind = "add" if sign == "+" else "del"
            out = [self.paint(Line(kind, prefix, x), indent) for x in lines]
        limit = ro.max_lines if limit is None else limit
        if limit and len(out) > limit:
            more = len(out) - limit
            out = out[:limit] + [indent + self.s("%s %s not shown (--max-lines=%d)" % (
                self.ell, _plural(more, "more line"), limit), "dim")]
        return out

    def cell_ref(self, cd: CellDiff) -> str:
        if cd.a is not None and cd.b is not None:
            if cd.a.number == cd.b.number:
                return "cell %d" % cd.a.number
            return "cell %d%s%d" % (cd.a.number, self.arrow, cd.b.number)
        return "cell %d" % cd.cell.number

    def hint(self, hidden: List[str]) -> str:
        if not hidden:
            return ""
        flags = {"outputs": "-o", "metadata": "-m"}
        what = " and ".join(hidden)
        verb = "differ" if len(hidden) > 1 or hidden[0] == "outputs" else "differs"
        use = ", ".join(flags[h] for h in hidden if h in flags)
        return self.s("  (%s %s; use %s to show)" % (what, verb, use), "dim")

    def preview(self, cd: CellDiff, room: int = 60) -> str:
        for ln in split_lines(cd.cell.source):
            ln = ln.strip()
            if ln:
                if len(ln) > room:
                    ln = ln[: room - 1] + self.ell
                return self.s("  " + ln, "dim")
        return self.s("  (empty)", "dim")

    # -- sections ----------------------------------------------------------

    def file_header(self, nd: NotebookDiff) -> List[str]:
        s = self.s
        m1, m2 = ("***", "---") if self.ro.format == "context" else ("---", "+++")
        out = [s("%s %s\t%s" % (m1, nd.a.label, nd.a.timestamp()), "bold"),
               s("%s %s\t%s" % (m2, nd.b.label, nd.b.timestamp()), "bold")]
        info = []
        na = "%d" % len(nd.a.cells) if nd.a.exists else "absent"
        nb = "%d" % len(nd.b.cells) if nd.b.exists else "absent"
        info.append("%s %s %s cells" % (na, self.arrow, nb) if na != nb else _plural(len(nd.a.cells), "cell"))
        ka, kb = nd.a.kernel_name, nd.b.kernel_name
        if ka and kb and ka != kb:
            info.append("kernel %s %s %s" % (ka, self.arrow, kb))
        elif ka or kb:
            info.append("kernel %s" % (ka or kb))
        info.append("matched by %s" % ("cell id" if nd.strategy == "id" else "content"))
        o = nd.options
        scope = ["source"] + [n for n, on in (("outputs", o.outputs), ("metadata", o.metadata),
                                             ("execution counts", o.execution_count)) if on]
        info.append("comparing " + ", ".join(scope))
        out.append(s("  " + (" %s " % self.dot).join(info), "dim"))
        return out

    def meta_lines(self, changes: List[MetaChange], indent: str) -> List[str]:
        out = []
        for mc in changes:
            if mc.kind in ("removed", "changed"):
                out.append(self.paint(Line("del", "- ", "%s: %s" % (mc.path, short_json(mc.old))), indent))
            if mc.kind in ("added", "changed"):
                out.append(self.paint(Line("add", "+ ", "%s: %s" % (mc.path, short_json(mc.new))), indent))
        return out

    def notebook_meta(self, nd: NotebookDiff) -> List[str]:
        if nd.metadata:
            sym, style = STATUS_STYLE["modified"]
            return [self.s("%s notebook metadata modified" % sym, *style)] + self.meta_lines(nd.metadata, "    ")
        if nd.metadata_hidden and self.ro.format != "stat":
            return [self.s("  (notebook metadata differs; use -m to show)", "dim")]
        return []

    def unchanged_lines(self, run: List[CellDiff]) -> List[str]:
        mode = self.ro.unchanged
        sym, style = STATUS_STYLE["unchanged"]
        if not run or mode == "hide":
            return []
        if mode == "collapse" and len(run) > 1:
            a1, a2 = run[0].a.number, run[-1].a.number
            b1, b2 = run[0].b.number, run[-1].b.number
            rng = "%d%s%d" % (a1, self.dash, a2)
            if (a1, a2) != (b1, b2):
                rng += " %s %d%s%d" % (self.arrow, b1, self.dash, b2)
            return [self.s("%s cells %s unchanged (%d cells)" % (sym, rng, len(run)), *style)]
        out = []
        for cd in run:
            out.append(self.s("%s %s [%s] unchanged" % (sym, self.cell_ref(cd), cd.cell_type), *style)
                       + self.preview(cd) + self.hint(cd.hidden))
            if mode == "show":
                lines = split_lines(cd.b.source)
                if self.ro.format == "side-by-side" and cd.source is not None:
                    out.extend(self.format_ld(cd.source, "  "))
                else:
                    pre = "  " if self.ro.format in ("context", "normal") else " "
                    out.extend(self.paint(Line("ctx", pre, x), "  ") for x in lines)
        return out

    def cell_header(self, cd: CellDiff, pos: Dict[int, int], here: int) -> str:
        sym, style = STATUS_STYLE[cd.status]
        s = self.s
        ref = self.cell_ref(cd)
        if cd.status == "moved-away":
            where = "below" if pos.get(id(cd.b), here) > here else "above"
            return s("%s cell %d [%s] moved %s cell %d (shown %s)" % (
                sym, cd.a.number, cd.a.cell_type, self.arrow, cd.b.number, where), *style)
        head = "%s %s [%s] " % (sym, ref, cd.cell_type)
        if cd.status == "added":
            head += "added (only in %s)" % self.b_label
        elif cd.status == "deleted":
            head += "deleted (only in %s)" % self.a_label
        elif cd.status == "moved":
            head += "moved"
            if cd.changed:
                head += ", modified: " + ", ".join(self.aspect_list(cd))
        else:
            head += "modified: " + ", ".join(self.aspect_list(cd))
        line = s(head, *style)
        if cd.similarity is not None and ("source" in cd.changed or "type" in cd.changed):
            line += s("  (%d%% similar)" % similarity_pct(cd.similarity), "dim")
        return line + self.hint(cd.hidden)

    def aspect_list(self, cd: CellDiff) -> List[str]:
        if self.ro.format != "stat":
            return list(cd.changed)
        out = []
        for asp in cd.changed:
            if asp == "source" and cd.source is not None:
                add, rem = cd.source.stats()
                out.append("source (+%d %s%d)" % (add, self.minus, rem))
            elif asp == "outputs" and cd.outputs is not None:
                c = cd.outputs.counts()
                bits = ["%d %s" % (c[k], k) for k in ("modified", "added", "deleted") if c.get(k)]
                out.append("outputs (%s)" % ", ".join(bits))
            elif asp == "execution count" and cd.exec_counts:
                out.append("execution count (%s %s %s)" % (cd.exec_counts[0], self.arrow, cd.exec_counts[1]))
            else:
                out.append(asp)
        return out

    def cell_body(self, cd: CellDiff) -> List[str]:
        out: List[str] = []
        ind = "  "
        if cd.status in ("added", "deleted"):
            sign = "+" if cd.status == "added" else "-"
            out.extend(self.dump(cd.cell.source, sign, ind, limit=0))
        else:
            if cd.type_changed:
                out.append(self.s("%stype: %s %s %s" % (ind, cd.a.cell_type, self.arrow, cd.b.cell_type), "yellow"))
            if "source" in cd.changed and cd.source is not None:
                out.extend(self.format_ld(cd.source, ind))
            if cd.exec_counts and "execution count" in cd.changed:
                out.append(self.s("%sexecution count: %s %s %s" % (
                    ind, cd.exec_counts[0], self.arrow, cd.exec_counts[1]), "yellow"))
        if cd.outputs is not None and (cd.outputs.changed or cd.status in ("added", "deleted")):
            out.extend(self.outputs_section(cd.outputs, cd))
        if cd.metadata:
            out.append(ind + self.s("metadata:", "bold"))
            out.extend(self.meta_lines(cd.metadata, ind + "  "))
        if cd.attachments:
            out.append(ind + self.s("attachments:", "bold"))
            for mc in cd.attachments:
                sign = {"added": "+", "removed": "-", "changed": "~"}[mc.kind]
                kind = {"added": "add", "removed": "del", "changed": "chg"}[mc.kind]
                out.append(self.paint(Line(kind, sign + " ", "%s %s" % (mc.path, mc.kind)), ind + "  "))
        return out

    def outputs_section(self, od: OutputsDiff, cd: CellDiff) -> List[str]:
        ind, ind2 = "  ", "    "
        counts = od.counts()
        if cd.status in ("added", "deleted"):
            title = "outputs: %d" % len(od.changes)
        else:
            title = "outputs: " + ", ".join("%d %s" % (counts[k], k) for k in
                                           ("modified", "added", "deleted", "unchanged") if counts.get(k))
        out = [ind + self.s(title, "bold")]
        for oc in od.changes:
            if oc.status == "unchanged":
                continue
            out.extend(self.output_change(oc, cd, ind2))
        return out

    def output_change(self, oc: OutputChange, cd: CellDiff, ind: str) -> List[str]:
        sym, style = STATUS_STYLE["modified" if oc.status == "modified" else oc.status]
        body = ind + "    "
        out: List[str] = []
        if oc.status == "modified":
            ia, ib = oc.a.index, oc.b.index
            ref = "output %d" % ia if ia == ib else "output %d%s%d" % (ia, self.arrow, ib)
            out.append(self.s("%s %s [%s] modified" % (sym, ref, oc.label), *style))
            out[-1] = ind + out[-1]
            text_mods = [pc for pc in oc.parts if pc.status == "modified" and pc.diff is not None]
            main = _pick_part([pc.name for pc in text_mods])
            single = len(oc.parts) == 1 and oc.parts[0].name == "text"
            for pc in oc.parts:
                if pc.status == "modified" and pc.diff is not None:
                    if pc.name == main:
                        if not single:
                            out.append(body + self.s(pc.name + ":", "dim"))
                        out.extend(self.format_ld(pc.diff, body))
                    else:
                        add, rem = pc.diff.stats()
                        out.append(body + self.s("%s: modified (+%d %s%d lines)" % (
                            pc.name, add, self.minus, rem), "yellow"))
                elif pc.status == "modified":
                    out.append(body + self.s("%s: changed (%s)" % (pc.name, pc.note()), "yellow"))
                elif pc.status == "added":
                    out.append(self.paint(Line("add", "+ ", "%s: %s" % (pc.name, pc.note())), body))
                else:
                    out.append(self.paint(Line("del", "- ", "%s: %s" % (pc.name, pc.note())), body))
            return out
        o = oc.b if oc.status == "added" else oc.a
        sign = "+" if oc.status == "added" else "-"
        where = ""
        if cd.status not in ("added", "deleted"):
            where = " %s (only in %s)" % (oc.status, self.b_label if oc.status == "added" else self.a_label)
        out.append(ind + self.s("%s output %d [%s]%s" % (sym, o.index, o.label, where), *style))
        out.extend(self.dump_output(o.parts, sign, body))
        return out

    def dump_output(self, parts: Dict[str, Part], sign: str, ind: str) -> List[str]:
        out: List[str] = []
        names = [n for n, p in parts.items() if not p.binary and n not in ("metadata",)]
        if "traceback" in parts and parts["traceback"].text.strip():
            # the traceback already ends with "ename: evalue"
            return self.dump(parts["traceback"].text, sign, ind)
        main = _pick_part(names)
        for n, p in parts.items():
            if n == main:
                if len(parts) > 1 and n not in ("text", "traceback"):
                    out.append(ind + self.s(n + ":", "dim"))
                out.extend(self.dump(p.text, sign, ind))
            else:
                kind = "add" if sign == "+" else "del"
                out.append(self.paint(Line(kind, sign + " ", "%s: %s" % (n, p.describe())), ind))
        return out

    def summary(self, nd: NotebookDiff) -> str:
        s = self.s
        c = nd.counts()
        na = len(nd.a.cells) if nd.a.exists else 0
        nb = len(nd.b.cells) if nd.b.exists else 0
        parts = []
        for k in ("unchanged", "modified", "added", "deleted", "moved"):
            if c[k]:
                sym, style = STATUS_STYLE[k]
                parts.append(s("%d %s" % (c[k], k), *style) if k != "unchanged" else "%d %s" % (c[k], k))
        cells = "%d %s %d cells" % (na, self.arrow, nb) if na != nb else _plural(na, "cell")
        line = s("Summary:", "bold") + " %s: %s" % (cells, ", ".join(parts) or "no cells")
        asp = nd.aspect_counts()
        extras = []
        for k in ("outputs", "metadata", "execution count"):
            if asp.get(k):
                extras.append("%s changed in %s" % (k, _plural(asp[k], "cell")))
        if nd.metadata:
            extras.append("notebook metadata changed")
        hidden = nd.hidden_aspects()
        if hidden:
            flags = {"outputs": "-o", "metadata": "-m"}
            extras.append(s("not compared but different: %s (use %s)" % (
                ", ".join(hidden), " ".join(flags[h] for h in hidden)), "dim"))
        if extras:
            line += "; " + "; ".join(extras)
        return line

    # -- entry point -------------------------------------------------------

    def render(self, nd: NotebookDiff) -> List[str]:
        self.a_label, self.b_label = nd.a.label, nd.b.label
        out: List[str] = []
        if self.ro.header:
            out.extend(self.file_header(nd))
            out.append("")
        out.extend(self.notebook_meta(nd))
        pos = {id(cd.b): k for k, cd in enumerate(nd.cells) if cd.status == "moved"}
        run: List[CellDiff] = []
        for k, cd in enumerate(nd.cells):
            if cd.status == "unchanged":
                if cd.hidden and self.ro.unchanged == "collapse":
                    out.extend(self.unchanged_lines(run))
                    run = []
                    out.extend(self.unchanged_lines([cd]))
                else:
                    run.append(cd)
                continue
            out.extend(self.unchanged_lines(run))
            run = []
            out.append(self.cell_header(cd, pos, k))
            if self.ro.format != "stat" and cd.status != "moved-away":
                out.extend(self.cell_body(cd))
        out.extend(self.unchanged_lines(run))
        if self.ro.summary:
            out.append("")
            out.append(self.summary(nd))
        return out


def render_terminal(nd: NotebookDiff, ro: RenderOptions) -> List[str]:
    return TerminalRenderer(ro).render(nd)
