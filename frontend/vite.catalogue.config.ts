import path from "path"
import { mergeConfig } from "vite"
import builder from "./vite.config"

/**
 * The kit's catalogue (specs/frontend-shared): a page of haute-ui's components in every
 * variant and state, for whoever builds with them. A developer's tool and no part of the
 * product: the builder's plugins, aliases and stylesheet, its own entry,
 * `catalogue.html`, written under `dist/catalogue/`, which nothing packages.
 * `vite --config vite.catalogue.config.ts` serves it at `/catalogue.html`.
 */
export default mergeConfig(builder, {
  server: {
    port: 5175,
  },
  build: {
    outDir: "dist/catalogue",
    emptyOutDir: true,
    rollupOptions: {
      input: path.resolve(__dirname, "catalogue.html"),
    },
  },
})
