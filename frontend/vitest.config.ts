import { defineConfig } from "vitest/config"

export default defineConfig({
  test: {
    environment: "jsdom",
    // Node >= 22.4 ships experimental web-storage globals (on by default from
    // Node 25). Node's file-less localStorage stub (no .clear) would shadow
    // jsdom's real Storage, because the jsdom environment skips window keys
    // already present on globalThis. Pin the behaviour off on every Node
    // rather than pinning a Node version; setupTests.ts carries the canary.
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
