# Kith · server

The **independent** Flask service behind Kith. It owns all the chat logic: it
talks to a local [Ollama](https://ollama.com), holds the persona and
chat parameters, separates the model's reasoning from its answer, and streams
both. The web UI is just one client — the API is documented with **OpenAPI** so
anything can drive it.

## Setup

From the repository root:

```sh
make venv                          # creates server/.venv and installs both requirement files
```

Or by hand:

```sh
cd server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
```

## Run

```sh
.venv/bin/python app.py            # http://127.0.0.1:8611
```

`app.py` serves with waitress by default and with Werkzeug's auto-reloader when `KITH_RELOAD=1`.
See [`docs/startup.md`](docs/startup.md) for which you get and why, and for what starts in the
background at launch.

From the repository root, `./run dev` starts the reloading server and the Vite dev server
together; `make server` runs the backend natively on :8611.

## API

Interactive docs (Swagger UI): **http://127.0.0.1:8611/docs**
OpenAPI spec: **http://127.0.0.1:8611/openapi.json**

A selection — there are around 80 paths in total, and `/docs` is the complete, current list.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | Liveness + whether Ollama is reachable |
| GET | `/api/config` | Effective defaults (model, sizes, persona) |
| PATCH | `/api/config` | Persist config changes to the config database |
| POST | `/api/chat` | Stream an agent turn (`application/x-ndjson`) |
| POST | `/api/chat/<id>/stop` | Ask the turn running in a conversation to stop |
| GET | `/api/activity` | The flight recorder — what each turn cost |
| GET | `/api/activity/status` | Work and plans waiting on you |
| GET | `/api/activity/recent` | The feed's backlog, plus the log position it was taken at |
| GET | `/api/events` | The one server-sent stream: `changed` and `activity`, resumable with `Last-Event-ID` |

`POST /api/chat` body:

```json
{
  "messages": [{ "role": "user", "content": "hello" }],
  "config": { "model": "qwen3:4b", "numCtx": 40960, "numPredict": 8192, "system": "..." }
}
```

`config` is optional — omit it to use the defaults. The response is a stream of
newline-delimited JSON events:

```
{"type":"delta","role":"reasoning","text":"..."}
{"type":"delta","role":"text","text":"..."}
{"type":"stats","stats":{"promptTokens":191,"responseTokens":42,"tokensPerSecond":143.2,"totalSeconds":1.2,"loadSeconds":0.0}}
{"type":"done"}
```

Try it without the UI:

```sh
curl -N -X POST http://127.0.0.1:8611/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"say hi in one word"}]}'
```

## The agent loop

`/api/chat` runs an **agentic loop**: the model is given tools, and when it calls
one the server runs it and feeds the result back, looping until it answers.

There are 63 of them, declared in `kith/tools/`. `registry.names()` is the current list;
what follows is the shape of it, not an inventory to keep in step:

- **Memory** — `remember`, `recall`, `forget`, `journal`, `read_journal`, `set_memory_level`.
  This is how Kith carries anything across conversations.
- **Work** — `add_task`, `update_task`, `view_task`, `create_project`, `add_milestone`,
  `add_deliverable`, `check_item`, and the rest of the board.
- **Computer** — `shell`, `read_file`, `write_file`, `edit_file(s)`, `glob`, `grep`,
  `start_process`, `run_tests`, `commit`. Everything here goes through `infra/permissions.py`.
- **Code** — `repo_map`, `outline`, `definition`, `references`, `find_symbol`,
  `rename_symbol`, `diagnostics`, backed by real language servers.
- **Reaching out** — `web_search`, `fetch_url`, `browse_page`, `read_source`, `ask`,
  `reach_out`, `delegate_subtask`.

Servers configured over MCP contribute more, namespaced `mcp__<label>__<tool>` so a built-in
can never be shadowed.

## The workspace — where Kith works

There is no container. Kith works in a real folder on your machine — `~/Kith` by default,
or wherever `KITH_WORKSPACE` points — with the tools you already have installed. Docker is
not required to run any of this.

What a container boundary used to enforce is enforced by `infra/permissions.py` instead: it
decides what may be read, written and run, and it is the only thing standing between the model
and the disk. `infra/workspace/` is the module that actually touches files; the tool layer
imports it under the name `sandbox`, which is a leftover name for a boundary that is now a
permission check rather than a machine.

**A turn runs on its own thread.** `POST /api/chat` starts it and streams it back, but
the stream is only a reader: closing the tab or switching conversations costs you the
live view, not the work. Stopping is therefore said rather than inferred —
`POST /api/chat/<id>/stop`.

**A scheduler** (`services/scheduler.py`) is the one thing that runs unasked, and it does
one thing: every thirty seconds it asks whether a reminder or a schedule has come due,
and if so continues the conversation that thing was set in.


## Persona

The system prompt is assembled from the fragment files in [`persona/`](persona)
— one instruction per file, merged in filename order, live on each request. See
[`persona/README.md`](persona/README.md) for the rules (ordering, disabling,
comments). Point elsewhere with `KITH_PERSONA_DIR`, or bypass the folder with a
single inline prompt via `KITH_SYSTEM`.

## Configuration

The chat settings (`model`, `num_ctx`, `num_predict`, `think`) live in a SQLite
**config database** at `data/config.db`, seeded on first run. They persist across
restarts and can be changed at runtime with `PATCH /api/config` (no env vars, no
code edits). Resolution order, highest first: **environment variable → config DB
→ built-in default**.

```sh
# change a default and keep it (persists in the DB)
curl -X PATCH http://127.0.0.1:8611/api/config \
  -H 'Content-Type: application/json' -d '{"numCtx": 32768}'
```

Most of what changes Kith's *behaviour* is not here — how long a turn runs, his spending
ceilings, which providers he prefers — because a packaged desktop build has no shell to export a
variable in. Those live in the tunable registry (`kith/domain/tuning.py`) and are edited in
Settings → Advanced.

What is set before launch, and only there (see `.env.example`):

| Variable | What it does |
|----------|--------------|
| `HOST`, `PORT` | Where the server binds. Default `127.0.0.1:8611` |
| `KITH_DATA_DIR` | Where the databases, workspace state and persona copy live |
| `KITH_WORKSPACE` | The folder Kith works in. Default `~/Kith` |
| `KITH_UI_DIST` | A built UI to serve from this process. Unset = the Vite dev server serves it |
| `KITH_PERSONA_DIR` | Persona fragments to use instead of the default folder |
| `KITH_SYSTEM` | One inline prompt, bypassing the persona folder entirely |
| `KITH_SKILLS_DIR` | Where skills are installed |
| `KITH_RELOAD` | `1` to serve with Werkzeug's auto-reloader instead of waitress |
| `KITH_THREADS` | waitress worker threads. Default 16 |
| `KITH_NO_BACKGROUND` | `1` to start without the scheduler. MCP still connects |

The chat settings above (`KITH_MODEL`, `KITH_NUM_CTX`, `KITH_NUM_PREDICT`, `KITH_THINK`) also
work as environment overrides, and win over the config database when set.

`kith/settings.py` is the one file that reads any of these, and the startup log prints every
resolved value — check the log before reading code when something is configured oddly.

## Layout

The package is layered, and imports only ever point downward. `tests/test_the_layers_point_one_way.py`
enforces it.

| Layer | Path | Responsibility |
|-------|------|----------------|
| 0 | `kith/settings.py` | Every launch-time knob: paths, the UI directory, what is served from where |
| 0 | `kith/kernel/` | Runtime primitives — the clock, turn context, the event log, stopping |
| 1 | `kith/domain/` | Vocabulary and rules, no IO: `Config`, `Connection`, the tunable registry |
| 2 | `kith/infra/` | The outside world: SQLite, the workspace, permissions, web search |
| 2 | `kith/llm/` | The two transports (Ollama, OpenAI-compatible), token accounting, caching |
| 3 | `kith/engine/` | Reading and running code — repo map, outline, language servers, processes |
| 4 | `kith/services/` | The turn itself: the agent loop, conversations, history, tasks, MCP |
| 4 | `kith/config.py` | Resolves the effective chat config (env → config DB → default) |
| 4 | `kith/schemas.py` | Request/response shapes, which drive the OpenAPI spec |
| 5 | `kith/api/` | The HTTP endpoints |
| 5 | `kith/tools/` | What the model may call, as schemas and handlers |
| 6 | `kith/app.py` | The composition root: `create_app`, and the work that outlives a request |

Outside the package:

| Path | Responsibility |
|------|----------------|
| `app.py` | Process entry point — picks a WSGI server and binds the port |
| `persona/` | The system prompt, as ordered instruction fragments |
| `skills/` | Skills that ship with Kith, seeded into the data folder on first run |
| `data/` | SQLite databases, workspace state and the persona copy (git-ignored) |
| `docs/` | [startup](docs/startup.md), [typing](docs/typing.md), [nested turns](docs/nested-turns.md) |

The files worth opening first: `app.py` and `kith/app.py` for startup, `kith/services/agent_loop.py`
for the turn, `kith/api/routes/chat.py` for the endpoint that drives it, and
`kith/infra/permissions.py` for what the model is allowed to do.
