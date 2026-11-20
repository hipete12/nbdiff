import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from helpers import ROOT, code, md, nb, result, run, stream, write

BIN = os.path.join(ROOT, "bin", "nbdiff")


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nbdiff-test-")
        self.a = write(self.path("a.ipynb"), nb(
            md("# Title"), code("x = 1\ny = 2", [stream("3\n")], 1), code("print('bye')", [stream("bye\n")], 2),
            kernelspec={"name": "python3", "display_name": "Python 3"}))
        self.b = write(self.path("b.ipynb"), nb(
            md("# Title"), code("x = 1\ny = 20", [stream("21\n")], 1), code("print('bye')", [stream("bye!\n")], 3),
            md("Extra notes"), kernelspec={"name": "python3", "display_name": "Python 3"}))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def path(self, *parts):
        return os.path.join(self.tmp, *parts)


class TestExitStatusAndBasics(CliTestCase):
    def test_exit_codes(self):
        self.assertEqual(run(self.a, self.a)[0], 0)
        self.assertEqual(run(self.a, self.b)[0], 1)
        self.assertEqual(run(self.a, self.path("missing.ipynb"))[0], 2)
        bad = self.path("bad.ipynb")
        with open(bad, "w") as f:
            f.write("{oops")
        code_, _, err = run(self.a, bad)
        self.assertEqual(code_, 2)
        self.assertIn("invalid JSON", err)

    def test_identical_prints_nothing(self):
        self.assertEqual(run(self.a, self.a)[1], "")

    def test_operand_errors(self):
        code_, _, err = run(self.a)
        self.assertEqual(code_, 2)
        self.assertIn("missing operand after", err)
        code_, _, err = run(self.a, self.b, self.a)
        self.assertEqual(code_, 2)
        self.assertIn("extra operand", err)
        code_, _, err = run("-u", "-c", self.a, self.b)
        self.assertEqual(code_, 2)
        self.assertIn("conflicting output style", err)
        self.assertEqual(run("--bogus", self.a, self.b)[0], 2)

    def test_brief_and_report_identical(self):
        code_, out, _ = run("-q", self.a, self.b)
        self.assertEqual((code_, out.strip()), (1, "Files %s and %s differ" % (self.a, self.b)))
        code_, out, _ = run("-s", self.a, self.a)
        self.assertEqual((code_, out.strip()), (0, "Files %s and %s are identical" % (self.a, self.a)))

    def test_default_output(self):
        code_, out, _ = run("--color=never", self.a, self.b)
        self.assertIn("--- %s" % self.a, out)
        self.assertIn("~ cell 2 [code] modified: source", out)
        self.assertIn("-y = 2", out)
        self.assertIn("+y = 20", out)
        self.assertIn("+ cell 4 [markdown] added (only in %s)" % self.b, out)
        self.assertIn("(outputs differ; use -o to show)", out)
        self.assertIn("Summary:", out)
        self.assertNotIn("\x1b[", out)

    def test_outputs_flag(self):
        _, out, _ = run("--color=never", "-o", self.a, self.b)
        self.assertIn("modified: source, outputs", out)
        self.assertIn("-3", out)
        self.assertIn("+21", out)
        self.assertIn("-bye", out)

    def test_hint_when_only_hidden_aspects_differ(self):
        b = write(self.path("c.ipynb"), nb(
            md("# Title"), code("x = 1\ny = 2", [stream("99\n")], 1), code("print('bye')", [stream("bye\n")], 2),
            kernelspec={"name": "python3", "display_name": "Python 3"}))
        code_, out, err = run(self.a, b)
        self.assertEqual((code_, out), (0, ""))
        self.assertIn("outputs differ (use -o)", err)
        self.assertEqual(run("-o", self.a, b)[0], 1)

    def test_color(self):
        _, out, _ = run("--color=always", self.a, self.b)
        self.assertIn("\x1b[", out)
        _, out, _ = run("--color", self.a, self.b)  # auto: not a tty here
        self.assertNotIn("\x1b[", out)

    def test_optional_argument_rewriting(self):
        _, out0, _ = run("--color=never", "--unified=0", self.a, self.b)
        self.assertIn("@@ -2 +2 @@", out0)
        _, out1, _ = run("--color=never", "--unified", self.a, self.b)
        self.assertIn("@@ -1,2 +1,2 @@", out1)
        _, out2, _ = run("--color=never", "--context=1", self.a, self.b)
        self.assertIn("***************", out2)

    def test_formats(self):
        _, out, _ = run("--color=never", "--normal", self.a, self.b)
        self.assertIn("2c2", out)
        self.assertIn("< y = 2", out)
        _, out, _ = run("--color=never", "-y", "-W", "60", self.a, self.b)
        self.assertIn("|", out)
        _, out, _ = run("--color=never", "--stat", "-A", self.a, self.b)
        self.assertIn("source (+1", out)
        self.assertNotIn("+y = 20", out)

    def test_labels(self):
        _, out, _ = run("--color=never", "--label", "OLD", "--label", "NEW", self.a, self.b)
        self.assertIn("--- OLD", out)
        self.assertIn("only in NEW", out)
        self.assertEqual(run("--label", "1", "--label", "2", "--label", "3", self.a, self.b)[0], 2)

    def test_ignore_options(self):
        b = write(self.path("ws.ipynb"), nb(md("#   title"), code("x  =  1\ny = 2", [], 1),
                                            code("PRINT('bye')", [], 2)))
        a = write(self.path("ws0.ipynb"), nb(md("# Title"), code("x = 1\ny = 2", [], 1), code("print('bye')", [], 2)))
        self.assertEqual(run(a, b)[0], 1)
        self.assertEqual(run("-w", "-i", a, b)[0], 0)
        self.assertEqual(run("-w", a, b)[0], 1)

    def test_unchanged_modes(self):
        _, out, _ = run("--color=never", "--unchanged=hide", self.a, self.b)
        self.assertNotIn("unchanged", out.split("Summary")[0])
        _, out, _ = run("--color=never", "--unchanged=list", self.a, self.b)
        self.assertIn("= cell 1 [markdown] unchanged  # Title", out)
        _, out, _ = run("--color=never", "--unchanged=show", self.a, self.b)
        self.assertIn("   # Title", out)

    def test_json(self):
        code_, out, _ = run("--json", "-A", self.a, self.b)
        self.assertEqual(code_, 1)
        doc = json.loads(out)
        cmp = doc["comparisons"][0]
        self.assertFalse(doc["identical"])
        self.assertEqual(cmp["counts"]["added"], 1)
        statuses = [c["status"] for c in cmp["cells"]]
        self.assertEqual(statuses, ["unchanged", "modified", "modified", "added"])
        self.assertEqual(cmp["cells"][2]["execution_count"], [2, 3])

    def test_stdin(self):
        with open(self.b, "rb") as f:
            p = subprocess.run([sys.executable, BIN, "--color=never", self.a, "-"], stdin=f,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(p.returncode, 1)
        self.assertIn(b"+++ -", p.stdout)

    def test_control_characters_are_escaped(self):
        a = write(self.path("crlf_a.ipynb"), nb(code("x = 1\r\ny = 2\r\n")))
        b = write(self.path("crlf_b.ipynb"), nb(code("x = 1\r\ny = 3\r\n")))
        _, out, _ = run("--color=never", a, b)
        self.assertIn("+y = 3^M", out)
        self.assertNotIn("\r", out)
        self.assertEqual(run("--strip-trailing-cr", a, write(self.path("lf.ipynb"), nb(code("x = 1\ny = 2"))))[0], 0)

    def test_visualize_escapes_table_pipes(self):
        a = write(self.path("p_a.ipynb"), nb(code("x = a | b")))
        b = write(self.path("p_b.ipynb"), nb(code("x = a | b | c")))
        out_nb = self.path("pipes.ipynb")
        run("--visualize", out_nb, a, b)
        with open(out_nb) as f:
            glance = "".join(json.load(f)["cells"][1]["source"])
        self.assertIn("a \\| b \\| c", glance)

    def test_help_and_version(self):
        code_, out, _ = run("--help")
        self.assertEqual(code_, 0)
        self.assertIn("--visualize=FILE", out)
        self.assertIn("--diff", out)
        code_, out, _ = run("--version")
        self.assertEqual(code_, 0)
        self.assertTrue(out.startswith("nbdiff "))

    def test_broken_pipe_is_quiet(self):
        p = subprocess.run("%s %s --unchanged=show -A %s %s | head -1" % (
            sys.executable, BIN, self.a, self.b), shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(p.stderr, b"")


class TestDirectories(CliTestCase):
    def setUp(self):
        super().setUp()
        for d in ("d1", "d2", "d1/sub", "d2/sub", "d1/.ipynb_checkpoints"):
            os.makedirs(self.path(d))
        shutil.copy(self.a, self.path("d1", "same.ipynb"))
        shutil.copy(self.a, self.path("d2", "same.ipynb"))
        shutil.copy(self.a, self.path("d1", "sub", "changed.ipynb"))
        shutil.copy(self.b, self.path("d2", "sub", "changed.ipynb"))
        shutil.copy(self.a, self.path("d1", "only1.ipynb"))
        shutil.copy(self.a, self.path("d1", ".ipynb_checkpoints", "x-checkpoint.ipynb"))
        with open(self.path("d1", "notes.txt"), "w") as f:
            f.write("not a notebook")

    def test_non_recursive(self):
        code_, out, _ = run("-q", self.path("d1"), self.path("d2"))
        self.assertEqual(code_, 1)
        self.assertIn("Only in %s: only1.ipynb" % self.path("d1"), out)
        self.assertIn("Common subdirectories", out)
        self.assertNotIn("notes.txt", out)
        self.assertNotIn("checkpoint", out)

    def test_recursive(self):
        code_, out, _ = run("-q", "-r", self.path("d1"), self.path("d2"))
        self.assertEqual(code_, 1)
        self.assertIn("Files %s and %s differ" % (self.path("d1", "sub", "changed.ipynb"),
                                                   self.path("d2", "sub", "changed.ipynb")), out)
        self.assertNotIn("same.ipynb", out)

    def test_new_file(self):
        code_, out, _ = run("--color=never", "-N", self.path("d1"), self.path("d2"))
        self.assertNotIn("Only in", out)
        self.assertIn("deleted (only in %s)" % self.path("d1", "only1.ipynb"), out)

    def test_exclude(self):
        _, out, _ = run("-q", "-x", "only*", self.path("d1"), self.path("d2"))
        self.assertNotIn("only1", out)

    def test_dir_and_file(self):
        code_, out, _ = run("-q", self.path("d1", "sub", "changed.ipynb"), self.path("d2", "sub"))
        self.assertEqual(code_, 1)

    def test_from_file(self):
        code_, out, _ = run("-q", "--from-file", self.a, self.a, self.b)
        self.assertEqual(code_, 1)
        self.assertEqual(out.count("differ"), 1)


class TestVisualize(CliTestCase):
    def test_writes_valid_notebook(self):
        out_nb = self.path("diff.ipynb")
        code_, out, err = run("--visualize", out_nb, self.a, self.b)
        self.assertEqual(code_, 1)
        self.assertEqual(out, "")
        self.assertIn("wrote diff notebook", err)
        with open(out_nb) as f:
            d = json.load(f)
        self.assertEqual((d["nbformat"], d["nbformat_minor"]), (4, 5))
        ids = [c["id"] for c in d["cells"]]
        self.assertEqual(len(ids), len(set(ids)))
        roles = [c["metadata"].get("nbdiff", {}).get("role") for c in d["cells"]]
        self.assertEqual(roles[0], "overview")
        # outputs differ in the second cell -> before/after code cells
        self.assertIn("before", roles)
        self.assertIn("after", roles)
        before = d["cells"][roles.index("before")]
        self.assertEqual(before["cell_type"], "code")
        self.assertEqual(before["outputs"][1]["text"], "3\n")
        self.assertTrue(before["metadata"]["jupyter"]["source_hidden"])
        text = json.dumps(d)
        self.assertIn("```diff", text)
        self.assertIn("added", text)
        for c in d["cells"]:
            self.assertIn(c["cell_type"], ("markdown", "code"))
            self.assertIn("metadata", c)
            if c["cell_type"] == "code":
                self.assertIn("outputs", c)
                self.assertIn("execution_count", c)

    def test_refuses_to_overwrite_input(self):
        code_, _, err = run("--visualize", self.a, self.a, self.b)
        self.assertEqual(code_, 2)
        self.assertIn("refusing to overwrite", err)

    def test_sources_only(self):
        out_nb = self.path("diff.ipynb")
        run("--visualize", out_nb, "--sources-only", self.a, self.b)
        with open(out_nb) as f:
            d = json.load(f)
        roles = [c["metadata"].get("nbdiff", {}).get("role") for c in d["cells"]]
        self.assertNotIn("before", roles)

    def test_identical(self):
        out_nb = self.path("diff.ipynb")
        self.assertEqual(run("--visualize", out_nb, self.a, self.a)[0], 0)
        with open(out_nb) as f:
            self.assertIn("No differences", f.read())

    def test_stdout(self):
        code_, out, _ = run("--visualize", "-", self.a, self.b)
        self.assertEqual(json.loads(out)["nbformat"], 4)


@unittest.skipUnless(shutil.which("git"), "git not available")
class TestGitMode(CliTestCase):
    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def setUp(self):
        super().setUp()
        self.repo = self.path("repo")
        os.makedirs(self.repo)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        self.git("config", "commit.gpgsign", "false")
        shutil.copy(self.a, os.path.join(self.repo, "nb.ipynb"))
        shutil.copy(self.a, os.path.join(self.repo, "old.ipynb"))
        with open(os.path.join(self.repo, "README"), "w") as f:
            f.write("x\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "one")
        self.git("checkout", "-q", "-b", "feature")
        shutil.copy(self.b, os.path.join(self.repo, "nb.ipynb"))
        self.git("mv", "old.ipynb", "renamed.ipynb")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "two")
        self.cwd = os.getcwd()
        os.chdir(self.repo)

    def tearDown(self):
        os.chdir(self.cwd)
        super().tearDown()

    def test_branches(self):
        code_, out, _ = run("--color=never", "--diff", "main", "feature")
        self.assertEqual(code_, 1)
        self.assertIn("--- main:nb.ipynb", out)
        self.assertIn("+++ feature:nb.ipynb", out)
        self.assertIn("renamed old.ipynb → renamed.ipynb", out)
        self.assertIn("2 notebooks changed in git", out)

    def test_range_syntax_and_paths(self):
        code_, out, _ = run("-q", "--diff", "main..feature", "--", "nb.ipynb")
        self.assertEqual((code_, out.strip()), (1, "Files main:nb.ipynb and feature:nb.ipynb differ"))
        code_, out, _ = run("-q", "--diff", "main...feature")
        self.assertIn(":nb.ipynb", out)

    def test_worktree_and_index(self):
        self.assertEqual(run("--diff")[0], 0)
        shutil.copy(self.a, os.path.join(self.repo, "nb.ipynb"))
        code_, out, _ = run("-q", "--diff")
        self.assertEqual((code_, out.strip()), (1, "Files index:nb.ipynb and nb.ipynb differ"))
        self.assertEqual(run("--diff", "--cached")[0], 0)
        self.git("add", "nb.ipynb")
        self.assertEqual(run("--diff")[0], 0)
        code_, out, _ = run("-q", "--diff", "--cached")
        self.assertEqual((code_, out.strip()), (1, "Files HEAD:nb.ipynb and index:nb.ipynb differ"))
        # working tree vs a branch
        self.assertEqual(run("-q", "--diff", "main")[0], 0)

    def test_errors(self):
        code_, _, err = run("--diff", "no-such-rev")
        self.assertEqual(code_, 2)
        self.assertIn("unknown revision", err)
        self.assertEqual(run("--cached", self.a, self.b)[0], 2)

    def test_visualize_multi(self):
        out_nb = self.path("review.ipynb")
        code_, _, _ = run("--diff", "main", "feature", "--visualize", out_nb)
        self.assertEqual(code_, 1)
        with open(out_nb) as f:
            d = json.load(f)
        self.assertIn("git: main → feature", "".join(d["cells"][0]["source"]))

    def test_external_diff_protocol(self):
        env = dict(os.environ, GIT_EXTERNAL_DIFF="%s %s --color=never" % (sys.executable, BIN))
        p = subprocess.run(["git", "diff", "main", "feature"], cwd=self.repo, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("nbdiff --git a/nb.ipynb b/nb.ipynb", p.stdout)
        self.assertIn("+y = 20", p.stdout)
        # renames make git pass 9 arguments
        p = subprocess.run(["git", "diff", "-M", "main", "feature", "--", "old.ipynb", "renamed.ipynb"],
                           cwd=self.repo, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True)
        self.assertEqual(p.returncode, 0, p.stderr)


if __name__ == "__main__":
    unittest.main()
