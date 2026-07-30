import { defineConfig } from "vitest/config"

export default defineConfig({
  test: {
    environment: "jsdom",
    // Node >= 22.4 ships experimental web-storage globals (on by default from
    // Node 25), and the jsdom environment skips window keys already present
    // on globalThis, so Node's globals shadow jsdom's: localStorage as an
    // inert file-less stub (no .clear), sessionStorage as a real but
    // process-global Storage that silently leaks state across test files.
    // Pin the behaviour off on every Node rather than pinning a Node version;
    // setupTests.ts carries the canary. Requires Node >= 22.4 (older Nodes
    // reject the flag and crash the workers); from Node 25 this spelling is
    // the legacy alias of --webstorage.
    execArgv: ["--no-experimental-webstorage"],
    include: ["src/**/__tests__/**/*.test.{ts,tsx}", "e2e/__tests__/**/*.test.ts"],
    setupFiles: ["./src/setupTests.ts"],
    allowOnly: false,
    testTimeout: 30000,
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "src/**/__tests__/**",
        "src/setupTests.ts",
        "src/main.tsx",
        "src/vite-env.d.ts",
      ],
      reporter: ["text", "text-summary", "json-summary"],
      thresholds: {
        statements: 80,
        branches: 75,
        functions: 80,
        lines: 80,
      },
    },
  },
})
