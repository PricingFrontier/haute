import { execFileSync } from "child_process"
import { readFileSync } from "fs"
import path from "path"
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from "@tailwindcss/vite"

const toml = readFileSync(path.resolve(__dirname, "../pyproject.toml"), "utf-8")
const projectStart = toml.indexOf("[project]")
if (projectStart === -1) {
  throw new Error("Unable to read package version: pyproject.toml has no [project] section")
}
const nextSection = toml.indexOf("\n[", projectStart + "[project]".length)
const projectToml = toml.slice(projectStart, nextSection === -1 ? undefined : nextSection)
const versionMatch = projectToml.match(/^version\s*=\s*"([^"]+)"\s*$/m)
if (!versionMatch) {
  throw new Error("Unable to read package version: [project].version is missing")
}
const appVersion = versionMatch[1]

/** One line of `git` output, or null where there is no repository (a source archive). */
function git(...args: string[]): string | null {
  try {
    const out = execFileSync("git", args, { cwd: __dirname, encoding: "utf-8", stdio: ["ignore", "pipe", "ignore"] })
    return out.trim() || null
  } catch {
    return null
  }
}

// When this build was made and from which commit, for the toolbar's version details.
// SOURCE_DATE_EPOCH (seconds) pins the build time for a reproducible build.
const sourceDateEpoch = Number(process.env.SOURCE_DATE_EPOCH)
const appBuild = {
  builtAt: (Number.isFinite(sourceDateEpoch) && process.env.SOURCE_DATE_EPOCH
    ? new Date(sourceDateEpoch * 1000)
    : new Date()
  ).toISOString(),
  commit: git("rev-parse", "HEAD"),
  commitDate: git("log", "-1", "--format=%cI"),
}

const backendUrl = new URL(
  process.env.HAUTE_BACKEND_URL ?? "http://127.0.0.1:8000",
)
const websocketUrl = new URL(backendUrl)
websocketUrl.protocol = backendUrl.protocol === "https:" ? "wss:" : "ws:"

export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: {
    __APP_VERSION__: JSON.stringify(appVersion),
    __APP_BUILD__: JSON.stringify(appBuild),
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
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
    manifest: "manifest.json",
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          if (!id.includes("node_modules")) return;
          if (id.includes("/echarts/") || id.includes("/zrender/")) {
            return "vendor-charts";
          }
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
