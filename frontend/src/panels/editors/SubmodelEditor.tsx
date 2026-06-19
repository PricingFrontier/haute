import { useState } from "react"
import { Package, ChevronRight } from "lucide-react"
import { configField } from "../../utils/configField"
import { withAlpha } from "../../utils/color"
import { EditorLabel } from "../../components/form"

/**
 * A single wrapper I/O frame, rendered as a collapsible row.
 *
 * This is the read-only FIRST STAGE of the wrapper I/O surface: it gives the
 * side-pane the per-frame structure the spec calls for (each frame collapsible
 * in its own row) without the editing affordances — schema view, output-field
 * selection, rename, and preview. Those are gated on the wrapper output
 * data-model (a wrapper output is currently just the internal node it derives
 * from, with no place to store a per-output name or field selection) and land
 * in a later stage. A frame's name is the internal node it binds to.
 */
function WrapperFramePane({
  name,
  direction,
  accentColor,
}: {
  name: string
  direction: "input" | "output"
  accentColor: string
}) {
  const [open, setOpen] = useState(false)
  const tint = direction === "output" ? accentColor : "var(--accent)"
  return (
    <div className="rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)" }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        data-testid={`wrapper-frame-${direction}-${name}`}
        className="w-full flex items-center gap-2 px-2.5 py-1.5 text-left"
        style={{ background: "var(--bg-input)" }}
      >
        <ChevronRight
          size={12}
          style={{
            color: "var(--text-muted)",
            transition: "transform 150ms",
            transform: open ? "rotate(90deg)" : undefined,
          }}
        />
        <span className="text-[11px] font-mono" style={{ color: tint }}>
          {name}
        </span>
        <span
          className="ml-auto text-[10px] uppercase tracking-wide"
          style={{ color: "var(--text-muted)" }}
        >
          {direction === "output" ? "out" : "in"}
        </span>
      </button>
      {open && (
        <div
          className="px-3 py-2 space-y-1.5 text-[11px]"
          style={{ borderTop: "1px solid var(--border)", color: "var(--text-secondary)" }}
        >
          <div>
            {direction === "output" ? "Output frame produced by node " : "Input frame feeding node "}
            <span className="font-mono" style={{ color: "var(--text-primary)" }}>
              {name}
            </span>
            .
          </div>
          <div style={{ color: "var(--text-muted)" }}>
            Schema, output-field selection, rename and preview arrive with the wrapper output model.
          </div>
        </div>
      )}
    </div>
  )
}

export default function SubmodelEditor({
  config,
  accentColor,
}: {
  config: Record<string, unknown>
  accentColor: string
}) {
  const file = configField(config, "file", "")
  const childNodeIds = configField<string[]>(config, "childNodeIds", [])
  const inputPorts = configField<string[]>(config, "inputPorts", [])
  const outputPorts = configField<string[]>(config, "outputPorts", [])

  return (
    <div className="px-4 py-3 space-y-3">
      <div className="flex items-center gap-2 px-2.5 py-2 rounded-lg" style={{ background: withAlpha(accentColor, 0.08), border: `1px solid ${withAlpha(accentColor, 0.2)}` }}>
        <Package size={14} style={{ color: accentColor }} />
        <span className="text-xs font-medium" style={{ color: accentColor }}>Wrapper</span>
        <span className="ml-auto text-[11px] font-mono" style={{ color: 'var(--text-muted)' }}>{childNodeIds.length} nodes</span>
      </div>

      {file && (
        <div>
          <EditorLabel>File</EditorLabel>
          <div className="mt-1 text-xs font-mono px-2.5 py-1.5 rounded-lg" style={{ background: 'var(--bg-input)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
            {file}
          </div>
        </div>
      )}

      {inputPorts.length > 0 && (
        <div>
          <EditorLabel>Inputs</EditorLabel>
          <div className="mt-1 space-y-1.5">
            {inputPorts.map((port) => (
              <WrapperFramePane key={port} name={port} direction="input" accentColor={accentColor} />
            ))}
          </div>
        </div>
      )}

      {outputPorts.length > 0 && (
        <div>
          <EditorLabel>Outputs</EditorLabel>
          <div className="mt-1 space-y-1.5">
            {outputPorts.map((port) => (
              <WrapperFramePane key={port} name={port} direction="output" accentColor={accentColor} />
            ))}
          </div>
        </div>
      )}

      <div className="text-[11px] pt-1" style={{ color: 'var(--text-muted)' }}>
        Double-click to view internal nodes
      </div>
    </div>
  )
}
