# Contributing

Thanks for helping make personal match analysis clearer and more reliable.

## Set up

Use Python 3.11–3.13. Clone your fork, create `.venv`, and install `requirements-dev.txt`. Follow the [README demo instructions](README.md#try-it-without-a-riot-key): no API key or personal database is needed to develop most of the app.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m scripts.start_demo
```

On macOS/Linux, substitute `.venv/bin/python`.

CI runs the complete suite in three isolated worker processes with `python -m pytest -q -n 3 --dist worksteal --durations=10`. Workers can rebalance queued tests when some test files take longer than others. Use the same command locally, or omit the parallel options for a sequential run. Databases and mocked services remain isolated by test; no personal data or Riot access is required.

## Before opening a pull request

```powershell
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m compileall -q app.py analytics config core pages ui scripts tests
.venv\Scripts\python -m pip check
git diff --check
```

- Keep changes focused. Describe the problem, your solution and how you tested it.
- For a bug, add a synthetic regression test that reproduces the failure.
- Use temporary databases and mocked HTTP responses. Tests must not require `.env`, a personal database or Riot network access.
- For UI changes, attach screenshots from the synthetic demo only. The interface is French-first; keep labels consistent with the surrounding page.
- Keep calculations in the analytics layer. Show sample sizes, preserve missing values and avoid claims of causality or prediction.
- Preserve profile isolation and export contracts. Explain any schema change explicitly.

## Keep private data out

Never commit API keys, `.env`, SQLite files, personal exports, real player identifiers or personal screenshots. Check `git status` and your staged diff before committing; `.gitignore` does not untrack a file already committed.

For ordinary bugs and ideas, [open an issue](https://github.com/urattems/lol-analytics/issues). For a suspected vulnerability or leaked secret, follow [SECURITY](SECURITY.md) instead of posting sensitive details publicly.
