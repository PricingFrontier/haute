import { ChevronRight } from "lucide-react"

export interface ViewLevel {
  type: "pipeline" | "submodel"
  name: string
  file: string
  _savedNodes?: import("@xyflow/react").Node[]
  _savedEdges?: import("@xyflow/react").Edge[]
}

interface BreadcrumbBarProps {
  viewStack: ViewLevel[]
  onNavigate: (depth: number) => void
}

export default function BreadcrumbBar({ viewStack, onNavigate }: BreadcrumbBarProps) {
  if (viewStack.length <= 1) return null

  return (
    <div
      className="absolute top-2 left-1/2 -translate-x-1/2 z-10 flex items-center gap-1 px-3 py-1.5 rounded-lg"
      style={{
        background: "var(--chrome)",
        border: "1px solid var(--chrome-border)",
        boxShadow: "0 2px 8px rgba(0,0,0,.3)",
      }}
    >
      {viewStack.map((level, i) => (
        <span key={i} className="flex items-center gap-1">
          {i > 0 && <ChevronRight size={12} style={{ color: "var(--text-muted)" }} />}
          <button
            onClick={() => onNavigate(i)}
            className="text-[12px] font-medium px-1.5 py-0.5 rounded hover-crumb"
            style={{
              cursor: i === viewStack.length - 1 ? "default" : "pointer",
            }}
            disabled={i === viewStack.length - 1}
          >
            {level.name}
          </button>
        </span>
      ))}
    </div>
  )
}
