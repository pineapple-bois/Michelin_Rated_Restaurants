# Parameterized legacy notebooks

These notebooks preserve the latest (2026) notebook-led implementations that
preceded the reusable Python pipeline:

- `Data-Preparation.ipynb`
- `France_Processing.ipynb`
- `Monaco_Processing.ipynb`
- `France_Arrondissements.ipynb`
- `France_Changes.ipynb`

Each notebook starts with one configuration cell:

```python
YEAR = 2026
SAVE = False
```

Change `YEAR` to inspect another available annual input. Keep `SAVE = False`
for exploration. If `SAVE = True` is set deliberately, exports are isolated
under `notebooks/outputs/<YEAR>/`; they never replace canonical files under
`data/`.

The copies use repository-root canonical input paths instead of their original
year-relative `../../` paths. `France_Changes.ipynb` compares `YEAR - 1` with
`YEAR`. Cell outputs and execution counts were cleared so displayed results
cannot be mistaken for results from the selected year.

These files are historical references, not the supported production workflow.
Use the commands documented in the root `README.md` for validated builds and
reports.
