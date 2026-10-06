import { AlertTriangle } from "lucide-react"

import useDocumentStatusStore from "../stores/useDocumentStatusStore"
import type { PipelineNameViolation } from "../types/pipelineDocument"

interface NameViolationsBannerProps {
  /** Select the violation's nodes (a submodel child by its occurrences). */
  onSelectViolation?: (violation: PipelineNameViolation) => void
}

/**
 * The loaded file's remaining name violations. The document stays editable
 * but cannot be saved, run or previewed until renames clear the list, which
 * shrinks as the server revalidates each edit.
 */
export default function NameViolationsBanner({ onSelectViolation }: NameViolationsBannerProps) {
  const violations = useDocumentStatusStore((state) => state.nameViolations)

  if (violations.length === 0) return null
  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="name-violations-banner"
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
          Resolve {violations.length === 1 ? "this name issue" : `these ${violations.length} name issues`}{" "}
          before saving or running the pipeline.
        </span>
      </div>
      <ol className="mt-1 max-h-40 overflow-auto space-y-1" aria-label="Name violations">
        {violations.map((violation) => (
          <li key={`${violation.kind}:${violation.name}:${violation.message}`}>
            <button
              type="button"
              className="w-full rounded px-2 py-1 text-left hover-chrome-solid disabled:cursor-default"
              disabled={onSelectViolation === undefined || violation.parties.length === 0}
              onClick={() => onSelectViolation?.(violation)}
            >
              {violation.message}
            </button>
          </li>
        ))}
      </ol>
    </div>
  )
}
