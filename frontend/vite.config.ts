import { readFileSync } from "fs"
import path from "path"
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from "@tailwindcss/vite"

const toml = readFileSync(path.resolve(__dirname, "../pyproject.toml"), "utf-8")
const versionMatch = toml.match(/^version\s*=\s*"(.+)"/m)
const appVersion = versionMatch ? versionMatch[1] : "0.1.0"

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
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/ws": {
        target: "ws://127.0.0.1:8000",
        ws: true,
      },
    },
  },
  build: {
    outDir: "../src/haute/static",
    emptyOutDir: true,
  },
})
