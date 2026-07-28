import { AlertTriangle, ArrowUpDown, Plus, Trash2 } from "lucide-react"
import { useEffect, useRef, type CSSProperties } from "react"
import ToggleButtonGroup from "../../components/ToggleButtonGroup"
import { CommittedTextField, EditorLabel } from "../../components/form"
import { withAlpha } from "../../utils/color"
import {
  analyzeEdgeJoinNode,
  type EdgeJoinColumnInfo,
} from "../../utils/edgeJoinValidation"
import { useGraph } from "../useGraph"
import { INPUT_STYLE, SELECT_STYLE } from "./_shared"
import type { OnUpdateConfig, SimpleEdge, SimpleNode } from "./_shared"

type KeyMode = "same" | "paired"

type ColumnInfo = EdgeJoinColumnInfo

const JOIN_HOW_OPTIONS = [
  { value: "left", label: "Left" },
  { value: "inner", label: "Inner" },
  { value: "full", label: "Full" },
  { value: "right", label: "Right" },
  { value: "semi", label: "Semi" },
  { value: "anti", label: "Anti" },
  { value: "cross", label: "Cross" },
] as const

const VALIDATE_OPTIONS = [
  { value: "", label: "Not set" },
  { value: "1:1", label: "1:1" },
  { value: "1:m", label: "1:m" },
  { value: "m:1", label: "m:1" },
  { value: "m:m", label: "m:m" },
] as const

const MAINTAIN_ORDER_OPTIONS = [
  { value: "", label: "Not set" },
  { value: "none", label: "None" },
  { value: "left", label: "Left" },
  { value: "right", label: "Right" },
  { value: "left_right", label: "Left then right" },
  { value: "right_left", label: "Right then left" },
] as const

export default function EdgeJoinEditor({
  config,
  onUpdate,
  nodeId,
  accentColor,
  onDeleteInput,
  onSwapInputs,
}: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  nodeId: string
  accentColor: string
  onDeleteInput?: (edgeId: string) => void
  onSwapInputs?: () => void
}) {
  const { allNodes, edges } = useGraph()
  const nodeMap = new Map(allNodes.map((node) => [node.id, node]))
  const analysis = analyzeEdgeJoinNode({
    nodeId,
    config,
    nodes: allNodes,
    edges,
  })
  const {
    diagnostics,
    how,
    suffix,
    onKeys,
    leftKeys,
    rightKeys,
    coalesce,
    validate,
    maintainOrder,
    baseRoleEdges,
    joinRoleEdges,
    baseRoleEdge,
    joinRoleEdge,
    baseColumns,
    joinColumns,
    commonColumns,
  } = analysis

  // With both inputs connected, make the first available common column an
  // explicit starting point. Remember the node after that first suggestion so
  // a user clearing it is a durable choice rather than a recurring edit.
  const seededNodesRef = useRef<Set<string>>(new Set())
  const hasAnyKeys = onKeys.length > 0 || leftKeys.length > 0 || rightKeys.length > 0
  const canSeed =
    !hasAnyKeys &&
    how !== "cross" &&
    baseRoleEdge !== undefined &&
    joinRoleEdge !== undefined &&
    commonColumns.length > 0
  const seedColumnName = canSeed ? commonColumns[0].name : null
  useEffect(() => {
    if (!canSeed || seedColumnName === null || seededNodesRef.current.has(nodeId)) return
    seededNodesRef.current.add(nodeId)
    onUpdate({ on: [seedColumnName], leftOn: [], rightOn: [] })
  }, [canSeed, nodeId, onUpdate, seedColumnName])

  const hasSameConfig = onKeys.length > 0
  const hasPairedConfig = leftKeys.length > 0 || rightKeys.length > 0
  const keyMode: KeyMode = hasPairedConfig && !hasSameConfig ? "paired" : "same"

  const sameRows = onKeys.length > 0 ? onKeys : [""]
  const pairedRows = buildPairedRows(leftKeys, rightKeys)
  const canSwapInputs = Boolean(onSwapInputs && baseRoleEdges.length === 1 && joinRoleEdges.length === 1)
  const focusVars = {
    ["--focus-ring-border" as string]: withAlpha(accentColor, 0.3),
    ["--focus-ring-shadow" as string]: withAlpha(accentColor, 0.1),
  }

  const updateSameKey = (index: number, value: string) => {
    const next = replaceAt(sameRows, index, value)
    onUpdate({ on: normalizeKeyRows(next), leftOn: [], rightOn: [] })
  }

  const addSameKey = () => {
    onUpdate({ on: [...sameRows, ""], leftOn: [], rightOn: [] })
  }

  const removeSameKey = (index: number) => {
    const next = sameRows.filter((_, rowIndex) => rowIndex !== index)
    onUpdate({ on: normalizeKeyRows(next), leftOn: [], rightOn: [] })
  }

  const updatePairedKey = (index: number, side: "left" | "right", value: string) => {
    const nextLeft = replaceAt(pairedRows.map((row) => row.left), index, side === "left" ? value : pairedRows[index]?.left ?? "")
    const nextRight = replaceAt(pairedRows.map((row) => row.right), index, side === "right" ? value : pairedRows[index]?.right ?? "")
    onUpdate({ on: [], leftOn: normalizeKeyRows(nextLeft), rightOn: normalizeKeyRows(nextRight) })
  }

  const addPairedKey = () => {
    onUpdate({
      on: [],
      leftOn: [...pairedRows.map((row) => row.left), ""],
      rightOn: [...pairedRows.map((row) => row.right), ""],
    })
  }

  const removePairedKey = (index: number) => {
    onUpdate({
      on: [],
      leftOn: normalizeKeyRows(pairedRows.map((row) => row.left).filter((_, rowIndex) => rowIndex !== index)),
      rightOn: normalizeKeyRows(pairedRows.map((row) => row.right).filter((_, rowIndex) => rowIndex !== index)),
    })
  }

  return (
    <div className="px-4 py-3 space-y-4">
      {diagnostics.length > 0 && (
        <div
          role="alert"
          className="rounded-lg px-3 py-2 space-y-1.5"
          style={{ background: "var(--warning-soft)", border: "1px solid var(--warning-border)" }}
        >
          <div className="flex items-center gap-1.5">
            <AlertTriangle size={12} style={{ color: "var(--warning-strong)" }} />
            <span className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--warning-strong)" }}>
              Edge join config needs attention
            </span>
          </div>
          <ul className="space-y-0.5">
            {diagnostics.map((message) => (
              <li key={message} className="text-[11px] leading-relaxed" style={{ color: "var(--text-secondary)" }}>
                {message}
              </li>
            ))}
          </ul>
        </div>
      )}

      <section className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          <EditorLabel as="div">Input Roles</EditorLabel>
          <button
            type="button"
            aria-label="Swap inputs"
            title="Swap inputs"
            disabled={!canSwapInputs}
            onClick={onSwapInputs}
            className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] font-semibold disabled:cursor-not-allowed disabled:opacity-50"
            style={{
              background: canSwapInputs ? withAlpha(accentColor, 0.1) : "var(--bg-input)",
              border: `1px solid ${canSwapInputs ? withAlpha(accentColor, 0.3) : "var(--border)"}`,
              color: canSwapInputs ? accentColor : "var(--text-muted)",
            }}
          >
            <ArrowUpDown size={12} />
            Swap
          </button>
        </div>
        <div className="grid grid-cols-1 gap-2">
          <RoleDisplay
            label="Dominant Input"
            roleEdge={baseRoleEdge}
            nodeMap={nodeMap}
            onDeleteInput={onDeleteInput}
            focusVars={focusVars}
          />
          <RoleDisplay
            label="Joining Input"
            roleEdge={joinRoleEdge}
            nodeMap={nodeMap}
            onDeleteInput={onDeleteInput}
            focusVars={focusVars}
          />
        </div>
      </section>

      <section className="space-y-2">
        <div>
          <EditorLabel htmlFor="edge-join-how" className="block mb-1.5">Join Type</EditorLabel>
          <select
            id="edge-join-how"
            aria-label="Join Type"
            value={how}
            onChange={(event) => {
              const nextHow = event.target.value
              if (nextHow === "cross") {
                onUpdate({ how: nextHow, on: [], leftOn: [], rightOn: [] })
              } else {
                onUpdate("how", nextHow)
              }
            }}
            className="focus-ring w-full px-2.5 py-1.5 rounded-lg text-[12px] font-mono"
            style={{ ...SELECT_STYLE, ...focusVars }}
          >
            {!JOIN_HOW_OPTIONS.some((option) => option.value === how) && (
              <option value={how}>Invalid join type ({how})</option>
            )}
            {JOIN_HOW_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </div>

        {how !== "cross" && (
          <div className="space-y-2">
            <ToggleButtonGroup<KeyMode>
              value={keyMode}
              onChange={(mode) => {
                if (mode === "paired") {
                  const seed = sameRows.length > 0 ? sameRows : [""]
                  onUpdate({ on: [], leftOn: seed, rightOn: seed })
                } else {
                  const seed = pairedRows.map((row) => row.left === row.right ? row.left : "")
                  onUpdate({ on: normalizeKeyRows(seed.length > 0 ? seed : [""]), leftOn: [], rightOn: [] })
                }
              }}
              options={[
                { key: "same", label: "Same-name keys" },
                { key: "paired", label: "Paired base/join keys" },
              ]}
              accentColor={accentColor}
              ariaLabel="Join key mode"
            />

            {keyMode === "same" ? (
              <div className="space-y-1.5">
                {sameRows.map((key, index) => (
                  <div key={`same-${index}`} className="flex items-end gap-2">
                    <KeyInput
                      id={`edge-join-same-key-${index}`}
                      label={`Same-name key ${index + 1}`}
                      value={key}
                      columns={commonColumns}
                      onChange={(value) => updateSameKey(index, value)}
                      focusVars={focusVars}
                    />
                    {sameRows.length > 1 && (
                      <IconButton label={`Remove same-name key ${index + 1}`} onClick={() => removeSameKey(index)} />
                    )}
                  </div>
                ))}
                <AddButton label="Add same-name key" onClick={addSameKey} accentColor={accentColor} />
              </div>
            ) : (
              <div className="space-y-1.5">
                {pairedRows.map((row, index) => (
                  <div key={`pair-${index}`} className="grid grid-cols-[1fr_1fr_auto] gap-2 items-end">
                    <KeyInput
                      id={`edge-join-left-key-${index}`}
                      label={`Base key ${index + 1}`}
                      value={row.left}
                      columns={baseColumns}
                      onChange={(value) => updatePairedKey(index, "left", value)}
                      focusVars={focusVars}
                    />
                    <KeyInput
                      id={`edge-join-right-key-${index}`}
                      label={`Join key ${index + 1}`}
                      value={row.right}
                      columns={joinColumns}
                      onChange={(value) => updatePairedKey(index, "right", value)}
                      focusVars={focusVars}
                    />
                    {pairedRows.length > 1 && (
                      <IconButton label={`Remove key pair ${index + 1}`} onClick={() => removePairedKey(index)} />
                    )}
                  </div>
                ))}
                <AddButton label="Add key pair" onClick={addPairedKey} accentColor={accentColor} />
              </div>
            )}
          </div>
        )}
      </section>

      <section className="space-y-3">
        <div>
          <EditorLabel htmlFor="edge-join-suffix" className="block mb-1.5">Suffix</EditorLabel>
          <CommittedTextField
            id="edge-join-suffix"
            aria-label="Suffix"
            type="text"
            value={suffix}
            onCommit={(v) => onUpdate("suffix", v)}
            className="focus-ring w-full px-2.5 py-1.5 rounded-lg text-[12px] font-mono"
            style={{ ...INPUT_STYLE, ...focusVars }}
          />
        </div>

        <div className="grid grid-cols-1 gap-2">
          <div>
            <EditorLabel htmlFor="edge-join-coalesce" className="block mb-1.5">Coalesce</EditorLabel>
            <select
              id="edge-join-coalesce"
              aria-label="Coalesce"
              value={coalesce}
              onChange={(event) => onUpdate("coalesce", coalesceValueToConfig(event.target.value))}
              className="focus-ring w-full px-2.5 py-1.5 rounded-lg text-[12px] font-mono"
              style={{ ...SELECT_STYLE, ...focusVars }}
            >
              <option value="">Not set</option>
              <option value="true">True</option>
              <option value="false">False</option>
            </select>
          </div>

          <div>
            <EditorLabel htmlFor="edge-join-validate" className="block mb-1.5">Validate</EditorLabel>
            <select
              id="edge-join-validate"
              aria-label="Validate"
              value={validate}
              onChange={(event) => onUpdate("validate", event.target.value || null)}
              className="focus-ring w-full px-2.5 py-1.5 rounded-lg text-[12px] font-mono"
              style={{ ...SELECT_STYLE, ...focusVars }}
            >
              {validate && !VALIDATE_OPTIONS.some((option) => option.value === validate) && (
                <option value={validate}>Invalid validate ({validate})</option>
              )}
              {VALIDATE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </div>

          <div>
            <EditorLabel htmlFor="edge-join-maintain-order" className="block mb-1.5">Maintain Order</EditorLabel>
            <select
              id="edge-join-maintain-order"
              aria-label="Maintain Order"
              value={maintainOrder}
              onChange={(event) => onUpdate("maintainOrder", event.target.value || null)}
              className="focus-ring w-full px-2.5 py-1.5 rounded-lg text-[12px] font-mono"
              style={{ ...SELECT_STYLE, ...focusVars }}
            >
              {maintainOrder && !MAINTAIN_ORDER_OPTIONS.some((option) => option.value === maintainOrder) && (
                <option value={maintainOrder}>Invalid maintain order ({maintainOrder})</option>
              )}
              {MAINTAIN_ORDER_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </div>
        </div>
      </section>
    </div>
  )
}

function RoleDisplay({
  label,
  roleEdge,
  nodeMap,
  onDeleteInput,
  focusVars,
}: {
  label: string
  roleEdge?: SimpleEdge
  nodeMap: Map<string, SimpleNode>
  onDeleteInput?: (edgeId: string) => void
  focusVars: CSSProperties
}) {
  const sourceLabel = roleEdge ? nodeLabel(roleEdge.source, nodeMap) : "Not connected"
  const sourceTitle = roleEdge ? `${sourceLabel} (${roleEdge.source})` : sourceLabel

  return (
    <div>
      <EditorLabel as="div" className="block mb-1.5">{label}</EditorLabel>
      <div className="flex gap-2">
        <div
          aria-label={label}
          title={sourceTitle}
          className="min-w-0 flex-1 px-2.5 py-1.5 rounded-lg text-[12px] font-mono"
          style={{ ...SELECT_STYLE, ...focusVars }}
        >
          <span
            className="block truncate"
            style={{ color: roleEdge ? "var(--text-primary)" : "var(--text-muted)" }}
          >
            {sourceLabel}
          </span>
        </div>
        {onDeleteInput && roleEdge && (
          <button
            type="button"
            aria-label={`Remove ${label}`}
            title={`Remove ${label}`}
            onClick={() => onDeleteInput(roleEdge.id)}
            className="icon-danger-btn h-[31px] w-[31px] rounded-lg flex items-center justify-center shrink-0"
            style={{ border: "1px solid var(--border)" }}
          >
            <Trash2 size={13} />
          </button>
        )}
      </div>
    </div>
  )
}

function KeyInput({
  id,
  label,
  value,
  columns,
  onChange,
  focusVars,
}: {
  id: string
  label: string
  value: string
  columns: ColumnInfo[]
  onChange: (value: string) => void
  focusVars: CSSProperties
}) {
  const hasColumns = columns.length > 0
  const hasMissingValue = Boolean(value) && !columns.some((column) => column.name === value)
  return (
    <div className="min-w-0 flex-1">
      <EditorLabel htmlFor={id} className="block mb-1">{label}</EditorLabel>
      {hasColumns ? (
        <select
          id={id}
          aria-label={label}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          className="focus-ring w-full px-2.5 py-1.5 rounded-lg text-[12px] font-mono"
          style={{ ...SELECT_STYLE, ...focusVars }}
        >
          <option value="">Select column</option>
          {hasMissingValue && <option value={value}>Missing column ({value})</option>}
          {columns.map((column) => (
            <option key={column.name} value={column.name}>{column.name}</option>
          ))}
        </select>
      ) : (
        <CommittedTextField
          id={id}
          aria-label={label}
          type="text"
          value={value}
          onCommit={onChange}
          className="focus-ring w-full px-2.5 py-1.5 rounded-lg text-[12px] font-mono"
          style={{ ...INPUT_STYLE, ...focusVars }}
        />
      )}
    </div>
  )
}

function AddButton({
  label,
  onClick,
  accentColor,
}: {
  label: string
  onClick: () => void
  accentColor: string
}) {
  return (
    <button
      type="button"
      aria-label={label}
      onClick={onClick}
      className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] font-semibold"
      style={{
        background: withAlpha(accentColor, 0.1),
        border: `1px solid ${withAlpha(accentColor, 0.3)}`,
        color: accentColor,
      }}
    >
      <Plus size={12} />
      {label}
    </button>
  )
}

function IconButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className="icon-danger-btn h-[31px] w-[31px] rounded-lg flex items-center justify-center shrink-0"
      style={{ border: "1px solid var(--border)" }}
    >
      <Trash2 size={13} />
    </button>
  )
}

function coalesceValueToConfig(value: string): boolean | null {
  if (value === "true") return true
  if (value === "false") return false
  return null
}

function nodeLabel(nodeId: string, nodeMap: Map<string, SimpleNode>): string {
  return nodeMap.get(nodeId)?.data.label ?? nodeId
}

function buildPairedRows(leftKeys: string[], rightKeys: string[]): { left: string; right: string }[] {
  const count = Math.max(leftKeys.length, rightKeys.length, 1)
  return Array.from({ length: count }, (_, index) => ({
    left: leftKeys[index] ?? "",
    right: rightKeys[index] ?? "",
  }))
}

function replaceAt(values: string[], index: number, value: string): string[] {
  return values.map((existing, rowIndex) => rowIndex === index ? value : existing)
}

function normalizeKeyRows(values: string[]): string[] {
  let lastMeaningfulIndex = -1
  for (let index = values.length - 1; index >= 0; index -= 1) {
    if (values[index] !== "") {
      lastMeaningfulIndex = index
      break
    }
  }
  if (lastMeaningfulIndex === -1) return []
  return values.slice(0, lastMeaningfulIndex + 1)
}
