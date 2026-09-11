/**
 * Tests for useSettingsStore — MLflow dedup, file list cache,
 * collapsible sections, and row limit.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"

// Mock the API module BEFORE importing the store
vi.mock("../../api/client.ts", () => ({
  getMlflowStatus: vi.fn(),
}))

import useSettingsStore from "../../stores/useSettingsStore.ts"
import { getMlflowStatus } from "../../api/client.ts"
import type { MlflowStatusResponse } from "../../api/types.ts"

// ── Helpers ──────────────────────────────────────────────────────

const OK_LOCAL: MlflowStatusResponse = {
  mlflow_installed: true,
  mlflow_importable: true,
  configured: true,
  mode: "local",
  destination: "C:/proj/mlruns",
  config_source: "default",
  detail: "",
}

function resetStore() {
  useSettingsStore.setState({
    rowLimit: 100,  // store default is 100, not 1000
    streamingChunkSize: 500_000,
    openSections: {},
    mlflow: {
      status: "pending",
      mode: "",
      destination: "",
      configSource: "",
      installed: null,
      importable: null,
      configured: null,
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
      const mockStatus = vi.mocked(getMlflowStatus)
      let resolvePromise: (value: MlflowStatusResponse) => void
      const promise = new Promise<MlflowStatusResponse>((resolve) => {
        resolvePromise = resolve
      })
      mockStatus.mockReturnValue(promise)

      const s = useSettingsStore.getState()
      s.fetchMlflow()
      s.fetchMlflow() // second call should be deduped

      expect(mockStatus).toHaveBeenCalledTimes(1)

      // Resolve the promise to clean up
      resolvePromise!(OK_LOCAL)
      // Wait for microtask to flush the .then()
      await vi.waitFor(() => {
        expect(useSettingsStore.getState()._mlflowFetching).toBe(false)
      })
    })

    it("successful MLflow status sets connected with mode and destination", async () => {
      const mockStatus = vi.mocked(getMlflowStatus)
      mockStatus.mockResolvedValue({
        mlflow_installed: true,
        mlflow_importable: true,
        configured: true,
        mode: "databricks",
        destination: "https://db.example.com",
        config_source: "env",
        detail: "",
      })

      useSettingsStore.getState().fetchMlflow()

      await vi.waitFor(() => {
        expect(useSettingsStore.getState().mlflow.status).toBe("connected")
      })

      const { mlflow } = useSettingsStore.getState()
      expect(mlflow.mode).toBe("databricks")
      expect(mlflow.destination).toBe("https://db.example.com")
      expect(mlflow.configSource).toBe("env")
      expect(mlflow.installed).toBe(true)
      expect(mlflow.importable).toBe(true)
      expect(mlflow.configured).toBe(true)
      expect(mlflow.detail).toBe("")
    })

    it("failed MLflow status request sets error status", async () => {
      const mockStatus = vi.mocked(getMlflowStatus)
      mockStatus.mockRejectedValue(new Error("Network error"))

      useSettingsStore.getState().fetchMlflow()

      await vi.waitFor(() => {
        expect(useSettingsStore.getState().mlflow.status).toBe("error")
      })
      const { mlflow } = useSettingsStore.getState()
      expect(mlflow.installed).toBeNull()
      expect(mlflow.importable).toBeNull()
      expect(mlflow.configured).toBeNull()
      expect(mlflow.detail).toBe("Network error")
    })

    it("does not re-fetch after successful connection", async () => {
      const mockStatus = vi.mocked(getMlflowStatus)
      mockStatus.mockResolvedValue(OK_LOCAL)

      useSettingsStore.getState().fetchMlflow()
      await vi.waitFor(() => {
        expect(useSettingsStore.getState().mlflow.status).toBe("connected")
      })

      // Second call should be a no-op since status is "connected"
      mockStatus.mockClear()
      useSettingsStore.getState().fetchMlflow()
      expect(mockStatus).not.toHaveBeenCalled()
    })

    it("mlflow_installed: false sets error status", async () => {
      const mockStatus = vi.mocked(getMlflowStatus)
      mockStatus.mockResolvedValue({
        mlflow_installed: false,
        mlflow_importable: false,
        configured: false,
        mode: "",
        destination: "",
        config_source: "",
        detail: "MLflow package is not installed",
      })

      useSettingsStore.getState().fetchMlflow()

      await vi.waitFor(() => {
        expect(useSettingsStore.getState().mlflow.status).toBe("error")
      })
      const { mlflow } = useSettingsStore.getState()
      expect(mlflow.installed).toBe(false)
      expect(mlflow.importable).toBe(false)
      expect(mlflow.configured).toBe(false)
      expect(mlflow.detail).toBe("MLflow package is not installed")
    })

    it("installed MLflow with a misconfigured selection keeps package availability distinct", async () => {
      const mockStatus = vi.mocked(getMlflowStatus)
      mockStatus.mockResolvedValue({
        mlflow_installed: true,
        mlflow_importable: true,
        configured: false,
        mode: "",
        destination: "",
        config_source: "",
        detail: "Databricks tracking is selected but DATABRICKS_TOKEN is not set in the environment (.env).",
      })

      useSettingsStore.getState().fetchMlflow()

      await vi.waitFor(() => {
        expect(useSettingsStore.getState().mlflow.status).toBe("error")
      })
      const { mlflow } = useSettingsStore.getState()
      expect(mlflow.installed).toBe(true)
      expect(mlflow.importable).toBe(true)
      expect(mlflow.configured).toBe(false)
      expect(mlflow.detail).toContain("DATABRICKS_TOKEN")
    })
  })

  // ────────────────────────────────────────────────────────────────
  // MLflow invalidation (settings changed → refetch)
  // ────────────────────────────────────────────────────────────────

  describe("invalidateMlflow", () => {
    it("resets to pending and refetches fresh status", async () => {
      const mockStatus = vi.mocked(getMlflowStatus)
      mockStatus.mockResolvedValue(OK_LOCAL)

      useSettingsStore.getState().fetchMlflow()
      await vi.waitFor(() => {
        expect(useSettingsStore.getState().mlflow.status).toBe("connected")
      })

      mockStatus.mockResolvedValue({
        mlflow_installed: true,
        mlflow_importable: true,
        configured: true,
        mode: "server",
        destination: "http://localhost:5000",
        config_source: "toml",
        detail: "",
      })
      useSettingsStore.getState().invalidateMlflow()

      await vi.waitFor(() => {
        expect(useSettingsStore.getState().mlflow.mode).toBe("server")
      })
      expect(useSettingsStore.getState().mlflow.destination).toBe("http://localhost:5000")
      expect(useSettingsStore.getState().mlflow.configSource).toBe("toml")
    })

    it("invalidation during an in-flight fetch results in exactly one follow-up fetch", async () => {
      const mockStatus = vi.mocked(getMlflowStatus)
      let resolveFirst: (value: MlflowStatusResponse) => void
      mockStatus.mockReturnValueOnce(
        new Promise<MlflowStatusResponse>((resolve) => {
          resolveFirst = resolve
        }),
      )
      mockStatus.mockResolvedValue({
        ...OK_LOCAL,
        mode: "server",
        destination: "http://localhost:5000",
        config_source: "toml",
      })

      useSettingsStore.getState().fetchMlflow()
      useSettingsStore.getState().invalidateMlflow() // while first fetch is in flight

      resolveFirst!(OK_LOCAL)

      await vi.waitFor(() => {
        expect(useSettingsStore.getState().mlflow.mode).toBe("server")
      })
      expect(mockStatus).toHaveBeenCalledTimes(2)
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
  // MLflow 5-second timeout race
  // Catches: if the 5s timeout is removed, a hung MLflow check would
  // block the UI indefinitely with status "pending" / spinner.
  // ────────────────────────────────────────────────────────────────

  describe("MLflow 5s timeout", () => {
    beforeEach(() => {
      vi.useFakeTimers()
    })

    afterEach(() => {
      vi.useRealTimers()
    })

    it("times out and sets error status when the status request hangs for >5s", async () => {
      const mockStatus = vi.mocked(getMlflowStatus)
      // Return a promise that never resolves
      mockStatus.mockReturnValue(new Promise(() => {}))

      useSettingsStore.getState().fetchMlflow()

      // Advance past the 5s timeout
      vi.advanceTimersByTime(5_001)

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
    })

    it("succeeds before the timeout if the status request resolves quickly", async () => {
      const mockStatus = vi.mocked(getMlflowStatus)
      mockStatus.mockResolvedValue(OK_LOCAL)

      useSettingsStore.getState().fetchMlflow()

      // Let the resolved promise flush
      await vi.waitFor(() => {
        expect(useSettingsStore.getState().mlflow.status).toBe("connected")
      })

      // The timeout should not overwrite the connected status even if it fires later
      vi.advanceTimersByTime(6_000)
      expect(useSettingsStore.getState().mlflow.status).toBe("connected")
    })
  })
})
