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

| Path | Responsibility |
|------|----------------|
| `src/App.tsx` | Root: connect to the server, then render the workspace |
| `src/components/workspace.tsx` | Ready state: runtime, header, thread, Mind panel |
| `src/components/app-header.tsx` | Top bar (name, model, Mind, settings) |
| `src/components/mind-panel.tsx` | Autonomy controls + live activity feed |
| `src/components/settings-dialog.tsx` | Edit model / context / output / persona |
| `src/components/connection-splash.tsx` | Loading & "server unreachable" screens |
| `src/hooks/use-backend-config.ts` | Loads server config, tracks connection status |
| `src/hooks/use-autonomy.ts` | Autonomy status + live activity (SSE) |
| `src/lib/backend/` | Server client: `types`, `config`, `stream`, `adapter`, `autonomy` |
| `src/components/assistant-ui/` | assistant-ui chat components (Thread, reasoning, tools, …) |
| `src/components/ui/` | shadcn/ui primitives |
