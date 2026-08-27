# Startup: why the entry point looks the way it does

Two files own startup, and they do different jobs.

| File | Job |
| --- | --- |
| `app.py` | The process entry point. Picks a WSGI server and binds a port. This is what `run` launches and what PyInstaller freezes. |
| `kith/app.py` | The composition root. `create_app()` — blueprints, CORS, auth, SPA/CSP, databases, background work. Never binds a port. |

`kith/__init__.py` deliberately does *not* build the app. It forwards `create_app` through a
PEP 562 `__getattr__`, so importing anything from `kith` — a constant, a type — does not
construct the whole application as a side effect.

## Why waitress, and why Werkzeug only for reloading

Werkzeug's own documentation says not to use its development server to serve an application,
and this is not a script someone runs in a terminal — it is the backend of a desktop app that
people leave open all day. So waitress is the default, and Werkzeug is what `KITH_RELOAD=1`
gets you, because the auto-reloader is the one thing it has that waitress does not.

waitress rather than gunicorn or uvicorn: it is pure Python with no C extensions, which is what
lets PyInstaller freeze it into the desktop bundle without a compiler on the build machine. It
is also the one that works unchanged on Windows.

## Why sixteen threads

Every long-running response holds its worker for as long as it lasts, and this app has two
kinds:

- the event stream, open for the life of a window;
- a turn's `POST /api/chat`, open for the life of a turn — and a turn parked on an `ask` or a
  permission prompt can sit there for its full fifteen-minute deadline.

Sixteen is generous for a single-user machine and cheap. The failure it prevents is the whole
app appearing to hang because every thread is holding a stream. It is a floor on concurrency,
not a performance dial.

`channel_timeout=300` has to outlast a stream that is deliberately idle. The event stream sends
a keep-alive comment every fifteen seconds, so anything above that is safe; 300 is well above
it, because the cost of being wrong is a window that silently stops hearing.

## Why never `debug=True`

`use_reloader=True` alone, never `debug=True`. Debug mode also mounts the Werkzeug debugger,
which is an interactive Python console on any traceback — a remote shell for anything that can
reach the port, and every process running as you can reach loopback. Reloading was the part
that was wanted; the console was not.

`reloader_type="stat"` rather than the default `"auto"`. Auto uses watchdog when it is
installed, and watchdog watches whole *directories* — including `server/`, which holds `data/`
with the config and agent databases in it. Those are written to constantly, so every tool call
would restart the server underneath itself. The stat reloader polls only the `.py` files
actually imported, which is exactly the set worth watching.

## Why background work is guarded

`flask run --reload` re-executes the module, so `app = create_app()` happens in **two**
processes: the watcher that never serves a request, and the child that does. Nothing started in
`_start_background` is idempotent across processes:

- `runner.ensure_loop` guards on `self._thread`, which is per-process, so two processes means
  two schedulers firing the same reminders and taking the same steps twice.
- `connect_async` spawns MCP servers under `npx`, so it means two sets of those — and there is
  no `atexit` anywhere to reap the ones the previous child left behind.

So `_owns_background()` decides it rather than leaving it to luck. With the reloader on, only
the child Werkzeug marks as the serving process (`WERKZEUG_RUN_MAIN=true`) owns any of it.
Without the reloader — packaged, Docker, a plain `python app.py`, the test suite — there is one
process, and it does.

### What `KITH_NO_BACKGROUND=1` skips

The scheduler only, since that is the one thing a reloader restart can do real damage with: it
restarts mid-step and can re-fire a reminder that already fired.

It deliberately does **not** skip MCP any more. It did at first, on the grounds that a reload
drops MCP child processes without reaping them, so each save leaks an `npx`. True, but the
wrong trade by a wide margin: `agent_loop` reads `mcp_manager.snapshot()`, which is empty until
something has connected, so skipping this silently handed the model *no MCP tools at all* for a
whole dev session. A leaked process is untidy; a model quietly missing half its tools is a
different program.

## Why CORS is not `*`

It was `*`, once. A wildcard there means every website you visit while Kith is running can read
his files, his config and his memory, and can POST as you — a page at `evil.example` was
answered with `Access-Control-Allow-Origin: https://evil.example` and got the lot. Being bound
to loopback is no protection: the browser is on loopback too.

Nothing legitimate needed the wildcard. The desktop app serves the interface from this same
process, so it is same-origin and sends no CORS preflight at all; the only real cross-origin
caller is the Vite dev server, which is why `_ALLOWED_ORIGINS` lists both spellings of loopback
on the two Vite ports. curl and other tools send no `Origin` header and are unaffected by any
of this.

CORS is not the access control, though — `auth.register` is. CORS stops a page *reading* the
reply, not *sending* the request, and every process running as you is on loopback too. Every
API call needs the shared secret.

## Why the ordering in `create_app` matters

1. **Databases and settings before anything can serve a request.** `tuning._migrate_from_database()`
   carries an existing installation across on the first start after upgrading and does nothing
   on every start after that. Doing it here means no reader ever sees the half-moved state.
2. **Interrupted questions before serving.** A parked turn is a daemon thread waiting on an
   in-memory event, so a restart took both and wrote nothing down. Recovering here means the
   card is already back by the time the window reconnects.
3. **Background work last, and off the request path.** Each item is either a network call or a
   subprocess that downloads on first run, and none of them may stand between launching and
   answering.

## Why the CSP hashes inline scripts

`csp.register` hashes the bundle's inline scripts so they still run under the policy. Two
concrete failures if it does not: the pre-paint theme script gets blocked, so every launch is a
white flash before the dark class lands; and the token script — injected into the page on the
way out — gets blocked, so the page loads with no token and 401s every call.

## Why the startup log prints everything

Nearly every confusing failure in this project has been a configuration one: a host that does
not resolve, a path that is not set, a search provider forced to something unexpected. Printing
the resolved values means the log answers that before anyone starts reading code.

## Why some imports are inside functions

Two of them are load-bearing, not laziness:

- `from kith.api.routes.chat import continue_conversation` inside `_start_background`. The
  scheduler is *told* how to wake a conversation rather than importing the way. The composition
  root is allowed to know about both halves; the service is not.
- `from kith.services import questions` inside `_recover_interrupted_questions`, for the same
  layering reason.

## Why `kith/__init__.py` imports nothing

**Do not add an import to it.** Python runs a package's `__init__` before any module inside it,
so anything imported there is paid for by every import of anything under `kith.` — including a
module that needs none of it, and including every test in the suite.

The cost, measured with the venv interpreter rather than read off the import graph, from when
that file still built the application:

```
import kith.domain.stall     ->  153 kith modules, 905 modules total, 0.68s
                                 apiflask, flask, requests, sqlite3, yaml
```

`domain/stall.py` is regex and set arithmetic whose own docstring says it needs no database, no
clock and no IO. Importing it started a web framework.

A `kernel/` package that imports nothing does not solve this on its own: reaching it still means
running `kith/__init__.py` first, so the cost is decided here regardless of how the tree below is
arranged.

`create_app` lives in `kith.app`, and the PEP 562 `__getattr__` in `__init__.py` resolves it on
attribute access so `from kith import create_app` keeps working — the cost lands on a caller that
actually wants an application. Extend that accessor only for something with the same property.
