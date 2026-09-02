<h1 align="center">Kith</h1>

<p align="center">
  <em>An AI agent that runs on your machine, remembers across conversations,<br>
  and works in your real files.</em>
</p>

<p align="center">
  <a href="https://github.com/saifullahsaeed/kith/actions/workflows/ci.yml"><img alt="check" src="https://github.com/saifullahsaeed/kith/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/saifullahsaeed/kith/releases/latest"><img alt="latest release" src="https://img.shields.io/github/v/release/saifullahsaeed/kith"></a>
  <img alt="status: beta" src="https://img.shields.io/badge/status-beta-orange">
  <img alt="platform: macOS" src="https://img.shields.io/badge/platform-macOS%20(Apple%20Silicon)-lightgrey">
</p>

> [!IMPORTANT]
> **Kith is beta software, and macOS on Apple Silicon is the only supported
> platform today.** Windows support is planned. Expect rough edges, and expect
> some releases to change behaviour you were relying on. Because Kith operates on
> real files with a real shell, please point it at a workspace whose contents you
> have backed up.

## What it is

Kith is a desktop application running a local agent loop against a model you
choose — a hosted endpoint, or Ollama with nothing leaving the machine. It
reasons before it answers, and its persona is a set of markdown fragments you can
edit in Settings: the shipped one is tuned to verify before it claims, and to
hold its answer when you push back rather than agree to be agreeable.

Three things distinguish it from a chat window:

- **A persistent store.** A SQLite database of memories, notes, a journal,
  people, projects and tasks, which the model reads and writes through tools. It
  carries between conversations.
- **Your actual computer.** One folder you choose (`~/Kith` by default), your
  shell, your `PATH`, and the programs you already have installed. No container.
- **Turns that outlive the window.** A turn runs on its own thread, so closing
  the window costs you the live view rather than the work in progress.

## Install

**[Download the latest release →](https://github.com/saifullahsaeed/kith/releases/latest)**
(`Kith-<version>-arm64.dmg`.) The bundle carries its own Python, server and
interface; there is nothing else to install.

### First launch

Kith is signed but not notarized — notarization requires a paid Apple Developer
account. macOS will therefore refuse the first launch. This happens once.

**Right-click → Open no longer works;** that bypass was removed in macOS 15. Use
this instead:

1. Double-click Kith and let it be blocked. Click **Done**.
2. Open **System Settings → Privacy & Security → Security**. A line now reads
   *"Kith" was blocked to protect your Mac* → click **Open Anyway**.
3. Authenticate, then click **Open Anyway** once more.

Step 1 is required rather than a formality: the button does not appear until
something has been blocked, and it expires roughly an hour later.

### Connect a model

A fresh install has nothing to think with — it defaults to `qwen3:4b` on a local
Ollama that may not be present. Open **Settings** and choose one:

- **Hosted** — any OpenAI-compatible endpoint; nothing to install. OpenRouter is
  the tuned path: provider pinning and a per-conversation session id keep
  consecutive rounds on a single warm prompt cache, which accounts for most of
  what a long turn costs.
- **Local** — [Ollama](https://ollama.com). No API key, and nothing leaves the
  machine:
  ```sh
  ollama pull qwen3:4b
  ollama pull nomic-embed-text   # required for semantic recall
  ```

## Capabilities

Roughly sixty tools, all of them acting on the real machine rather than a
sandbox.

| Area | What it covers |
| --- | --- |
| **Memory** | Remember, recall, forget. A level sets how present a fact is: `core` rides in every prompt, `recall` returns when reached for. Recall is semantic across everything stored. |
| **Work tracking** | Projects, milestones, tasks, checklists and deliverables — a board the agent maintains itself rather than one you keep for it. |
| **Filesystem and shell** | Read, write and edit files; glob; grep; a real shell; long-running processes it can start, inspect and stop. |
| **Code intelligence** | Repository map, symbol search, references, rename, diagnostics and test runs — via a language server where one is installed, and explicit about the gap where one is not. |
| **Git** | Working-tree changes, history, commit, push and fetch, so work in a shared repository does not silently stay local. |
| **Web** | Search, fetch a URL, browse a page. |
| **Documents** | Sources you provide, indexed and searchable. |
| **Scheduling** | Reminders and recurring schedules. |
| **Delegation** | Sub-agents, so an expensive search consumes their context rather than yours. |
| **Skills** | Folders of instructions loaded on demand instead of carried in every prompt. |
| **MCP** | Any Model Context Protocol server, configured in Settings. |
| **Notifications** | The agent can open the conversation itself — a finding, a question, a blocker — and it reaches you as a system notification. |

## Building from source

macOS is what Kith is built and tested on; parts of the server and all of the
desktop shell contain macOS-specific code. You will need **Python 3.13**,
**Node 22** and **npm**, matching the versions CI uses.

```sh
git clone https://github.com/saifullahsaeed/kith.git
cd kith

make venv                 # creates server/.venv and installs both requirements files
( cd ui      && npm install )
( cd desktop && npm install )
```

Then:

```sh
./run          # build the interface if it changed, start the server, open the window
./run dev      # the same window, with both halves reloading on save
./run server   # backend only — a browser at 127.0.0.1:8611, or a second window
./run stop     # stop everything
```

`./run` exists rather than a line in this file because the working command was
six lines long: the server only serves the interface when `KITH_UI_DIST` points
at a built `ui/dist`, and the window attaches to a server that is already
answering. Get either wrong and the app opens on "Not Found" with nothing to
explain why. The header of [`run`](run) documents what reloads and what does not.

Before opening a pull request:

```sh
./check         # ruff, ruff format, pyright, pytest, tsc, vite build
./check server  # Python only — the fast loop
./check ui      # TypeScript only
```

Run the script rather than the individual commands. `pytest | tail -1 && ruff`
reports the exit status of `tail`, so a failing suite can scroll past as a single
line of green — which happened twice in one night, and a commit went out on top
of it.

## Project layout

```
kith/
├── server/    Flask service — the agent loop, persona, tools, memory (SQLite),
│              and the model transport. OpenAPI docs at /docs.
├── ui/        React + assistant-ui — chat, tool activity, the Work panel and the
│              control panel.
└── desktop/   Electron shell — the window, the native integrations (notifications,
               Finder, screen capture), and the process that starts the server.
```

[`server/README.md`](server/README.md) covers the API and the startup path, and
[`server/docs/`](server/docs) goes into typing, nested turns and what starts in
the background at launch. [`ui/README.md`](ui/README.md) covers the interface;
[`desktop/PACKAGING.md`](desktop/PACKAGING.md) covers the app bundle.

## Permissions

There is no container. Kith works in a real folder with real tools, which is the
point — it can open the file you pointed at and use the programs you already
have. What a container boundary would otherwise enforce,
[`infra/permissions.py`](server/kith/infra/permissions.py) enforces instead:
inside the workspace it is unrestricted; outside it, or anything destructive
anywhere, requires your approval. A prompt **holds the turn** until you answer.

That makes the folder you choose the boundary itself, which is why several
locations are refused outright rather than warned about — `/`, your home folder,
Desktop, Documents, Downloads and Library. Selecting your home folder would not
grant a large workspace; it would silently disable the permission system for
every file you own, and a warning you can click past is not a boundary.

The agent can also ask a direct question — options to choose from, several at
once, or free text — and wait. Both a question and a permission request raise a
notification and take you to the conversation that is waiting.

## Where data lives

- `~/Kith` — the workspace: the work itself, plus `.kith/` for transcripts.
  Chosen in the app. The default is short and space-free because the agent writes
  shell commands against it constantly. `KITH_WORKSPACE` takes precedence when
  set.
- `~/.kith` — the databases in a packaged install: `agent.db` (memory, tasks,
  journal) and `config.db` (settings, **including the hosted API key in
  plaintext**), alongside the editable persona fragments. From a source checkout
  these live in `server/data/` instead, which is never committed — see
  [`.gitignore`](.gitignore), written before `git init` for that reason.
  `KITH_DATA_DIR` overrides both.
- `server/persona/` — the persona fragments that ship with the build, merged in
  filename order. A packaged install copies them to `~/.kith/persona` the first
  time it needs them and reads from there afterwards, so an update cannot
  overwrite a persona you have edited. A source checkout reads this folder
  directly. Either way they are re-read per request, so edits need no restart.

## Reading the code

The comments are the documentation. They explain *why* a thing has the shape it
does, usually by naming the failure that produced it — a fold that ran on the
request path and made the app look slow, a `.text` call that downloaded an entire
reply before showing its first word, a permission prompt that granted access for
next time instead of for the call you were looking at. If you are about to change
something and the comment above it reads as overwrought, read it anyway; it is
probably describing the bug you are about to reintroduce.

## Contributing

[`CONTRIBUTING.md`](CONTRIBUTING.md) covers the house style and the conventions
that exist because their absence cost something: comments explain why, thresholds
are measured rather than estimated, tests are named after the behaviour they
protect, and the permission surface never widens quietly.

One point bears repeating here. A bug in Kith does not return a wrong answer; it
deletes something. Transcripts under `server/data/conversations/` are plain JSONL
so that a bug report can carry the turn that produced it — please scrub them
before posting, as they contain your notes, your contacts and your work.
