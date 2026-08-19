import { AlertTriangle, FileCode2 } from "lucide-react"

import useDocumentStatusStore from "../stores/useDocumentStatusStore"

function revisionLabel(revision: string | null): string {
  if (!revision) return "unknown revision"
  return revision.length > 12 ? revision.slice(0, 12) : revision
}

export default function StalePipelineReferenceBanner() {
  const retainedCanvas = useDocumentStatusStore((state) => state.retainedCanvas)
  const currentRevision = useDocumentStatusStore((state) => state.sourceRevision)
  const sourceFile = useDocumentStatusStore((state) => state.sourceFile)
  const sourceText = useDocumentStatusStore((state) => state.sourceText)
  const diagnostics = useDocumentStatusStore((state) => state.diagnostics)
  const diagnosticsOmitted = useDocumentStatusStore((state) => state.diagnosticsOmitted)

  if (retainedCanvas === null) return null
  const localDirty = retainedCanvas.kind === "local_dirty"

  return (
    <section
      role="alert"
      data-testid="stale-pipeline-reference-banner"
      className="px-3 py-2 text-[12px]"
      style={{
        background: "var(--danger-soft)",
        color: "var(--danger-text)",
        borderBottom: "1px solid var(--danger-border)",
      }}
    >
      <div className="flex items-center gap-2 font-medium">
        <AlertTriangle size={14} aria-hidden="true" />
        <span className="flex-1">
          {localDirty
            ? "The current source cannot be reconstructed. Your unsaved local canvas is retained read-only"
            : `The current source cannot be reconstructed. Showing the last ${retainedCanvas.loadStatus} canvas as a stale read-only reference`}
          {` (canvas ${revisionLabel(retainedCanvas.sourceRevision)}; current ${revisionLabel(currentRevision)}). `}
          It cannot be saved or executed.
        </span>
      </div>
      <details className="mt-1">
        <summary className="cursor-pointer select-none text-[11px]">
          Review current source and diagnostics
        </summary>
        {diagnostics.length > 0 && (
          <ul className="mt-2 max-h-28 space-y-1 overflow-auto" aria-label="Current source diagnostics">
            {diagnostics.map((diagnostic) => (
              <li key={diagnostic.diagnostic_id}>
                {diagnostic.message}
                {diagnostic.source_span && ` (line ${diagnostic.source_span.start_line})`}
              </li>
            ))}
            {diagnosticsOmitted > 0 && <li>{diagnosticsOmitted} additional issues omitted.</li>}
          </ul>
        )}
        <div
          className="mt-2 flex items-center gap-2 text-[10px] font-mono"
          style={{ color: "var(--text-muted)" }}
        >
          <FileCode2 size={12} aria-hidden="true" />
          {sourceFile || "Pipeline source"}
        </div>
        <pre
          aria-label="Current pipeline source"
          className="mt-1 max-h-40 overflow-auto rounded p-2 text-[11px] leading-relaxed"
          style={{ background: "var(--bg-panel)", color: "var(--text-primary)" }}
        >
          <code>{sourceText}</code>
        </pre>
      </details>
    </section>
  )
}
