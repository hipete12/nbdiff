import base64
import os
import struct
import tempfile
import unittest

from helpers import code, nb, run, write
from nbdiff import figures
from nbdiff.diff import DiffOptions, diff_notebooks
from nbdiff.notebook import parse_notebook
from nbdiff.outputs import diff_outputs, normalize_outputs, outputs_equal
from nbdiff.textdiff import CompareOptions

PLOTLY = figures.PLOTLY_MIME
TEMPLATE = {"layout": {"font": {"color": "#2a3f5f"}, "plot_bgcolor": "white"},
            "data": {"bar": [{"marker": {"line": {"color": "white"}}}]}}


def typed(values, code_="d", dtype="f8", shape=None):
    d = {"dtype": dtype, "bdata": base64.b64encode(struct.pack("<%d%s" % (len(values), code_), *values)).decode()}
    if shape:
        d["shape"] = shape
    return d


def figure(y, title="Rides", name="Downtown", template=TEMPLATE):
    return {"data": [{"type": "scatter", "name": name, "mode": "lines", "x": list(range(len(y))), "y": y}],
            "layout": {"template": template, "title": {"text": title}}}


def html(fig, div_id="0b6ee2a8-4c4e-4b8e-9c55-3f8f0e9b1d2a"):
    """What plotly's notebook renderers write (trimmed)."""
    import json
    return ('<div>  <div id="%s" class="plotly-graph-div" style="height:525px; width:100%%;"></div>'
            '<script type="text/javascript">  window.PLOTLYENV=window.PLOTLYENV || {};'
            '  if (document.getElementById("%s")) {   Plotly.newPlot(   "%s",   %s,   %s,   {"responsive": true}  )'
            '  };  </script> </div>' % (div_id, div_id, div_id, json.dumps(fig["data"]), json.dumps(fig["layout"])))


LOADER = ('<script type="text/javascript">window.PlotlyConfig = {MathJaxConfig: \'local\'};</script>\n'
          '<script type="module">import "https://cdn.plot.ly/plotly-4.1.1.min"</script>')


def display(data):
    return {"output_type": "display_data", "data": data, "metadata": {}}


class TestArrays(unittest.TestCase):
    def test_typed_array_equals_list(self):
        vals = [1.5, 2.0, 3.25, 10.0] * 10
        self.assertEqual(figures.format_array(typed(vals)), figures.format_array(vals))
        self.assertRegex(figures.format_array(vals), r"^40 values, min 1\.5, max 10, mean 4\.1875  #[0-9a-f]{8}$")

    def test_integer_dtypes_and_shape(self):
        self.assertEqual(figures.format_array(typed([1, 2, 3], "b", "i1")), "[1, 2, 3]")
        s = figures.format_array(typed(list(range(24 * 7)), "H", "u2", shape="7, 24"))
        self.assertTrue(s.startswith("7×24 values, min 0, max 167"), s)
        self.assertTrue(figures.format_array([[1, 2, 3]] * 20).startswith("20×3 values"))

    def test_different_values_differ(self):
        a = [1.0, 2.0, 3.0, 4.0] * 5
        b = [1.0, 3.0, 2.0, 4.0] * 5  # same count/min/max/mean, different order
        self.assertNotEqual(figures.format_array(a), figures.format_array(b))

    def test_missing_and_strings(self):
        self.assertIn("10 missing", figures.format_array([1, None, 3, None] * 5 + [7]))
        s = figures.format_array(["a", "b", "a"] * 10)
        self.assertTrue(s.startswith('30 strings (2 distinct), "a" … "a"'), s)

    def test_colorscale(self):
        scale = [[i / 8, "rgb(%d,%d,255)" % (i, i)] for i in range(9)]
        self.assertTrue(figures.format_array(scale).startswith('9 stops, "rgb(0,0,255)" … "rgb(8,8,255)"'))


class TestFigureText(unittest.TestCase):
    def test_lines(self):
        fig = figure([5, 7, 9])
        fig["layout"]["annotations"] = [{"text": "A"}, {"text": "B"}]
        lines = figures.figure_lines(fig)
        self.assertEqual(lines[0], 'data[0]: scatter "Downtown"')
        self.assertIn('data[0].mode: "lines"', lines)
        self.assertIn("data[0].y: [5, 7, 9]", lines)
        self.assertIn('layout.annotations[1].text: "B"', lines)
        self.assertIn('layout.title.text: "Rides"', lines)
        tpl = [ln for ln in lines if ln.startswith("layout.template")]
        self.assertEqual(len(tpl), 1)
        self.assertRegex(tpl[0], r"^layout.template: 3 settings  #[0-9a-f]{8}$")

    def test_summary(self):
        fig = figure([1, 2])
        fig["data"].append({"type": "bar", "y": [1]})
        fig["data"].append({"type": "bar", "y": [2]})
        self.assertEqual(figures.figure_summary(fig), 'plotly figure: 3 traces (scatter, bar ×2), "Rides"')

    def test_html_roundtrip(self):
        fig = figure([5, 7, 9])
        got = figures.figure_from_html(html(fig))
        self.assertEqual(got["data"], fig["data"])
        self.assertEqual(got["layout"], fig["layout"])
        self.assertEqual(got["config"], {"responsive": True})
        self.assertIsNone(figures.figure_from_html("<div>Plotly.newPlot(broken</div>"))

    def test_loader(self):
        self.assertEqual(figures.loader_text(LOADER), "plotly.js loader: https://cdn.plot.ly/plotly-4.1.1.min")
        inline = LOADER + "<script>/** plotly.js v2.35.2 */" + "x" * 200000 + "</script>"
        self.assertTrue(figures.loader_text(inline).startswith("plotly.js loader: inline plotly.js v2.35.2"))
        self.assertIsNone(figures.loader_text("<b>plotly is great</b>"))
        self.assertIsNone(figures.loader_text(html(figure([1]))))


class TestPlotlyOutputs(unittest.TestCase):
    co = CompareOptions()

    def test_rerun_with_new_div_id_is_unchanged(self):
        fig = figure([5, 7, 9])
        a = [display({PLOTLY: fig, "text/html": html(fig, "11111111-1111-1111-1111-111111111111")})]
        b = [display({PLOTLY: fig, "text/html": html(fig, "22222222-2222-2222-2222-222222222222")})]
        self.assertTrue(outputs_equal(a, b, self.co))
        # HTML-only figures (renderer "notebook_connected") too
        self.assertTrue(outputs_equal([display({"text/html": html(fig, "1" * 8 + "-1111-1111-1111-" + "1" * 12)})],
                                      [display({"text/html": html(fig)})], self.co))

    def test_html_rendering_is_not_compared_twice(self):
        fig = figure([5, 7, 9])
        (o,) = normalize_outputs([display({PLOTLY: fig, "text/html": html(fig)})])
        self.assertEqual(list(o.parts), [PLOTLY])
        self.assertEqual(o.describe(), 'display_data application/vnd.plotly.v1+json (plotly figure: 1 trace '
                                       '(scatter), "Rides")')
        # ... unless the JSON is ignored
        (o,) = normalize_outputs([display({PLOTLY: fig, "text/html": html(fig)})], ignore_mimes=[PLOTLY])
        self.assertIn('data[0]: scatter "Downtown"', o.parts["text/html"].text)

    def test_figure_change_is_a_setting_level_diff(self):
        a = [display({PLOTLY: figure([5, 7, 9])})]
        b = [display({PLOTLY: figure([5, 7, 10], title="Rides per day")})]
        od = diff_outputs(a, b, self.co)
        (pc,) = od.changes[0].parts
        removed = [pc.diff.a[i] for op in pc.diff.ops if op.tag != "equal" for i in range(op.i1, op.i2)]
        self.assertEqual(pc.diff.stats(), (2, 2))
        self.assertEqual(removed, ["data[0].y: [5, 7, 9]", 'layout.title.text: "Rides"'])

    def test_figure_is_not_paired_with_loader(self):
        fig = figure([1, 2, 3])
        a = [display({PLOTLY: fig})]
        b = [display({"text/html": LOADER}), display({PLOTLY: figure([1, 2, 4])})]
        od = diff_outputs(a, b, self.co)
        self.assertEqual([c.status for c in od.changes], ["added", "modified"])
        self.assertEqual(od.changes[0].b.parts["text/html"].text,
                         "plotly.js loader: https://cdn.plot.ly/plotly-4.1.1.min")


class TestExecutionMetadata(unittest.TestCase):
    def test_timing_metadata_ignored_unless_execution_count(self):
        t = {"iopub.status.busy": "2026-01-01T10:00:00Z", "shell.execute_reply": "2026-01-01T10:00:01Z"}
        t2 = {"iopub.status.busy": "2026-02-01T10:00:00Z", "shell.execute_reply": "2026-02-01T10:00:02Z"}
        a = parse_notebook(nb(code("x", count=1, execution=t, tags=["keep"])), "a")
        b = parse_notebook(nb(code("x", count=1, execution=t2, tags=["keep"])), "b")
        nd = diff_notebooks(a, b, DiffOptions(metadata=True))
        self.assertTrue(nd.identical)
        self.assertEqual(nd.hidden_aspects(), [])
        nd = diff_notebooks(a, b, DiffOptions(metadata=True, execution_count=True))
        self.assertEqual([m.path for m in nd.cells[0].metadata],
                         ["execution.iopub.status.busy", "execution.shell.execute_reply"])


class TestPlotlyCli(unittest.TestCase):
    def test_rerun_notebook_is_identical(self):
        fig = figure([5, 7, 9])
        with tempfile.TemporaryDirectory() as d:
            pa = write(os.path.join(d, "a.ipynb"), nb(code("fig.show()", [
                display({"text/html": LOADER}),
                display({PLOTLY: fig, "text/html": html(fig, "11111111-1111-1111-1111-111111111111")})], count=1)))
            pb = write(os.path.join(d, "b.ipynb"), nb(code("fig.show()", [
                display({"text/html": LOADER}),
                display({PLOTLY: fig, "text/html": html(fig, "22222222-2222-2222-2222-222222222222")})], count=1)))
            self.assertEqual(run("-A", pa, pb)[0], 0)
            pc = write(os.path.join(d, "c.ipynb"), nb(code("fig.show()", [
                display({"text/html": LOADER}),
                display({PLOTLY: figure([5, 7, 9], title="New")})], count=1)))
            rc, out, _ = run("-o", "--color=never", pa, pc)
            self.assertEqual(rc, 1)
            self.assertIn('-layout.title.text: "Rides"', out)
            self.assertIn('+layout.title.text: "New"', out)
            self.assertNotIn("plotly-graph-div", out)


if __name__ == "__main__":
    unittest.main()
