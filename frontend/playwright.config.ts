import { defineConfig, devices } from "@playwright/test"

export default defineConfig({
  testDir: "./e2e",
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
    baseURL: "http://127.0.0.1:5173",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  webServer: {
    command: "uv run python scripts/run_frontend_e2e_server.py",
    cwd: "..",
    url: "http://127.0.0.1:5174/ready",
    timeout: 180_000,
    reuseExistingServer: !process.env.CI,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "chromium-mobile-smoke",
      grep: /@smoke/,
      retries: 0,
      use: { ...devices["Pixel 5"] },
    },
    {
      name: "firefox-smoke",
      grep: /@smoke/,
      retries: 0,
      use: { ...devices["Desktop Firefox"] },
    },
  ],
})
