# Pyright, and why it is not in `./check`

Run it by hand:

    cd server && .venv/bin/pyright

`pyrightconfig.json` exists for one reason: without it the tool resolved against the system
interpreter and reported **130 "missing import" errors for pytest, flask and sqlalchemy**, all
of which are installed. Three-quarters of its output was noise about the venv, which is how a
tool stops being read.

That mattered. On 14 Aug 2026 two real bugs shipped in a commit that 2,250 passing tests signed
off on, and pyright had flagged both:

* `services/code/processes.py` called `scheduler._continue()` with the old two-argument
  signature after it grew a third. It would have raised `TypeError` the moment a background
  task finished. The test covering that path patches `_continue` with a two-argument lambda —
  it stubs out the exact thing that changed, so the suite could not see it.
* `infra/websearch.py` called `sandbox.docker_available()`, removed in `fb55331` ("He works on
  your computer now, not in a container") along with the container it asked about. The call
  site was missed, so that branch had been raising `AttributeError` instead of its intended
  message, and the model was being told
  `searx: AttributeError: module ... has no attribute 'docker_available'`.

Neither is the kind of thing a test suite is good at. Both are the kind of thing a type checker
is good at.

## Why it is still not a gate

With the venv resolved it reports **172 errors, and none of them are bugs**. The shape:

| Count | Rule | What it actually is |
|------:|------|---------------------|
| ~98 | `reportOptionalMemberAccess`, `reportOptionalSubscript` | mostly **tests** doing `repo.tasks.get(...)["id"]` — a lookup that returns `X \| None`, subscripted directly because the test just created the row |
| ~37 | `reportAttributeAccessIssue` | SQLAlchemy rows typed as `object`; the ORM's typing does not reach through `scalars()` |
| ~20 | `reportArgumentType` | `int(payload.get("x"))` where the `TypeError` is deliberately caught one line below |

Making that green means sprinkling `cast()` and `assert x is not None` through code that is
already correct, in a codebase that has never run a type checker. That is churn bought with
review attention, and it would bury the two-per-year findings above under 172 suppressions.

**So: not a gate, but no longer noise.** The bar for adding it to `./check` is the count
reaching zero on `kith/` alone, which is a smaller job than it looks — production is about 40
of the 172 — and worth doing the next time someone is in the repositories anyway.

## The one thing worth keeping in mind

A test that patches the function whose signature you changed cannot fail when you change it.
That is not a flaw in the test; it is what stubbing means. It is the reason to run something
that reads signatures rather than behaviour before believing a green suite.
