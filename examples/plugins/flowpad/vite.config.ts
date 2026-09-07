import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/**
 * One file in, two files out, and nothing left to fetch.
 *
 * A plugin surface runs inside Kith's seal, which has **no network at all** —
 * `default-src 'none'`, verified against a real browser. So a CDN is not an option and neither
 * is code-splitting: the host inlines every relative `<script src>` and stylesheet into the
 * document before serving it, and anything it cannot inline is refused at install with the URL
 * quoted rather than silently never arriving.
 *
 * Hence `inlineDynamicImports` and a fixed pair of output names. React, React DOM and React Flow
 * all end up in `board.js`; `board.css` is React Flow's own stylesheet. `ui/board.html`
 * references both relatively, which is exactly what the host's inliner expects.
 */
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "ui",
    emptyOutDir: false,
    // The whole bundle is inlined into the document on every mount, so its bytes are paid for
    // each time the tab opens — which makes minification worth more here than usual.
    minify: true,
    rollupOptions: {
      input: "src/board.tsx",
      output: {
        inlineDynamicImports: true,
        entryFileNames: "board.js",
        assetFileNames: "board.[ext]",
      },
    },
  },
});
