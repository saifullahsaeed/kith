# Kith

A local AI you work with — *kith and kin*: a peer, not a servant. It runs as a
desktop app on your machine, reasons before it answers, and holds its own
persona: free to disagree, decline, and tell you when it thinks you are wrong.

It has a **persistent brain** — a SQLite database of memories, notes, a journal,
people, projects and tasks that it reads and writes through tools, so it
remembers you across conversations. It works on **your computer**, in one folder
you choose (`~/Kith` by default), with your shell, your PATH and your installed
programs. And a turn is **its own thread**: it keeps going whether or not you are
watching, so closing the window costs you the live view rather than the work.

## Three parts

```
kith/
├── server/    Flask service — the agent loop, persona, tools, memory (SQLite),
│              and the model transport. OpenAPI docs at /docs.
├── ui/        React + assistant-ui — chat, tool activity, the Work panel and the
│              control panel.
└── desktop/   Electron shell — the window, the native bits (notifications,
│              Finder, screen capture), and the process that starts the server.
```

## Running it

```sh
./run          # build the interface if it changed, start the server, open the window
./run dev      # the same window, with both halves reloading on save
./run server   # backend only — a browser at 127.0.0.1:8611, or a second window
./run stop     # stop everything
```

`./run` exists rather than a line in this file because the working command was
six lines long: the server only serves the interface when `KITH_UI_DIST` points
at a built `ui/dist`, and the window attaches to a server that is already
answering. Get either wrong and the app opens on "Not Found" with nothing to say
why. Read the top of [`run`](run) — it documents what reloads and what does not.

Before shipping anything:

```sh
./check        # ruff, ruff format, pyright, pytest, tsc, vite build
```

## The model

Either a cloud endpoint or a local one, chosen in Settings.

- **Cloud** — any OpenAI-compatible endpoint. OpenRouter is what it is tuned
  for: provider pinning and a per-conversation session id keep consecutive
  rounds on one warm prompt cache, which is most of what a long turn costs.
- **Local** — [Ollama](https://ollama.com), no key and nothing leaves the
  machine:
  ```sh
  ollama pull qwen3:4b
  ollama pull nomic-embed-text   # semantic memory
  ```

## How it works on your machine

There is no container. Kith works in a real folder with your real tools, which
is the whole point — it can open the file you pointed at and use the programs you
already have. What a container boundary used to enforce,
[`services/permissions`](server/kith/services/permissions.py) enforces instead:
inside the workspace it is unrestricted; outside it, or anything destructive
anywhere, needs your yes. Asking **holds the turn** until you answer.

It can also ask you a question outright — options you pick from, several at once,
or your own words — and wait. Both a question and a permission raise a
notification and land you in the conversation that is waiting.

## Where things live

- `~/Kith` — his folder: the work itself, plus `.kith/` for transcripts.
- `server/data/` — `agent.db` (his memory) and `config.db` (settings, including
  the cloud key in plaintext). Never committed; see [`.gitignore`](.gitignore),
  which was written before `git init` for that reason.
- `server/persona/` — the persona, as markdown fragments merged in filename
  order. Edit them; they are read per request, so no restart.

## Reading the code

The comments are the documentation. They explain *why* a thing is the shape it
is, usually by naming the failure that produced it — a fold that ran on the
request path and made him look slow, a `.text` call that downloaded a whole reply
before showing its first word, a permission prompt that granted access for next
time instead of the call you were looking at. If you are about to change
something and the comment above it sounds overwrought, read it anyway; it is
probably describing the bug you are about to reintroduce.
