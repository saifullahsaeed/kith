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
        target: "http://127.0.0.1:8611",
        changeOrigin: true,
      },
    },
  },
});
