# Plotly example

An executed plotly analysis notebook (synthetic bike-share data) in two versions:

| file | |
|---|---|
| `before.ipynb` | first version |
| `after.ipynb` | revised version |
| `visualized-diff.ipynb` | `nbdiff before.ipynb after.ipynb --visualize visualized-diff.ipynb` |
| `make_plotly_examples.py` | builds and executes both notebooks (needs plotly, pandas, numpy, nbformat, nbclient, ipykernel) |

What changed between the versions:

* the daily-rides line chart switches to a 7-day rolling mean (new data, title, hover mode);
* the scatter plot gets another colour scale plus marginal histogram and box plots;
* the heatmap cell moves up unchanged. Its figure output differs only in plotly's random
  `<div id>`, which nbdiff ignores, but plotly's loader script moved into it;
* a `KeyError` is fixed, `df.describe()` is deleted, a stacked-bar + pie subplot is added;
* markdown edits. Every cell was re-executed, so execution timestamps differ everywhere.

Try:

```bash
nbdiff before.ipynb after.ipynb                 # sources only
nbdiff -o before.ipynb after.ipynb              # + outputs, figures diffed setting by setting
nbdiff -o --stat before.ipynb after.ipynb       # one line per changed cell
nbdiff -o -y -W 160 before.ipynb after.ipynb    # side by side
nbdiff -o before.ipynb after.ipynb | less -R    # page through the coloured output
```

Open `visualized-diff.ipynb` in JupyterLab or VS Code to see the old and new figures
(live, interactive) under each change.
