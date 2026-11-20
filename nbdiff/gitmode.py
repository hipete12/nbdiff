"""``--diff``: compare notebooks across git revisions, the index and the working tree.

Arguments are interpreted like ``git diff``::

    nbdiff --diff                      # working tree vs index (unstaged changes)
    nbdiff --diff --cached             # index vs HEAD (staged changes)
    nbdiff --diff HEAD                 # working tree vs HEAD
    nbdiff --diff main                 # working tree vs branch main
    nbdiff --diff --cached main        # index vs main
    nbdiff --diff main feature         # branch main vs branch feature
    nbdiff --diff main..feature        # same as above
    nbdiff --diff origin/main...HEAD   # changes on HEAD since it forked from origin/main
    nbdiff --diff HEAD~3 -- nb/        # limit to paths

Only ``.ipynb`` files are compared; renames are detected (``git diff -M``).
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .notebook import Notebook, empty_notebook, parse_notebook_bytes, read_notebook


class GitError(Exception):
    pass


def _run(args: Sequence[str], cwd: str, check: bool = True) -> subprocess.CompletedProcess:
    try:
        p = subprocess.run(["git", "-c", "diff.relative=false", "-c", "core.quotepath=false"] + list(args),
                           cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        raise GitError("git is not installed or not on PATH")
    if check and p.returncode != 0:
        msg = p.stderr.decode("utf-8", "replace").strip() or "git %s failed" % args[0]
        raise GitError(msg)
    return p


def repo_root(cwd: str = ".") -> str:
    p = _run(["rev-parse", "--show-toplevel"], cwd, check=False)
    if p.returncode != 0:
        raise GitError("not a git repository (or any of the parent directories)")
    return p.stdout.decode("utf-8", "surrogateescape").strip()


def is_commitish(tok: str, cwd: str) -> bool:
    if not tok or tok.startswith("-"):
        return False
    p = _run(["rev-parse", "--verify", "--quiet", "--end-of-options", tok + "^{commit}"], cwd, check=False)
    return p.returncode == 0


def _is_rev_token(tok: str, cwd: str) -> bool:
    for sep in ("...", ".."):
        if sep in tok:
            left, right = tok.split(sep, 1)
            return is_commitish(left or "HEAD", cwd) and is_commitish(right or "HEAD", cwd)
    return is_commitish(tok, cwd)


@dataclass
class Side:
    kind: str  # 'commit' | 'index' | 'worktree'
    rev: Optional[str] = None  # commit-ish to read from (for 'commit')
    name: str = ""  # human name: 'main', 'HEAD', 'index', 'working tree'
    desc: str = ""  # longer description, if any

    def label(self, path: str) -> str:
        if self.kind == "worktree":
            return path
        if self.kind == "index":
            return "index:%s" % path
        return "%s:%s" % (self.name, path)

    def describe(self) -> str:
        if self.desc:
            return self.desc
        return {"worktree": "working tree", "index": "index"}.get(self.kind, self.name)


@dataclass
class FilePair:
    status: str  # git name-status letter: M, A, D, R, C, T, U ...
    old_path: Optional[str]
    new_path: Optional[str]
    score: Optional[int] = None  # rename/copy similarity

    @property
    def display(self) -> str:
        if self.old_path and self.new_path and self.old_path != self.new_path:
            return "%s → %s" % (self.old_path, self.new_path)
        return self.new_path or self.old_path or "?"


@dataclass
class GitComparison:
    root: str
    left: Side
    right: Side
    diff_args: List[str]  # revision arguments passed to `git diff`
    pathspecs: List[str]

    def describe(self) -> str:
        return "%s → %s" % (self.left.describe(), self.right.describe())


def _short(rev: str, cwd: str) -> str:
    p = _run(["rev-parse", "--short", rev], cwd, check=False)
    return p.stdout.decode().strip() or rev


def parse_git_args(before: Sequence[str], after: Sequence[str], cached: bool,
                   cwd: str = ".") -> GitComparison:
    """Split ``[<commit> [<commit>]] [--] [<path>...]`` and resolve both sides."""
    root = repo_root(cwd)
    revs: List[str] = []
    paths: List[str] = []
    for tok in before:
        if not paths and len(revs) < 2 and _is_rev_token(tok, cwd):
            revs.append(tok)
        elif os.path.exists(tok):
            paths.append(tok)
        else:
            raise GitError("ambiguous argument '%s': unknown revision or path not in the working tree.\n"
                           "Use '--' to separate paths from revisions, like this:\n"
                           "'nbdiff --diff [<revision>...] -- [<file>...]'" % tok)
    paths.extend(after)

    ranged = [r for r in revs if ".." in r]
    if ranged and len(revs) > 1:
        raise GitError("a revision range (A..B or A...B) cannot be combined with another revision")
    if cached and (len(revs) == 2 or ranged):
        raise GitError("--cached takes at most one revision")

    if ranged:
        tok = ranged[0]
        if "..." in tok:
            l, r = tok.split("...", 1)
            l, r = l or "HEAD", r or "HEAD"
            p = _run(["merge-base", l, r], cwd, check=False)
            base = p.stdout.decode().strip()
            if p.returncode != 0 or not base:
                raise GitError("no merge base between %s and %s" % (l, r))
            sha = _short(base, cwd)
            left = Side("commit", base, sha, "merge-base of %s and %s (%s)" % (l, r, sha))
            right = Side("commit", r, r)
        else:
            l, r = tok.split("..", 1)
            l, r = l or "HEAD", r or "HEAD"
            left, right = Side("commit", l, l), Side("commit", r, r)
    elif len(revs) == 2:
        left, right = Side("commit", revs[0], revs[0]), Side("commit", revs[1], revs[1])
    elif len(revs) == 1:
        left = Side("commit", revs[0], revs[0])
        right = Side("index", name="index") if cached else Side("worktree", name="working tree")
    elif cached:
        left, right = Side("commit", "HEAD", "HEAD"), Side("index", name="index")
    else:
        left, right = Side("index", name="index"), Side("worktree", name="working tree")

    diff_args = (["--cached"] if cached else []) + revs
    # Make user paths relative to the repository root (we run git from there).
    specs = []
    for pth in paths:
        if pth.startswith(":"):
            specs.append(pth)  # already a git pathspec with magic
        else:
            specs.append(os.path.relpath(os.path.abspath(os.path.join(cwd, pth)), root))
    return GitComparison(root, left, right, diff_args, specs)


def changed_notebooks(cmp: GitComparison) -> List[FilePair]:
    """Notebook files that differ between the two sides, per ``git diff --name-status``."""
    specs = cmp.pathspecs or ["*.ipynb"]
    p = _run(["diff", "--name-status", "-z", "-M", "--no-ext-diff"] + cmp.diff_args + ["--"] + specs, cmp.root)
    toks = p.stdout.decode("utf-8", "surrogateescape").split("\0")
    pairs: List[FilePair] = []
    i = 0
    while i < len(toks) and toks[i]:
        st = toks[i]
        letter = st[0]
        score = int(st[1:]) if st[1:].isdigit() else None
        if letter in "RC":
            old, new = toks[i + 1], toks[i + 2]
            i += 3
        else:
            old = new = toks[i + 1]
            i += 2
        if not (old.endswith(".ipynb") or new.endswith(".ipynb")):
            continue
        if letter == "A":
            pairs.append(FilePair(letter, None, new))
        elif letter == "D":
            pairs.append(FilePair(letter, old, None))
        else:
            pairs.append(FilePair(letter, old, new, score))
    return pairs


def _commit_time_ns(rev: str, root: str) -> Optional[int]:
    p = _run(["log", "-1", "--format=%ct", rev, "--"], root, check=False)
    s = p.stdout.decode().strip()
    return int(s) * 1_000_000_000 if s.isdigit() else None


def load_side(side: Side, path: Optional[str], root: str, label_path: str) -> Notebook:
    """Load the notebook at ``path`` from one side (commit, index or working tree)."""
    label = side.label(label_path)
    if path is None:
        return empty_notebook(label_path, label, exists=False)
    if side.kind == "worktree":
        return read_notebook(os.path.join(root, path), label=label, missing_ok=True)
    spec = "%s:%s" % (side.rev, path) if side.kind == "commit" else ":%s" % path
    p = _run(["cat-file", "blob", spec], root, check=False)
    if p.returncode != 0:
        return empty_notebook(path, label, exists=False)
    mtime = _commit_time_ns(side.rev, root) if side.kind == "commit" else None
    return parse_notebook_bytes(p.stdout, path, label, mtime)


def load_pair(cmp: GitComparison, fp: FilePair) -> Tuple[Notebook, Notebook]:
    a = load_side(cmp.left, fp.old_path, cmp.root, fp.old_path or fp.new_path or "")
    b = load_side(cmp.right, fp.new_path, cmp.root, fp.new_path or fp.old_path or "")
    return a, b


def parse_external_diff_args(args: Sequence[str]) -> Optional[Tuple[str, str, str, str]]:
    """Recognise the 7 arguments git passes to GIT_EXTERNAL_DIFF / diff.external.

    ``path old-file old-hex old-mode new-file new-hex new-mode`` →
    (old_file, new_file, label_a, label_b), or None if ``args`` is not that shape.
    """
    if len(args) != 7:
        return None

    def is_mode(m: str) -> bool:
        return m == "." or (len(m) == 6 and all(c in "01234567" for c in m))

    def is_hex(h: str) -> bool:
        return h == "." or (len(h) >= 7 and all(c in "0123456789abcdef" for c in h.lower()))

    path, old_file, old_hex, old_mode, new_file, new_hex, new_mode = args
    if not (is_mode(old_mode) and is_mode(new_mode) and is_hex(old_hex) and is_hex(new_hex)):
        return None
    return old_file, new_file, "a/" + path, "b/" + path
