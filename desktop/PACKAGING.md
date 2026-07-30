# Packaging Kith for other people

The goal that defines this list: **someone installs Kith and it works — no Docker,
no Python, no Ollama, no terminal.** Today the app is a real desktop shell, but it
still requires the developer's Docker stack to be running. Everything below is what
closes that gap.

Ordered by dependency, not by size. Each item says what "done" looks like.

---

## 1. Ship the backend inside the app

Today `main.ts` only *waits* for a server on `127.0.0.1:8611`. It has to *own* one.

- [ ] **Freeze the Python server** — PyInstaller (or Nuitka) → a `kith-server`
      binary. `--onedir`, not `--onefile`: onefile unpacks to a temp directory on
      every launch, which is slow and trips Gatekeeper.
- [ ] **Replace Werkzeug with waitress.** `app.run(threaded=True)` is a
      development server and says so in its own warning. Waitress is pure Python,
      so it freezes cleanly. Verify streaming still works afterwards —
      `scripts/stream-check.py` is the gate, and it must still report incremental
      arrivals.
- [ ] **Spawn and supervise it from Electron main.** `child_process.spawn`, health
      poll (already written — `backend.ts`), restart on unexpected exit, kill in
      `before-quit`. Log its stdout/stderr somewhere retrievable. Doing this also
      removes the container-address rewrite in `infra/renderer.py` — a native server
      reaches the render service on plain loopback, so `_reachable()` becomes a no-op.
- [x] **~~Retire the nginx container.~~** Done — deleted, along with the server
      container and `docker-compose.yml` entirely. The backend serves the UI itself,
      so nginx was publishing the same build at a second port for no benefit. Browser
      access is unchanged, just on `:8611`. Docker now has exactly one job: the sandbox.
- [ ] **Move the database out of the repo.** Currently `./server/data` via a bind
      mount. Packaged, it must be `app.getPath("userData")`, passed to the backend
      as `KITH_DATA_DIR`. Include a migration path for an existing `agent.db`.
- [ ] **Decide who serves the UI.** `server/kith/api/spa.py` currently does, via
      `KITH_UI_DIST`. In a packaged app the renderer lives inside the app bundle,
      so either point that env var at `<Resources>/renderer`, or move to the Node
      static+proxy design (fully specified in the research; ~120 lines). The second
      keeps the shell owning its own renderer, which is the conventional split.
- [ ] Build per-arch (arm64 + x64) or universal2. Note universal2 merges two ASARs
      and requires them to match — rebuild `ui/dist` once, before both.

## 2. Deal with the sandbox

This is the hard one, and it is a product decision before it is an engineering one.

Kith's tools run in a Docker container where he has root. That is what lets him
install packages, run Playwright, and build his own tools. Remove Docker and that
capability has to come from somewhere else.

- [ ] **Choose the model.** Direct execution on the user's machine (what Claude
      Code does) is the only option that keeps his capabilities intact — and it
      means the sandbox stops being the safety mechanism.
- [ ] **Build the permission layer first, if so.** Every tool call already funnels
      through one `run_tool` dispatch — that is the chokepoint. Reads free; writes,
      deletes and anything outward-facing gated. Plus an audit log and a kill
      switch. Do not ship autonomous unsandboxed execution without this.
- [x] **~~Drop Playwright, use Electron's Chromium.~~** Done — `render-service.ts`
      renders in an offscreen `BrowserWindow` and the server prefers it over the
      sandbox (`infra/renderer.py`). Verified against the WAF-protected CMA page:
      identical output to Playwright (2077 chars, same circulars) in the same 3.9s.
      Playwright stays in the sandbox image as the headless fallback, and simply
      isn't shipped in the packaged app.

## 3. Remove the remaining external dependencies

- [ ] **Ollama** — only used for embeddings (`nomic-embed-text`). Either make
      semantic recall optional (it already degrades to keyword search) or move
      embeddings to a cloud API. Do not require a second install.
- [ ] **API key storage.** Currently plaintext in `config.db`. Move to
      `safeStorage` / the macOS Keychain. A shipped app storing someone's
      OpenRouter key in a readable SQLite file is not acceptable.
- [ ] **First-run setup.** A packaged app cannot assume a configured key. Needs an
      onboarding screen: paste key, pick model, done.

## 4. Make it distributable

- [ ] **electron-builder.** Renderer integration is one line:
      `files: [{ from: "../ui/dist", to: "renderer" }]`. Verified that `from`
      resolves outside the app dir, and that it lands before ASAR creation so
      integrity hashing stays valid.
- [ ] **Do not copy the renderer in `afterPack`** — that runs after ASAR creation
      and invalidates the integrity hash.
- [ ] **Code signing + notarization.** Developer ID, hardened runtime, `notarytool`.
      Note: electron-builder 26 no longer falls back to ad-hoc signing — with no
      identity configured it silently ships an **unsigned** app that Gatekeeper
      blocks on another machine.
- [ ] **Hardened runtime vs frozen Python** is a known friction point. Expect to
      need entitlements for the child process.
- [ ] **Auto-update** (`electron-updater`) — otherwise every fix is a manual
      re-download.
- [ ] **Not the Mac App Store.** MAS requires App Sandbox, which is incompatible
      with an agent that runs arbitrary local commands. Direct distribution only.

## 5. Owed regardless of packaging

- [ ] **Content Security Policy.** Currently unset — an Electron security checklist
      item we fail. Must hash the inline theme script in `index.html`, or the
      light-mode flash returns on every launch. Watch `style-src`: the app uses
      inline `style={{…}}` and runtime mood tinting.
- [ ] **Tests.** None exist. Characterisation tests over the repositories, the tick
      loop and tool dispatch.
- [ ] **ruff + oxlint in CI.** A hand-written AST checker found two real bugs during
      the layering refactor (a dropped constant, a dead import causing a cycle) that
      ruff catches for free.
- [ ] **Finish the ORM** — 6 of 14 repositories are still raw SQL, and the
      `db/agent_store.py` façade exists only to be deleted.
- [ ] **Bundle size** — 1.14 MB of JS with no code splitting; Vite warns about it.
- [ ] **Electron 44** lands after 2026-08-25. Only 41–43 are supported now, so this
      is a standing maintenance item, not a one-off.

---

## What already works and needs no further thought

Recorded so nobody re-litigates it:

- Frameless window with the app header as the drag region; window controls have
  reserved space, verified in the running app (`npm run dev` prints the computed
  result).
- Window geometry persists, and refuses to restore onto a display that no longer
  exists.
- No preload, no `contextBridge` — the renderer only needs same-origin `fetch`.
- Permissions locked to `notifications`. This matters: with no handler Electron
  grants everything, and this app does request `media`, `geolocation` and
  `background-sync`.
- Navigation confined to the local origin; external links scheme-checked before
  `shell.openExternal`, because task deliverables carry agent-authored URLs.
- `backgroundThrottling: false` — required, since closing the window hides it and a
  throttled window would starve the SSE feed.
- Single-instance lock; hide-on-close with a quit flag so the app stays quittable.
- **Do not** serve over `app://` — SSE through `protocol.handle` cannot be closed
  (Electron #47097) and `autonomy.ts` calls `source.close()`. **Do not** use
  `file://` — the built `index.html` uses root-absolute asset paths.
