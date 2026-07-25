import { defineConfig, devices } from "@playwright/test"

function localPort(name: string, fallback: number): number {
  const value = Number(process.env[name] ?? fallback)
  if (!Number.isInteger(value) || value < 1 || value > 65_535) {
    throw new Error(`${name} must be an integer port between 1 and 65535`)
  }
  return value
}

const frontendPort = localPort("HAUTE_E2E_FRONTEND_PORT", 5173)
const readinessPort = localPort("HAUTE_E2E_READINESS_PORT", 5174)

export default defineConfig({
  testDir: "./e2e",
  // Vitest unit tests for e2e helpers live in e2e/__tests__; keep them out of Playwright.
  testIgnore: "**/__tests__/**",
  timeout: 60_000,
  expect: {
    timeout: 15_000,
  },
  fullyParallel: false,
  forbidOnly: true,
  workers: 1,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${frontendPort}`,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  webServer: {
    command: "uv run python scripts/run_frontend_e2e_server.py",
    cwd: "..",
    url: `http://127.0.0.1:${readinessPort}/ready`,
    timeout: 180_000,
    reuseExistingServer: !process.env.CI,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "firefox-smoke",
      grep: /@smoke/,
      use: { ...devices["Desktop Firefox"] },
    },
  ],
})
