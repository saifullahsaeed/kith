# Type checking

    cd server && .venv/bin/pyright

This is the `types` step in `./check`, and it is a gate: the count is zero and stays zero.

## Why a type checker, next to 2,900 tests

Because the two find different things, and the gap between them is not academic. The case that
put this here: `engine/run/processes.py` called `scheduler._continue()` with the old two-argument
signature after it grew a third — a `TypeError` waiting for the next background task to finish.
The test covering that path patches `_continue` with a two-argument lambda, so it stubs out the
exact thing that changed and cannot fail when it changes.

That is not a flaw in the test; it is what stubbing means. Run something that reads signatures
before believing a green suite.

## What is checked

Everything `pyrightconfig.json` includes: `kith/`, `tests/`, `scripts/`, `app.py`.

`typeCheckingMode` is `standard`. `venvPath`/`venv` point at `server/.venv` and are load-bearing
— without them pyright resolves against the system interpreter and reports about 130 missing
imports for pytest, flask and sqlalchemy, all of which are installed.

## The one relaxation

`tests/` runs with four rules off, declared in `executionEnvironments`:

    reportOptionalSubscript   reportOptionalMemberAccess
    reportOptionalOperand     reportOptionalCall

In a test, `repo.projects.get_project(db, id)["status"] == "done"` subscripts an Optional
deliberately — a `None` there fails the test, which is the assertion. Guarding it would make the
test say less. Nothing else is relaxed, and nothing is relaxed outside `tests/`.

If you edit that block, keep `"extraPaths": ["."]` in it. `executionEnvironments` resets import
resolution, and without it every `kith` import inside `tests/` fails to resolve.

## Patterns that keep it at zero

Three helpers exist because the alternative was scattering `cast()` through correct code:

- **`infra/db/engine.changed(result)`** — how many rows a DML statement touched. `Session.execute`
  is typed `Result`, which has no `rowcount`; that lives on `CursorResult`. Use this rather than
  reading `.rowcount` off the result.
- **`tools/registry.require(name)`** — the tool, or a `KeyError` naming what is registered. Use it
  whenever you mean to *run* a tool. `registry.get` returns `Tool | None` and is for asking
  whether one exists.
- **`_rank()`** in `infra/db/repositories/sources.py` is generic in the row type, so callers get back what
  they passed in. Ranking helpers should be, rather than returning `object`.

For a genuinely untypeable third-party surface, a narrow `# type: ignore[rule]` with a reason
beside it is fine. A blanket ignore is not.
