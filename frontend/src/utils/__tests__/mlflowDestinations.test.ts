/**
 * Tests for the pure MLflow destination helpers: key validation, the
 * effective (node value vs auto) destination, inventory lookup, light state,
 * log availability, and the default experiment name.
 *
 * The oracle throughout is a mocked inventory shaped exactly like the wire
 * `MlflowDestinationsResponse.destinations`, so a backend field rename shows
 * up here rather than in a component test.
 */
import { describe, it, expect } from "vitest"

import type { MlflowDestinationEntry, MlflowDestinationKey } from "../../api/types.ts"
import {
  MLFLOW_DESTINATION_KEYS,
  MLFLOW_DESTINATION_LABELS,
  defaultExperimentName,
  effectiveMlflowDestination,
  isMlflowDestinationKey,
  mlflowDestinationEntry,
  mlflowLight,
  mlflowLogAvailability,
  type MlflowInventoryState,
} from "../mlflowDestinations.ts"

// ── Fixtures ─────────────────────────────────────────────────────

function entry(
  key: MlflowDestinationKey,
  over: Partial<MlflowDestinationEntry> = {},
): MlflowDestinationEntry {
  return {
    key,
    configured: false,
    destination: "",
    config_source: "",
    detail: "",
    probed: false,
    ok: false,
    category: "",
    ...over,
  }
}

/** Databricks green, server amber, local always available. */
const DATABRICKS_OK = entry("databricks", {
  configured: true,
  destination: "databricks://team",
  config_source: "env",
  probed: true,
  ok: true,
})
const SERVER_AMBER = entry("server", {
  configured: true,
  destination: "http://localhost:5000",
  config_source: "toml",
  detail: "Connection to the MLflow server timed out",
  probed: true,
  ok: false,
  category: "connectivity",
})
const SERVER_UNCONFIGURED = entry("server", {
  detail: "Set [mlflow] tracking_uri in haute.toml or MLFLOW_TRACKING_URI",
})
const LOCAL_OK = entry("local", {
  configured: true,
  destination: "C:/proj/mlruns",
  config_source: "default",
})

function inventory(over: Partial<MlflowInventoryState> = {}): MlflowInventoryState {
  return {
    status: "ready",
    installed: true,
    importable: true,
    auto: "databricks",
    destinations: [DATABRICKS_OK, SERVER_AMBER, LOCAL_OK],
    detail: "",
    ...over,
  }
}

// ── Test suites ──────────────────────────────────────────────────

describe("MLflow destination constants", () => {
  it("lists the three keys in the fixed display order", () => {
    expect(MLFLOW_DESTINATION_KEYS).toEqual(["databricks", "server", "local"])
  })

  it("labels the middle option 'MLflow server'", () => {
    expect(MLFLOW_DESTINATION_LABELS).toEqual({
      databricks: "Databricks",
      server: "MLflow server",
      local: "Local folder",
    })
  })
})

describe("isMlflowDestinationKey", () => {
  it("accepts exactly the three keys", () => {
    expect(isMlflowDestinationKey("databricks")).toBe(true)
    expect(isMlflowDestinationKey("server")).toBe(true)
    expect(isMlflowDestinationKey("local")).toBe(true)
  })

  it("rejects the empty string (auto is not a key)", () => {
    expect(isMlflowDestinationKey("")).toBe(false)
  })

  it("rejects unknown strings and non-strings", () => {
    expect(isMlflowDestinationKey("Databricks")).toBe(false)
    expect(isMlflowDestinationKey("dbx")).toBe(false)
    expect(isMlflowDestinationKey(undefined)).toBe(false)
    expect(isMlflowDestinationKey(null)).toBe(false)
    expect(isMlflowDestinationKey(3)).toBe(false)
    expect(isMlflowDestinationKey({ key: "local" })).toBe(false)
  })
})

describe("effectiveMlflowDestination", () => {
  it("uses an explicit valid node value over auto", () => {
    expect(effectiveMlflowDestination("local", "databricks")).toBe("local")
  })

  it("falls back to auto for the absent (auto) node value", () => {
    expect(effectiveMlflowDestination("", "server")).toBe("server")
    expect(effectiveMlflowDestination(undefined, "server")).toBe("server")
  })

  it("treats an unknown stored value as auto without mutating the config", () => {
    const stored = "azure"
    expect(effectiveMlflowDestination(stored, "local")).toBe("local")
    // The helper never rewrites the caller's value; it only reads it.
    expect(stored).toBe("azure")
  })

  it("returns the empty auto when nothing resolves", () => {
    expect(effectiveMlflowDestination("", "")).toBe("")
    expect(effectiveMlflowDestination(null, "")).toBe("")
  })
})

describe("mlflowDestinationEntry", () => {
  it("finds the entry for a key", () => {
    expect(mlflowDestinationEntry(inventory().destinations, "server")).toBe(SERVER_AMBER)
  })

  it("returns undefined for the auto key and for an absent entry", () => {
    expect(mlflowDestinationEntry(inventory().destinations, "")).toBeUndefined()
    expect(mlflowDestinationEntry([DATABRICKS_OK], "local")).toBeUndefined()
  })
})

describe("mlflowLight", () => {
  it("gives local no light, even while the inventory loads", () => {
    expect(mlflowLight(LOCAL_OK, "ready")).toBe("none")
    expect(mlflowLight(LOCAL_OK, "loading")).toBe("none")
  })

  it("is pending while the inventory loads", () => {
    expect(mlflowLight(DATABRICKS_OK, "loading")).toBe("pending")
    expect(mlflowLight(SERVER_UNCONFIGURED, "loading")).toBe("pending")
  })

  it("is grey for an unconfigured remote", () => {
    expect(mlflowLight(SERVER_UNCONFIGURED, "ready")).toBe("grey")
  })

  it("is grey when the inventory has no entry for the key", () => {
    expect(mlflowLight(undefined, "ready")).toBe("grey")
    expect(mlflowLight(undefined, "error")).toBe("grey")
  })

  it("is green for a configured remote whose probe passed", () => {
    expect(mlflowLight(DATABRICKS_OK, "ready")).toBe("green")
  })

  it("is amber for a configured remote whose probe failed", () => {
    expect(mlflowLight(SERVER_AMBER, "ready")).toBe("amber")
  })

  it("is pending for a configured remote that was not probed", () => {
    const notProbed = entry("server", {
      configured: true,
      destination: "http://localhost:5000",
      probed: false,
      ok: false,
    })
    expect(mlflowLight(notProbed, "ready")).toBe("pending")
  })
})

describe("defaultExperimentName", () => {
  it("prefixes /Shared/haute/ for databricks", () => {
    expect(defaultExperimentName("GLM Frequency", "databricks")).toBe("/Shared/haute/GLM Frequency")
  })

  it("uses the bare node label for server, local, and auto", () => {
    expect(defaultExperimentName("GLM Frequency", "server")).toBe("GLM Frequency")
    expect(defaultExperimentName("GLM Frequency", "local")).toBe("GLM Frequency")
    expect(defaultExperimentName("GLM Frequency", "")).toBe("GLM Frequency")
  })
})

describe("mlflowLogAvailability", () => {
  it("is unavailable while the inventory loads", () => {
    const result = mlflowLogAvailability(inventory({ status: "loading" }), "")
    expect(result.available).toBe(false)
    expect(result.reason).toBe("Checking MLflow…")
  })

  it("reports the package reason when MLflow is not installed", () => {
    const state = inventory({
      status: "error",
      installed: false,
      importable: false,
      auto: "",
      destinations: [],
      detail: "MLflow is not installed (pip install mlflow)",
    })
    const result = mlflowLogAvailability(state, "")
    expect(result.available).toBe(false)
    expect(result.reason).toBe("MLflow is not installed (pip install mlflow)")
  })

  it("reports the package reason when MLflow cannot be imported", () => {
    const state = inventory({
      status: "error",
      installed: true,
      importable: false,
      auto: "",
      destinations: [],
      detail: "MLflow is installed but cannot be imported",
    })
    expect(mlflowLogAvailability(state, "local").reason).toBe(
      "MLflow is installed but cannot be imported",
    )
  })

  it("reports the store reason when the inventory request failed", () => {
    const state = inventory({
      status: "error",
      installed: null,
      importable: null,
      auto: "",
      destinations: [],
      detail: "MLflow inventory check timed out after 15s",
    })
    const result = mlflowLogAvailability(state, "")
    expect(result.available).toBe(false)
    expect(result.reason).toBe("MLflow inventory check timed out after 15s")
  })

  it("reports the entry reason when the effective destination is unconfigured", () => {
    const state = inventory({
      auto: "local",
      destinations: [DATABRICKS_OK, SERVER_UNCONFIGURED, LOCAL_OK],
    })
    const result = mlflowLogAvailability(state, "server")
    expect(result.available).toBe(false)
    expect(result.key).toBe("server")
    expect(result.label).toBe("MLflow server")
    expect(result.reason).toBe(SERVER_UNCONFIGURED.detail)
  })

  it("reports the store reason when auto resolves to nothing", () => {
    const state = inventory({
      auto: "",
      destinations: [SERVER_UNCONFIGURED],
      detail: "[mlflow] mode is no longer a supported key",
    })
    const result = mlflowLogAvailability(state, "")
    expect(result.available).toBe(false)
    expect(result.key).toBe("")
    expect(result.label).toBe("")
    expect(result.reason).toBe("[mlflow] mode is no longer a supported key")
  })

  it("stays available when the probe failed — the amber light carries the warning", () => {
    const result = mlflowLogAvailability(inventory(), "server")
    expect(result).toEqual({
      available: true,
      key: "server",
      label: "MLflow server",
      destination: "http://localhost:5000",
      reason: "",
    })
  })

  it("resolves an explicit node value against the inventory", () => {
    const result = mlflowLogAvailability(inventory(), "local")
    expect(result).toEqual({
      available: true,
      key: "local",
      label: "Local folder",
      destination: "C:/proj/mlruns",
      reason: "",
    })
  })

  it("resolves the auto destination when the node stores no value", () => {
    const result = mlflowLogAvailability(inventory({ auto: "databricks" }), "")
    expect(result).toEqual({
      available: true,
      key: "databricks",
      label: "Databricks",
      destination: "databricks://team",
      reason: "",
    })
  })
})
