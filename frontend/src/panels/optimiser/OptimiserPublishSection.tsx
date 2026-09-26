/**
 * The Export pane's Publish section: save the node's last solve (or one of its
 * frontier points) as the artifact an Apply Optimisation node loads, or log it
 * to MLflow.
 *
 * The target is the result store's selected frontier point, the same
 * selection the result preview's frontier chart shows. State lives in the
 * shared publish store, so the frontier detail card's shortcuts and this
 * section report the same progress and receipts.
 */

import { useState } from "react"
import { AlertTriangle, Download, Loader2, Save, Upload } from "lucide-react"
import { selectFrontierPoint as selectFrontierPointApi } from "../../api/client"
import { apiErrorMessage } from "../../api/errors"
import { CommittedTextField } from "../../components/form"
import {
  captureDocumentExecutionFence,
  isDocumentExecutionFenceCurrent,
} from "../../stores/useDocumentStatusStore"
import useNodeResultsStore from "../../stores/useNodeResultsStore"
import useOptimiserPublishStore, { usePublishState } from "../../stores/useOptimiserPublishStore"
import { useMlflowDestinations } from "../../stores/useSettingsStore"
import useUIStore from "../../stores/useUIStore"
import { configField } from "../../utils/configField"
import { formatNumber } from "../../utils/formatValue"
import { mlflowLogAvailability } from "../../utils/mlflowDestinations"
import { NODE_TYPES } from "../../utils/nodeTypes"
import { downloadTextFile } from "../editors/shared/tableClipboard"
import type { OnUpdateConfig, OnUpdateConfigResult } from "../editors"
import { useGraph } from "../useGraph"
import { optimiserResultSavePath } from "./optimiserHelpers"
import { factorTablesCsv, hasFactorTables } from "./ratebookFactorTables"

const SECTION_LABEL_CLASS = "text-[11px] font-bold uppercase tracking-[0.08em]"
const INPUT_STYLE = { background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }

type OptimiserPublishSectionProps = {
  nodeId: string
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  /** The node config changed since the solve that produced this result. */
  isStale: boolean
  solving: boolean
  /** Writes config keys onto another node (the chosen Apply Optimisation node). */
  onUpdateNodeConfig?: (nodeId: string, patch: Record<string, unknown>) => OnUpdateConfigResult
}

function ActionButton({
  onClick,
  disabled,
  busy,
  icon,
  label,
  tone,
}: {
  onClick: () => void
  disabled: boolean
  busy: boolean
  icon: React.ReactNode
  label: string
  tone: "save" | "log"
}) {
  const enabledStyle = tone === "save"
    ? { background: "var(--warning-soft-emphasis)", color: "var(--warning-strong)" }
    : { background: "var(--accent-soft)", color: "var(--accent)" }
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium transition-colors disabled:opacity-60"
      style={disabled ? { background: "var(--chrome-hover)", color: "var(--text-muted)" } : enabledStyle}
    >
      {busy ? <Loader2 size={12} className="animate-spin" /> : icon}
      {label}
    </button>
  )
}

export default function OptimiserPublishSection({
  nodeId,
  config,
  onUpdate,
  isStale,
  solving,
  onUpdateNodeConfig,
}: OptimiserPublishSectionProps) {
  const { allNodes } = useGraph()
  const cached = useNodeResultsStore((s) => s.solveResults[nodeId])
  const selectPoint = useNodeResultsStore((s) => s.selectFrontierPoint)
  const recordPointSummary = useNodeResultsStore((s) => s.recordFrontierPointSummary)
  const jobId = cached && !cached.error ? cached.jobId : null
  const publish = usePublishState(nodeId, jobId)
  const save = useOptimiserPublishStore((s) => s.save)
  const log = useOptimiserPublishStore((s) => s.log)
  const dismissOverwrite = useOptimiserPublishStore((s) => s.dismissOverwrite)
  const mlflowInventory = useMlflowDestinations()
  const setMlflowSettingsOpen = useUIStore((s) => s.setMlflowSettingsOpen)
  const [version, setVersion] = useState("")
  const [applyNodeId, setApplyNodeId] = useState("")
  const [applyMessage, setApplyMessage] = useState<string | null>(null)
  const [tablesLoad, setTablesLoad] = useState<{ key: string; status: "loading" | "error"; error?: string } | null>(null)

  const nodeLabel = allNodes.find((node) => node.id === nodeId)?.data.label ?? "optimiser"
  const defaultPath = optimiserResultSavePath(nodeLabel, nodeId)
  const outputPath = configField(config, "result_export_path", "")
  const mlflowDestination = configField(config, "mlflow_destination", "")
  const mlflowExperiment = configField(config, "mlflow_experiment", "")
  const availability = mlflowLogAvailability(mlflowInventory, mlflowDestination)

  return (
    <section className="space-y-2" aria-labelledby="optimiser-publish-heading">
      <h3 id="optimiser-publish-heading" className={SECTION_LABEL_CLASS} style={{ color: "var(--text-muted)" }}>
        Publish
      </h3>
      {!cached || !jobId ? (
        <p className="text-xs" style={{ color: "var(--text-muted)" }}>
          Nothing to publish yet. Run the optimiser from the Solve pane; its result can then be saved
          for an Apply Optimisation node or logged to MLflow.
        </p>
      ) : (
        renderPublishBody()
      )}
    </section>
  )

  function renderPublishBody() {
    if (!cached || !jobId || cached.result === null) return null
    const points = cached.frontier?.points ?? []
    const target = cached.selectedPointIndex
    const busy = solving || !!publish?.saving || !!publish?.logging
    const isRatebook = cached.originalResult.mode === "ratebook"
    const factorTables = isRatebook && hasFactorTables(cached.result.factor_tables)
      ? cached.result.factor_tables
      : null
    // The scenario range the solve scored. Every frontier point shares its
    // solve's grid, so the solve's collar is the collar of whichever target is
    // published; the Apply node clips the combined factor to it.
    const collar = isRatebook ? cached.originalResult.combined_factor_bounds : null
    // A frontier point's tables arrive only when it is materialised; the Rates
    // tab does that on view, and Export can ask for them here.
    const tablesKey = `${jobId}:${target ?? "solved"}`
    // The reply is stored against the job and point that asked for it; the
    // selection is never changed by it.
    const loadPointTables = async () => {
      if (target === null) return
      const pointIndex = target
      const documentFence = captureDocumentExecutionFence()
      setTablesLoad({ key: tablesKey, status: "loading" })
      try {
        const response = await selectFrontierPointApi({ job_id: jobId, point_index: pointIndex, include_ratebook_tables: true })
        if (!isDocumentExecutionFenceCurrent(documentFence)) return
        recordPointSummary(nodeId, jobId, pointIndex, response)
        setTablesLoad((current) => (current?.key === tablesKey ? null : current))
      } catch (error) {
        setTablesLoad({ key: tablesKey, status: "error", error: apiErrorMessage(error, "The request failed.") })
      }
    }
    const pointTablesLoad = tablesLoad?.key === tablesKey ? tablesLoad : null
    const applyNodes = allNodes.filter((node) => node.data.nodeType === NODE_TYPES.OPTIMISER_APPLY)
    const saveReceipt = publish?.saveReceipt ?? null
    const logReceipt = publish?.logReceipt ?? null
    const targetLabel = (pointIndex: number | null) => (pointIndex === null ? "the solved result" : `frontier point ${pointIndex + 1}`)

    const runSave = () => save({
      nodeId,
      jobId,
      pointIndex: target,
      outputPath: outputPath || defaultPath,
      version: version.trim(),
      stale: isStale,
    })
    // The confirmation belongs to the refused request: it applies only while the
    // form still names the same file and result, and Replace resends exactly that.
    const overwritePrompt = publish?.overwritePrompt
      && publish.overwritePrompt.request.outputPath === (outputPath || defaultPath)
      && publish.overwritePrompt.request.pointIndex === target
      ? publish.overwritePrompt
      : null
    const runLog = () => log({
      nodeId,
      jobId,
      pointIndex: target,
      destination: mlflowDestination,
      experimentName: mlflowExperiment,
      stale: isStale,
    })
    const pointApplyNodeAtSave = () => {
      if (!saveReceipt || !applyNodeId || !onUpdateNodeConfig) return
      const result = onUpdateNodeConfig(applyNodeId, { sourceType: "file", artifact_path: saveReceipt.apply_path })
      const applyLabel = applyNodes.find((node) => node.id === applyNodeId)?.data.label ?? applyNodeId
      setApplyMessage(result.ok ? `${applyLabel} now loads ${saveReceipt.apply_path}.` : result.error)
    }

    return (
      <div className="space-y-3">
        <div>
          <label htmlFor={`${nodeId}-publish-target`} className="text-[11px]" style={{ color: "var(--text-muted)" }}>
            Result to publish
          </label>
          <select
            id={`${nodeId}-publish-target`}
            value={target === null ? "" : String(target)}
            onChange={(event) => selectPoint(nodeId, event.target.value === "" ? null : Number(event.target.value))}
            disabled={busy}
            className="w-full mt-0.5 px-2.5 py-1.5 rounded-lg text-xs"
            style={INPUT_STYLE}
          >
            <option value="">Solved result</option>
            {points.map((point, index) => (
              <option key={index} value={String(index)}>
                {`Frontier point ${index + 1} (objective ${formatNumber(point.total_objective)})`}
              </option>
            ))}
          </select>
        </div>

        {isStale && (
          <div
            role="alert"
            className="flex items-start gap-2 px-3 py-2 rounded-lg text-xs"
            style={{ background: "var(--warning-soft)", border: "1px solid var(--warning-border)", color: "var(--warning)" }}
          >
            <AlertTriangle size={12} className="mt-0.5 shrink-0" style={{ color: "var(--warning-strong)" }} />
            <span>The configuration has changed since this result was solved. Publishing records it as outdated.</span>
          </div>
        )}

        <div className="grid grid-cols-[minmax(0,1fr)_120px] gap-2">
          <div>
            <label htmlFor={`${nodeId}-result-path`} className="text-[11px]" style={{ color: "var(--text-muted)" }}>
              File path
            </label>
            <CommittedTextField
              id={`${nodeId}-result-path`}
              type="text"
              placeholder={defaultPath}
              value={outputPath}
              onCommit={(next) => onUpdate("result_export_path", next.trim() || undefined)}
              className="w-full mt-0.5 px-2.5 py-1.5 rounded-lg text-xs font-mono"
              style={INPUT_STYLE}
            />
          </div>
          <div>
            <label htmlFor={`${nodeId}-result-version`} className="text-[11px]" style={{ color: "var(--text-muted)" }}>
              Version label
            </label>
            <input
              id={`${nodeId}-result-version`}
              type="text"
              placeholder="Automatic"
              value={version}
              onChange={(event) => setVersion(event.target.value)}
              className="w-full mt-0.5 px-2.5 py-1.5 rounded-lg text-xs font-mono"
              style={INPUT_STYLE}
            />
          </div>
        </div>

        <div className="flex flex-wrap gap-2">
          <ActionButton
            onClick={() => void runSave()}
            disabled={busy}
            busy={!!publish?.saving}
            icon={<Save size={12} />}
            label={isStale ? "Save outdated result" : "Save to file"}
            tone="save"
          />
          <ActionButton
            onClick={() => void runLog()}
            disabled={busy || !availability.available}
            busy={!!publish?.logging}
            icon={<Upload size={12} />}
            label={isStale ? "Log outdated result" : "Log to MLflow"}
            tone="log"
          />
          {factorTables && collar && (
            <ActionButton
              onClick={() => downloadTextFile(factorTablesCsv(factorTables, collar), `${nodeLabel}_factor_tables.csv`, "text/csv")}
              disabled={false}
              busy={false}
              icon={<Download size={12} />}
              label="Download factor tables (CSV)"
              tone="save"
            />
          )}
          {isRatebook && !factorTables && target !== null && (
            <ActionButton
              onClick={() => void loadPointTables()}
              disabled={pointTablesLoad?.status === "loading"}
              busy={pointTablesLoad?.status === "loading"}
              icon={<Download size={12} />}
              label="Load factor tables for CSV"
              tone="save"
            />
          )}
        </div>
        {collar && (
          <p data-testid="optimiser-combined-factor-collar" className="text-xs" style={{ color: "var(--text-secondary)" }}>
            {`Combined factor collar [${collar.min}, ${collar.max}] — apply it in your rating engine. `}
            <span style={{ color: "var(--text-muted)" }}>
              The optimiser scored only this range, so the Apply Optimisation node clips each quote&apos;s product of
              factors to it. The CSV repeats it on every row.
            </span>
          </p>
        )}
        {isRatebook && !collar && (
          <div role="alert" className="px-3 py-2 rounded-lg text-xs" style={{ background: "var(--danger-soft)", color: "var(--danger)" }}>
            This ratebook result has no combined factor collar, so its factor tables cannot be published safely. Re-run
            the solve.
          </div>
        )}
        {pointTablesLoad?.status === "error" && (
          <div role="alert" className="px-3 py-2 rounded-lg text-xs" style={{ background: "var(--danger-soft)", color: "var(--danger)" }}>
            Factor tables could not be loaded: {pointTablesLoad.error}
          </div>
        )}
        {!availability.available && (
          <p className="text-[10px]" style={{ color: "var(--text-muted)" }}>
            {`${availability.reason} `}
            <button type="button" onClick={() => setMlflowSettingsOpen(true)} className="underline" style={{ color: "var(--text-accent)" }}>
              Configure MLflow
            </button>
          </p>
        )}

        {overwritePrompt && (
          <div role="alert" className="space-y-1.5 px-3 py-2 rounded-lg text-xs" style={{ background: "var(--warning-soft)", border: "1px solid var(--warning-border)", color: "var(--warning)" }}>
            <div>{overwritePrompt.message}</div>
            <div className="flex gap-2">
              <button type="button" onClick={() => void save({ ...overwritePrompt.request, overwrite: true })} className="px-2 py-0.5 rounded font-medium" style={{ background: "var(--warning-soft-emphasis)", color: "var(--warning-strong)" }}>
                Replace existing file
              </button>
              <button type="button" onClick={() => dismissOverwrite(nodeId, jobId)} className="px-2 py-0.5 rounded" style={{ color: "var(--text-muted)" }}>
                Cancel
              </button>
            </div>
          </div>
        )}
        {publish?.saveError && (
          <div role="alert" className="px-3 py-2 rounded-lg text-xs" style={{ background: "var(--danger-soft)", color: "var(--danger)" }}>
            Save failed: {publish.saveError}
          </div>
        )}
        {publish?.logError && (
          <div role="alert" className="px-3 py-2 rounded-lg text-xs space-y-1" style={{ background: "var(--danger-soft)", color: "var(--danger)" }}>
            <div>MLflow log failed: {publish.logError}</div>
            {(publish.logErrorCode === "mlflow_connectivity" || publish.logErrorCode === "mlflow_authentication") && (
              <button type="button" onClick={() => setMlflowSettingsOpen(true)} className="underline">
                Test connection in MLflow settings
              </button>
            )}
          </div>
        )}

        {saveReceipt && (
          <div data-testid="optimiser-save-receipt" className="space-y-1.5 px-3 py-2 rounded-lg text-xs" style={{ background: "var(--bg-panel)", border: "1px solid var(--border)" }}>
            <div style={{ color: "var(--text-secondary)" }}>
              {`Saved ${targetLabel(saveReceipt.pointIndex)} to `}
              <span className="font-mono" style={{ color: "var(--text-primary)" }}>{saveReceipt.apply_path}</span>
            </div>
            {applyNodes.length > 0 && onUpdateNodeConfig && (
              <div className="flex items-center gap-2">
                <select
                  aria-label="Apply Optimisation node"
                  value={applyNodeId}
                  onChange={(event) => { setApplyNodeId(event.target.value); setApplyMessage(null) }}
                  className="flex-1 min-w-0 px-2 py-1 rounded text-xs"
                  style={INPUT_STYLE}
                >
                  <option value="">Choose an Apply Optimisation node...</option>
                  {applyNodes.map((node) => <option key={node.id} value={node.id}>{node.data.label}</option>)}
                </select>
                <button
                  type="button"
                  onClick={pointApplyNodeAtSave}
                  disabled={!applyNodeId}
                  className="px-2 py-1 rounded text-xs font-medium disabled:opacity-50"
                  style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
                >
                  Use in Apply node
                </button>
              </div>
            )}
            {applyMessage && <div style={{ color: "var(--text-muted)" }}>{applyMessage}</div>}
          </div>
        )}
        {logReceipt && (
          <div data-testid="optimiser-log-receipt" className="px-3 py-2 rounded-lg text-xs" style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
            {`Logged ${targetLabel(logReceipt.pointIndex)}`}
            {logReceipt.experiment_name ? ` to ${logReceipt.experiment_name}` : ""}
            {`: run ${logReceipt.run_id}`}
            {logReceipt.run_url && (
              <div>
                <a href={logReceipt.run_url} target="_blank" rel="noreferrer" className="underline" style={{ color: "var(--text-accent)" }}>
                  {logReceipt.backend === "databricks" ? "Open in Databricks" : "Open run"}
                </a>
              </div>
            )}
          </div>
        )}
      </div>
    )
  }
}
