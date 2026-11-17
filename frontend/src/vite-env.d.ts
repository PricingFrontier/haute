/// <reference types="vite/client" />

/** The app's version, defined by Vite at build time (`vite.config.ts`). */
declare const __APP_VERSION__: string

/**
 * When and from which commit this build was made, defined by Vite at build time
 * (`vite.config.ts`). The commit fields are null when the build had no git repository.
 */
declare const __APP_BUILD__: { builtAt: string; commit: string | null; commitDate: string | null }
