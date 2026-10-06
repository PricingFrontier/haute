import { useMemo, useState, type CSSProperties } from "react"
import { AlertTriangle, Plus, Trash2, Variable } from "lucide-react"
import PanelShell from "./PanelShell"
import { INPUT_STYLE } from "./editors/_shared"
import { isPlainObject } from "../types/guards"
import useGraphStore from "../stores/useGraphStore"
import useSettingsStore from "../stores/useSettingsStore"
import {
  GLOBAL_CONSTANT_TYPES,
  MISSING_VALUE,
  constantIssues,
  type GlobalConstantDraft,
  type GlobalConstantIssues,
  type GlobalConstantType,
} from "../utils/globalConstants"
import {
  constantReaders,
  joinSources,
  newConstantDraft,
  splitBySource,
  unknownSourceKeys,
  valuesDiscardedByJoining,
  withType,
} from "../utils/globalConstantsEditing"

interface GlobalConstantsPanelProps {
  onClose: () => void
  readOnly?: boolean
}

type ReaderNode = { id: string; data: Record<string, unknown> }

const TYPE_LABELS: Record<GlobalConstantType, string> = {
  integer: "Integer",
  float: "Decimal",
  text: "Text",
  boolean: "True/false",
  date: "Date",
}

const CELL_INPUT = "w-full min-w-0 px-2 py-1 text-xs font-mono rounded-md focus:outline-none focus:ring-2 disabled:opacity-60"
const HEADER_CELL = "px-2 py-1.5 text-left text-[11px] font-bold uppercase tracking-[0.08em] whitespace-nowrap"
/** A source column's header: the source's own name, as the toolbar shows it. */
const SOURCE_HEADER_CELL = "px-2 py-1.5 text-left text-[11px] font-mono font-bold whitespace-nowrap"

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

function cellStyle(problem: string | undefined): CSSProperties {
  if (problem === MISSING_VALUE) return { ...INPUT_STYLE, border: "1px dashed var(--warning)" }
  if (problem) return { ...INPUT_STYLE, border: "1px solid var(--danger)" }
  return INPUT_STYLE
}

interface ValueCellProps {
  type: GlobalConstantType
  value: string
  disabled: boolean
  problem: string | undefined
  testId: string
  label: string
  /** Shown while the cell is empty and nothing is wrong with it. */
  placeholder?: string
  onChange: (value: string) => void
}

/** One value as a table cell: the input that fits the type, marked when invalid or missing. */
function ValueCell({ type, value, disabled, problem, testId, label, placeholder, onChange }: ValueCellProps) {
  const title = problem === MISSING_VALUE ? "Missing" : problem
  if (type === "boolean") {
    return (
      <select
        data-testid={testId}
        aria-label={label}
        title={title}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        className={CELL_INPUT}
        style={cellStyle(problem)}
      >
        <option value="">{problem === MISSING_VALUE ? "missing" : "—"}</option>
        <option value="true">true</option>
        <option value="false">false</option>
      </select>
    )
  }
  return (
    <input
      data-testid={testId}
      aria-label={label}
      title={title}
      type={type === "date" ? "date" : "text"}
      inputMode={type === "integer" || type === "float" ? "decimal" : undefined}
      placeholder={problem === MISSING_VALUE ? "missing" : placeholder}
      value={value}
      disabled={disabled}
      aria-invalid={problem && problem !== MISSING_VALUE ? true : undefined}
      onChange={(event) => onChange(event.target.value)}
      className={CELL_INPUT}
      style={{ ...cellStyle(problem), colorScheme: "dark" }}
    />
  )
}

function SplitSwitch({ on, disabled, testId, label, onToggle }: { on: boolean; disabled: boolean; testId: string; label: string; onToggle: () => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      title={on ? "One value per source" : "One value for every source"}
      data-testid={testId}
      disabled={disabled}
      onClick={onToggle}
      className="shrink-0 relative w-7 h-4 rounded-full transition-colors disabled:opacity-50"
      style={{ background: on ? "var(--accent)" : "var(--chrome-border)" }}
    >
      <span
        aria-hidden="true"
        className="absolute top-0.5 w-3 h-3 rounded-full transition-[left]"
        style={{ left: on ? "14px" : "2px", background: "var(--bg-elevated)" }}
      />
    </button>
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

function ConstantRow({ index, draft, issues, sources, readers, disabled, onChange, onRename, onDelete }: ConstantRowProps) {
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
  const label = draft.name || "constant"
  return (
    <tr data-testid={`constant-row-${index}`} className="group" style={{ borderTop: "1px solid var(--border-subtle)" }}>
      <td className="px-2 py-1.5 align-top">
        <input
          data-testid={`constant-name-${index}`}
          aria-label={`Constant ${index + 1} name`}
          title={issues.name}
          value={name}
          disabled={disabled}
          aria-invalid={issues.name ? true : undefined}
          onChange={(event) => setName(event.target.value)}
          onBlur={commitName}
          onKeyDown={(event) => {
            if (event.key === "Enter") event.currentTarget.blur()
          }}
          className={CELL_INPUT}
          style={cellStyle(issues.name)}
        />
      </td>
      <td className="px-2 py-1.5 align-top">
        <select
          data-testid={`constant-type-${index}`}
          aria-label={`${label} type`}
          value={draft.type}
          disabled={disabled}
          onChange={(event) => onChange(withType(draft, event.target.value as GlobalConstantType))}
          className={`${CELL_INPUT} font-sans`}
          style={INPUT_STYLE}
        >
          {GLOBAL_CONSTANT_TYPES.map((type) => (
            <option key={type} value={type}>{TYPE_LABELS[type]}</option>
          ))}
        </select>
      </td>
      <td className="px-2 py-1.5 align-top">
        <div className="flex h-[26px] items-center justify-center">
          <SplitSwitch
            on={draft.split}
            disabled={disabled}
            testId={`constant-split-${index}`}
            label={`Split ${label} by source`}
            onToggle={toggleSplit}
          />
        </div>
      </td>
      {draft.split ? (
        sources.map((source) => (
          <td key={source} className="px-2 py-1.5 align-top">
            <ValueCell
              type={draft.type}
              value={draft.bySource[source] ?? ""}
              disabled={disabled}
              problem={issues.bySource[source]}
              testId={`constant-value-${index}-${source}`}
              label={`${label} ${source} value`}
              onChange={(value) => onChange({ ...draft, bySource: { ...draft.bySource, [source]: value } })}
            />
          </td>
        ))
      ) : (
        <td colSpan={sources.length} className="px-2 py-1.5 align-top">
          <ValueCell
            type={draft.type}
            value={draft.value}
            disabled={disabled}
            problem={issues.value}
            testId={`constant-value-${index}`}
            label={`${label} value`}
            placeholder={sources.length > 1 ? "same value for every source" : undefined}
            onChange={(value) => onChange({ ...draft, value })}
          />
        </td>
      )}
      <td className="px-2 py-1.5 align-top text-center">
        <span
          data-testid={`constant-readers-${index}`}
          aria-label={readers.length ? `Read by ${readers.join(", ")}` : "Not read"}
          title={readers.length ? `Read by ${readers.join(", ")}` : "No node reads it"}
          className="inline-flex h-[26px] items-center text-[11px] font-mono"
          style={{ color: readers.length ? "var(--text-secondary)" : "var(--text-muted)" }}
        >
          {readers.length || "—"}
        </span>
      </td>
      <td className="px-1 py-1.5 align-top">
        {!disabled && (
          <button
            type="button"
            data-testid={`constant-delete-${index}`}
            onClick={onDelete}
            aria-label={`Remove ${label}`}
            title={`Remove ${label}`}
            className="flex h-[26px] items-center p-1 rounded opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity hover:text-[var(--danger)] focus-visible:text-[var(--danger)]"
            style={{ color: "var(--text-muted)" }}
          >
            <Trash2 size={12} />
          </button>
        )}
      </td>
    </tr>
  )
}

/** The reasons under the table: each invalid name or value, and each missing source value. */
function Problems({ drafts, issues }: { drafts: readonly GlobalConstantDraft[]; issues: readonly GlobalConstantIssues[] }) {
  const entries = drafts.flatMap((draft, index) => {
    const issue = issues[index]
    const label = draft.name || `Constant ${index + 1}`
    return [
      ...(issue.name ? [{ testId: `constant-name-issue-${index}`, label, text: issue.name, missing: false }] : []),
      ...(issue.value ? [{ testId: `constant-value-issue-${index}`, label, text: issue.value, missing: false }] : []),
      ...Object.entries(issue.bySource).map(([source, problem]) => ({
        testId: `constant-value-issue-${index}-${source}`,
        label: `${label} · ${source}`,
        text: problem === MISSING_VALUE ? "Missing" : problem,
        missing: problem === MISSING_VALUE,
      })),
    ]
  })
  if (entries.length === 0) return null
  return (
    <ul className="m-0 p-0 list-none space-y-0.5" aria-label="Constant problems">
      {entries.map((entry) => (
        <li key={entry.testId} data-testid={entry.testId} className="text-[11px]">
          <span className="font-mono" style={{ color: "var(--text-secondary)" }}>{entry.label}</span>
          <span style={{ color: entry.missing ? "var(--warning)" : "var(--danger)" }}> — {entry.text}</span>
        </li>
      ))}
    </ul>
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
  const add = () => setDrafts([...drafts, newConstantDraft(drafts)])
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

  const addButton = !disabled && (
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
  )

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
            {addButton}
          </div>
        ) : (
          <>
            <div className="overflow-x-auto rounded-lg" style={{ background: "var(--bg-panel)", border: "1px solid var(--border)" }}>
              <table className="w-full border-collapse" aria-label="Global constants">
                <thead>
                  <tr style={{ color: "var(--text-muted)" }}>
                    <th scope="col" className={`${HEADER_CELL} min-w-32`}>Name</th>
                    <th scope="col" className={`${HEADER_CELL} w-28`}>Type</th>
                    <th scope="col" className={`${HEADER_CELL} w-12 text-center`} title="One value per source">Split</th>
                    {sources.map((source) => (
                      <th key={source} scope="col" className={`${SOURCE_HEADER_CELL} min-w-28`}>
                        <span className="inline-flex items-center gap-1.5">
                          {source === "live" && <span aria-hidden="true" className="w-1.5 h-1.5 rounded-full bg-green-400" />}
                          {source}
                        </span>
                      </th>
                    ))}
                    <th scope="col" className={`${HEADER_CELL} w-14 text-center`} title="How many nodes read it">Used by</th>
                    <th scope="col" className="w-7"><span className="sr-only">Remove</span></th>
                  </tr>
                </thead>
                <tbody>
                  {drafts.map((draft, index) => (
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
                  ))}
                </tbody>
              </table>
            </div>
            <Problems drafts={drafts} issues={issues} />
            {addButton && <div>{addButton}</div>}
          </>
        )}
      </div>
    </PanelShell>
  )
}
