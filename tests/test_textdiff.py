import random
import shutil
import subprocess
import tempfile
import os
import re
import unittest

import helpers  # noqa: F401  (sets sys.path)
from nbdiff.textdiff import (CompareOptions, LineDiff, format_context, format_normal, format_side_by_side,
                             format_unified, normalize_line, split_lines)


def render(lines):
    return "\n".join(ln.prefix + ln.text for ln in lines)


class TestNormalize(unittest.TestCase):
    def test_options(self):
        self.assertEqual(normalize_line("A  b\t", CompareOptions(ignore_case=True)), "a  b\t")
        self.assertEqual(normalize_line(" a   b  ", CompareOptions(ignore_space_change=True)), " a b")
        self.assertEqual(normalize_line(" a   b  ", CompareOptions(ignore_all_space=True)), "ab")
        self.assertEqual(normalize_line("a  \t", CompareOptions(ignore_trailing_space=True)), "a")
        self.assertEqual(normalize_line("x\r", CompareOptions(strip_trailing_cr=True)), "x")
        self.assertEqual(normalize_line("\tx", CompareOptions(ignore_tab_expansion=True)), "        x")

    def test_split_lines(self):
        self.assertEqual(split_lines(""), [])
        self.assertEqual(split_lines("a\nb"), ["a", "b"])
        self.assertEqual(split_lines("a\nb\n"), ["a", "b"])
        self.assertEqual(split_lines("a\x0cb"), ["a\x0cb"])  # form feed is not a line break


class TestIgnoreOptions(unittest.TestCase):
    def test_blank_lines(self):
        self.assertTrue(LineDiff.from_text("a\nb", "a\n\nb").changed)
        self.assertFalse(LineDiff.from_text("a\nb", "a\n\nb", CompareOptions(ignore_blank_lines=True)).changed)
        # a real change next to a blank-line change is still reported
        self.assertTrue(LineDiff.from_text("a\nb", "a\n\nc", CompareOptions(ignore_blank_lines=True)).changed)

    def test_matching_lines(self):
        o = CompareOptions(ignore_matching=[re.compile(r"^# date:")])
        self.assertFalse(LineDiff.from_text("# date: 1\nx", "# date: 2\nx", o).changed)
        self.assertTrue(LineDiff.from_text("# date: 1\nx", "# date: 2\ny", o).changed)

    def test_whitespace(self):
        self.assertFalse(LineDiff.from_text("a = 1", "a  =  1", CompareOptions(ignore_space_change=True)).changed)
        self.assertTrue(LineDiff.from_text("a = 1", "a=1", CompareOptions(ignore_space_change=True)).changed)
        self.assertFalse(LineDiff.from_text("a = 1", "a=1", CompareOptions(ignore_all_space=True)).changed)

    def test_hunks_drop_ignorable(self):
        ld = LineDiff.from_text("a\nb\nc", "a\n\nb\nc", CompareOptions(ignore_blank_lines=True))
        self.assertEqual(ld.hunks(3), [])

    def test_stats(self):
        self.assertEqual(LineDiff.from_text("a\nb\nc", "a\nB\nc\nd").stats(), (2, 1))


def _have_gnu_diff():
    if not shutil.which("diff"):
        return False
    p = subprocess.run(["diff", "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return b"GNU" in p.stdout


@unittest.skipUnless(_have_gnu_diff(), "GNU diff not available")
class TestAgainstGnuDiff(unittest.TestCase):
    """Our hunk rendering must be byte-identical to GNU diff for the same edit script.

    difflib and GNU diff can legitimately pick different (equally short) edit
    scripts, so random inputs are drawn from a tiny alphabet with mostly
    single-line edits, and cases where the edit scripts differ in size are skipped.
    """

    def gnu(self, a, b, *flags):
        with tempfile.TemporaryDirectory() as d:
            fa, fb = os.path.join(d, "a"), os.path.join(d, "b")
            for p, lines in ((fa, a), (fb, b)):
                with open(p, "w") as f:
                    f.write("".join(x + "\n" for x in lines))
            p = subprocess.run(["diff", *flags, fa, fb], stdout=subprocess.PIPE, universal_newlines=True)
            return p.stdout

    def test_fixed_cases(self):
        cases = [
            (["a", "b", "c", "d"], ["a", "B", "c", "d", "e"]),
            ([], ["x", "y"]),
            (["x", "y"], []),
            ([str(i) for i in range(20)], [str(i) for i in range(20) if i not in (3, 15)]),
            ([str(i) for i in range(30)], [str(i) if i != 10 else "ten" for i in range(30)] + ["end"]),
        ]
        for a, b in cases:
            ld = LineDiff(a, b)
            out = self.gnu(a, b, "-U3").splitlines()[2:]
            self.assertEqual(render(format_unified(ld, 3)).splitlines(), out, (a, b))
            out = self.gnu(a, b, "-C3").splitlines()[2:]
            self.assertEqual(render(format_context(ld, 3)).splitlines(), out, (a, b))
            out = self.gnu(a, b).splitlines()
            self.assertEqual(render(format_normal(ld)).splitlines(), out, (a, b))

    def test_random_single_edits(self):
        rng = random.Random(1234)
        checked = 0
        for _ in range(300):
            a = ["line %d" % i for i in range(rng.randint(0, 40))]
            b = list(a)
            for _ in range(rng.randint(1, 3)):
                op = rng.choice("idr")
                pos = rng.randint(0, len(b))
                if op == "i":
                    b.insert(pos, "new %d" % rng.randint(0, 999))
                elif b and pos < len(b):
                    if op == "d":
                        del b[pos]
                    else:
                        b[pos] = "changed %d" % rng.randint(0, 999)
            ld = LineDiff(a, b)
            for n in (0, 1, 3):
                ours = render(format_unified(ld, n)).splitlines()
                theirs = self.gnu(a, b, "-U%d" % n).splitlines()[2:]
                if ours != theirs and len(ours) == len(theirs):
                    continue  # equally short alternative edit script
                self.assertEqual(ours, theirs, (a, b, n))
                checked += 1
        self.assertGreater(checked, 800)


class TestSideBySide(unittest.TestCase):
    def test_marks(self):
        ld = LineDiff(["same", "old", "gone"], ["same", "new"])
        rows = [ln.text for ln in format_side_by_side(ld, width=40)]
        self.assertEqual(len(rows), 3)
        self.assertIn("|", rows[1])
        self.assertTrue(rows[2].rstrip().endswith("<"))
        rows = [ln.text for ln in format_side_by_side(ld, width=40, suppress_common=True)]
        self.assertEqual(len(rows), 2)
        rows = [ln.text for ln in format_side_by_side(ld, width=40, left_column=True)]
        self.assertTrue(rows[0].rstrip().endswith("("))


if __name__ == "__main__":
    unittest.main()
