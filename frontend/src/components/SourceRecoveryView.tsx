import { AlertTriangle, FileCode2 } from "lucide-react"

import useDocumentStatusStore from "../stores/useDocumentStatusStore"

function location(sourceFile: string | null, line: number | undefined): string | null {
  if (!sourceFile) return null
  return line === undefined ? sourceFile : `${sourceFile}:${line}`
}

export default function SourceRecoveryView() {
  const sourceFile = useDocumentStatusStore((state) => state.sourceFile)
  const sourceText = useDocumentStatusStore((state) => state.sourceText)
  const diagnostics = useDocumentStatusStore((state) => state.diagnostics)
  const diagnosticsOmitted = useDocumentStatusStore((state) => state.diagnosticsOmitted)

  return (
    <main
      data-testid="source-recovery-view"
      className="flex-1 min-h-0 overflow-auto p-6"
      style={{ background: "var(--bg-base)" }}
    >
      <div className="mx-auto flex max-w-5xl flex-col gap-4">
        <section
          role="alert"
          className="rounded-xl p-4"
          style={{
            background: "var(--danger-soft)",
            border: "1px solid var(--danger-border)",
            color: "var(--danger-text)",
          }}
        >
          <div className="flex items-center gap-2 font-semibold">
            <AlertTriangle size={16} aria-hidden="true" />
            Pipeline source needs repair
          </div>
          <p className="mt-2 text-[12px] leading-relaxed">
            Haute could read this file but could not reconstruct a trustworthy graph. The current
            source is shown below and has not been changed.
          </p>
        </section>

        {diagnostics.length > 0 && (
          <section
            aria-label="Pipeline diagnostics"
            className="rounded-xl p-4"
            style={{ background: "var(--bg-panel)", border: "1px solid var(--border)" }}
          >
            <h2 className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>
              Diagnostics
            </h2>
            <ul className="mt-3 space-y-3">
              {diagnostics.map((diagnostic) => {
                const diagnosticLocation = location(
                  diagnostic.source_file,
                  diagnostic.source_span?.start_line,
                )
                return (
                  <li key={diagnostic.diagnostic_id} className="text-[12px] leading-relaxed">
                    <div style={{ color: "var(--danger-text)" }}>{diagnostic.message}</div>
                    {diagnosticLocation && (
                      <div className="mt-1 font-mono text-[10px]" style={{ color: "var(--text-muted)" }}>
                        {diagnosticLocation}
                      </div>
                    )}
                    {diagnostic.remediation && (
                      <div className="mt-1" style={{ color: "var(--text-secondary)" }}>
                        {diagnostic.remediation}
                      </div>
                    )}
                  </li>
                )
              })}
            </ul>
            {diagnosticsOmitted > 0 && (
              <p className="mt-3 text-[10px]" style={{ color: "var(--text-muted)" }}>
                {diagnosticsOmitted} additional diagnostics were omitted from this response.
              </p>
            )}
          </section>
        )}

        <section
          className="min-h-0 overflow-hidden rounded-xl"
          style={{ background: "var(--bg-panel)", border: "1px solid var(--border)" }}
        >
          <div
            className="flex items-center gap-2 px-4 py-2 text-[11px] font-mono"
            style={{ color: "var(--text-muted)", borderBottom: "1px solid var(--border)" }}
          >
            <FileCode2 size={13} aria-hidden="true" />
            {sourceFile || "Pipeline source"}
          </div>
          <pre
            aria-label="Current pipeline source"
            className="max-h-[60vh] overflow-auto p-4 text-[12px] leading-relaxed"
            style={{ color: "var(--text-primary)" }}
          >
            <code>{sourceText}</code>
          </pre>
        </section>
      </div>
    </main>
  )
}
