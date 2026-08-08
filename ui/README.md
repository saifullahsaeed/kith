# Kith · web

The browser client for Kith — a thin [assistant-ui](https://assistant-ui.com)
front end. It holds no chat logic: it fetches the server's config, streams from
`/api/chat`, and renders. Everything real lives in `../server`.

## Run

The [server](../server) must be running first. Then:

```sh
cd kith/ui
npm install
npm run dev            # http://127.0.0.1:8610
```

Requests to `/api/*` are proxied to the server at `http://127.0.0.1:8611`
(see `vite.config.ts`), so the browser makes same-origin calls — no CORS.

## Layout

One folder per surface. Nothing loose in `components/` — if a new file has no obvious
folder, that is a sign it is two things.

| Path | Responsibility |
|------|----------------|
| `src/App.tsx` | Root: connect to the server, then render the workspace |
| `src/components/shell/` | The frame: `workspace` (the ready state), header, presence, splash, error boundary, window-wide context menu and drop zone |
| `src/components/chat/` | Around the thread: session bar, find-in-chat, history, inbox, the Mind panel (`work-panel`) |
| `src/components/assistant-ui/` | The thread itself — assistant-ui components (reasoning, tool calls, attachments, permission prompt) |
| `src/components/control-panel/` | The full-screen panel: one module per tab, plus `task-detail` and the roadmap graph |
| `src/components/files/` | Showing a file. Import from `@/components/files`; inside it are `viewer`, `media`, `markdown`, `code-block`, `kinds` |
| `src/components/settings/` | Settings, one module per tab |
| `src/components/onboarding/` | First-run setup, one module per step |
| `src/components/ui/` | shadcn/ui primitives |
| `src/hooks/` | `use-backend-config` (server config + connection status), `use-activity` (the live feed over SSE), and the rest |
| `src/lib/backend/` | Server client: `types`, `config`, `stream`, `adapter`, `activity`, … |
| `src/lib/` | Everything else shared: files and the clipboard, routing, theme, token accounting, tool vocabulary |
