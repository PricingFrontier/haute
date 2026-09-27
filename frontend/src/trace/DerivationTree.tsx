import { useContext, useState } from "react"
import { ChevronDown, ChevronRight } from "lucide-react"
import type { TraceColumnDerivation } from "../types/trace"
import { ExpressionChainRowContentView } from "./ExpressionChain"
import { formatDisplayExpression } from "./traceFormatting"
import { evaluatedValue, notComputableNote } from "./traceHelpers"
import { TraceNavigationContext } from "./traceContext"
import { derivationSourceLabel, substitutionRestatesValue, type DerivationTreeNode } from "./derivationTreeHelpers"

// ---------------------------------------------------------------------------
// DerivationTree — how a value in the trace was calculated, down to the values
// that were loaded: each row is a formula evaluated on the traced row, a load,
// or a rule (a model, an optimiser), with the values it read nested beneath.
// Pure tree building lives in ./derivationTreeHelpers.
// ---------------------------------------------------------------------------

function displaySubstituted(text: string | null): string | null {
  return text ? text.replace(/\*/g, "×").replace(/\//g, "÷") : null
}

function DerivationRowContent({ node }: { node: DerivationTreeNode }) {
  const navigation = useContext(TraceNavigationContext)
  const valueKnown = node.kind !== "untraced" && node.kind !== "before_code" && !(node.kind === "unresolved" && node.value === undefined)
  const nodeId = node.nodeId
  return (
    // Pointing at a row rings its node on the canvas; its step label opens the card.
    <div
      onMouseEnter={nodeId ? () => navigation.hoverStep(nodeId) : undefined}
      onMouseLeave={nodeId ? () => navigation.hoverStep(null) : undefined}
    >
      <ExpressionChainRowContentView
        column={node.column}
        formulaText={node.expressionText ? formatDisplayExpression(node.expressionText).text : null}
        substitutedText={substitutionRestatesValue(node.substitutedText, node.value) ? null : displaySubstituted(node.substitutedText)}
        value={valueKnown ? node.value : "?"}
        source={derivationSourceLabel(node)}
        note={node.note}
        onSourceClick={nodeId ? () => navigation.focusStep(nodeId) : undefined}
        sourceLinkLabel={nodeId
          ? `Go to ${node.nodeName ?? nodeId}${node.stepNumber == null ? "" : `, step ${node.stepNumber}`}`
          : undefined}
      />
    </div>
  )
}

function DerivationBranch({ node }: { node: DerivationTreeNode }) {
  // Formulas open by default; a model's or an optimiser's inputs open on request.
  const [open, setOpen] = useState(node.kind === "formula")
  const hasChildren = node.children.length > 0
  return (
    <div
      className="text-[11px] font-mono"
      style={{ position: "relative", paddingLeft: 18, marginBottom: 4 }}
      data-testid="derivation-row"
      data-kind={node.kind}
    >
      {hasChildren ? (
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          aria-label={`${open ? "Hide" : "Show"} what ${node.column} was calculated from`}
          style={{ position: "absolute", left: 0, top: 1, color: "var(--text-muted)" }}
        >
          {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </button>
      ) : (
        <div style={{ position: "absolute", left: 4, top: 6, width: 5, height: 5, borderRadius: "50%", background: "var(--text-accent-dot)", border: "1px solid var(--text-accent-strong)" }} />
      )}
      <DerivationRowContent node={node} />
      {hasChildren && open && (
        <div style={{ marginTop: 4, borderLeft: "1px solid var(--text-accent-line)", paddingLeft: 6 }}>
          {node.children.map((child) => <DerivationBranch key={child.key} node={child} />)}
        </div>
      )}
    </div>
  )
}

export function DerivationTree({ root, label }: { root: DerivationTreeNode; label: string }) {
  return (
    <div aria-label={label} role="group">
      <DerivationBranch node={root} />
    </div>
  )
}

/** A step's own formulas for the columns the traced value depends on. */
export function ComputedHere({ derivations }: { derivations: TraceColumnDerivation[] }) {
  if (derivations.length === 0) return null
  return (
    <div className="my-2 space-y-1.5 text-[11px] font-mono" aria-label="Computed here" data-testid="trace-computed-here">
      <div style={{ color: "var(--text-muted)", fontSize: 10, fontFamily: "inherit" }} className="font-semibold uppercase">
        Computed here
      </div>
      {derivations.map((derivation) => {
        const value = evaluatedValue(derivation, undefined)
        return (
        <div key={derivation.column} style={{ paddingLeft: 8 }}>
          <ExpressionChainRowContentView
            column={derivation.column}
            formulaText={derivation.expression_text ? formatDisplayExpression(derivation.expression_text).text : null}
            substitutedText={substitutionRestatesValue(derivation.substituted_text, value) ? null : displaySubstituted(derivation.substituted_text)}
            value={value}
            source={null}
            note={derivation.error ?? notComputableNote(derivation)}
          />
        </div>
        )
      })}
    </div>
  )
}
