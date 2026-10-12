import path from "path"
import { mergeConfig } from "vite"
import builder from "./vite.config"

/**
 * The underwriter app's page (specs/workbench), built apart from the builder's so the
 * builder's chunks are left as they are: the builder's plugins, aliases, defines and vendor
 * chunks, its own entry, `app.html`, written under `static/app/` once the builder's build
 * has replaced `static/`. `vite --config vite.app.config.ts` serves it against
 * `haute app serve`.
 */
const backendUrl = new URL(process.env.HAUTE_APP_URL ?? "http://127.0.0.1:8001")

export default mergeConfig(builder, {
  server: {
    port: 5174,
    proxy: {
      "/api": { target: backendUrl.origin, changeOrigin: false },
    },
  },
  build: {
    outDir: "../src/haute/static/app",
    emptyOutDir: true,
    rollupOptions: {
      input: path.resolve(__dirname, "app.html"),
    },
  },
})
