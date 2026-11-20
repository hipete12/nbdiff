import base64
import struct
import unittest
import zlib

from helpers import code, md, nb, result, stream
from nbdiff.align import align_cells
from nbdiff.diff import DiffOptions, diff_json, diff_notebooks
from nbdiff.notebook import Cell, NotebookError, parse_notebook, parse_notebook_bytes
from nbdiff.outputs import apply_carriage_returns, diff_outputs, image_dims, normalize_outputs, strip_ansi
from nbdiff.textdiff import CompareOptions


def png(w, h):
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def P(notebook, name="x.ipynb"):
    return parse_notebook(notebook, name)


class TestNotebookParsing(unittest.TestCase):
    def test_v4_list_and_string_sources(self):
        n = P(nb(code(["a = 1\n", "b = 2"]), md("# T")))
        self.assertEqual(n.cells[0].source, "a = 1\nb = 2")
        self.assertEqual(n.cells[1].source, "# T")
        self.assertEqual(n.cells[0].number, 1)

    def test_v3_conversion(self):
        v3 = {"nbformat": 3, "nbformat_minor": 0, "metadata": {}, "worksheets": [{"cells": [
            {"cell_type": "heading", "level": 2, "source": "Title"},
            {"cell_type": "code", "input": ["x\n", "y"], "prompt_number": 4, "language": "python",
             "outputs": [{"output_type": "pyout", "prompt_number": 4, "text": ["3"], "png": "AAAA"},
                         {"output_type": "stream", "stream": "stderr", "text": "warn"},
                         {"output_type": "pyerr", "ename": "E", "evalue": "v", "traceback": []}]},
        ]}]}
        n = P(v3)
        self.assertEqual(n.cells[0].cell_type, "markdown")
        self.assertEqual(n.cells[0].source, "## Title")
        c = n.cells[1]
        self.assertEqual((c.source, c.execution_count), ("x\ny", 4))
        self.assertEqual(c.outputs[0]["output_type"], "execute_result")
        self.assertEqual(set(c.outputs[0]["data"]), {"text/plain", "image/png"})
        self.assertEqual(c.outputs[1]["name"], "stderr")
        self.assertEqual(c.outputs[2]["output_type"], "error")

    def test_errors(self):
        with self.assertRaises(NotebookError):
            parse_notebook_bytes(b"{not json", "bad.ipynb")
        with self.assertRaises(NotebookError):
            parse_notebook_bytes(b"[1, 2]", "bad.ipynb")
        with self.assertRaises(NotebookError):
            parse_notebook_bytes(b'{"foo": 1}', "bad.ipynb")
        self.assertEqual(parse_notebook_bytes(b"", "empty.ipynb").cells, [])


class TestOutputs(unittest.TestCase):
    def test_carriage_returns(self):
        self.assertEqual(apply_carriage_returns("10%\r50%\r100%\ndone\n"), "100%\ndone\n")
        self.assertEqual(apply_carriage_returns("abcdef\rXY"), "XYcdef")
        self.assertEqual(apply_carriage_returns("a\r\nb"), "a\nb")

    def test_ansi(self):
        self.assertEqual(strip_ansi("\x1b[0;31mError\x1b[0m: x"), "Error: x")

    def test_stream_merge(self):
        outs = normalize_outputs([stream("a\n"), stream("b\n"), stream("c\n", "stderr"), stream("d\n")])
        self.assertEqual([o.label for o in outs], ["stream:stdout", "stream:stderr", "stream:stdout"])
        self.assertEqual(outs[0].parts["text"].text, "a\nb\n")

    def test_image_dims_and_change(self):
        self.assertEqual(image_dims(png(7, 5)), (7, 5))
        a = [{"output_type": "display_data", "metadata": {},
              "data": {"image/png": base64.b64encode(png(4, 4)).decode()}}]
        b = [{"output_type": "display_data", "metadata": {},
              "data": {"image/png": base64.b64encode(png(8, 4)).decode()}}]
        od = diff_outputs(a, b, CompareOptions())
        self.assertEqual(od.changes[0].status, "modified")
        self.assertIn("4×4 → 8×4", od.changes[0].parts[0].note())
        self.assertFalse(diff_outputs(a, a, CompareOptions()).changed)

    def test_added_deleted_and_modified(self):
        a = [stream("x = 1\n"), {"output_type": "error", "ename": "E", "evalue": "boom", "traceback": ["tb"]}]
        b = [stream("x = 2\n"), result("42")]
        od = diff_outputs(a, b, CompareOptions())
        self.assertEqual([c.status for c in od.changes], ["modified", "deleted", "added"])

    def test_ignore_regex_in_outputs(self):
        import re
        o = CompareOptions(ignore_matching=[re.compile(r"took \d+ ms")])
        self.assertFalse(diff_outputs([stream("took 5 ms\n")], [stream("took 9 ms\n")], o).changed)

    def test_ignore_mime(self):
        a = [result("1", **{"text/html": "<b>1</b>"})]
        b = [result("1", **{"text/html": "<i>1</i>"})]
        self.assertTrue(diff_outputs(a, b, CompareOptions()).changed)
        self.assertFalse(diff_outputs(a, b, CompareOptions(), ignore_mimes=["text/*ml"]).changed)


def cells(*srcs, ids=None):
    return [Cell(i, "code", s, id=ids[i] if ids else None) for i, s in enumerate(srcs)]


class TestAlignment(unittest.TestCase):
    def test_modified_and_inserted(self):
        a = cells("import os", "x = 1\ny = 2\nz = 3", "plot()")
        b = cells("import os", "x = 1\ny = 20\nz = 3", "brand new cell", "plot()")
        entries, strategy = align_cells(a, b)
        self.assertEqual(strategy, "content")
        self.assertEqual(entries, [("pair", 0, 0), ("pair", 1, 1), ("added", None, 2), ("pair", 2, 3)])

    def test_unrelated_replacement_is_delete_plus_add(self):
        entries, _ = align_cells(cells("a", "zzz qqq", "c"), cells("a", "completely different 12345", "c"))
        self.assertEqual(entries, [("pair", 0, 0), ("deleted", 1, None), ("added", None, 1), ("pair", 2, 2)])

    def test_move_by_content(self):
        a = cells("first()", "def helper():\n    return 42", "second()", "third()")
        b = cells("def helper():\n    return 42", "first()", "second()", "third()")
        entries, _ = align_cells(a, b)
        kinds = [e[0] for e in entries]
        self.assertIn("moved", kinds)
        self.assertIn("moved-away", kinds)
        self.assertNotIn("added", kinds)
        self.assertNotIn("deleted", kinds)
        self.assertEqual(align_cells(a, b, detect_moves=False)[0].count(("added", None, 0)), 1)

    def test_id_strategy(self):
        a = cells("a", "b", "c", "d", ids=["1", "2", "3", "4"])
        b = cells("a", "c", "b changed", "d", "e", ids=["1", "3", "2", "4", "5"])
        entries, strategy = align_cells(a, b)
        self.assertEqual(strategy, "id")
        self.assertIn(("moved", 2, 1), entries)
        self.assertIn(("pair", 1, 2), entries)
        self.assertIn(("added", None, 4), entries)

    def test_id_falls_back_without_shared_ids(self):
        a = cells("a", "b", ids=["1", "2"])
        b = cells("a", "b", ids=["x", "y"])
        self.assertEqual(align_cells(a, b)[1], "content")


class TestDiffModel(unittest.TestCase):
    def test_statuses_and_hints(self):
        a = P(nb(md("# T"), code("print(1)", [stream("1\n")]), code("old()"), kernelspec={"name": "py"}))
        b = P(nb(md("# T"), code("print(1)", [stream("2\n")]), code("new_cell_entirely_different_123()"),
                 kernelspec={"name": "py3"}))
        nd = diff_notebooks(a, b)
        self.assertEqual([c.status for c in nd.cells], ["unchanged", "unchanged", "deleted", "added"])
        self.assertEqual(nd.cells[1].hidden, ["outputs"])
        self.assertTrue(nd.metadata_hidden)
        self.assertFalse(nd.identical)

        nd = diff_notebooks(a, b, DiffOptions(outputs=True, metadata=True))
        self.assertEqual(nd.cells[1].status, "modified")
        self.assertEqual(nd.cells[1].changed, ["outputs"])
        self.assertEqual(nd.metadata[0].path, "kernelspec.name")

    def test_identical_ignoring_whitespace(self):
        a = P(nb(code("x = 1")))
        b = P(nb(code("x  =  1")))
        self.assertFalse(diff_notebooks(a, b).identical)
        self.assertTrue(diff_notebooks(a, b, DiffOptions(compare=CompareOptions(ignore_all_space=True))).identical)

    def test_execution_count_scope(self):
        a = P(nb(code("x", count=1)))
        b = P(nb(code("x", count=7)))
        self.assertTrue(diff_notebooks(a, b).identical)
        nd = diff_notebooks(a, b, DiffOptions(execution_count=True))
        self.assertEqual(nd.cells[0].changed, ["execution count"])

    def test_type_change(self):
        nd = diff_notebooks(P(nb(code("# Title words here"))), P(nb(md("# Title words here"))))
        self.assertEqual(nd.cells[0].status, "modified")
        self.assertIn("type", nd.cells[0].changed)

    def test_diff_json_ignore(self):
        a = {"execution": {"t": 1}, "tags": ["a"]}
        b = {"execution": {"t": 2}, "tags": ["a", "b"]}
        self.assertEqual([m.path for m in diff_json(a, b)], ["execution.t", "tags"])
        self.assertEqual([m.path for m in diff_json(a, b, ignore=["execution"])], ["tags"])


if __name__ == "__main__":
    unittest.main()
