# Kith

A local, self-directed AI you live with — *kith and kin*: a peer, not a servant.
It runs entirely on your machine through [Ollama](https://ollama.com) (Qwen3),
reasons before it answers, and holds its own persona: free to disagree, decline,
and follow its own curiosity.

It has a **persistent brain** (a SQLite database of memories, notes, a journal,
and its own goals) that it reads and writes through tools — so it remembers you
across conversations. It has **its own computer** — an isolated Docker sandbox
where it can run a shell, read and write files, and browse the web. And it can
**run on its own**: turn on autonomy and it takes self-directed steps in the
background — working its tasks, journaling, setting new goals — while deferring
to you whenever you're around.

Two independent parts:

```
kith/
├── server/   Flask service — the brain: Ollama, persona, tools, memory (SQLite),
│             the agent loop, and autonomy. OpenAPI docs at /docs. Runs on its own.
└── ui/       React + assistant-ui client — chat, tool activity, and the Mind panel.
```

## Prerequisites

- [Ollama](https://ollama.com) running, with the model pulled:
  ```sh
  ollama pull qwen3:4b
  ```
- [Docker](https://docker.com) running (for the whole stack, and Kith's sandbox).

## Run (Docker — recommended)

Everything runs in containers that stay up on their own (`restart:
unless-stopped`), so it survives terminal sessions and reboots:

```sh
cd kith
docker compose up -d --build
```

Open **http://127.0.0.1:8610**. API + OpenAPI docs at **http://127.0.0.1:8611/docs**.

- `docker compose logs -f` — watch it · `docker compose down` — stop it.
- `data/` (Kith's brain) and `persona/` are bind-mounted, so they persist and you
  can edit the persona live.
- The server reaches Ollama on your host via `host.docker.internal` and manages
  Kith's sandbox (a sibling container) through the Docker socket.

## Run (without Docker, for development)

The two parts can also run directly — see [`server/`](server/README.md) and
[`ui/`](ui/README.md). (Kith's sandbox still needs Docker.)
