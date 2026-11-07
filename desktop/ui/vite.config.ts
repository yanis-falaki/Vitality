// Builds the interface into dist/, which the Tauri app embeds. `npm run dev` serves it with hot reload
// for `cargo tauri dev`; `mock.html` (dev server only) runs it against a fake core for screenshots.
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: { port: 5173, strictPort: true },
  build: { outDir: "dist", emptyOutDir: true, target: "es2022" },
});
