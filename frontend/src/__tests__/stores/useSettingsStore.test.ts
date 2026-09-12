/**
 * Tests for useSettingsStore — the MLflow destinations inventory (dedup,
 * invalidation, 15-second deadline), file list cache, collapsible sections,
 * and row limit.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"

// Mock the API module BEFORE importing the store
vi.mock("../../api/client.ts", () => ({
  getMlflowDestinations: vi.fn(),
}))

import useSettingsStore from "../../stores/useSettingsStore.ts"
import { getMlflowDestinations } from "../../api/client.ts"
import type {
  MlflowDestinationEntry,
  MlflowDestinationKey,
  MlflowDestinationsResponse,
} from "../../api/types.ts"

// ── Helpers ──────────────────────────────────────────────────────

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

const LOCAL_ENTRY = entry("local", {
  configured: true,
  destination: "C:/proj/mlruns",
  config_source: "default",
})

/** A server whose probe exhausted its budget: configured, probed, not ok. */
const SERVER_AMBER = entry("server", {
  configured: true,
  destination: "http://localhost:5000",
  config_source: "toml",
  detail: "Connection to the MLflow server timed out",
  probed: true,
  ok: false,
  category: "connectivity",
})

const OK_LOCAL: MlflowDestinationsResponse = {
  mlflow_installed: true,
  mlflow_importable: true,
  auto: "local",
  destinations: [entry("databricks"), entry("server"), LOCAL_ENTRY],
  detail: "",
}

const OK_SERVER: MlflowDestinationsResponse = {
  mlflow_installed: true,
  mlflow_importable: true,
  auto: "server",
  destinations: [
    entry("databricks"),
    entry("server", {
      configured: true,
      destination: "http://localhost:5000",
      config_source: "toml",
      probed: true,
      ok: true,
    }),
    LOCAL_ENTRY,
  ],
  detail: "",
}

function resetStore() {
  useSettingsStore.setState({
    rowLimit: 100,  // store default is 100, not 1000
    streamingChunkSize: 500_000,
    openSections: {},
    mlflow: {
      status: "pending",
      installed: null,
      importable: null,
      auto: "",
      destinations: [],
      detail: "",
    },
    _mlflowFetching: false,
    _mlflowLastAttempt: 0,
    sources: ["live"],
    activeSource: "live",
    fileListCache: {},
  })
}

// ── Test suites ──────────────────────────────────────────────────

describe("useSettingsStore", () => {
  beforeEach(() => {
    resetStore()
    vi.clearAllMocks()
  })

  // ────────────────────────────────────────────────────────────────
  // Row limit
  // ────────────────────────────────────────────────────────────────

  describe("setRowLimit", () => {
    it("defaults to 100", () => {
      // The store's actual default is 100 (not 1000). This test catches drift
      // between the reset helper and the real store initialiser.
      expect(useSettingsStore.getState().rowLimit).toBe(100)
    })

    it("updates row limit", () => {
      useSettingsStore.getState().setRowLimit(500)
      expect(useSettingsStore.getState().rowLimit).toBe(500)
    })
  })

  // ────────────────────────────────────────────────────────────────
  // Streaming chunk size
  // ────────────────────────────────────────────────────────────────

  describe("setStreamingChunkSize", () => {
    it("defaults to 500_000", () => {
      expect(useSettingsStore.getState().streamingChunkSize).toBe(500_000)
    })

    it("updates streaming chunk size", () => {
      useSettingsStore.getState().setStreamingChunkSize(250_000)
      expect(useSettingsStore.getState().streamingChunkSize).toBe(250_000)
    })

    it("clamps below-min sizes up to MIN_STREAMING_CHUNK_SIZE", () => {
      useSettingsStore.getState().setStreamingChunkSize(5)
      expect(useSettingsStore.getState().streamingChunkSize).toBe(1000)
    })

    it("clamps above-max sizes down to MAX_STREAMING_CHUNK_SIZE", () => {
      useSettingsStore.getState().setStreamingChunkSize(50_000_000)
      expect(useSettingsStore.getState().streamingChunkSize).toBe(10_000_000)
    })

    it("rounds fractional sizes to an integer", () => {
      useSettingsStore.getState().setStreamingChunkSize(123_456.78)
      expect(useSettingsStore.getState().streamingChunkSize).toBe(123_457)
    })
  })

  // ────────────────────────────────────────────────────────────────
  // MLflow dedup
  // ────────────────────────────────────────────────────────────────

  describe("MLflow fetch dedup", () => {
    it("calling fetchMlflow twice rapidly only makes one API request", async () => {
      const mockInventory = vi.mocked(getMlflowDestinations)
      let resolvePromise: (value: MlflowDestinationsResponse) => void
      const promise = new Promise<MlflowDestinationsResponse>((resolve) => {
        resolvePromise = resolve
      })
      mockInventory.mockReturnValue(promise)

      const s = useSettingsStore.getState()
      s.fetchMlflow()
      s.fetchMlflow() // second call should be deduped

      expect(mockInventory).toHaveBeenCalledTimes(1)

      // Resolve the promise to clean up
      resolvePromise!(OK_LOCAL)
      // Wait for microtask to flush the .then()
      await vi.waitFor(() => {
        expect(useSettingsStore.getState()._mlflowFetching).toBe(false)
      })
    })

    it("requests the inventory with probing enabled", async () => {
      const mockInventory = vi.mocked(getMlflowDestinations)
      mockInventory.mockResolvedValue(OK_LOCAL)

      useSettingsStore.getState().fetchMlflow()

      await vi.waitFor(() => {
        expect(useSettingsStore.getState().mlflow.status).toBe("ready")
      })
      expect(mockInventory).toHaveBeenCalledWith(true)
    })

    it("successful inventory sets ready with auto and destinations", async () => {
      const mockInventory = vi.mocked(getMlflowDestinations)
      mockInventory.mockResolvedValue({
        mlflow_installed: true,
        mlflow_importable: true,
        auto: "databricks",
        destinations: [
          entry("databricks", {
            configured: true,
            destination: "databricks://team",
            config_source: "env",
            probed: true,
            ok: true,
          }),
          entry("server"),
          LOCAL_ENTRY,
        ],
        detail: "",
      })

      useSettingsStore.getState().fetchMlflow()

      await vi.waitFor(() => {
        expect(useSettingsStore.getState().mlflow.status).toBe("ready")
      })

      const { mlflow } = useSettingsStore.getState()
      expect(mlflow.auto).toBe("databricks")
      expect(mlflow.destinations.map((d) => d.key)).toEqual(["databricks", "server", "local"])
      expect(mlflow.destinations[0].destination).toBe("databricks://team")
      expect(mlflow.installed).toBe(true)
      expect(mlflow.importable).toBe(true)
      expect(mlflow.detail).toBe("")
    })

    it("failed inventory request sets error status", async () => {
      const mockInventory = vi.mocked(getMlflowDestinations)
      mockInventory.mockRejectedValue(new Error("Network error"))

      useSettingsStore.getState().fetchMlflow()

      await vi.waitFor(() => {
        expect(useSettingsStore.getState().mlflow.status).toBe("error")
      })
      const { mlflow } = useSettingsStore.getState()
      expect(mlflow.installed).toBeNull()
      expect(mlflow.importable).toBeNull()
      expect(mlflow.auto).toBe("")
      expect(mlflow.destinations).toEqual([])
      expect(mlflow.detail).toBe("Network error")
    })

    it("does not re-fetch after a ready inventory", async () => {
      const mockInventory = vi.mocked(getMlflowDestinations)
      mockInventory.mockResolvedValue(OK_LOCAL)

      useSettingsStore.getState().fetchMlflow()
      await vi.waitFor(() => {
        expect(useSettingsStore.getState().mlflow.status).toBe("ready")
      })

      // Second call should be a no-op since status is "ready"
      mockInventory.mockClear()
      useSettingsStore.getState().fetchMlflow()
      expect(mockInventory).not.toHaveBeenCalled()
    })

    it("mlflow_installed: false sets error status with the package detail", async () => {
      const mockInventory = vi.mocked(getMlflowDestinations)
      mockInventory.mockResolvedValue({
        mlflow_installed: false,
        mlflow_importable: false,
        auto: "",
        destinations: [],
        detail: "MLflow is not installed (pip install mlflow)",
      })

      useSettingsStore.getState().fetchMlflow()

      await vi.waitFor(() => {
        expect(useSettingsStore.getState().mlflow.status).toBe("error")
      })
      const { mlflow } = useSettingsStore.getState()
      expect(mlflow.installed).toBe(false)
      expect(mlflow.importable).toBe(false)
      expect(mlflow.detail).toBe("MLflow is not installed (pip install mlflow)")
    })

    it("an unimportable MLflow package sets error status", async () => {
      const mockInventory = vi.mocked(getMlflowDestinations)
      mockInventory.mockResolvedValue({
        mlflow_installed: true,
        mlflow_importable: false,
        auto: "",
        destinations: [],
        detail: "MLflow is installed but cannot be imported",
      })

      useSettingsStore.getState().fetchMlflow()

      await vi.waitFor(() => {
        expect(useSettingsStore.getState().mlflow.status).toBe("error")
      })
      const { mlflow } = useSettingsStore.getState()
      expect(mlflow.installed).toBe(true)
      expect(mlflow.importable).toBe(false)
      expect(mlflow.detail).toContain("cannot be imported")
    })

    it("an inventory with nothing configured is still ready, with the reason in detail", async () => {
      const mockInventory = vi.mocked(getMlflowDestinations)
      mockInventory.mockResolvedValue({
        mlflow_installed: true,
        mlflow_importable: true,
        auto: "",
        destinations: [
          entry("databricks", {
            detail: "Set DATABRICKS_HOST and DATABRICKS_TOKEN, or MLFLOW_TRACKING_URI=databricks://<profile>",
          }),
          entry("server", { detail: "Set [mlflow] tracking_uri in haute.toml" }),
          entry("local", { detail: "[mlflow] mode is no longer a supported key" }),
        ],
        detail: "[mlflow] mode is no longer a supported key",
      })

      useSettingsStore.getState().fetchMlflow()

      // The package is present, so the inventory arrived: the per-entry
      // reasons are the story, not a whole-inventory error.
      await vi.waitFor(() => {
        expect(useSettingsStore.getState().mlflow.status).toBe("ready")
      })
      const { mlflow } = useSettingsStore.getState()
      expect(mlflow.auto).toBe("")
      expect(mlflow.destinations[0].detail).toContain("DATABRICKS_TOKEN")
      expect(mlflow.detail).toContain("mode")
    })
  })

  // ────────────────────────────────────────────────────────────────
  // MLflow invalidation (settings changed → refetch)
  // ────────────────────────────────────────────────────────────────

  describe("invalidateMlflow", () => {
    it("resets to pending and refetches a fresh inventory", async () => {
      const mockInventory = vi.mocked(getMlflowDestinations)
      mockInventory.mockResolvedValue(OK_LOCAL)

      useSettingsStore.getState().fetchMlflow()
      await vi.waitFor(() => {
        expect(useSettingsStore.getState().mlflow.status).toBe("ready")
      })
      expect(useSettingsStore.getState().mlflow.auto).toBe("local")

      mockInventory.mockResolvedValue(OK_SERVER)
      useSettingsStore.getState().invalidateMlflow()

      await vi.waitFor(() => {
        expect(useSettingsStore.getState().mlflow.auto).toBe("server")
      })
      const { mlflow } = useSettingsStore.getState()
      expect(mlflow.status).toBe("ready")
      expect(mlflow.destinations[1].destination).toBe("http://localhost:5000")
      expect(mlflow.destinations[1].ok).toBe(true)
    })

    it("invalidation during an in-flight fetch results in exactly one follow-up fetch", async () => {
      const mockInventory = vi.mocked(getMlflowDestinations)
      let resolveFirst: (value: MlflowDestinationsResponse) => void
      mockInventory.mockReturnValueOnce(
        new Promise<MlflowDestinationsResponse>((resolve) => {
          resolveFirst = resolve
        }),
      )
      mockInventory.mockResolvedValue(OK_SERVER)

      useSettingsStore.getState().fetchMlflow()
      useSettingsStore.getState().invalidateMlflow() // while first fetch is in flight

      resolveFirst!(OK_LOCAL)

      await vi.waitFor(() => {
        expect(useSettingsStore.getState().mlflow.auto).toBe("server")
      })
      expect(mockInventory).toHaveBeenCalledTimes(2)
    })
  })

  // ────────────────────────────────────────────────────────────────
  // File list cache
  // ────────────────────────────────────────────────────────────────

  describe("file list cache", () => {
    beforeEach(() => {
      vi.useFakeTimers()
    })

    afterEach(() => {
      vi.useRealTimers()
    })

    it("setFileListCache then getFileListCache returns items within TTL", () => {
      const items = [
        { name: "data.csv", path: "/data/data.csv", type: "file" as const },
        { name: "models", path: "/data/models", type: "directory" as const },
      ]

      useSettingsStore.getState().setFileListCache("dir|csv", items)

      const result = useSettingsStore.getState().getFileListCache("dir|csv")
      expect(result).toEqual(items)
    })

    it("getFileListCache returns null for unknown key", () => {
      expect(useSettingsStore.getState().getFileListCache("nope")).toBeNull()
    })

    it("cache expires after 30 seconds", () => {
      const items = [{ name: "test.csv", path: "/test.csv", type: "file" as const }]
      useSettingsStore.getState().setFileListCache("key1", items)

      // Still fresh at 29 seconds
      vi.advanceTimersByTime(29_000)
      expect(useSettingsStore.getState().getFileListCache("key1")).toEqual(items)

      // Expired at 31 seconds
      vi.advanceTimersByTime(2_000)
      expect(useSettingsStore.getState().getFileListCache("key1")).toBeNull()
    })

    it("cache is exactly expired at 30001ms", () => {
      const items = [{ name: "a.csv", path: "/a.csv", type: "file" as const }]
      useSettingsStore.getState().setFileListCache("k", items)

      vi.advanceTimersByTime(30_001)
      expect(useSettingsStore.getState().getFileListCache("k")).toBeNull()
    })
  })

  // ────────────────────────────────────────────────────────────────
  // Collapsible sections
  // ────────────────────────────────────────────────────────────────

  describe("collapsible sections", () => {
    it("toggleSection toggles a section on and off", () => {
      const s = useSettingsStore.getState()

      // Initially undefined (uses default)
      s.toggleSection("advanced")
      expect(useSettingsStore.getState().openSections["advanced"]).toBe(true)

      useSettingsStore.getState().toggleSection("advanced")
      expect(useSettingsStore.getState().openSections["advanced"]).toBe(false)
    })

    it("isSectionOpen returns defaultOpen when section has no stored value", () => {
      const s = useSettingsStore.getState()
      // Default is false when not specified
      expect(s.isSectionOpen("unknown")).toBe(false)
      // Default is true when specified
      expect(s.isSectionOpen("unknown", true)).toBe(true)
    })

    it("isSectionOpen returns stored value regardless of default", () => {
      const s = useSettingsStore.getState()
      s.toggleSection("sec1") // sets to true (toggling from undefined/false)

      expect(useSettingsStore.getState().isSectionOpen("sec1")).toBe(true)
      expect(useSettingsStore.getState().isSectionOpen("sec1", false)).toBe(true)
    })

    it("multiple sections are independent", () => {
      const s = useSettingsStore.getState()
      s.toggleSection("a")
      s.toggleSection("b")

      expect(useSettingsStore.getState().openSections["a"]).toBe(true)
      expect(useSettingsStore.getState().openSections["b"]).toBe(true)

      useSettingsStore.getState().toggleSection("a")
      expect(useSettingsStore.getState().openSections["a"]).toBe(false)
      expect(useSettingsStore.getState().openSections["b"]).toBe(true)
    })
  })

  // ────────────────────────────────────────────────────────────────
  // Source slug (B12 fix; key mint moved to portableKey —
  // case is now PRESERVED. Full identity battery lives in
  // stores/__tests__/useSettingsStore.addSource.test.ts)
  // ────────────────────────────────────────────────────────────────

  describe("addSource returns a discriminated result", () => {
    it("returns ok + the sanitized key on success", () => {
      const result = useSettingsStore.getState().addSource("My Test Source")
      expect(result).toEqual({ ok: true, key: "My_Test_Source" })
      expect(useSettingsStore.getState().sources).toContain("My_Test_Source")
    })

    it("returns a duplicate rejection (naming the colliding key) for a duplicate source", () => {
      useSettingsStore.getState().addSource("dup")
      const result = useSettingsStore.getState().addSource("dup")
      expect(result).toEqual({ ok: false, reason: "duplicate", key: "dup" })
    })

    it("returns an empty rejection for a blank name", () => {
      const result = useSettingsStore.getState().addSource("   ")
      expect(result).toEqual({ ok: false, reason: "empty" })
    })

    it("maps each space to an underscore, per the blessed identity", () => {
      // portableKey encodes EVERY interior space (runs are not collapsed),
      // so "a  b" and "a b" stay distinct keys — the old fold merged them.
      const result = useSettingsStore.getState().addSource("  Two  Words  ")
      expect(result).toEqual({ ok: true, key: "Two__Words" })
    })

    it("key is consistent with what gets stored in sources list", () => {
      const result = useSettingsStore.getState().addSource("New Source")
      const sources = useSettingsStore.getState().sources
      expect(result.ok).toBe(true)
      if (result.ok) expect(sources).toContain(result.key)
    })
  })

  // ────────────────────────────────────────────────────────────────
  // removeSource
  // Catches: removing a source that is currently active would leave
  // activeSource pointing at a nonexistent source, breaking data
  // source routing.
  // ────────────────────────────────────────────────────────────────

  describe("removeSource", () => {
    it("removes a non-live source from the list", () => {
      useSettingsStore.getState().addSource("test_sc")
      expect(useSettingsStore.getState().sources).toContain("test_sc")

      useSettingsStore.getState().removeSource("test_sc")
      expect(useSettingsStore.getState().sources).not.toContain("test_sc")
    })

    it("cannot remove the 'live' source (always present)", () => {
      useSettingsStore.getState().removeSource("live")
      expect(useSettingsStore.getState().sources).toContain("live")
    })

    it("resets activeSource to 'live' when removing the active source", () => {
      useSettingsStore.getState().addSource("staging")
      useSettingsStore.getState().setActiveSource("staging")
      expect(useSettingsStore.getState().activeSource).toBe("staging")

      useSettingsStore.getState().removeSource("staging")
      expect(useSettingsStore.getState().activeSource).toBe("live")
    })

    it("does not change activeSource when removing a non-active source", () => {
      useSettingsStore.getState().addSource("sc_a")
      useSettingsStore.getState().addSource("sc_b")
      useSettingsStore.getState().setActiveSource("sc_a")

      useSettingsStore.getState().removeSource("sc_b")
      expect(useSettingsStore.getState().activeSource).toBe("sc_a")
    })

    it("removing a nonexistent source is a no-op", () => {
      const before = useSettingsStore.getState().sources.slice()
      useSettingsStore.getState().removeSource("ghost")
      expect(useSettingsStore.getState().sources).toEqual(before)
    })
  })

  // ────────────────────────────────────────────────────────────────
  // setSources / setActiveSource — direct setters
  // Catches: if setSources were accidentally removed or renamed,
  // pipeline load (which bulk-sets sources from the backend) would
  // break silently.
  // ────────────────────────────────────────────────────────────────

  describe("setSources / setActiveSource", () => {
    it("setSources replaces the entire source list", () => {
      useSettingsStore.getState().setSources(["live", "staging", "prod"])
      expect(useSettingsStore.getState().sources).toEqual(["live", "staging", "prod"])
    })

    it("setActiveSource switches the active source", () => {
      useSettingsStore.getState().setSources(["live", "staging"])
      useSettingsStore.getState().setActiveSource("staging")
      expect(useSettingsStore.getState().activeSource).toBe("staging")
    })

    it("setSources does not affect activeSource when it still exists in new list", () => {
      useSettingsStore.getState().setActiveSource("live")
      useSettingsStore.getState().setSources(["live", "new_sc"])
      expect(useSettingsStore.getState().activeSource).toBe("live")
    })

    it("setSources resets activeSource to 'live' when active source is removed from list", () => {
      useSettingsStore.getState().setSources(["live", "staging", "prod"])
      useSettingsStore.getState().setActiveSource("staging")
      expect(useSettingsStore.getState().activeSource).toBe("staging")

      // Replace sources without "staging" — activeSource should reset to "live"
      useSettingsStore.getState().setSources(["live", "prod"])
      expect(useSettingsStore.getState().activeSource).toBe("live")
    })

    it("setSources preserves activeSource when it exists in the new list", () => {
      useSettingsStore.getState().setSources(["live", "staging", "prod"])
      useSettingsStore.getState().setActiveSource("prod")

      useSettingsStore.getState().setSources(["live", "prod"])
      expect(useSettingsStore.getState().activeSource).toBe("prod")
    })
  })

  // ────────────────────────────────────────────────────────────────
  // MLflow 15-second inventory deadline
  // Catches: if the deadline is removed, a hung inventory request would
  // block every MLflow surface indefinitely with status "pending". The
  // deadline is 15s, not 5s, because the backend probes each remote
  // concurrently under its own 5s budget — a slow probe must arrive as an
  // amber entry rather than trip a whole-inventory error.
  // ────────────────────────────────────────────────────────────────

  describe("MLflow 15s inventory deadline", () => {
    beforeEach(() => {
      vi.useFakeTimers()
    })

    afterEach(() => {
      vi.useRealTimers()
    })

    it("times out and sets error status when the inventory request hangs past 15s", async () => {
      const mockInventory = vi.mocked(getMlflowDestinations)
      // Return a promise that never resolves
      mockInventory.mockReturnValue(new Promise(() => {}))

      useSettingsStore.getState().fetchMlflow()

      // Advance past the 15s deadline
      vi.advanceTimersByTime(15_001)

      // Wait for BOTH the error status (.catch) and the fetching flag
      // clear (.finally) — .finally runs as a separate microtask after
      // .catch, so checking _mlflowFetching outside waitFor can race
      // the microtask queue (observed flake: run 30874054542, Frontend
      // Shuffle — waitFor saw status="error" and returned before
      // .finally flushed, leaving _mlflowFetching=true).
      await vi.waitFor(() => {
        expect(useSettingsStore.getState().mlflow.status).toBe("error")
        expect(useSettingsStore.getState()._mlflowFetching).toBe(false)
      })
      expect(useSettingsStore.getState().mlflow.detail).toContain("15")
    })

    it("a remote that exhausted its probe budget still lands as ready with an amber entry", async () => {
      const mockInventory = vi.mocked(getMlflowDestinations)
      // The backend spent 6s on the server probe (its own 5s budget plus
      // request overhead) and reported it as a failed probe, not an error.
      mockInventory.mockReturnValue(
        new Promise<MlflowDestinationsResponse>((resolve) => {
          setTimeout(() => resolve({
            mlflow_installed: true,
            mlflow_importable: true,
            auto: "server",
            destinations: [entry("databricks"), SERVER_AMBER, LOCAL_ENTRY],
            detail: "",
          }), 6_000)
        }),
      )

      useSettingsStore.getState().fetchMlflow()

      vi.advanceTimersByTime(6_000)

      await vi.waitFor(() => {
        expect(useSettingsStore.getState().mlflow.status).toBe("ready")
      })
      const amber = useSettingsStore.getState().mlflow.destinations[1]
      expect(amber.key).toBe("server")
      expect(amber.configured).toBe(true)
      expect(amber.probed).toBe(true)
      expect(amber.ok).toBe(false)
      expect(amber.detail).toContain("timed out")
    })

    it("succeeds before the deadline if the inventory request resolves quickly", async () => {
      const mockInventory = vi.mocked(getMlflowDestinations)
      mockInventory.mockResolvedValue(OK_LOCAL)

      useSettingsStore.getState().fetchMlflow()

      // Let the resolved promise flush
      await vi.waitFor(() => {
        expect(useSettingsStore.getState().mlflow.status).toBe("ready")
      })

      // The deadline should not overwrite the ready inventory when it fires later
      vi.advanceTimersByTime(16_000)
      expect(useSettingsStore.getState().mlflow.status).toBe("ready")
    })
  })
})
