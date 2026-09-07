import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/** One JS file and one CSS file, both inlined by the host — see `flowpad/vite.config.ts` for
 *  why nothing may be fetched. */
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "ui",
    emptyOutDir: false,
    minify: true,
    rollupOptions: {
      input: "src/board.tsx",
      output: { inlineDynamicImports: true, entryFileNames: "board.js", assetFileNames: "board.[ext]" },
    },
  },
});
