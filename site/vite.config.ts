import { defineConfig } from "vite";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const root = dirname(fileURLToPath(import.meta.url));

// GitHub Pages project sites live at /<repo>/ — override with VITE_BASE_PATH
const base = process.env.VITE_BASE_PATH || "/";

export default defineConfig({
  base,
  root,
  publicDir: "public",
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  resolve: {
    alias: {
      "@": resolve(root, "src"),
    },
  },
});
