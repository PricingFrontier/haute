/**
 * MLflow destinations inventory editor (ModalShell-based).
 *
 * The workspace offers up to three tracking destinations and each node picks
 * one; this modal edits the two the workspace can configure — the MLflow
 * server URL and the local folder — and reports what Databricks resolves to
 * read-only, since that configuration lives in the environment. It fetches
 * `GET /api/mlflow/settings` on mount, reads the inventory from the settings
 * store, tests each remote on demand, and saves through
 * `PUT /api/mlflow/settings` followed by `invalidateMlflow()` so every node's
 * selector refreshes without a reload.
 *
 * No credential is ever rendered or submitted: stored server URLs are
 * credential-free by backend validation, inventory destinations arrive
 * pre-redacted, and the modal adds no credential inputs.
 *
 * Per `specs/frontend-shared/low-level.md` ("MLflow settings modal").
 */
import { useCallback, useEffect, useRef, useState } from "react"
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
import useSettingsStore, { useMlflowDestinations } from "../stores/useSettingsStore"
import {
  mlflowDestinationEntry,
  type MlflowInventoryState,
} from "../utils/mlflowDestinations"

/** The two destinations that can actually be probed. */
type RemoteKey = "server" | "databricks"

const DATABRICKS_PROFILE_PREFIX = "databricks://"

/**
 * What Databricks resolves to, in words: the selected profile, the detected
 * host, or what is missing. Never a credential — the inventory's destination
 * is already the secret-free display form.
 */
function describeDatabricks(state: MlflowInventoryState): string {
  const entry = mlflowDestinationEntry(state.destinations, "databricks")
  if (!entry) {
    // No entry yet (still fetching) or none at all (the fetch failed): the
    // store's own detail is the only reason anybody has.
    return state.status === "loading" ? "Checking Databricks…" : state.detail
  }
  if (!entry.configured) return entry.detail
  if (entry.destination.startsWith(DATABRICKS_PROFILE_PREFIX)) {
    const profile = entry.destination.slice(DATABRICKS_PROFILE_PREFIX.length)
    return `Profile: ${profile} (from MLFLOW_TRACKING_URI)`
  }
  return `Host: ${entry.destination} (from DATABRICKS_HOST)`
}

function TestResult({ result, testId }: { result: MlflowTestConnectionResponse; testId: string }) {
  return (
    <p
      data-testid={testId}
      className="mt-1 flex items-center gap-1 text-[11px]"
      style={{ color: result.ok ? "var(--success)" : "var(--warning-strong)" }}
    >
      {result.ok ? (
        <>
          <CheckCircle2 size={12} aria-hidden="true" />
          Connection OK
        </>
      ) : (
        <>
          <TriangleAlert size={12} aria-hidden="true" />
          {result.detail || `Connection test failed (${result.category}).`}
        </>
      )}
    </p>
  )
}

export default function MlflowSettingsModal({ onClose }: { onClose: () => void }) {
  const state = useMlflowDestinations()
  const fetchMlflow = useSettingsStore((s) => s.fetchMlflow)
  const invalidateMlflow = useSettingsStore((s) => s.invalidateMlflow)

  const [settings, setSettings] = useState<MlflowSettingsResponse | null>(null)
  const [loadError, setLoadError] = useState("")
  const [serverDraft, setServerDraft] = useState("")
  const [folderDraft, setFolderDraft] = useState("")
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState("")
  const [saved, setSaved] = useState(false)
  const [testing, setTesting] = useState<Record<RemoteKey, boolean>>({
    server: false,
    databricks: false,
  })
  const [results, setResults] = useState<Record<RemoteKey, MlflowTestConnectionResponse | null>>({
    server: null,
    databricks: null,
  })
  // A test result belongs to the draft it probed, per remote: any draft edit
  // bumps both sequences so a completion for the old target is discarded.
  const testSeqRef = useRef<Record<RemoteKey, number>>({ server: 0, databricks: 0 })

  const inventoryLoading = state.status === "loading"
  useEffect(() => {
    if (inventoryLoading) fetchMlflow()
  }, [inventoryLoading, fetchMlflow])

  useEffect(() => {
    let cancelled = false
    getMlflowSettings()
      .then((data) => {
        if (cancelled) return
        setSettings(data)
        setServerDraft(data.tracking_uri)
        setFolderDraft(data.folder)
      })
      .catch((e: unknown) => {
        if (cancelled) return
        setLoadError(e instanceof Error ? e.message : "Failed to load MLflow settings")
      })
    return () => {
      cancelled = true
    }
  }, [])

  /** A draft edit invalidates every displayed result and the save receipt. */
  const handleDraftEdit = useCallback(() => {
    testSeqRef.current = {
      server: testSeqRef.current.server + 1,
      databricks: testSeqRef.current.databricks + 1,
    }
    setResults({ server: null, databricks: null })
    setSaved(false)
  }, [])

  const handleTest = useCallback(
    async (key: RemoteKey) => {
      if (testing[key] || saving) return
      setTesting((prev) => ({ ...prev, [key]: true }))
      setResults((prev) => ({ ...prev, [key]: null }))
      const seq = (testSeqRef.current[key] += 1)
      try {
        // The server is probed against the draft, not the saved value;
        // Databricks has nothing to draft, so the key alone identifies it.
        const result = await testMlflowConnection(
          key === "server"
            ? { destination: "server", tracking_uri: serverDraft.trim() }
            : { destination: "databricks" },
        )
        if (seq === testSeqRef.current[key]) setResults((prev) => ({ ...prev, [key]: result }))
      } catch (e: unknown) {
        if (seq === testSeqRef.current[key]) {
          setResults((prev) => ({
            ...prev,
            [key]: {
              ok: false,
              category: "unknown",
              detail: e instanceof Error ? e.message : "Connection test failed",
            },
          }))
        }
      } finally {
        setTesting((prev) => ({ ...prev, [key]: false }))
      }
    },
    [testing, saving, serverDraft],
  )

  const handleSave = useCallback(async () => {
    if (saving) return
    setSaving(true)
    setSaveError("")
    setSaved(false)
    try {
      const updated = await putMlflowSettings({
        tracking_uri: serverDraft.trim(),
        folder: folderDraft.trim(),
      })
      setSettings(updated)
      setServerDraft(updated.tracking_uri)
      setFolderDraft(updated.folder)
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
  }, [saving, serverDraft, folderDraft, invalidateMlflow])

  const fieldStyle = {
    background: "var(--bg-input)",
    border: "1px solid var(--border)",
    color: "var(--text-primary)",
  }
  const buttonStyle = {
    background: "var(--bg-input)",
    border: "1px solid var(--border)",
    color: "var(--text-secondary)",
  }

  return (
    <ModalShell
      ariaLabel="MLflow settings"
      onClose={onClose}
      width="w-[460px]"
      testId="mlflow-settings-modal"
    >
      <div className="px-4 py-3" style={{ borderBottom: "1px solid var(--border)" }}>
        <h2 className="text-sm font-bold" style={{ color: "var(--text-primary)" }}>
          MLflow destinations
        </h2>
        <p className="mt-1 text-[11px]" style={{ color: "var(--text-muted)" }}>
          Each node chooses where it logs. This is what the workspace offers.
        </p>
        {settings?.detail && (
          <p
            className="mt-1 flex items-center gap-1 text-[11px]"
            style={{ color: "var(--warning-strong)" }}
          >
            <TriangleAlert size={12} aria-hidden="true" />
            {settings.detail}
          </p>
        )}
        {/* A failed load leaves no drafts to edit — showing empty fields would
            invite a save that wipes a configuration nobody has seen. */}
        {loadError && (
          <p className="mt-1 text-[11px]" style={{ color: "var(--danger)" }}>
            {loadError}
          </p>
        )}
      </div>

      <div className="space-y-3 px-4 py-3">
        {!settings && !loadError && (
          <div className="flex items-center gap-2 text-xs" style={{ color: "var(--text-muted)" }}>
            <Loader2 size={14} className="animate-spin" aria-hidden="true" />
            Loading settings…
          </div>
        )}

        {settings && (
          <>
            <div
              data-testid="mlflow-databricks-block"
              className="rounded-lg px-3 py-2"
              style={{ background: "var(--bg-input)", border: "1px solid var(--border)" }}
            >
              <span className="block text-xs font-semibold" style={{ color: "var(--text-primary)" }}>
                Databricks
              </span>
              <span className="mt-0.5 block break-all text-[11px]" style={{ color: "var(--text-muted)" }}>
                {describeDatabricks(state)}
              </span>
              <button
                onClick={() => void handleTest("databricks")}
                disabled={testing.databricks || saving}
                className="mt-1.5 rounded-lg px-2.5 py-1 text-[11px]"
                style={buttonStyle}
              >
                {testing.databricks ? "Testing…" : "Test Databricks"}
              </button>
              {results.databricks && (
                <TestResult result={results.databricks} testId="mlflow-test-result-databricks" />
              )}
            </div>

            <div>
              <label
                htmlFor="mlflow-server-url"
                className="block text-[11px]"
                style={{ color: "var(--text-muted)" }}
              >
                MLflow server URL
              </label>
              <input
                id="mlflow-server-url"
                type="text"
                value={serverDraft}
                disabled={saving}
                onChange={(e) => {
                  setServerDraft(e.target.value)
                  handleDraftEdit()
                }}
                placeholder="http://localhost:5000"
                className="mt-0.5 w-full rounded-lg px-2.5 py-1.5 font-mono text-xs"
                style={fieldStyle}
              />
              <button
                onClick={() => void handleTest("server")}
                disabled={testing.server || saving}
                className="mt-1.5 rounded-lg px-2.5 py-1 text-[11px]"
                style={buttonStyle}
              >
                {testing.server ? "Testing…" : "Test server"}
              </button>
              {results.server && (
                <TestResult result={results.server} testId="mlflow-test-result-server" />
              )}
            </div>

            <div>
              <label
                htmlFor="mlflow-local-folder"
                className="block text-[11px]"
                style={{ color: "var(--text-muted)" }}
              >
                Local folder
              </label>
              <input
                id="mlflow-local-folder"
                type="text"
                value={folderDraft}
                disabled={saving}
                onChange={(e) => {
                  setFolderDraft(e.target.value)
                  handleDraftEdit()
                }}
                placeholder={settings.resolved_folder}
                className="mt-0.5 w-full rounded-lg px-2.5 py-1.5 font-mono text-xs"
                style={fieldStyle}
              />
              <span className="mt-0.5 block break-all text-[10px]" style={{ color: "var(--text-muted)" }}>
                Resolves to: {settings.resolved_folder}
              </span>
            </div>

            {saveError && (
              <p
                className="rounded-lg px-3 py-2 text-[11px]"
                style={{
                  background: "var(--danger-soft-subtle)",
                  border: "1px solid var(--danger-border)",
                  color: "var(--danger-text-soft)",
                }}
              >
                {saveError}
              </p>
            )}
            {saved && !saveError && (
              <p className="flex items-center gap-1 text-[11px]" style={{ color: "var(--success)" }}>
                <CheckCircle2 size={12} aria-hidden="true" />
                Saved.
              </p>
            )}
          </>
        )}
      </div>

      <div className="flex items-center gap-2 px-4 py-3" style={{ borderTop: "1px solid var(--border)" }}>
        <div className="flex-1" />
        <button onClick={onClose} className="rounded-lg px-3 py-1.5 text-xs" style={buttonStyle}>
          Close
        </button>
        {settings && (
          <button
            onClick={() => void handleSave()}
            disabled={saving}
            className="rounded-lg px-3 py-1.5 text-xs font-medium"
            style={{
              background: "var(--accent-soft-strong)",
              border: "1px solid var(--accent-ring)",
              color: "var(--text-accent)",
            }}
          >
            {saving ? "Saving…" : "Save"}
          </button>
        )}
      </div>
    </ModalShell>
  )
}
