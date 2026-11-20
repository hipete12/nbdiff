"""Shared helpers for building notebooks in tests."""

import contextlib
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nbdiff.cli import main  # noqa: E402


def md(src, cid=None, **meta):
    c = {"cell_type": "markdown", "metadata": meta, "source": src}
    if cid:
        c["id"] = cid
    return c


def code(src, outputs=(), count=None, cid=None, **meta):
    c = {"cell_type": "code", "metadata": meta, "source": src, "execution_count": count, "outputs": list(outputs)}
    if cid:
        c["id"] = cid
    return c


def stream(text, name="stdout"):
    return {"output_type": "stream", "name": name, "text": text}


def result(plain, count=1, **more):
    data = {"text/plain": plain}
    data.update(more)
    return {"output_type": "execute_result", "execution_count": count, "data": data, "metadata": {}}


def nb(*cells, minor=5, **meta):
    return {"cells": list(cells), "metadata": meta, "nbformat": 4, "nbformat_minor": minor}


def write(path, notebook):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(notebook, f, indent=1)
    return path


def run(*argv):
    """Run the CLI in-process; returns (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code_ = main(list(argv))
        except SystemExit as e:  # argparse errors
            code_ = e.code
    return code_, out.getvalue(), err.getvalue()
