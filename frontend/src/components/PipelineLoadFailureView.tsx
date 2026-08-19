import { AlertTriangle } from "lucide-react"

interface PipelineLoadFailureViewProps {
  detail: string
}

export default function PipelineLoadFailureView({ detail }: PipelineLoadFailureViewProps) {
  return (
    <main
      data-testid="pipeline-load-failure"
      className="flex flex-1 items-center justify-center p-6"
      style={{ background: "var(--bg-base)" }}
    >
      <section
        role="alert"
        className="w-full max-w-xl rounded-xl p-5"
        style={{
          background: "var(--danger-soft)",
          border: "1px solid var(--danger-border)",
          color: "var(--danger-text)",
        }}
      >
        <div className="flex items-center gap-2 text-[14px] font-semibold">
          <AlertTriangle size={17} aria-hidden="true" />
          Haute could not open this pipeline
        </div>
        <p className="mt-2 text-[12px] leading-relaxed">
          This is a server, permission, or response-contract failure rather than an authored node
          error. The editor has stayed read-only so it cannot overwrite the document.
        </p>
        <pre
          className="mt-3 overflow-auto rounded-md p-3 text-[11px]"
          style={{ background: "var(--bg-base)", color: "var(--text-secondary)" }}
        >
          {detail}
        </pre>
      </section>
    </main>
  )
}
