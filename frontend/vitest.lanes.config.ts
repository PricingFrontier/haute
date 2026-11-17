import { configDefaults, defineConfig } from "vitest/config"

import base from "./vitest.config"

// The scheduled lanes' tests (`*.lane.test.*`), which the ordinary suite leaves
// out, in the same environment and setup. A lane's script runs them with the
// inputs they read (scripts/run_step_round_trip.py).
export default defineConfig({
  test: {
    ...base.test,
    include: ["src/**/__tests__/**/*.lane.test.{ts,tsx}"],
    exclude: configDefaults.exclude,
  },
})
