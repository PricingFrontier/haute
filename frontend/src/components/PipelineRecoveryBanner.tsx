import { AlertTriangle } from "lucide-react"

import useDocumentStatusStore from "../stores/useDocumentStatusStore"

interface PipelineRecoveryBannerProps {
  onSelectElement?: (elementId: string) => void
  onRemoveConnection?: (target: { sourceFile: string; recoveryId: string }) => void
}

/**
 * Diagnostics of a connection a connect() statement declares, which Remove
 * connection deletes. An ambiguous implicit binding is a parameter name, with
 * no statement to remove.
 */
const DECLARED_CONNECTION_CODES = new Set([
  "connection_declaration_invalid",
  "connection_duplicate",
  "connection_endpoint_missing",
  "connection_endpoint_ambiguous",
  "connection_frame_missing",
  "submodel_input_port_invalid",
  "submodel_output_port_invalid",
])

function diagnosticLocation(sourceFile: string | null, line: number | undefined): string | null {
  if (sourceFile === null) return line === undefined ? null : `line ${line}`
  return line === undefined ? sourceFile : `${sourceFile}:${line}`
}

export default function PipelineRecoveryBanner({
  onSelectElement,
  onRemoveConnection,
}: PipelineRecoveryBannerProps) {
  const loadStatus = useDocumentStatusStore((state) => state.loadStatus)
  const diagnostics = useDocumentStatusStore((state) => state.diagnostics)
  const diagnosticsOmitted = useDocumentStatusStore((state) => state.diagnosticsOmitted)

  if (loadStatus !== "degraded") return null
  const total = diagnostics.length + diagnosticsOmitted
  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="pipeline-recovery-banner"
      className="px-3 py-2 text-[12px] font-medium"
      style={{
        background: "var(--warning-soft-emphasis)",
        color: "var(--warning)",
        borderBottom: "1px solid var(--warning-border)",
      }}
    >
      <div className="flex items-center gap-2">
        <AlertTriangle size={14} aria-hidden="true" />
        <span className="flex-1">
          Pipeline opened in recovery mode. {total} {total === 1 ? "issue" : "issues"} must be
          resolved in source before editing, saving, or running the pipeline.
        </span>
        {diagnosticsOmitted > 0 && (
          <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>
            {diagnosticsOmitted} additional {diagnosticsOmitted === 1 ? "issue" : "issues"} omitted
          </span>
        )}
      </div>
      {diagnostics.length > 0 && (
        <details className="mt-1" data-testid="pipeline-issues-navigator">
          <summary className="cursor-pointer select-none text-[11px]">
            Review detected issues
          </summary>
          <ol className="mt-1 max-h-40 overflow-auto space-y-1" aria-label="Pipeline issues">
            {diagnostics.map((diagnostic) => {
              const location = diagnosticLocation(
                diagnostic.source_file,
                diagnostic.source_span?.start_line,
              )
              const canNavigate = diagnostic.element_id !== null && onSelectElement !== undefined
              const sourceFile = diagnostic.source_file
              const connectionId = DECLARED_CONNECTION_CODES.has(diagnostic.code)
                ? diagnostic.element_id
                : null
              return (
                <li key={diagnostic.diagnostic_id} className="flex items-start gap-2">
                  <button
                    type="button"
                    className="min-w-0 flex-1 rounded px-2 py-1 text-left hover-chrome-solid disabled:cursor-default"
                    disabled={!canNavigate}
                    onClick={() => {
                      if (diagnostic.element_id !== null) onSelectElement?.(diagnostic.element_id)
                    }}
                  >
                    <span className="block">{diagnostic.message}</span>
                    {(location !== null || diagnostic.remediation !== null) && (
                      <span className="block text-[10px]" style={{ color: "var(--text-muted)" }}>
                        {[location, diagnostic.remediation].filter(Boolean).join(" · ")}
                      </span>
                    )}
                  </button>
                  {onRemoveConnection && sourceFile !== null && connectionId !== null && (
                    <button
                      type="button"
                      className="shrink-0 rounded px-2 py-1 text-[11px] font-semibold"
                      style={{
                        color: "var(--danger-text)",
                        background: "var(--danger-soft)",
                        border: "1px solid var(--danger-border)",
                      }}
                      onClick={() => onRemoveConnection({ sourceFile, recoveryId: connectionId })}
                    >
                      Remove connection
                    </button>
                  )}
                </li>
              )
            })}
          </ol>
        </details>
      )}
    </div>
  )
}
