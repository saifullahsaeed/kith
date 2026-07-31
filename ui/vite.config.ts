import fs from "node:fs";
import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The web app talks to the Kith server through this dev-server proxy, so the
// browser makes same-origin requests (no CORS, no localhost/IPv6 surprises).
// The server, in turn, talks to Ollama — the web app never touches Ollama.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    host: "127.0.0.1",
    port: 8610,
    proxy: {
      "/api": {
        // Overridable so the app can be pointed at a server on another port — a second
        // instance for testing, or one started with PORT= set.
        target: process.env.KITH_API_TARGET ?? "http://127.0.0.1:8611",
        changeOrigin: true,
        // The API needs a shared secret on every call now. When the Kith server serves this
        // app itself it injects the token into the document, but here Vite serves the page
        // and cannot — so the proxy reads the same file the server wrote and adds the header
        // on the way through. Looked up per request until found, because `npm run dev` often
        // beats the server to it and a value captured at startup would stay empty all session.
        configure: (proxy) => {
          proxy.on("proxyReq", (proxyReq) => {
            const token = readToken();
            if (token) proxyReq.setHeader("X-Kith-Token", token);
          });
        },
      },
    },
  },
});

/** The server's API token, or "" until it has written one. Cached once found: it persists
 *  across restarts by design, so re-reading it forever would be pointless work. */
let cachedToken = "";
function readToken(): string {
  if (cachedToken) return cachedToken;
  const candidates = [
    process.env.KITH_DATA_DIR ? path.join(process.env.KITH_DATA_DIR, "api.token") : "",
    path.resolve(__dirname, "../server/data/api.token"),
  ].filter(Boolean);
  for (const candidate of candidates) {
    try {
      const value = fs.readFileSync(candidate, "utf8").trim();
      if (value) {
        cachedToken = value;
        return value;
      }
    } catch {
      // Not there yet. The next request will look again.
    }
  }
  return "";
}
