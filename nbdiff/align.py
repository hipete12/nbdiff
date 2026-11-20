"""Aligning the cells of two notebooks.

The result is an ordered list of entries describing how cells correspond:

* ``('pair', i, j)``      cell i of A corresponds to cell j of B (maybe edited)
* ``('deleted', i, None)`` cell i exists only in A
* ``('added', None, j)``   cell j exists only in B
* ``('moved', i, j)``      cell i of A moved to position j of B (shown at j)
* ``('moved-away', i, j)`` placeholder shown at A's old position of a move

Strategy:

1. *Anchors.*  If both notebooks carry nbformat >= 4.5 cell ids and enough
   of them are shared, cells with the same id are anchors (``--match=id``).
   Otherwise, cells whose type and normalized source are identical are
   anchored with a longest-matching-blocks alignment (``--match=content``).
   Only an order-preserving subset of anchors is kept (longest increasing
   subsequence); id-matched cells outside that subset are *moves*.
2. *Fuzzy pairing.*  Between consecutive anchors, remaining cells are paired
   by source similarity with an order-preserving dynamic-programming
   alignment (pairs below ``threshold`` are not paired).
3. *Moves.*  Leftover deleted/added cells with near-identical sources are
   reported as moved instead of deleted + added.
"""

from __future__ import annotations

import bisect
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Sequence, Tuple

from .notebook import Cell
from .textdiff import CompareOptions, split_lines, text_key

Entry = Tuple[str, Optional[int], Optional[int]]

MOVE_THRESHOLD = 0.8  # minimum similarity for a delete+add to count as a move


class _Keys:
    """Cache of normalized cell texts."""

    def __init__(self, cells: Sequence[Cell], opts: CompareOptions):
        self.text = [text_key(c.source, opts) for c in cells]
        self.lines = [t.split("\n") if t else [] for t in self.text]
        self.type = [c.cell_type for c in cells]


def similarity(ka: _Keys, i: int, kb: _Keys, j: int) -> float:
    """Similarity in [0, 1] of cell i of A and cell j of B."""
    ta, tb = ka.text[i], kb.text[j]
    if ta == tb:
        s = 1.0
    elif not ta or not tb:
        s = 0.0
    else:
        la, lb = ka.lines[i], kb.lines[j]
        sm = SequenceMatcher(None, la, lb, autojunk=False)
        s = sm.ratio()
        if len(ta) + len(tb) <= 4000 and s < 0.95:
            cm = SequenceMatcher(None, ta, tb, autojunk=False)
            if cm.real_quick_ratio() > s and cm.quick_ratio() > s:
                s = max(s, cm.ratio())
    if ka.type[i] != kb.type[j]:
        s *= 0.7
    return s


def _lis(seq: Sequence[int]) -> List[int]:
    """Indices (into seq) of a longest strictly increasing subsequence."""
    tails: List[int] = []
    tails_idx: List[int] = []
    prev = [-1] * len(seq)
    for k, v in enumerate(seq):
        pos = bisect.bisect_left(tails, v)
        if pos == len(tails):
            tails.append(v)
            tails_idx.append(k)
        else:
            tails[pos] = v
            tails_idx[pos] = k
        prev[k] = tails_idx[pos - 1] if pos > 0 else -1
    out: List[int] = []
    k = tails_idx[-1] if tails_idx else -1
    while k >= 0:
        out.append(k)
        k = prev[k]
    return out[::-1]


def choose_strategy(a: Sequence[Cell], b: Sequence[Cell], requested: str = "auto") -> str:
    if requested in ("id", "content"):
        return requested
    ids_a = [c.id for c in a]
    ids_b = [c.id for c in b]
    if not a or not b or not all(ids_a) or not all(ids_b):
        return "content"
    if len(set(ids_a)) != len(ids_a) or len(set(ids_b)) != len(ids_b):
        return "content"  # duplicate ids: not trustworthy
    shared = len(set(ids_a) & set(ids_b))
    return "id" if shared >= 0.5 * min(len(a), len(b)) else "content"


def _fuzzy_gap(ka: _Keys, kb: _Keys, a_idx: List[int], b_idx: List[int],
               threshold: float) -> List[Entry]:
    """Order-preserving best pairing of the cells in a gap between anchors."""
    n, m = len(a_idx), len(b_idx)
    if n == 0:
        return [("added", None, j) for j in b_idx]
    if m == 0:
        return [("deleted", i, None) for i in a_idx]
    # Avoid quadratic blowups on huge rewrites: only compare a diagonal band.
    band = None if n * m <= 40000 else max(50, abs(n - m) + 50)
    sim: Dict[Tuple[int, int], float] = {}
    for x in range(n):
        for y in range(m):
            if band is not None and abs(x * m / n - y) > band:
                continue
            s = similarity(ka, a_idx[x], kb, b_idx[y])
            if s >= threshold:
                sim[(x, y)] = s
    # score[x][y] = best total similarity aligning a[:x] with b[:y]
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    for x in range(1, n + 1):
        row, prow = score[x], score[x - 1]
        for y in range(1, m + 1):
            best = prow[y] if prow[y] >= row[y - 1] else row[y - 1]
            s = sim.get((x - 1, y - 1))
            if s is not None and prow[y - 1] + s > best:
                best = prow[y - 1] + s
            row[y] = best
    # backtrack
    pairs: List[Tuple[int, int]] = []
    x, y = n, m
    while x > 0 and y > 0:
        s = sim.get((x - 1, y - 1))
        if s is not None and abs(score[x][y] - (score[x - 1][y - 1] + s)) < 1e-12:
            pairs.append((x - 1, y - 1))
            x -= 1
            y -= 1
        elif score[x][y] == score[x - 1][y]:
            x -= 1
        else:
            y -= 1
    pairs.reverse()
    out: List[Entry] = []
    px = py = 0
    for x, y in pairs:
        out.extend(("deleted", a_idx[k], None) for k in range(px, x))
        out.extend(("added", None, b_idx[k]) for k in range(py, y))
        out.append(("pair", a_idx[x], b_idx[y]))
        px, py = x + 1, y + 1
    out.extend(("deleted", a_idx[k], None) for k in range(px, n))
    out.extend(("added", None, b_idx[k]) for k in range(py, m))
    return out


def align_cells(a: Sequence[Cell], b: Sequence[Cell], opts: Optional[CompareOptions] = None,
                strategy: str = "auto", threshold: float = 0.5,
                detect_moves: bool = True) -> Tuple[List[Entry], str]:
    """Align cells; returns (entries, strategy actually used)."""
    opts = opts or CompareOptions()
    ka, kb = _Keys(a, opts), _Keys(b, opts)
    strategy = choose_strategy(a, b, strategy)

    moved: List[Tuple[int, int]] = []
    if strategy == "id":
        pos_a = {c.id: i for i, c in enumerate(a) if c.id}
        matched: List[Tuple[int, int]] = []  # sorted by j
        used: set = set()
        for j, c in enumerate(b):
            i = pos_a.get(c.id) if c.id else None
            if i is not None and i not in used:
                used.add(i)
                matched.append((i, j))
        keep = set(_lis([i for i, _ in matched]))
        anchors = [p for k, p in enumerate(matched) if k in keep]
        moved = [p for k, p in enumerate(matched) if k not in keep]
    else:
        keys_a = [(ka.type[i], ka.text[i]) for i in range(len(a))]
        keys_b = [(kb.type[j], kb.text[j]) for j in range(len(b))]
        sm = SequenceMatcher(None, keys_a, keys_b, autojunk=False)
        anchors = []
        for blk in sm.get_matching_blocks():
            anchors.extend((blk.a + k, blk.b + k) for k in range(blk.size))

    moved_a = {i for i, _ in moved}
    moved_b = {j for _, j in moved}

    # Walk the gaps between anchors.
    entries: List[Entry] = []
    pa = pb = 0
    for ai, bj in anchors + [(len(a), len(b))]:
        gap_a = [i for i in range(pa, ai) if i not in moved_a]
        gap_b = [j for j in range(pb, bj) if j not in moved_b]
        gap_entries = _fuzzy_gap(ka, kb, gap_a, gap_b, threshold)
        # Re-insert id-moved cells at their positions so entries stay ordered.
        for i in range(pa, ai):
            if i in moved_a:
                gap_entries.append(("deleted", i, None))
        for j in range(pb, bj):
            if j in moved_b:
                gap_entries.append(("added", None, j))
        entries.extend(_order_gap(gap_entries))
        if ai < len(a) and bj < len(b):
            entries.append(("pair", ai, bj))
        pa, pb = ai + 1, bj + 1

    # Moves: explicit id moves, plus delete/add pairs with near-identical content.
    move_pairs: List[Tuple[int, int]] = list(moved)
    if detect_moves:
        dels = [e[1] for e in entries if e[0] == "deleted" and e[1] not in moved_a]
        adds = [e[2] for e in entries if e[0] == "added" and e[2] not in moved_b]
        if dels and adds and len(dels) * len(adds) <= 250000:
            cands = []
            for i in dels:
                for j in adds:
                    if ka.type[i] != kb.type[j] or not ka.text[i]:
                        continue
                    s = similarity(ka, i, kb, j)
                    if s >= max(MOVE_THRESHOLD, threshold):
                        cands.append((-s, i, j))
            cands.sort()
            used_a, used_b = set(), set()
            for _, i, j in cands:
                if i not in used_a and j not in used_b:
                    used_a.add(i)
                    used_b.add(j)
                    move_pairs.append((i, j))
    elif moved:
        # Without move detection, id-moved cells are plain deletes + adds.
        move_pairs = []

    if move_pairs:
        by_a = {i: j for i, j in move_pairs}
        by_b = {j: i for i, j in move_pairs}
        new_entries: List[Entry] = []
        for kind, i, j in entries:
            if kind == "deleted" and i in by_a:
                new_entries.append(("moved-away", i, by_a[i]))
            elif kind == "added" and j in by_b:
                new_entries.append(("moved", by_b[j], j))
            else:
                new_entries.append((kind, i, j))
        entries = new_entries
    return entries, strategy


def _order_gap(entries: List[Entry]) -> List[Entry]:
    """Order the entries of one gap for top-to-bottom reading.

    Pairs and additions follow B order; each deletion is emitted just before
    the first pair that comes after it in A (so '-' cells precede '+' cells,
    as in a unified diff).
    """
    rest = sorted((e for e in entries if e[0] != "deleted"), key=lambda e: e[2])
    deletes = sorted((e for e in entries if e[0] == "deleted"), key=lambda e: e[1])
    bound = [float("inf")] * len(rest)
    nxt = float("inf")
    for k in range(len(rest) - 1, -1, -1):
        if rest[k][0] == "pair":
            nxt = rest[k][1]
        bound[k] = nxt
    out: List[Entry] = []
    di = 0
    for k, e in enumerate(rest):
        while di < len(deletes) and deletes[di][1] < bound[k]:
            out.append(deletes[di])
            di += 1
        out.append(e)
    out.extend(deletes[di:])
    return out
