import { useMemo, useState, type CSSProperties, type ReactNode } from "react"
import { AlertTriangle, Plus, Variable } from "lucide-react"
import PanelShell from "./PanelShell"
import { INPUT_STYLE } from "./editors/_shared"
import SearchableItemList from "./editors/shared/SearchableItemList"
import { useSearchableList, type SearchableListItem } from "./editors/shared/useSearchableList"
import ToggleButtonGroup from "../components/ToggleButtonGroup"
import { EditorLabel } from "../components/form"
import { isPlainObject } from "../types/guards"
import useGraphStore from "../stores/useGraphStore"
import useSettingsStore from "../stores/useSettingsStore"
import {
  GLOBAL_CONSTANT_TYPES,
  MISSING_VALUE,
  constantIssues,
  constantReaders,
  holdsSourceValue,
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

/** The app accent (`--accent`) as a hex value, which the list and type buttons tint with. */
const ACCENT = "#3b82f6"

const TYPE_LABELS: Record<GlobalConstantType, string> = {
  integer: "Integer",
  float: "Decimal",
  text: "Text",
  boolean: "True/false",
  date: "Date",
}

const INPUT_CLASS = "w-full px-2 py-1.5 text-xs font-mono rounded-lg focus:outline-none focus:ring-2 disabled:opacity-60"

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

function inputStyle(problem: string | undefined): CSSProperties {
  return problem && problem !== MISSING_VALUE ? { ...INPUT_STYLE, border: "1px solid var(--danger)" } : INPUT_STYLE
}

/** The problems that make a constant need attention, for the list's health dot and tooltip. */
function problemsOf(issues: GlobalConstantIssues): string[] {
  return [
    ...(issues.name ? [issues.name] : []),
    ...(issues.value ? [issues.value] : []),
    ...Object.entries(issues.bySource).map(([source, problem]) =>
      problem === MISSING_VALUE ? `No ${source} value` : `${source}: ${problem}`,
    ),
  ]
}

/** The list badge for a constant's value: the value, or how many sources hold one. */
function valueBadge(draft: GlobalConstantDraft, sources: readonly string[]): string {
  if (!draft.split) return draft.value === "" ? "—" : draft.value
  const held = sources.filter((source) => holdsSourceValue(draft, source)).length
  return `${held}/${sources.length} sources`
}

function Problem({ text, testId }: { text: string | undefined; testId: string }) {
  if (!text) return null
  const missing = text === MISSING_VALUE
  return (
    <p data-testid={testId} className="m-0 mt-1 text-[11px]" style={{ color: missing ? "var(--warning)" : "var(--danger)" }}>
      {missing ? "Missing" : text}
    </p>
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
        <option value="">Choose…</option>
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
      style={{ ...inputStyle(problem), colorScheme: "dark" }}
    />
  )
}

/** A field's uppercase label, with optional content on the right of the same line. */
function SectionLabel({ children, htmlFor, aside }: { children: ReactNode; htmlFor?: string; aside?: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-2 mb-1">
      <EditorLabel htmlFor={htmlFor}>{children}</EditorLabel>
      {aside}
    </div>
  )
}

interface ConstantDetailProps {
  index: number
  draft: GlobalConstantDraft
  issues: GlobalConstantIssues
  sources: readonly string[]
  readers: string[]
  disabled: boolean
  onChange: (draft: GlobalConstantDraft) => void
  /** False when the analyst declines the rename. */
  onRename: (name: string) => boolean
}

function ConstantDetail({ index, draft, issues, sources, readers, disabled, onChange, onRename }: ConstantDetailProps) {
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
    const entries = Object.entries(valuesDiscardedByJoining(draft))
    if (entries.length > 0) {
      const listed = entries.map(([source, value]) => `${source}: ${value}`).join(", ")
      if (!window.confirm(`Use one value for ${draft.name}? This keeps the live value and discards ${listed}.`)) {
        return
      }
    }
    onChange(joinSources(draft))
  }
  const nameId = `constant-name-${index}`
  return (
    <div className="space-y-3" data-testid={`constant-row-${index}`}>
      <div>
        <SectionLabel htmlFor={nameId}>Name</SectionLabel>
        <input
          id={nameId}
          data-testid={nameId}
          value={name}
          disabled={disabled}
          aria-invalid={issues.name ? true : undefined}
          onChange={(event) => setName(event.target.value)}
          onBlur={commitName}
          onKeyDown={(event) => {
            if (event.key === "Enter") event.currentTarget.blur()
          }}
          className={INPUT_CLASS}
          style={inputStyle(issues.name)}
        />
        <Problem text={issues.name} testId={`constant-name-issue-${index}`} />
      </div>

      <div>
        <SectionLabel>Type</SectionLabel>
        <div data-testid={`constant-type-${index}`} className={disabled ? "pointer-events-none opacity-60" : undefined}>
          <ToggleButtonGroup
            value={draft.type}
            onChange={(type) => onChange(withType(draft, type))}
            options={GLOBAL_CONSTANT_TYPES.map((type) => ({ key: type, label: TYPE_LABELS[type], disabled }))}
            accentColor={ACCENT}
            ariaLabel="Type"
          />
        </div>
      </div>

      <div>
        <SectionLabel
          aside={
            <label className="flex items-center gap-2 text-[11px]" style={{ color: "var(--text-secondary)" }}>
              Split by source
              <button
                type="button"
                role="switch"
                aria-checked={draft.split}
                aria-label="Split by source"
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
            </label>
          }
        >
          {draft.split ? "Values" : "Value"}
        </SectionLabel>
        {draft.split ? (
          <div className="rounded-lg overflow-hidden" style={{ background: "var(--bg-panel)", border: "1px solid var(--border)" }}>
            <div className="grid grid-cols-[minmax(5rem,auto)_1fr] gap-x-2 px-2 py-1.5 text-[11px]" style={{ color: "var(--text-muted)", borderBottom: "1px solid var(--border-subtle)" }}>
              <span>Source</span>
              <span>Value</span>
            </div>
            {sources.map((source) => (
              <div
                key={source}
                className="grid grid-cols-[minmax(5rem,auto)_1fr] items-start gap-x-2 px-2 py-1.5 last:border-b-0"
                style={{ borderBottom: "1px solid var(--border-subtle)" }}
              >
                <span className="flex items-center gap-1.5 pt-1.5 text-xs font-mono" style={{ color: "var(--text-secondary)" }}>
                  {source === "live"
                    ? <span aria-hidden="true" className="w-1.5 h-1.5 rounded-full bg-green-400 shrink-0" />
                    : <span aria-hidden="true" className="w-1.5 shrink-0" />}
                  {source}
                </span>
                <div>
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
          <>
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
          </>
        )}
      </div>

      <div>
        <SectionLabel>Read by</SectionLabel>
        <div data-testid={`constant-readers-${index}`} className="flex flex-wrap gap-1.5">
          {readers.length > 0 ? (
            readers.map((reader) => (
              <span
                key={reader}
                className="rounded-md px-2 py-0.5 text-[11px] font-mono"
                style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}
              >
                {reader}
              </span>
            ))
          ) : (
            <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>No node reads it</span>
          )}
        </div>
      </div>
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
  const [selected, setSelected] = useState(0)
  const selectedIndex = Math.min(selected, Math.max(drafts.length - 1, 0))
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
  const items: SearchableListItem[] = drafts.map((draft, index) => {
    const problems = problemsOf(issues[index])
    return {
      index,
      name: draft.name || "(unnamed)",
      searchTerms: [TYPE_LABELS[draft.type]],
      healthy: problems.length === 0,
      issues: problems,
      badges: [TYPE_LABELS[draft.type], valueBadge(draft, sources)],
    }
  })
  const list = useSearchableList(items, selectedIndex, setSelected, drafts.length > 0)

  const setDrafts = (next: GlobalConstantDraft[]) => useGraphStore.getState().setGlobalConstantsRaw(next)
  const replace = (index: number, draft: GlobalConstantDraft) =>
    setDrafts(drafts.map((current, at) => (at === index ? draft : current)))
  const add = () => {
    list.reset()
    setDrafts([...drafts, newConstantDraft(drafts)])
    setSelected(drafts.length)
  }
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
    if (selectedIndex >= index && selectedIndex > 0) setSelected(selectedIndex - 1)
  }
  const move = (from: number, to: number) => {
    const next = [...drafts]
    const [moved] = next.splice(from, 1)
    next.splice(to, 0, moved)
    setDrafts(next)
    setSelected(to)
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
    >
      <div className="flex-1 min-h-0 overflow-y-auto px-4 py-3 space-y-3">
        {loadError !== null && (
          <div
            data-testid="constants-load-error"
            role="alert"
            className="flex gap-2 rounded-lg px-3 py-2 text-[11px]"
            style={{ background: "var(--danger-soft)", border: "1px solid var(--danger-border)", color: "var(--danger-text)" }}
          >
            <AlertTriangle size={13} className="shrink-0 mt-px" aria-hidden="true" />
            <span>{loadError}</span>
          </div>
        )}
        {unknownSources.length > 0 && !disabled && (
          <div
            data-testid="constants-unknown-sources"
            className="flex items-center gap-2 rounded-lg px-3 py-2 text-[11px]"
            style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}
          >
            <AlertTriangle size={13} className="shrink-0" style={{ color: "var(--warning)" }} aria-hidden="true" />
            <span className="flex-1">Values for sources this pipeline no longer has: {unknownSources.join(", ")}</span>
            <button
              type="button"
              data-testid="constants-remove-unknown"
              onClick={removeUnknownSources}
              className="px-2 py-0.5 rounded-md text-[11px] font-medium hover:bg-[var(--bg-hover)]"
              style={{ color: "var(--accent)" }}
            >
              Remove
            </button>
          </div>
        )}
        {drafts.length === 0 ? (
          <div
            className="flex flex-col items-center gap-2 rounded-lg px-3 py-6 text-center"
            style={{ background: "var(--bg-panel)", border: "1px dashed var(--border)" }}
          >
            <span className="text-[12px]" style={{ color: "var(--text-muted)" }}>No constants yet</span>
            {!disabled && (
              <button
                type="button"
                onClick={add}
                aria-label="Add constant"
                className="add-row-btn flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium rounded-lg"
                style={{ color: "var(--text-secondary)", border: "1px solid var(--border)" }}
              >
                <Plus size={12} />
                Add constant
              </button>
            )}
          </div>
        ) : (
          <>
            <SearchableItemList
              list={list}
              selectedIndex={selectedIndex}
              onSelect={setSelected}
              onAdd={disabled ? undefined : add}
              onRemove={disabled ? undefined : remove}
              onMove={disabled ? undefined : move}
              labels={{
                list: "Global constants",
                search: "Search constants",
                add: "Add constant",
                remove: (name) => `Remove ${name}`,
                status: (healthy) => (healthy ? "complete" : "needs attention"),
                empty: "No matching constants",
              }}
              accentColor={ACCENT}
            />
            {list.noneVisible ? (
              <div className="px-2 py-4 text-center text-[11px]" style={{ color: "var(--text-muted)" }}>
                Select a matching constant to edit it
              </div>
            ) : (
              <ConstantDetail
                key={selectedIndex}
                index={selectedIndex}
                draft={drafts[selectedIndex]}
                issues={issues[selectedIndex]}
                sources={sources}
                readers={readers[selectedIndex]}
                disabled={disabled}
                onChange={(next) => replace(selectedIndex, next)}
                onRename={(name) => rename(selectedIndex, name)}
              />
            )}
          </>
        )}
      </div>
    </PanelShell>
  )
}
