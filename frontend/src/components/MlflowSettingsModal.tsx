/**
 * MLflow tracking settings dialog (ModalShell-based).
 *
 * Fetches GET /api/mlflow/settings on mount, offers the three tracking
 * modes as plain-language cards, tests the connection on demand, and saves
 * through PUT /api/mlflow/settings followed by invalidateMlflow() so the
 * toolbar chip and every panel refresh without a reload. `folder` is always
 * sent empty — the backend persists the currently resolved local folder.
 */
import { useCallback, useEffect, useState } from "react"
import { CheckCircle2, Loader2, TriangleAlert } from "lucide-react"
import ModalShell from "./ModalShell"
import {
  ApiError,
  getMlflowSettings,
  putMlflowSettings,
  testMlflowConnection,
} from "../api/client"
import type {
  MlflowSettingsResponse,
  MlflowTestConnectionResponse,
} from "../api/types"
import useSettingsStore from "../stores/useSettingsStore"

type TrackingMode = "local" | "server" | "databricks"

const MODE_CARDS: { key: TrackingMode; title: string; description: string }[] = [
  {
    key: "local",
    title: "Local folder",
    description: "Zero setup — runs are saved to a folder in your project.",
  },
  {
    key: "server",
    title: "MLflow server",
    description: "Connect to a running MLflow server by URL.",
  },
  {
    key: "databricks",
    title: "Databricks",
    description: "Uses the workspace credentials from your .env file.",
  },
]

const MODE_NAMES: Record<TrackingMode, string> = {
  local: "Local folder",
  server: "MLflow server",
  databricks: "Databricks",
}

const SOURCE_NAMES: Record<string, string> = {
  toml: "from haute.toml",
  env: "from environment",
  default: "default",
}

function describeResolution(settings: MlflowSettingsResponse): string {
  if (!settings.resolved) return ""
  const { mode, destination, config_source } = settings.resolved
  return `${MODE_NAMES[mode]} — ${destination} (${SOURCE_NAMES[config_source] ?? config_source})`
}

function initialMode(settings: MlflowSettingsResponse): TrackingMode {
  if (settings.mode === "local" || settings.mode === "server" || settings.mode === "databricks") {
    return settings.mode
  }
  return settings.resolved?.mode ?? "local"
}

export default function MlflowSettingsModal({ onClose }: { onClose: () => void }) {
  const invalidateMlflow = useSettingsStore((s) => s.invalidateMlflow)

  const [settings, setSettings] = useState<MlflowSettingsResponse | null>(null)
  const [loadError, setLoadError] = useState("")
  const [selectedMode, setSelectedMode] = useState<TrackingMode>("local")
  const [serverUri, setServerUri] = useState("")
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState("")
  const [saved, setSaved] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<MlflowTestConnectionResponse | null>(null)

  useEffect(() => {
    let cancelled = false
    getMlflowSettings()
      .then((data) => {
        if (cancelled) return
        setSettings(data)
        setSelectedMode(initialMode(data))
        setServerUri(
          data.tracking_uri
            || (data.resolved?.mode === "server" ? data.resolved.destination : ""),
        )
      })
      .catch((e: unknown) => {
        if (cancelled) return
        setLoadError(e instanceof Error ? e.message : "Failed to load MLflow settings")
      })
    return () => {
      cancelled = true
    }
  }, [])

  const handleSave = useCallback(async () => {
    if (saving) return
    setSaving(true)
    setSaveError("")
    setSaved(false)
    try {
      const updated = await putMlflowSettings({
        mode: selectedMode,
        tracking_uri: selectedMode === "server" ? serverUri.trim() : "",
        folder: "",
      })
      setSettings(updated)
      setSaved(true)
      invalidateMlflow()
    } catch (e: unknown) {
      setSaveError(
        e instanceof ApiError && e.detail
          ? e.detail
          : e instanceof Error
            ? e.message
            : "Saving MLflow settings failed",
      )
    } finally {
      setSaving(false)
    }
  }, [saving, selectedMode, serverUri, invalidateMlflow])

  const handleTest = useCallback(async () => {
    if (testing) return
    setTesting(true)
    setTestResult(null)
    try {
      setTestResult(await testMlflowConnection())
    } catch (e: unknown) {
      setTestResult({
        ok: false,
        category: "unknown",
        detail: e instanceof Error ? e.message : "Connection test failed",
      })
    } finally {
      setTesting(false)
    }
  }, [testing])

  const localFolderDisplay =
    settings?.resolved?.mode === "local"
      ? settings.resolved.destination
      : settings?.folder || "mlruns/ in your project (default)"

  return (
    <ModalShell ariaLabel="MLflow settings" onClose={onClose} width="w-[440px]" testId="mlflow-settings-modal">
      <div className="px-4 py-3" style={{ borderBottom: "1px solid var(--border)" }}>
        <h2 className="text-sm font-bold" style={{ color: "var(--text-primary)" }}>
          MLflow tracking
        </h2>
        {settings && settings.resolved && (
          <p className="mt-1 text-[11px]" style={{ color: "var(--text-muted)" }}>
            Currently: {describeResolution(settings)}
          </p>
        )}
        {settings && !settings.resolved && settings.detail && (
          <p className="mt-1 flex items-center gap-1 text-[11px]" style={{ color: "var(--warning-strong)" }}>
            <TriangleAlert size={12} aria-hidden="true" />
            {settings.detail}
          </p>
        )}
        {loadError && (
          <p className="mt-1 text-[11px]" style={{ color: "var(--danger)" }}>{loadError}</p>
        )}
      </div>

      <div className="px-4 py-3 space-y-2">
        {!settings && !loadError && (
          <div className="flex items-center gap-2 text-xs" style={{ color: "var(--text-muted)" }}>
            <Loader2 size={14} className="animate-spin" aria-hidden="true" />
            Loading settings…
          </div>
        )}
        {settings && (
          <div role="radiogroup" aria-label="Tracking destination" className="space-y-2">
            {MODE_CARDS.map((card) => {
              const selected = selectedMode === card.key
              return (
                <button
                  key={card.key}
                  role="radio"
                  aria-checked={selected}
                  onClick={() => {
                    setSelectedMode(card.key)
                    setSaved(false)
                  }}
                  className="w-full rounded-lg px-3 py-2 text-left"
                  style={{
                    background: selected ? "var(--accent-soft-subtle)" : "var(--bg-input)",
                    border: `1px solid ${selected ? "var(--accent-ring)" : "var(--border)"}`,
                  }}
                >
                  <span className="block text-xs font-semibold" style={{ color: "var(--text-primary)" }}>
                    {card.title}
                  </span>
                  <span className="block text-[11px]" style={{ color: "var(--text-muted)" }}>
                    {card.description}
                  </span>
                  {card.key === "local" && (
                    <span className="mt-0.5 block break-all font-mono text-[10px]" style={{ color: "var(--text-muted)" }}>
                      {localFolderDisplay}
                    </span>
                  )}
                </button>
              )
            })}
          </div>
        )}
        {settings && selectedMode === "server" && (
          <label className="block text-[11px]" style={{ color: "var(--text-muted)" }}>
            Server URL
            <input
              type="text"
              aria-label="Server URL"
              value={serverUri}
              onChange={(e) => {
                setServerUri(e.target.value)
                setSaved(false)
              }}
              placeholder="http://localhost:5000"
              className="mt-0.5 w-full rounded-lg px-2.5 py-1.5 font-mono text-xs"
              style={{
                background: "var(--bg-input)",
                border: "1px solid var(--border)",
                color: "var(--text-primary)",
              }}
            />
          </label>
        )}
        {saveError && (
          <p className="rounded-lg px-3 py-2 text-[11px]" style={{ background: "var(--danger-soft-subtle)", border: "1px solid var(--danger-border)", color: "var(--danger-text-soft)" }}>
            {saveError}
          </p>
        )}
        {saved && !saveError && (
          <p className="flex items-center gap-1 text-[11px]" style={{ color: "var(--success)" }}>
            <CheckCircle2 size={12} aria-hidden="true" />
            Saved.
          </p>
        )}
        {testResult && (
          <p
            className="flex items-center gap-1 text-[11px]"
            style={{ color: testResult.ok ? "var(--success)" : "var(--warning-strong)" }}
          >
            {testResult.ok ? (
              <>
                <CheckCircle2 size={12} aria-hidden="true" />
                Connection OK
              </>
            ) : (
              <>
                <TriangleAlert size={12} aria-hidden="true" />
                {testResult.detail || `Connection test failed (${testResult.category}).`}
              </>
            )}
          </p>
        )}
      </div>

      <div className="flex items-center gap-2 px-4 py-3" style={{ borderTop: "1px solid var(--border)" }}>
        <button
          onClick={handleTest}
          disabled={testing || !settings}
          className="rounded-lg px-3 py-1.5 text-xs"
          style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}
        >
          {testing ? "Testing…" : "Test connection"}
        </button>
        <div className="flex-1" />
        <button
          onClick={onClose}
          className="rounded-lg px-3 py-1.5 text-xs"
          style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}
        >
          Close
        </button>
        <button
          onClick={handleSave}
          disabled={saving || !settings || (selectedMode === "server" && !serverUri.trim())}
          className="rounded-lg px-3 py-1.5 text-xs font-medium"
          style={{ background: "var(--accent-soft-strong)", border: "1px solid var(--accent-ring)", color: "var(--text-accent)" }}
        >
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </ModalShell>
  )
}
