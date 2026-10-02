import { useMemo, useState } from "react"
import { Plus, Trash2, Variable } from "lucide-react"
import PanelShell from "./PanelShell"
import { isPlainObject } from "../types/guards"
import useGraphStore from "../stores/useGraphStore"
import useSettingsStore from "../stores/useSettingsStore"
import {
  GLOBAL_CONSTANT_TYPES,
  MISSING_VALUE,
  constantIssues,
  constantReaders,
  joinSources,
  newConstantDraft,
  splitBySource,
  unknownSourceKeys,
  valuesDiscardedByJoining,
  withType,
  type GlobalConstantDraft,
  type GlobalConstantIssues,
  type GlobalConstantType,
} from "../utils/globalConstants"

interface GlobalConstantsPanelProps {
  onClose: () => void
  readOnly?: boolean
}

type ReaderNode = { id: string; data: Record<string, unknown> }


/** Every node of the canvas and of each submodel definition, once. */
function allReaderNodes(nodes: readonly ReaderNode[], submodels: Record<string, unknown>): ReaderNode[] {
  const byId = new Map<string, ReaderNode>()
  for (const node of nodes) byId.set(node.id, node)
  for (const definition of Object.values(submodels)) {
    const graph = isPlainObject(definition) && isPlainObject(definition.graph) ? definition.graph : null
    const definitionNodes = graph && Array.isArray(graph.nodes) ? graph.nodes : []
    for (const node of definitionNodes) {
      if (isPlainObject(node) && typeof node.id === "string" && isPlainObject(node.data) && !byId.has(node.id)) {
        byId.set(node.id, { id: node.id, data: node.data })
      }
    }
  }
  return [...byId.values()]
}

const INPUT_CLASS = "w-full px-2 py-1 text-[12px] font-mono rounded focus:outline-none disabled:opacity-60"

function inputStyle(problem: string | undefined) {
  return {
    background: "var(--bg-input)",
    border: `1px solid ${problem && problem !== MISSING_VALUE ? "var(--danger)" : "var(--border)"}`,
    color: "var(--text-primary)",
  }
}

function Problem({ text, testId }: { text: string | undefined; testId: string }) {
  if (!text) return null
  const missing = text === MISSING_VALUE
  return (
    <span
      data-testid={testId}
      className="text-[10px]"
      style={{ color: missing ? "var(--text-muted)" : "var(--danger)" }}
    >
      {missing ? "Missing" : text}
    </span>
  )
}

interface ValueInputProps {
  type: GlobalConstantType
  value: string
  disabled: boolean
  problem: string | undefined
  testId: string
  label: string
  onChange: (value: string) => void
}

function ValueInput({ type, value, disabled, problem, testId, label, onChange }: ValueInputProps) {
  if (type === "boolean") {
    return (
      <select
        data-testid={testId}
        aria-label={label}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        className={INPUT_CLASS}
        style={inputStyle(problem)}
      >
        <option value="" />
        <option value="true">true</option>
        <option value="false">false</option>
      </select>
    )
  }
  return (
    <input
      data-testid={testId}
      aria-label={label}
      type={type === "date" ? "date" : "text"}
      inputMode={type === "integer" || type === "float" ? "decimal" : undefined}
      value={value}
      disabled={disabled}
      aria-invalid={problem && problem !== MISSING_VALUE ? true : undefined}
      onChange={(event) => onChange(event.target.value)}
      className={INPUT_CLASS}
      style={inputStyle(problem)}
    />
  )
}

interface ConstantRowProps {
  index: number
  draft: GlobalConstantDraft
  issues: GlobalConstantIssues
  sources: readonly string[]
  readers: string[]
  disabled: boolean
  onChange: (draft: GlobalConstantDraft) => void
  /** False when the analyst declines the rename. */
  onRename: (name: string) => boolean
  onDelete: () => void
}

function ConstantRow({
  index, draft, issues, sources, readers, disabled, onChange, onRename, onDelete,
}: ConstantRowProps) {
  // The name commits on blur or Enter, so a rename of a constant that is read
  // asks once rather than on every keystroke.
  const [name, setName] = useState(draft.name)
  const [editedFrom, setEditedFrom] = useState(draft.name)
  if (editedFrom !== draft.name) {
    setEditedFrom(draft.name)
    setName(draft.name)
  }
  const commitName = () => {
    if (name === draft.name) return
    if (!onRename(name)) setName(draft.name)
  }
  const toggleSplit = () => {
    if (!draft.split) {
      onChange(splitBySource(draft, sources))
      return
    }
    const discarded = valuesDiscardedByJoining(draft)
    const entries = Object.entries(discarded)
    if (entries.length > 0) {
      const listed = entries.map(([source, value]) => `${source}: ${value}`).join(", ")
      if (!window.confirm(`Use one value for ${draft.name}? This keeps the live value and discards ${listed}.`)) {
        return
      }
    }
    onChange(joinSources(draft))
  }
  return (
    <div
      data-testid={`constant-row-${index}`}
      className="px-3 py-2 flex flex-col gap-1.5"
      style={{ borderBottom: "1px solid var(--border)" }}
    >
      <div className="flex items-center gap-1.5">
        <input
          data-testid={`constant-name-${index}`}
          aria-label="Name"
          value={name}
          disabled={disabled}
          aria-invalid={issues.name ? true : undefined}
          onChange={(event) => setName(event.target.value)}
          onBlur={commitName}
          onKeyDown={(event) => {
            if (event.key === "Enter") event.currentTarget.blur()
          }}
          className={`${INPUT_CLASS} flex-1 min-w-0`}
          style={inputStyle(issues.name)}
        />
        <select
          data-testid={`constant-type-${index}`}
          aria-label="Type"
          value={draft.type}
          disabled={disabled}
          onChange={(event) => onChange(withType(draft, event.target.value as GlobalConstantType))}
          className="px-1.5 py-1 text-[12px] rounded focus:outline-none disabled:opacity-60"
          style={inputStyle(undefined)}
        >
          {GLOBAL_CONSTANT_TYPES.map((type) => (
            <option key={type} value={type}>{type}</option>
          ))}
        </select>
        <button
          type="button"
          data-testid={`constant-delete-${index}`}
          onClick={onDelete}
          disabled={disabled}
          aria-label={`Delete ${draft.name || "constant"}`}
          title={`Delete ${draft.name || "constant"}`}
          className="p-1.5 rounded-md transition-colors hover:bg-[var(--danger-soft)] hover:text-[var(--danger)] disabled:opacity-50"
          style={{ color: "var(--text-muted)" }}
        >
          <Trash2 size={13} />
        </button>
      </div>
      <Problem text={issues.name} testId={`constant-name-issue-${index}`} />
      <label className="flex items-center gap-2 text-[11px]" style={{ color: "var(--text-secondary)" }}>
        <button
          type="button"
          role="switch"
          aria-checked={draft.split}
          data-testid={`constant-split-${index}`}
          disabled={disabled}
          onClick={toggleSplit}
          className="shrink-0 relative w-7 h-4 rounded-full transition-colors disabled:opacity-50"
          style={{ background: draft.split ? "var(--accent)" : "var(--chrome-border)" }}
        >
          <span
            aria-hidden="true"
            className="absolute top-0.5 w-3 h-3 rounded-full transition-[left]"
            style={{ left: draft.split ? "14px" : "2px", background: "var(--bg-elevated)" }}
          />
        </button>
        Split by source
      </label>
      {draft.split ? (
        <div className="grid grid-cols-[auto_1fr] items-center gap-x-2 gap-y-1">
          {sources.map((source) => (
            <div key={source} className="contents">
              <span className="text-[11px] font-mono" style={{ color: "var(--text-muted)" }}>{source}</span>
              <div className="flex flex-col gap-0.5">
                <ValueInput
                  type={draft.type}
                  value={draft.bySource[source] ?? ""}
                  disabled={disabled}
                  problem={issues.bySource[source]}
                  testId={`constant-value-${index}-${source}`}
                  label={`${draft.name} ${source} value`}
                  onChange={(value) => onChange({ ...draft, bySource: { ...draft.bySource, [source]: value } })}
                />
                <Problem text={issues.bySource[source]} testId={`constant-value-issue-${index}-${source}`} />
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="flex flex-col gap-0.5">
          <ValueInput
            type={draft.type}
            value={draft.value}
            disabled={disabled}
            problem={issues.value}
            testId={`constant-value-${index}`}
            label={`${draft.name} value`}
            onChange={(value) => onChange({ ...draft, value })}
          />
          <Problem text={issues.value} testId={`constant-value-issue-${index}`} />
        </div>
      )}
      <span data-testid={`constant-readers-${index}`} className="text-[10px]" style={{ color: "var(--text-muted)" }}>
        {readers.length > 0 ? `Read by ${readers.join(", ")}` : "Not read"}
      </span>
    </div>
  )
}

export default function GlobalConstantsPanel({ onClose, readOnly = false }: GlobalConstantsPanelProps) {
  const drafts = useGraphStore((s) => s.globalConstants)
  const loadError = useGraphStore((s) => s.globalConstantsError)
  const nodes = useGraphStore((s) => s.nodes)
  const submodels = useGraphStore((s) => s.submodels)
  const sources = useSettingsStore((s) => s.sources)
  const disabled = readOnly || loadError !== null
  const issues = useMemo(() => constantIssues(drafts, sources), [drafts, sources])
  const readerNodes = useMemo(
    () => allReaderNodes(nodes as unknown as ReaderNode[], submodels),
    [nodes, submodels],
  )
  const readers = useMemo(
    () => drafts.map((draft) => constantReaders(readerNodes, draft.name)),
    [drafts, readerNodes],
  )
  const unknownSources = useMemo(() => unknownSourceKeys(drafts, sources), [drafts, sources])

  const setDrafts = (next: GlobalConstantDraft[]) => useGraphStore.getState().setGlobalConstantsRaw(next)
  const replace = (index: number, draft: GlobalConstantDraft) =>
    setDrafts(drafts.map((current, at) => (at === index ? draft : current)))
  const rename = (index: number, name: string): boolean => {
    const draft = drafts[index]
    const readBy = readers[index]
    if (readBy.length > 0 && !window.confirm(
      `Rename ${draft.name} to ${name}? ${readBy.join(", ")} read ${draft.name}; edit their code to read ${name}.`,
    )) {
      return false
    }
    replace(index, { ...draft, name })
    return true
  }
  const remove = (index: number) => {
    const draft = drafts[index]
    const readBy = readers[index]
    if (readBy.length > 0 && !window.confirm(`Delete ${draft.name}? ${readBy.join(", ")} read it.`)) return
    setDrafts(drafts.filter((_, at) => at !== index))
  }
  const removeUnknownSources = () =>
    setDrafts(drafts.map((draft) => {
      if (!draft.split) return draft
      const bySource = Object.fromEntries(
        Object.entries(draft.bySource).filter(([source]) => sources.includes(source)),
      )
      return { ...draft, bySource }
    }))

  return (
    <PanelShell
      testId="global-constants-panel"
      title="Global Constants"
      onClose={onClose}
      icon={<Variable size={14} style={{ color: "var(--accent)" }} />}
      subtitle={
        <span className="text-[11px] font-mono" style={{ color: "var(--text-muted)" }}>
          global_constants.&lt;name&gt;
        </span>
      }
      actions={
        <button
          type="button"
          data-testid="constants-add"
          onClick={() => setDrafts([...drafts, newConstantDraft(drafts)])}
          disabled={disabled}
          className="p-1.5 rounded-md transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--accent)] disabled:opacity-50"
          style={{ color: "var(--text-muted)" }}
          title="Add a constant"
          aria-label="Add a constant"
        >
          <Plus size={14} />
        </button>
      }
    >
      {loadError !== null && (
        <div
          data-testid="constants-load-error"
          role="alert"
          className="px-3 py-2 text-[11px] shrink-0"
          style={{ color: "var(--danger)", borderBottom: "1px solid var(--border)" }}
        >
          {loadError}
        </div>
      )}
      {unknownSources.length > 0 && !disabled && (
        <div
          data-testid="constants-unknown-sources"
          className="px-3 py-2 flex items-center gap-2 text-[11px] shrink-0"
          style={{ color: "var(--text-secondary)", borderBottom: "1px solid var(--border)" }}
        >
          <span className="flex-1">Values for unknown sources: {unknownSources.join(", ")}</span>
          <button
            type="button"
            data-testid="constants-remove-unknown"
            onClick={removeUnknownSources}
            className="px-2 py-0.5 rounded-md text-[11px] hover:bg-[var(--bg-hover)]"
            style={{ color: "var(--accent)" }}
          >
            Remove
          </button>
        </div>
      )}
      <div className="flex-1 min-h-0 overflow-y-auto">
        {drafts.length === 0 ? (
          <div className="flex items-center justify-center h-full text-[12px]" style={{ color: "var(--text-muted)" }}>
            No constants
          </div>
        ) : (
          drafts.map((draft, index) => (
            <ConstantRow
              key={index}
              index={index}
              draft={draft}
              issues={issues[index]}
              sources={sources}
              readers={readers[index]}
              disabled={disabled}
              onChange={(next) => replace(index, next)}
              onRename={(name) => rename(index, name)}
              onDelete={() => remove(index)}
            />
          ))
        )}
      </div>
    </PanelShell>
  )
}
