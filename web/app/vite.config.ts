// Builds the app into dist/, which vitality-web serves. `npm run dev` serves it with hot reload and
// sends the API and the screenshots to a vitality-web on 127.0.0.1:4000 (see README.md).
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true, chunkSizeWarningLimit: 900 },
  server: {
    proxy: { "/api": "http://127.0.0.1:4000", "/captures": "http://127.0.0.1:4000" },
  },
});
