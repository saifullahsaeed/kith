# Kith · server

The **independent** Flask service behind Kith. It owns all the chat logic: it
talks to a local [Ollama](https://ollama.com), holds the persona and
chat parameters, separates the model's reasoning from its answer, and streams
both. The web UI is just one client — the API is documented with **OpenAPI** so
anything can drive it.

## Setup

```sh
cd kith/server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Run

```sh
.venv/bin/python app.py            # http://127.0.0.1:8611
```

Run it in the background (independent of the web app):

```sh
nohup .venv/bin/python app.py > server.log 2>&1 &
```

## API

Interactive docs (Swagger UI): **http://127.0.0.1:8611/docs**
OpenAPI spec: **http://127.0.0.1:8611/openapi.json**

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

Its tools fall in two groups:

- **Brain** (backed by `data/agent.db`): `remember`, `recall`, `take_note`,
  `read_notes`, `update_note`, `journal`, `read_journal`, `add_task`,
  `list_tasks`, `update_task`. This is how Kith remembers across conversations.
- **Computer** (backed by the sandbox, below): `shell`, `read_file`,
  `write_file`, `list_files`, `fetch_url`, `web_search`.

## Sandbox — Kith's computer

Kith has its own machine: a Docker container (`kith-sandbox`, Debian) that its
`shell`/file/internet tools run inside. It is **isolated from the host** — it
runs unprivileged as user `kith`, with memory/CPU/pid limits, all Linux
capabilities dropped, no host filesystem mounts, and no Docker socket. Its home
(`/home/kith`) persists in a named volume (`kith-home`). It has internet access.

The image is built on first use; requires **Docker running**. See
`kith/sandbox.py` and `sandbox/Dockerfile`. Wipe it for a fresh machine with
`sandbox.reset()` (removes the container and its volume).

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

Env overrides (see `.env.example`): `PORT`, `OLLAMA_HOST`, `KITH_DATA_DIR`,
`KITH_MODEL`, `KITH_NUM_CTX`, `KITH_NUM_PREDICT`, `KITH_THINK`, `KITH_SYSTEM`,
`KITH_PERSONA_DIR`.

## Layout

| Path | Responsibility |
|------|----------------|
| `persona/` | The system prompt, as ordered instruction fragments |
| `sandbox/Dockerfile` | Kith's computer — an isolated Debian image |
| `data/` | SQLite databases (git-ignored) |
| `kith/db/connection.py` | SQLite connection + migration helpers |
| `kith/db/config_store.py` | The config database (settings key/value store) |
| `kith/db/agent_store.py` | The agent's database: memories, notes, journal, tasks |
| `kith/persona.py` | Loads and merges the persona fragments |
| `kith/config.py` | Resolves effective config (env → DB → default) |
| `kith/ollama_client.py` | Single-turn streaming client for Ollama |
| `kith/tools.py` | The agent's tools (schemas + handlers over the agent DB) |
| `kith/agent.py` | The agentic loop (call model → run tools → repeat) |
| `kith/services/activity.py` | The live feed, and what a session has spent |
| `kith/services/scheduler.py` | Due reminders and schedules, waking their own chat |
| `kith/sandbox.py` | Manages Kith's Docker computer (shell, files, internet) |
| `kith/think_splitter.py` | Splits inline `<think>` reasoning from the answer |
| `kith/schemas.py` | Request/response schemas (drive the OpenAPI spec) |
| `kith/routes.py` | The HTTP endpoints |
| `kith/__init__.py` | App factory (OpenAPI + CORS + DB init) |
| `app.py` | Entry point |
