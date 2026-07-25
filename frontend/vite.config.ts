import { readFileSync } from "fs"
import path from "path"
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from "@tailwindcss/vite"

const toml = readFileSync(path.resolve(__dirname, "../pyproject.toml"), "utf-8")
const versionMatch = toml.match(/^version\s*=\s*"(.+)"/m)
const appVersion = versionMatch ? versionMatch[1] : "0.1.0"
const backendUrl = new URL(
  process.env.HAUTE_BACKEND_URL ?? "http://127.0.0.1:8000",
)
const websocketUrl = new URL(backendUrl)
websocketUrl.protocol = backendUrl.protocol === "https:" ? "wss:" : "ws:"

export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: {
    __APP_VERSION__: JSON.stringify(appVersion),
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    proxy: {
      "/api": {
        target: backendUrl.origin,
        changeOrigin: false,
      },
      "/ws": {
        target: websocketUrl.origin,
        ws: true,
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: "../src/haute/static",
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          if (!id.includes("node_modules")) return;
          // React + ReactFlow share a chunk to avoid circular imports
          if (
            id.includes("/react-dom/") ||
            id.includes("/react/") ||
            id.includes("/scheduler/") ||
            id.includes("/use-sync-external-store/") ||
            id.includes("@xyflow/")
          ) {
            return "vendor-react";
          }
          if (id.includes("/elkjs/")) {
            return "vendor-layout";
          }
          if (id.includes("@codemirror/") || id.includes("@lezer/")) {
            return "vendor-codemirror";
          }
          if (id.includes("/lucide-react/")) {
            return "vendor-ui";
          }
        },
      },
    },
  },
})
