"""Command line interface: ``nbdiff [OPTION]... FILES``."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shlex
import subprocess
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import __version__
from .diff import DiffOptions, NotebookDiff, diff_notebooks
from .gitmode import GitError, changed_notebooks, load_pair, parse_external_diff_args, parse_git_args
from .notebook import Notebook, NotebookError, read_notebook
from .render_json import diff_to_dict, render_json
from .render_nb import FileSection, VisualizeOptions, render_notebook, write_notebook
from .render_term import RenderOptions, Style, render_terminal
from .textdiff import CompareOptions

EXIT_SAME, EXIT_DIFF, EXIT_TROUBLE = 0, 1, 2

HELP = """\
Usage: nbdiff [OPTION]... FILES
       nbdiff --diff [OPTION]... [<commit> [<commit>]] [--] [<path>...]
Compare Jupyter notebooks cell by cell.

FILES are 'NB1 NB2', 'DIR1 DIR2', 'DIR NB', or 'NB DIR'.  If a FILE is '-',
read standard input.  Exit status is 0 if inputs are the same, 1 if
different, 2 if trouble.

Cells are paired up (by nbformat cell id when both notebooks have them,
otherwise by content), and each cell is reported as unchanged, modified,
added (only in the second notebook), deleted (only in the first) or moved.
By default the cell source (and type) is compared; outputs, metadata and
execution counts are compared on request.  Differences in aspects that
were not compared are still pointed out as hints.

Notebook options:
  -o, --outputs                 also compare cell outputs (text, HTML, images,
                                  errors, ...)
  -m, --metadata                also compare cell and notebook metadata and
                                  attachments
      --execution-count         also compare execution counts
  -A, --all                     same as -o -m --execution-count
      --no-outputs, --no-metadata, --no-execution-count
                                turn an aspect off again (after -A, or with
                                  --visualize)
      --sources-only            compare cell sources only
      --visualize=FILE          write the diff as a notebook to FILE ('-' for
                                  stdout) instead of printing it; implies -o -m
      --match=STRATEGY          pair cells by 'id', 'content' or 'auto' (default)
      --match-threshold=X       minimum similarity (0..1) for two edited cells
                                  to be paired as 'modified' (default 0.5)
      --no-moves                report moved cells as deleted + added
      --ignore-metadata=PAT     ignore metadata keys matching PAT (dotted path,
                                  glob), e.g. 'execution', 'ExecuteTime',
                                  'collapsed', 'widgets', 'language_info.*'
      --ignore-mime=PAT         ignore output mimetypes matching PAT, e.g.
                                  'image/*' or 'text/html'
      --unchanged=MODE          how to show unchanged cells: 'collapse' runs
                                  into one line (default), 'list' each cell,
                                  'show' their source, or 'hide' them
      --suppress-common-cells   same as --unchanged=hide
      --max-lines=NUM           show at most NUM lines of each added or deleted
                                  output (default 50, 0 = no limit)
      --stat                    only list cells with status and change counts
      --json                    print a machine-readable JSON description
      --no-summary              do not print the summary line
      --no-intraline            do not highlight changed characters in lines

Git options:
      --diff, --git             compare notebooks in a git repository; the
                                  arguments are read like 'git diff' reads them:
                                  --diff                  working tree vs index
                                  --diff --cached         index vs HEAD
                                  --diff HEAD             working tree vs HEAD
                                  --diff main             working tree vs main
                                  --diff main feature     main vs feature
                                  --diff main..feature    main vs feature
                                  --diff origin/main...   changes since this
                                                          branch forked
                                  --diff HEAD~3 -- dir/   limit to paths
      --cached, --staged        with --diff, use the index (staged changes)

diff(1)-compatible options:
      --normal                  output a normal diff
  -q, --brief                   report only when notebooks differ
  -s, --report-identical-files  report when two notebooks are the same
  -c, -C NUM, --context[=NUM]   output NUM (default 3) lines of copied context
  -u, -U NUM, --unified[=NUM]   output NUM (default 3) lines of unified context
                                  (this is the default output format)
  -y, --side-by-side            output in two columns
  -W, --width=NUM               output at most NUM (default 130) print columns
      --left-column             output only the left column of common lines
      --suppress-common-lines   do not output common lines (side-by-side)

  -r, --recursive               recursively compare any subdirectories found
  -N, --new-file                treat absent notebooks as empty
      --unidirectional-new-file treat absent first notebooks as empty
  -x, --exclude=PAT             exclude files that match PAT
  -X, --exclude-from=FILE       exclude files that match any pattern in FILE
  -S, --starting-file=FILE      start with FILE when comparing directories
      --from-file=FILE1         compare FILE1 to all operands
      --to-file=FILE2           compare all operands to FILE2

  -i, --ignore-case             ignore case differences in file contents
  -E, --ignore-tab-expansion    ignore changes due to tab expansion
  -Z, --ignore-trailing-space   ignore white space at line end
  -b, --ignore-space-change     ignore changes in the amount of white space
  -w, --ignore-all-space        ignore all white space
  -B, --ignore-blank-lines      ignore changes where lines are all blank
  -I, --ignore-matching-lines=RE  ignore changes where all lines match RE
                                  (applies to sources and text outputs)
      --strip-trailing-cr       strip trailing carriage return on input
  -d, --minimal                 try hard to find a smaller set of changes
      --speed-large-files       assume large files, many scattered small changes

  -t, --expand-tabs             expand tabs to spaces in output
  -T, --initial-tab             make tabs line up by prepending a tab
      --tabsize=NUM             tab stops every NUM (default 8) print columns
      --label LABEL             use LABEL instead of file name (can be repeated)
      --color[=WHEN]            colorize the output; WHEN is 'never', 'always',
                                  or 'auto' (the default; honours NO_COLOR)

  -h, --help                    display this help and exit
  -v, --version                 output version information and exit

Examples:
  nbdiff before.ipynb after.ipynb           cell sources, unified format
  nbdiff -o before.ipynb after.ipynb        ... plus outputs
  nbdiff -A -w before.ipynb after.ipynb     everything, ignoring white space
  nbdiff -q -r old/ new/                    which notebooks differ
  nbdiff --visualize=diff.ipynb a.ipynb b.ipynb
  nbdiff --diff main -- analysis.ipynb      working tree vs branch main
  nbdiff --diff -o origin/main...HEAD --visualize review.ipynb

Use with git without --diff:
  git difftool -y -x nbdiff main -- '*.ipynb'
  git config diff.nbdiff.command 'nbdiff --color=always'   # plus, in
  echo '*.ipynb diff=nbdiff' >> .gitattributes            # .gitattributes
"""

VERSION_TEXT = """\
nbdiff %s
A diff(1)-like tool for Jupyter notebooks.  Python %s.
""" % (__version__, sys.version.split()[0])


class UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        message = message.replace("unrecognized arguments", "unrecognized option(s)")
        self.exit(EXIT_TROUBLE, "nbdiff: %s\nnbdiff: Try 'nbdiff --help' for more information.\n" % message)


def _nonneg_int(s: str) -> int:
    try:
        v = int(s)
    except ValueError:
        raise argparse.ArgumentTypeError("invalid number '%s'" % s)
    if v < 0:
        raise argparse.ArgumentTypeError("invalid number '%s'" % s)
    return v


def _pos_int(s: str) -> int:
    v = _nonneg_int(s)
    if v == 0:
        raise argparse.ArgumentTypeError("invalid number '%s'" % s)
    return v


def _fraction(s: str) -> float:
    try:
        v = float(s)
    except ValueError:
        raise argparse.ArgumentTypeError("invalid number '%s'" % s)
    if not 0.0 <= v <= 1.0:
        raise argparse.ArgumentTypeError("threshold must be between 0 and 1, not '%s'" % s)
    return v


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="nbdiff", add_help=False, allow_abbrev=True)
    p.set_defaults(formats=[], outputs=None, metadata=None, execution_count=None)
    a = p.add_argument
    a("files", nargs="*")
    a("-h", "--help", action="store_true")
    a("-v", "--version", action="store_true")
    # output formats
    a("--normal", dest="formats", action="append_const", const="normal")
    a("-u", dest="formats", action="append_const", const="unified")
    a("-U", dest="unified_n", type=_nonneg_int)
    a("-c", dest="formats", action="append_const", const="context")
    a("-C", dest="context_n", type=_nonneg_int)
    a("-y", "--side-by-side", dest="formats", action="append_const", const="side-by-side")
    a("--stat", dest="formats", action="append_const", const="stat")
    a("--json", dest="formats", action="append_const", const="json")
    a("-q", "--brief", action="store_true")
    a("-s", "--report-identical-files", action="store_true")
    a("-W", "--width", type=_pos_int, default=130)
    a("--left-column", action="store_true")
    a("--suppress-common-lines", action="store_true")
    a("-t", "--expand-tabs", action="store_true")
    a("-T", "--initial-tab", action="store_true")
    a("--tabsize", type=_pos_int, default=8)
    a("--color", choices=["never", "always", "auto"], default=None)
    a("--label", action="append", default=[])
    # comparison
    a("-i", "--ignore-case", action="store_true")
    a("-E", "--ignore-tab-expansion", action="store_true")
    a("-Z", "--ignore-trailing-space", action="store_true")
    a("-b", "--ignore-space-change", action="store_true")
    a("-w", "--ignore-all-space", action="store_true")
    a("-B", "--ignore-blank-lines", action="store_true")
    a("-I", "--ignore-matching-lines", action="append", default=[], metavar="RE")
    a("--strip-trailing-cr", action="store_true")
    a("-d", "--minimal", action="store_true")
    a("--speed-large-files", action="store_true")
    a("--horizon-lines", type=_nonneg_int)  # accepted for compatibility; no effect
    # files and directories
    a("-r", "--recursive", action="store_true")
    a("-N", "--new-file", action="store_true")
    a("--unidirectional-new-file", action="store_true")
    a("-x", "--exclude", action="append", default=[])
    a("-X", "--exclude-from", action="append", default=[])
    a("-S", "--starting-file")
    a("--from-file")
    a("--to-file")
    # notebook
    a("-o", "--outputs", dest="outputs", action="store_const", const=True)
    a("--no-outputs", dest="outputs", action="store_const", const=False)
    a("-m", "--metadata", dest="metadata", action="store_const", const=True)
    a("--no-metadata", dest="metadata", action="store_const", const=False)
    a("--execution-count", "--exec-count", dest="execution_count", action="store_const", const=True)
    a("--no-execution-count", "--no-exec-count", dest="execution_count", action="store_const", const=False)
    a("-A", "--all", action="store_true")
    a("--sources-only", action="store_true")
    a("--visualize", metavar="FILE")
    a("--match", choices=["auto", "id", "content"], default="auto")
    a("--match-threshold", type=_fraction, default=0.5)
    a("--no-moves", action="store_true")
    a("--ignore-metadata", action="append", default=[])
    a("--ignore-mime", "--ignore-output-mime", action="append", default=[])
    a("--unchanged", choices=["collapse", "list", "show", "hide"], default=None)
    a("--suppress-common-cells", action="store_true")
    a("--max-lines", type=_nonneg_int, default=50)
    a("--no-summary", action="store_true")
    a("--no-intraline", action="store_true")
    # git
    a("--diff", "--git", dest="git", action="store_true")
    a("--cached", "--staged", action="store_true")
    return p


# GNU getopt semantics for options whose argument is optional: the argument
# must be attached with '=' (``--unified=5``), a bare ``--unified`` uses the
# default.  argparse cannot express this, so these are rewritten up front.
_OPTIONAL_ARG = {"--unified": ("-u", "-U"), "--context": ("-c", "-C"), "--color": None}


def preprocess(argv: Sequence[str], parser: argparse.ArgumentParser) -> Tuple[List[str], List[str]]:
    """Rewrite optional-argument long options; split off arguments after '--'."""
    longs = [s for s in parser._option_string_actions if s.startswith("--")] + list(_OPTIONAL_ARG)
    out: List[str] = []
    for k, tok in enumerate(argv):
        if tok == "--":
            return out, list(argv[k + 1:])
        if tok.startswith("--") and len(tok) > 2:
            name, eq, val = tok.partition("=")
            full = name if name in _OPTIONAL_ARG else None
            if full is None and len(name) >= 4:
                cands = sorted({o for o in longs if o.startswith(name)})
                if len(cands) == 1 and cands[0] in _OPTIONAL_ARG:
                    full = cands[0]
            if full == "--color":
                out.append("--color=" + (val if eq else "auto"))
                continue
            if full in ("--unified", "--context"):
                bare, with_arg = _OPTIONAL_ARG[full]  # type: ignore[misc]
                out.extend([with_arg, val] if eq else [bare])
                continue
        out.append(tok)
    return out, []


def _use_color(when: Optional[str]) -> bool:
    if when == "always":
        return True
    if when == "never":
        return False
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty() and os.environ.get("TERM") != "dumb"


def _use_unicode() -> bool:
    enc = (getattr(sys.stdout, "encoding", None) or "").lower().replace("-", "")
    return "utf" in enc


class Runner:
    """Holds resolved options and runs the comparisons."""

    def __init__(self, args: argparse.Namespace, after: List[str], argv: Sequence[str]):
        self.args = args
        self.after = after
        self.argv = list(argv)
        self.out = sys.stdout
        self.sections: List[FileSection] = []
        self.json_items: List[Dict[str, Any]] = []
        self.only_in: List[Dict[str, str]] = []
        self.inputs: List[str] = []
        self.multi = False
        self.compared = 0
        self.differing = 0
        self.hidden_seen: List[str] = []

        # output format
        formats = list(dict.fromkeys(args.formats))
        if args.unified_n is not None and "unified" not in formats:
            formats.append("unified")
        if args.context_n is not None and "context" not in formats:
            formats.append("context")
        if len(formats) > 1:
            raise UsageError("conflicting output style options")
        self.format = formats[0] if formats else "unified"
        n = 3
        if self.format == "unified" and args.unified_n is not None:
            n = args.unified_n
        elif self.format == "context" and args.context_n is not None:
            n = args.context_n
        self.context = n
        self.json = self.format == "json"
        self.visualize = args.visualize

        if len(args.label) > 2:
            raise UsageError("too many file label options")

        # what to compare
        viz = self.visualize is not None

        def aspect(val: Optional[bool], default: bool) -> bool:
            return default if val is None else val

        outputs = aspect(args.outputs, args.all or viz)
        metadata = aspect(args.metadata, args.all or viz)
        exec_count = aspect(args.execution_count, args.all)
        if args.sources_only:
            outputs = metadata = exec_count = False

        patterns = []
        for rx in args.ignore_matching_lines:
            try:
                patterns.append(re.compile(rx))
            except re.error as e:
                raise UsageError("invalid regular expression '%s': %s" % (rx, e))
        compare = CompareOptions(
            ignore_case=args.ignore_case, ignore_tab_expansion=args.ignore_tab_expansion,
            ignore_trailing_space=args.ignore_trailing_space, ignore_space_change=args.ignore_space_change,
            ignore_all_space=args.ignore_all_space, ignore_blank_lines=args.ignore_blank_lines,
            ignore_matching=patterns, strip_trailing_cr=args.strip_trailing_cr, minimal=args.minimal,
            speed_large_files=args.speed_large_files, tabsize=args.tabsize)
        self.diff_opts = DiffOptions(compare=compare, outputs=outputs, metadata=metadata,
                                     execution_count=exec_count, match=args.match,
                                     threshold=args.match_threshold, moves=not args.no_moves,
                                     ignore_metadata=args.ignore_metadata, ignore_mimes=args.ignore_mime)
        unchanged = args.unchanged or ("hide" if args.suppress_common_cells else "collapse")
        self.color = _use_color(args.color)
        self.render_opts = RenderOptions(
            format="unified" if self.format == "json" else self.format, context=n, width=args.width,
            left_column=args.left_column, suppress_common_lines=args.suppress_common_lines,
            unchanged=unchanged, max_lines=args.max_lines, summary=not args.no_summary,
            intraline=not args.no_intraline, color=self.color, unicode=_use_unicode(),
            initial_tab=args.initial_tab, expand_tabs=args.expand_tabs, tabsize=args.tabsize)
        self.style = Style(self.color)
        self.vis_opts = VisualizeOptions(
            context=n if (args.unified_n is not None or args.context_n is not None) else None,
            unchanged=unchanged, command=self.command_line())

        self.excludes: List[str] = list(args.exclude)
        for fname in args.exclude_from:
            try:
                with open(fname, encoding="utf-8") as f:
                    self.excludes.extend(ln.rstrip("\n") for ln in f if ln.strip())
            except OSError as e:
                raise UsageError("%s: %s" % (fname, e.strerror or e))

    # -- helpers -------------------------------------------------------------

    def command_line(self) -> str:
        return "nbdiff " + " ".join(shlex.quote(x) for x in self.argv)

    def write_lines(self, lines: Sequence[str]) -> None:
        if lines:
            self.out.write("\n".join(lines) + "\n")

    @staticmethod
    def error(msg: str) -> None:
        sys.stderr.write("nbdiff: %s\n" % msg)

    @property
    def printing(self) -> bool:
        """True when the diff is printed to the terminal (not JSON / notebook)."""
        return not self.json and self.visualize is None

    # -- comparing ------------------------------------------------------------

    def report(self, a: Notebook, b: Notebook, header: Optional[str] = None, heading: str = "",
               note: str = "", extra: Optional[Dict[str, Any]] = None) -> int:
        """Diff two loaded notebooks and emit the result in the selected format."""
        nd = diff_notebooks(a, b, self.diff_opts)
        differ = not nd.identical
        self.compared += 1
        self.differing += differ
        for h in nd.hidden_aspects():
            if h not in self.hidden_seen:
                self.hidden_seen.append(h)
        if self.visualize is not None and (differ or not self.multi):
            self.sections.append(FileSection(nd, heading or a.label, note))
        if self.json:
            self.json_items.append(diff_to_dict(nd, self.context, extra))
        if self.printing:
            args = self.args
            if args.brief:
                if differ:
                    self.write_lines(["Files %s and %s differ" % (a.label, b.label)])
            elif differ:
                lines = []
                if header:
                    lines.append(self.style(header, "bold"))
                lines.extend(render_terminal(nd, self.render_opts))
                if self.multi:
                    lines.append("")
                self.write_lines(lines)
            if not differ and args.report_identical_files:
                self.write_lines(["Files %s and %s are identical" % (a.label, b.label)])
            if not differ and not self.multi and not args.brief:
                hidden = nd.hidden_aspects()
                if hidden:
                    flags = {"outputs": "-o", "metadata": "-m"}
                    self.error("notebook sources are identical, but %s differ (use %s)" % (
                        " and ".join(hidden), " ".join(flags[h] for h in hidden)))
        return EXIT_DIFF if differ else EXIT_SAME

    def compare_files(self, p1: str, p2: str, header: Optional[str] = None) -> int:
        args = self.args
        labels = list(args.label)
        la = labels[0] if len(labels) > 0 and not self.multi else None
        lb = labels[1] if len(labels) > 1 and not self.multi else None
        try:
            a = read_notebook(p1, la, missing_ok=args.new_file or args.unidirectional_new_file)
            b = read_notebook(p2, lb, missing_ok=args.new_file)
        except NotebookError as e:
            self.error(str(e))
            return EXIT_TROUBLE
        if not a.exists and not b.exists:
            self.error("%s: No such file or directory" % p1)
            return EXIT_TROUBLE
        self.inputs.extend([p1, p2])
        return self.report(a, b, header=header)

    def _excluded(self, name: str) -> bool:
        return name == ".ipynb_checkpoints" or any(fnmatch.fnmatchcase(name, pat) for pat in self.excludes)

    @staticmethod
    def _has_notebooks(path: str) -> bool:
        for _root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d != ".ipynb_checkpoints"]
            if any(f.endswith(".ipynb") for f in files):
                return True
        return False

    def compare_dirs(self, d1: str, d2: str, top: bool = True) -> int:
        args = self.args
        self.multi = True

        def listing(d: str) -> set:
            try:
                return set(os.listdir(d))
            except FileNotFoundError:
                return set()
            except OSError as e:
                self.error("%s: %s" % (d, e.strerror or e))
                return set()

        n1, n2 = listing(d1), listing(d2)
        names = sorted(n for n in (n1 | n2) if not self._excluded(n))
        if top and args.starting_file:
            names = [n for n in names if n >= args.starting_file]
        status = EXIT_SAME
        flag = " -r" if args.recursive else ""
        for name in names:
            p1, p2 = os.path.join(d1, name), os.path.join(d2, name)
            in1, in2 = name in n1, name in n2
            dir1, dir2 = in1 and os.path.isdir(p1), in2 and os.path.isdir(p2)
            st = EXIT_SAME
            if in1 and in2:
                if dir1 and dir2:
                    if args.recursive:
                        st = self.compare_dirs(p1, p2, top=False)
                    elif self.printing:
                        self.write_lines(["Common subdirectories: %s and %s" % (p1, p2)])
                elif dir1 != dir2:
                    if name.endswith(".ipynb") or self._has_notebooks(p1 if dir1 else p2):
                        self.write_lines(["File %s is a %s while file %s is a %s" % (
                            p1, "directory" if dir1 else "regular file",
                            p2, "directory" if dir2 else "regular file")])
                        st = EXIT_DIFF
                elif name.endswith(".ipynb"):
                    st = self.compare_files(p1, p2, header="nbdiff%s %s %s" % (flag, p1, p2))
            else:
                path = p1 if in1 else p2
                isdir = dir1 or dir2
                if not (name.endswith(".ipynb") or (isdir and self._has_notebooks(path))):
                    continue
                as_new = args.new_file or (args.unidirectional_new_file and in2)
                if as_new and isdir and args.recursive:
                    st = self.compare_dirs(p1, p2, top=False)
                elif as_new and not isdir:
                    st = self.compare_files(p1, p2, header="nbdiff%s %s %s" % (flag, p1, p2))
                else:
                    where = d1 if in1 else d2
                    self.only_in.append({"directory": where, "name": name})
                    if not self.json:
                        self.write_lines(["Only in %s: %s" % (where, name)])
                    st = EXIT_DIFF
            status = _combine(status, st)
        return status

    def compare_operands(self, p1: str, p2: str) -> int:
        d1 = p1 != "-" and os.path.isdir(p1)
        d2 = p2 != "-" and os.path.isdir(p2)
        if d1 and d2:
            return self.compare_dirs(p1, p2)
        if d1:
            if p2 == "-":
                self.error("cannot compare '-' to a directory")
                return EXIT_TROUBLE
            p1 = os.path.join(p1, os.path.basename(p2))
        elif d2:
            if p1 == "-":
                self.error("cannot compare '-' to a directory")
                return EXIT_TROUBLE
            p2 = os.path.join(p2, os.path.basename(p1))
        return self.compare_files(p1, p2)

    def run_files(self) -> int:
        args = self.args
        files = list(args.files) + self.after
        ext = parse_external_diff_args(files)
        if ext is not None:
            return self.run_git_external(files, ext)
        if args.from_file and args.to_file:
            raise UsageError("--from-file and --to-file both specified")
        if args.from_file or args.to_file:
            if not files:
                raise UsageError("missing operand after '%s'" % (args.from_file or args.to_file))
            self.multi = len(files) > 1
            status = EXIT_SAME
            for f in files:
                pair = (args.from_file, f) if args.from_file else (f, args.to_file)
                status = _combine(status, self.compare_operands(*pair))
            return status
        if len(files) < 2:
            raise UsageError("missing operand after '%s'" % (files[0] if files else "nbdiff"))
        if len(files) > 2:
            raise UsageError("extra operand '%s'" % files[2])
        return self.compare_operands(files[0], files[1])

    def run_git_external(self, files: List[str], ext: Tuple[str, str, str, str]) -> int:
        """Invoked by git as GIT_EXTERNAL_DIFF / diff.<driver>.command (7 arguments)."""
        old_file, new_file, la, lb = ext
        path = files[0]
        if not (path.endswith(".ipynb") or lb.endswith(".ipynb")):
            # Not a notebook: behave like plain diff so git keeps working.
            subprocess.call(["diff", "-u", "--label", la, "--label", lb, old_file, new_file])
            return EXIT_SAME
        try:
            a = read_notebook(old_file, la, missing_ok=True)
            b = read_notebook(new_file, lb, missing_ok=True)
        except NotebookError as e:
            self.error(str(e))
            return EXIT_TROUBLE
        if old_file == os.devnull:
            a.exists = False
        if new_file == os.devnull:
            b.exists = False
        xfrm = files[8].strip().replace("\n", "; ") if len(files) == 9 else ""
        self.report(a, b, header="nbdiff --git %s %s" % (la, lb) + ("  (%s)" % xfrm if xfrm else ""))
        return EXIT_SAME  # git aborts if an external diff exits non-zero

    def run_git(self) -> int:
        args = self.args
        try:
            cmp = parse_git_args(args.files, self.after, args.cached)
            pairs = changed_notebooks(cmp)
        except GitError as e:
            self.error(str(e))
            return EXIT_TROUBLE
        self.multi = True
        self.vis_opts.subtitle = "git: %s" % cmp.describe()
        status = EXIT_SAME
        for fp in pairs:
            try:
                a, b = load_pair(cmp, fp)
            except (NotebookError, GitError) as e:
                self.error(str(e))
                status = EXIT_TROUBLE
                continue
            note = ""
            if fp.status == "R":
                note = "renamed from %s" % fp.old_path + (" (%d%% similar)" % fp.score if fp.score else "")
            elif fp.status == "C":
                note = "copied from %s" % fp.old_path
            elif fp.status == "A":
                note = "new notebook"
            elif fp.status == "D":
                note = "deleted notebook"
            header = "nbdiff --diff %s %s" % (a.label, b.label) + ("  (%s)" % note if note else "")
            if cmp.right.kind == "worktree" and fp.new_path:
                self.inputs.append(os.path.join(cmp.root, fp.new_path))
            st = self.report(a, b, header=header, heading=fp.display, note=note,
                             extra={"git_status": fp.status, "path": fp.display})
            if st == EXIT_SAME and fp.status in "RC" and self.printing and not args.brief:
                self.write_lines([self.style("nbdiff --diff: %s %s (%s); no differences in the compared aspects" % (
                    "renamed" if fp.status == "R" else "copied", fp.display,
                    "%d%% similar" % fp.score if fp.score is not None else "similar"), "bold"), ""])
            status = _combine(status, st)
        if self.printing and not args.brief and not args.no_summary:
            if not pairs:
                self.error("no notebook changes between %s" % cmp.describe())
            else:
                msg = "%s changed in git (%s): %d differ" % (
                    "1 notebook" if len(pairs) == 1 else "%d notebooks" % len(pairs), cmp.describe(), self.differing)
                same = self.compared - self.differing
                if same:
                    msg += ", %d identical in the compared aspects" % same
                    if self.hidden_seen:
                        flags = {"outputs": "-o", "metadata": "-m"}
                        msg += " (%s differ; use %s)" % (" and ".join(self.hidden_seen),
                                                         " ".join(flags[h] for h in self.hidden_seen))
                self.write_lines([self.style(msg, "bold")])
        return status

    def finish(self, status: int) -> int:
        if self.json:
            doc = render_json(self.json_items)
            if self.only_in:
                doc["only_in"] = self.only_in
                doc["identical"] = False
            self.out.write(json.dumps(doc, indent=2, ensure_ascii=False, default=str) + "\n")
        if self.visualize is not None and (status != EXIT_TROUBLE or self.sections):
            path = self.visualize
            if path != "-":
                if os.path.isdir(path):
                    self.error("%s: Is a directory" % path)
                    return EXIT_TROUBLE
                if os.path.exists(path):
                    for inp in self.inputs:
                        try:
                            if inp != "-" and os.path.exists(inp) and os.path.samefile(path, inp):
                                self.error("refusing to overwrite input notebook '%s' with the diff" % path)
                                return EXIT_TROUBLE
                        except OSError:
                            pass
            nb = render_notebook(self.sections, self.vis_opts)
            try:
                write_notebook(nb, path)
            except OSError as e:
                self.error("%s: %s" % (path, e.strerror or e))
                return EXIT_TROUBLE
            if path != "-" and not self.args.brief:
                if self.sections:
                    tot: Dict[str, int] = {}
                    for sec in self.sections:
                        for k, v in sec.diff.counts().items():
                            tot[k] = tot.get(k, 0) + v
                    what = ", ".join("%d %s" % (tot[k], k) for k in ("modified", "added", "deleted", "moved")
                                     if tot.get(k)) or "no differences"
                    if len(self.sections) > 1:
                        what = "%d notebooks: %s" % (len(self.sections), what)
                else:
                    what = "no differences"
                sys.stderr.write("nbdiff: wrote diff notebook %s (%s)\n" % (path, what))
        return status

    def run(self) -> int:
        status = self.run_git() if self.args.git else self.run_files()
        return self.finish(status)


def _combine(a: int, b: int) -> int:
    if EXIT_TROUBLE in (a, b):
        return EXIT_TROUBLE
    return max(a, b)


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        sys.stdout.reconfigure(errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    parser = build_parser()
    pre, after = preprocess(argv, parser)
    args = parser.parse_intermixed_args(pre)
    if args.help:
        sys.stdout.write(HELP)
        return EXIT_SAME
    if args.version:
        sys.stdout.write(VERSION_TEXT)
        return EXIT_SAME
    try:
        runner = Runner(args, after, argv)
        if args.cached and not args.git:
            raise UsageError("--cached/--staged only make sense with --diff")
        return runner.run()
    except UsageError as e:
        sys.stderr.write("nbdiff: %s\nnbdiff: Try 'nbdiff --help' for more information.\n" % e)
        return EXIT_TROUBLE
    except BrokenPipeError:
        # Output was piped into something like `head` that exited early.
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except OSError:
            pass
        return EXIT_DIFF
    except KeyboardInterrupt:
        return 130
