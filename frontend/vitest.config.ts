import { defineConfig } from "vitest/config"

export default defineConfig({
  test: {
    environment: "jsdom",
    // Node >= 22.4 ships experimental web-storage globals (on by default from
    // Node 25), and the jsdom environment skips window keys already present
    // on globalThis, so Node's globals shadow jsdom's. The shapes vary by
    // Node version (25: localStorage = inert file-less stub with no .clear;
    // 26: an accessor returning undefined; sessionStorage: a real but
    // process-global Storage that leaks state across test files) — what
    // matters is the key's mere presence, which stops jsdom's real Storage
    // being installed. Pin the behaviour off on every Node rather than
    // pinning a Node version; setupStorageCanary.ts (first in setupFiles)
    // asserts the pin holds. Entries here are APPENDED to the worker argv
    // vitest builds (which inherits only profiling flags from the parent
    // process, by vitest design) — they replace nothing. Requires
    // Node >= 22.4 (older Nodes reject the flag and crash the workers at
    // spawn); from Node 25 this spelling is the legacy alias of --webstorage,
    // and if a future Node drops the alias the symptom is that same
    // worker-spawn crash on an unrecognised option, not a canary message.
    // The webstoragePin meta-test gates this entry's presence, since on
    // CI's default-off Node deleting it would go unnoticed.
    execArgv: ["--no-experimental-webstorage"],
    include: ["src/**/__tests__/**/*.test.{ts,tsx}", "e2e/__tests__/**/*.test.ts"],
    setupFiles: ["./src/setupStorageCanary.ts", "./src/setupTests.ts"],
    allowOnly: false,
    testTimeout: 30000,
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "src/**/__tests__/**",
        "src/setupTests.ts",
        "src/setupStorageCanary.ts",
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
