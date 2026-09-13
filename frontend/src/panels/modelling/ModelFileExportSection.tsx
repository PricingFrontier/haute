/**
 * "Model file" section of the modelling Export pane: writes a copy of the
 * last trained model and its feature contract to a file in the project.
 *
 * It follows a file Data Output's flow with a `models/` folder in place of
 * `outputs/`: a required filename or path, the server-resolved destination
 * before saving, and an explicit confirmation before replacing a file.
 */
import { useEffect, useState } from "react"
import { HardDriveDownload, Loader2 } from "lucide-react"
import { resolveModelSaveDestination, saveTrainedModel } from "../../api/client"
import { apiErrorCode, apiErrorMessage } from "../../api/errors"
import type {
  ModelFileExportReceipt,
  ModelSaveDestinationResponse,
  SaveModelResponse,
} from "../../api/types"
import { MODEL_COLORS } from "../../theme/colors"
import { configField } from "../../utils/configField"
import type { OnUpdateConfig } from "../editors"
import PathPickerField from "../editors/shared/PathPickerField"
import { FieldHelpIcon } from "./FieldHelpIcon"
import { modelFileExtension, type ExportableAlgorithm } from "./modelExport"

type DestinationState = {
  path: string
  response?: ModelSaveDestinationResponse
  error?: string
}

/** Every outcome remembers the path it was for, so editing the path hides it. */
type SaveState =
  | { phase: "saving"; path: string }
  | { phase: "confirm_overwrite"; path: string; message: string }
  | { phase: "success"; path: string; response: SaveModelResponse }
  | { phase: "error"; path: string; message: string }

type ModelFileExportSectionProps = {
  /** The completed job to save, or null when no trained model is exportable. */
  trainJobId: string | null
  algorithm: ExportableAlgorithm
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  /** The job's most recent recorded save, if it was saved before. */
  lastReceipt?: ModelFileExportReceipt | null
  /** Called after a successful save so the parent can re-read the job's receipts. */
  onSaved?: () => void
}

export function ModelFileExportSection({
  trainJobId,
  algorithm,
  config,
  onUpdate,
  lastReceipt = null,
  onSaved,
}: ModelFileExportSectionProps) {
  const path = configField(config, "model_export_path", "")
  const extension = modelFileExtension(algorithm)
  const [destinationState, setDestinationState] = useState<DestinationState>()
  const [saveState, setSaveState] = useState<SaveState>()

  useEffect(() => {
    if (!path) return
    const controller = new AbortController()
    resolveModelSaveDestination({ output_path: path, algorithm }, { signal: controller.signal }).then(
      (response) => {
        if (!controller.signal.aborted) setDestinationState({ path, response })
      },
      (caught: unknown) => {
        if (controller.signal.aborted) return
        setDestinationState({ path, error: apiErrorMessage(caught, "Could not resolve destination.") })
      },
    )
    return () => controller.abort()
  }, [algorithm, path])

  const destination = path && destinationState?.path === path ? destinationState : undefined
  const saving = saveState?.phase === "saving"
  const visibleSave = saveState && (saving || saveState.path === path) ? saveState : undefined
  const suffixMismatch = destination?.response?.suffix_mismatch === true

  const save = async (overwrite: boolean) => {
    if (!trainJobId || !path || saving) return
    if (overwrite && visibleSave?.phase !== "confirm_overwrite") return
    setSaveState({ phase: "saving", path })
    try {
      const response = await saveTrainedModel({ job_id: trainJobId, output_path: path, overwrite })
      setSaveState({ phase: "success", path, response })
      onSaved?.()
    } catch (caught) {
      const message = apiErrorMessage(caught, "Model save failed.")
      setSaveState(
        apiErrorCode(caught) === "model_file_exists"
          ? { phase: "confirm_overwrite", path, message }
          : { phase: "error", path, message },
      )
    }
  }

  return (
    <section className="space-y-2" aria-labelledby="model-file-heading">
      <div className="flex items-center gap-1">
        <h3
          id="model-file-heading"
          className="text-[11px] font-bold uppercase tracking-[0.08em]"
          style={{ color: "var(--text-muted)" }}
        >
          Model file
        </h3>
        <FieldHelpIcon
          label="Writes a copy of the last trained model and its feature contract to a file in this project."
          ariaLabel="About saving the model to a file"
        />
      </div>
      <PathPickerField
        label="Filename or path *"
        description={
          "Filenames save in the project's models/ folder. Paths are relative to the project root. " +
          `The model's ${extension} extension is added if omitted.`
        }
        value={path}
        onSelect={(next) => onUpdate("model_export_path", next)}
        extensions={extension}
        manualEntry
        testIdPrefix="model-file-path"
      />

      {destination?.response && (
        <p className="text-xs" style={{ color: "var(--text-muted)" }}>
          Destination: {destination.response.path}
        </p>
      )}
      {suffixMismatch && (
        <p role="alert" className="text-xs" style={{ color: "var(--danger-text)" }}>
          The destination extension does not match the model format ({extension}).
        </p>
      )}
      {destination?.error && (
        <p role="alert" className="text-xs" style={{ color: "var(--danger-text)" }}>
          Could not resolve destination: {destination.error}
        </p>
      )}

      <button
        type="button"
        onClick={() => void save(false)}
        disabled={!trainJobId || !path || saving || suffixMismatch}
        className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-xs font-medium transition-colors disabled:opacity-60"
        style={{
          background: saving ? "var(--chrome-hover)" : "var(--accent-soft-strong)",
          color: saving ? "var(--text-muted)" : MODEL_COLORS.logAction,
          border: "1px solid var(--accent-ring)",
        }}
      >
        {saving ? <Loader2 size={14} className="animate-spin" /> : <HardDriveDownload size={14} />}
        {saving ? "Saving..." : "Save model to file"}
      </button>

      {lastReceipt && !visibleSave && (
        <p data-testid="model-file-last-saved" className="text-[10px] break-all" style={{ color: "var(--text-muted)" }}>
          Last saved to {lastReceipt.path}
        </p>
      )}
      {visibleSave?.phase === "confirm_overwrite" && (
        <div className="space-y-1">
          <p role="alert" className="text-xs" style={{ color: "var(--danger-text)" }}>
            {visibleSave.message}
          </p>
          <button
            type="button"
            onClick={() => void save(true)}
            className="px-3 py-1.5 rounded-lg text-xs font-medium"
            style={{ background: "var(--danger)", color: "white" }}
          >
            Replace existing file
          </button>
        </div>
      )}
      {visibleSave?.phase === "success" && (
        <div
          role="status"
          className="px-3 py-2 rounded-lg text-xs space-y-1 break-all"
          style={{ background: "var(--accent-soft-subtle)", border: "1px solid var(--accent-soft-hover)" }}
        >
          <div style={{ color: MODEL_COLORS.logAction }}>Saved model to {visibleSave.response.path}</div>
          <div style={{ color: "var(--text-muted)" }}>
            Feature contract: {visibleSave.response.feature_contract_path}
          </div>
        </div>
      )}
      {visibleSave?.phase === "error" && (
        <div
          role="alert"
          className="px-3 py-2 rounded-lg text-xs break-words"
          style={{ background: "var(--danger-soft-subtle)", border: "1px solid var(--danger-border)", color: "var(--danger-text-soft)" }}
        >
          {visibleSave.message}
        </div>
      )}
    </section>
  )
}
