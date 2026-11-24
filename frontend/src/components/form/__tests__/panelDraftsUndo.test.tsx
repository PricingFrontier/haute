/**
 * Every value a panel edits follows the document through Undo.
 *
 * Each entry is a field of the inventory of panel copies, on the shared draft now: it is
 * given a node config held with its undo history, as the graph store holds it, and driven
 * twice. Typed, then moved under from outside (an Undo, a Redo or a sync frame), it shows
 * the store's value and writes nothing of what was typed. Typed and committed, it writes
 * once, and one Undo puts the earlier value back on screen and in the store.
 * `draftsByHand.test.ts` keeps new panels from holding a copy any other way.
 */

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { useSyncExternalStore, type ReactElement } from "react"
import { afterEach, describe, expect, it } from "vitest"

import CommittedTextField, { CommittedTextArea, ValidatedTextField } from "../CommittedTextField"
import type { OnUpdateConfig } from "../../../panels/editors/_shared"
import { BandingRulesGrid } from "../../../panels/editors/banding/BandingRulesGrid"
import { BreakpointGrid } from "../../../panels/editors/banding/BreakpointGrid"
import { NumberCell } from "../../../panels/editors/shared/NumberCell"
import { IoArgumentsEditor } from "../../../panels/editors/_IoFormatEditor"
import { HyperparametersConfig } from "../../../panels/modelling/HyperparametersConfig"
import { formatHyperparameters, formatTuningSearchSpace } from "../../../panels/modelling/hyperparameters"
import { EBMInteractionsConfig } from "../../../panels/modelling/EBMInteractionsConfig"
import { NumberField } from "../../../panels/modelling/NumberField"
import { SplitAndMetricsConfig } from "../../../panels/modelling/SplitAndMetricsConfig"
import { TargetAndTaskConfig } from "../../../panels/modelling/TargetAndTaskConfig"
import { useHyperparameterDrafts } from "../../../panels/modelling/useHyperparameterDrafts"
import type { BandingFactor } from "../../../types/banding"
import type { EditorView } from "@codemirror/view"
import CodeMirrorEditor from "../../../panels/editors/CodeMirrorEditor"
import ColourSwatches from "../ColourSwatches"
import ColumnsTab from "../../../panels/editors/ColumnsTab"
import EdgeJoinEditor from "../../../panels/editors/EdgeJoinEditor"
import { GraphProvider } from "../../../panels/GraphContext"
import { documentRequestScope } from "../../../utils/requestGateway"

type Config = Record<string, unknown>

/** A node's config with its undo history: each update is one undo step. */
function storedConfig(initial: Config) {
  let history: Config[] = [initial]
  const listeners = new Set<() => void>()
  const emit = () => listeners.forEach((listener) => listener())
  const update: OnUpdateConfig = (keyOrUpdates, value) => {
    const current = history[history.length - 1]
    history = [...history, typeof keyOrUpdates === "string" ? { ...current, [keyOrUpdates]: value } : { ...current, ...keyOrUpdates }]
    emit()
    return { ok: true }
  }
  return {
    get: () => history[history.length - 1],
    writes: () => history.length - 1,
    update,
    undo: () => {
      history = history.slice(0, -1)
      emit()
    },
    /** A change from outside the field: an Undo of an earlier edit, a sync frame. */
    moveTo: (next: Config) => {
      history = [...history.slice(0, -1), next]
      emit()
    },
    subscribe: (listener: () => void) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
  }
}
type Stored = ReturnType<typeof storedConfig>

function Panel({ stored, view }: { stored: Stored; view: (config: Config, onUpdate: OnUpdateConfig) => ReactElement }) {
  const config = useSyncExternalStore(stored.subscribe, stored.get)
  return view(config, stored.update)
}

type Entry = {
  name: string
  initial: Config
  /** The config a change from outside brings, holding another value for the field. */
  moved: Config
  view: (config: Config, onUpdate: OnUpdateConfig) => ReactElement
  field: () => HTMLElement
  typed: string
  /** What the field shows for a stored config. */
  shows: (config: Config) => string
  commit?: "blur" | "enter"
  /** For a field that is no `<input>` (a code editor): how it is typed in, read and left. */
  type?: (text: string) => void
  read?: () => string
  leave?: () => void
}

/** The code editor's view while one is rendered, to type in it as CodeMirror is typed in. */
let codeView: EditorView | null = null
const codeViewOf = (): EditorView => {
  if (codeView === null) throw new Error("No code editor is rendered")
  return codeView
}

const factorRules = (config: Config) => (config.factor as BandingFactor).rules as Record<string, string>[]
const categorical = (rules: { value: string; assignment: string }[]): Config => ({
  factor: { banding: "categorical", column: "region", output_column: "region_band", rules, default: null },
})

function Params({ config, onUpdate }: { config: Config; onUpdate: OnUpdateConfig }) {
  const drafts = useHyperparameterDrafts({
    nodeId: "catboost_1",
    params: config.params as Record<string, unknown>,
    tuning: (config.tuning as Record<string, unknown> | null) ?? null,
    reservedKeys: [],
    reservedKeysHelp: "",
    onUpdate,
  })
  return (
    <HyperparametersConfig
      algorithmLabel="CatBoost"
      onUpdate={onUpdate}
      drafts={drafts}
      tuning={(config.tuning as Record<string, unknown> | null) ?? null}
      evaluation={{ schema_version: 1, strategy: "random", seed: 42, validation: { method: "cross_validation", fold_count: 5 } }}
      metrics={["gini"]}
      refitOnDevelopment
    />
  )
}
const SPLIT_COLUMNS = [{ name: "loss_amount", dtype: "Float64" }, { name: "date_col", dtype: "Date" }]
const temporal = (testStart: string) => ({
  schema_version: 1,
  strategy: "temporal",
  date_column: "date_col",
  validation: { method: "single", start: "2025-01-01" },
  test: { start: testStart },
})
function Split({ config, onUpdate }: { config: Config; onUpdate: OnUpdateConfig }) {
  return (
    <SplitAndMetricsConfig
      columns={SPLIT_COLUMNS}
      rowLimit={(config.row_limit as number | null) ?? null}
      onRowLimitChange={(limit) => onUpdate("row_limit", limit)}
      evaluation={config.evaluation as Record<string, unknown>}
      onEvaluationChange={(evaluation) => onUpdate("evaluation", evaluation)}
      refitOnDevelopment
      onRefitOnDevelopmentChange={() => {}}
      tuningEnabled={false}
      preview={null}
    />
  )
}
const CLASSIFICATION_COLUMNS = [{ name: "outcome", dtype: "String" }, { name: "exposure", dtype: "Float64" }]

const tuning = (searchSpace: Record<string, unknown>) => ({ schema_version: 1, trial_count: 20, seed: 42, metric: "gini", search_space: searchSpace })

const ENTRIES: Entry[] = [
  {
    name: "a code editor's text",
    initial: { code: "df = df" },
    moved: { code: "df = df.head()" },
    view: (config, onUpdate) => (
      <CodeMirrorEditor defaultValue={String(config.code)} onChange={(v) => onUpdate("code", v)} onEditorView={(view) => { codeView = view }} />
    ),
    field: () => codeViewOf().contentDOM,
    typed: "df = df.tail()",
    shows: (config) => String(config.code),
    type: (text) => {
      const view = codeViewOf()
      for (let i = 1; i <= text.length; i++) {
        act(() => { view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text.slice(0, i) } }) })
      }
    },
    read: () => codeViewOf().state.doc.toString(),
    leave: () => act(() => { codeViewOf().contentDOM.dispatchEvent(new Event("blur")) }),
  },
  {
    name: "the colour picker",
    initial: { colour: "#112233" },
    moved: { colour: "#445566" },
    view: (config, onUpdate) => (
      <ColourSwatches name="Series" value={config.colour as string | null} onCommit={(v) => onUpdate("colour", v)} />
    ),
    field: () => screen.getByLabelText("Custom colour for Series"),
    // The picker's value is a colour, lower case: each step of the pick is one.
    typed: "#aa0000",
    shows: (config) => String(config.colour).toLowerCase(),
    type: (text) => fireEvent.change(screen.getByLabelText("Custom colour for Series"), { target: { value: text } }),
  },
  {
    // A key box of a list: the box at the place of a row the change from outside removed
    // holds the same text, which a draft kept by position would be carried over.
    name: "an Edge Join key box",
    initial: { on: ["policy_id", "region", "region"] },
    moved: { on: ["region", "region"] },
    view: (config, onUpdate) => (
      <GraphProvider allNodes={[]} edges={[]} requestScope={documentRequestScope}>
        <EdgeJoinEditor config={config} onUpdate={onUpdate} nodeId="join_1" accentColor="#22d3ee" />
      </GraphProvider>
    ),
    field: () => screen.getByRole("textbox", { name: "Same-name key 2" }),
    typed: "region_code",
    shows: (config) => (config.on as string[])[1],
  },
  {
    name: "a committed text field",
    initial: { suffix: "_left" },
    moved: { suffix: "_l" },
    view: (config, onUpdate) => <CommittedTextField aria-label="Suffix" value={String(config.suffix)} onCommit={(v) => onUpdate("suffix", v)} />,
    field: () => screen.getByLabelText("Suffix") as HTMLInputElement,
    typed: "_right",
    shows: (config) => String(config.suffix),
  },
  {
    name: "a committed text area",
    initial: { query: "select 1" },
    moved: { query: "select 2" },
    view: (config, onUpdate) => <CommittedTextArea aria-label="Query" value={String(config.query)} onCommit={(v) => onUpdate("query", v)} />,
    field: () => screen.getByLabelText("Query") as HTMLTextAreaElement,
    typed: "select 3",
    shows: (config) => String(config.query),
  },
  {
    name: "a validated text field",
    initial: { name: "pivot_a" },
    moved: { name: "pivot_b" },
    view: (config, onUpdate) => (
      <ValidatedTextField
        dataTestId="pivot-name"
        ariaLabel="Pivot name"
        value={String(config.name)}
        validate={(v) => (v.trim() ? null : "Enter a name")}
        onCommit={(v) => onUpdate("name", v)}
        containerClassName=""
        className=""
        style={{}}
      />
    ),
    field: () => screen.getByLabelText("Pivot name") as HTMLInputElement,
    typed: "pivot_c",
    shows: (config) => String(config.name),
  },
  {
    name: "a Banding breakpoint's band name",
    initial: { rules: [{ boundary: "40", label: "Age 40 or below" }, { boundary: "", label: "Over 40" }] },
    moved: { rules: [{ boundary: "40", label: "Under 41" }, { boundary: "", label: "Over 40" }] },
    view: (config, onUpdate) => (
      <BreakpointGrid breakpoints={config.rules as { boundary: string; label: string }[]} onUpdate={(rules) => onUpdate("rules", rules)} accentColor="#22d3ee" />
    ),
    field: () => screen.getByLabelText("Breakpoint 1 label") as HTMLInputElement,
    typed: "Young",
    shows: (config) => (config.rules as { label: string }[])[0].label,
    commit: "enter",
  },
  {
    name: "a Banding breakpoint's boundary",
    initial: { rules: [{ boundary: "40", label: "Young" }] },
    moved: { rules: [{ boundary: "35", label: "Young" }] },
    view: (config, onUpdate) => (
      <BreakpointGrid breakpoints={config.rules as { boundary: string; label: string }[]} onUpdate={(rules) => onUpdate("rules", rules)} accentColor="#22d3ee" />
    ),
    field: () => screen.getByLabelText("Breakpoint 1 boundary") as HTMLInputElement,
    typed: "45",
    shows: (config) => (config.rules as { boundary: string }[])[0].boundary,
  },
  {
    name: "a Banding rule's value",
    initial: categorical([{ value: "North", assignment: "N" }]),
    moved: categorical([{ value: "South", assignment: "N" }]),
    view: (config, onUpdate) => (
      <BandingRulesGrid factor={config.factor as BandingFactor} onUpdateFactor={(patch) => onUpdate("factor", { ...(config.factor as BandingFactor), ...patch })} />
    ),
    field: () => screen.getByLabelText("Rule 1 match value") as HTMLInputElement,
    typed: "East",
    shows: (config) => factorRules(config)[0].value,
  },
  {
    name: "a Banding rule's group",
    initial: categorical([{ value: "North", assignment: "N" }]),
    moved: categorical([{ value: "North", assignment: "Northern" }]),
    view: (config, onUpdate) => (
      <BandingRulesGrid factor={config.factor as BandingFactor} onUpdateFactor={(patch) => onUpdate("factor", { ...(config.factor as BandingFactor), ...patch })} />
    ),
    field: () => screen.getByLabelText("Rule 1 group name") as HTMLInputElement,
    typed: "Top",
    shows: (config) => factorRules(config)[0].assignment,
  },
  {
    name: "the Parameters JSON",
    initial: { params: { depth: 6 }, tuning: null },
    moved: { params: { depth: 4 }, tuning: null },
    view: (config, onUpdate) => <Params config={config} onUpdate={onUpdate} />,
    field: () => screen.getByLabelText("CatBoost hyperparameters JSON") as HTMLTextAreaElement,
    typed: '{"depth": 8}',
    shows: (config) => formatHyperparameters(config.params as Record<string, unknown>),
  },
  {
    name: "the search space JSON",
    initial: { params: {}, tuning: tuning({ depth: [4, 6] }) },
    moved: { params: {}, tuning: tuning({ depth: [5, 7] }) },
    view: (config, onUpdate) => <Params config={config} onUpdate={onUpdate} />,
    field: () => screen.getByLabelText("CatBoost search space JSON") as HTMLTextAreaElement,
    typed: '{"depth": [8, 10]}',
    shows: (config) => formatTuningSearchSpace((config.tuning as { search_space: Record<string, unknown> }).search_space),
  },
  {
    name: "a modelling number field",
    initial: { cv_folds: 5 },
    moved: { cv_folds: 3 },
    view: (config, onUpdate) => (
      <NumberField label="Folds" value={config.cv_folds as number} min={2} integer onCommit={(v) => onUpdate("cv_folds", v)} />
    ),
    field: () => screen.getByLabelText("Folds") as HTMLInputElement,
    typed: "7",
    shows: (config) => String(config.cv_folds),
  },
  {
    name: "the row limit",
    initial: { row_limit: 50000, evaluation: temporal("2025-06-01") },
    moved: { row_limit: 20000, evaluation: temporal("2025-06-01") },
    view: (config, onUpdate) => <Split config={config} onUpdate={onUpdate} />,
    field: () => screen.getByLabelText("Row limit") as HTMLInputElement,
    typed: "75000",
    shows: (config) => String(config.row_limit),
  },
  {
    name: "a temporal test set's start date",
    initial: { row_limit: null, evaluation: temporal("2025-06-01") },
    moved: { row_limit: null, evaluation: temporal("2025-07-01") },
    view: (config, onUpdate) => <Split config={config} onUpdate={onUpdate} />,
    field: () => screen.getByLabelText("Test starts") as HTMLInputElement,
    typed: "2025-09-01",
    shows: (config) => (config.evaluation as { test: { start: string } }).test.start,
  },
  {
    name: "a classification target's positive class",
    initial: { loss_function: "Logloss", task: "classification", positive_class: "claim" },
    moved: { loss_function: "Logloss", task: "classification", positive_class: "lapse" },
    view: (config, onUpdate) => (
      <TargetAndTaskConfig config={config} onUpdate={onUpdate} columns={CLASSIFICATION_COLUMNS} target="outcome" weight="" metrics={["gini"]} />
    ),
    field: () => screen.getByLabelText("Positive class") as HTMLInputElement,
    typed: "renewal",
    shows: (config) => String(config.positive_class),
  },
  {
    name: "an EBM's interaction count",
    initial: { algorithm: "ebm", target: "claims", params: { interactions: 3 } },
    moved: { algorithm: "ebm", target: "claims", params: { interactions: 5 } },
    view: (config, onUpdate) => (
      <EBMInteractionsConfig config={config} onUpdate={onUpdate} columns={[{ name: "claims", dtype: "Float64" }, { name: "age", dtype: "Float64" }]} />
    ),
    field: () => screen.getByLabelText("Maximum interaction count") as HTMLInputElement,
    typed: "12",
    shows: (config) => String((config.params as { interactions: number }).interactions),
  },
  {
    name: "a rating table's relativity cell",
    initial: { value: 1.25 },
    moved: { value: 1.1 },
    view: (config, onUpdate) => (
      <NumberCell aria-label="Relativity" value={config.value as number} onCommit={(v) => onUpdate("value", v)} />
    ),
    field: () => screen.getByLabelText("Relativity") as HTMLInputElement,
    typed: "1.5",
    shows: (config) => String(config.value),
  },
  {
    name: "an I/O format argument's value",
    initial: { arguments: { separator: "," } },
    moved: { arguments: { separator: ";" } },
    view: (config, onUpdate) => (
      <IoArgumentsEditor value={config.arguments} argumentNames={["separator"]} context="CSV" onCommit={(next) => onUpdate("arguments", next)} />
    ),
    field: () => screen.getByLabelText("Argument 1 value") as HTMLInputElement,
    typed: '"|"',
    shows: (config) => JSON.stringify((config.arguments as Record<string, unknown>).separator),
  },
]

afterEach(cleanup)

/** Type *text* a key at a time, as an analyst does: each keystroke is a change of its own. */
function typeInto(field: () => HTMLElement, text: string) {
  for (let i = 1; i <= text.length; i++) fireEvent.change(field(), { target: { value: text.slice(0, i) } })
}

describe.each(ENTRIES)("$name", (entry) => {
  const leave = (field: HTMLElement) => {
    if (entry.leave) entry.leave()
    else if (entry.commit === "enter") fireEvent.keyDown(field, { key: "Enter" })
    else fireEvent.blur(field)
  }
  const type = (text: string) => (entry.type ? entry.type(text) : typeInto(entry.field, text))
  const shown = () => (entry.read ? entry.read() : (entry.field() as HTMLInputElement).value)

  it("shows the store's value when it moves under what is being typed, and writes nothing of it", () => {
    const stored = storedConfig(entry.initial)
    render(<Panel stored={stored} view={entry.view} />)
    type(entry.typed)
    expect(shown()).toBe(entry.typed)
    expect(stored.writes()).toBe(0)

    act(() => stored.moveTo(entry.moved))

    expect(shown()).toBe(entry.shows(entry.moved))
    leave(entry.field())
    expect(stored.get()).toEqual(entry.moved)
  })

  it("commits what was typed once, and one Undo puts the earlier value back", () => {
    const stored = storedConfig(entry.initial)
    render(<Panel stored={stored} view={entry.view} />)
    type(entry.typed)
    leave(entry.field())
    expect(stored.writes()).toBe(1)

    act(() => stored.undo())

    expect(stored.get()).toEqual(entry.initial)
    expect(shown()).toBe(entry.shows(entry.initial))
  })
})

describe("the Columns tab's empty selection", () => {
  const view = (config: Config, onUpdate: OnUpdateConfig) => (
    <ColumnsTab
      config={config}
      onUpdate={onUpdate}
      availableColumns={[{ name: "policy_id", dtype: "Int64" }, { name: "region", dtype: "String" }, { name: "premium", dtype: "Float64" }]}
      columns={[]}
    />
  )
  const none = () => screen.queryByText("Select at least one column to apply.")
  const ticked = () => screen.getAllByRole("checkbox").filter((box) => (box as HTMLInputElement).checked).length

  it("lets None go when the selection moves under it, and writes nothing of it", () => {
    const stored = storedConfig({ selected_columns: ["policy_id"] })
    render(<Panel stored={stored} view={view} />)
    fireEvent.click(screen.getByRole("button", { name: "None" }))
    expect(none()).not.toBeNull()
    expect(ticked()).toBe(0)

    act(() => stored.moveTo({ selected_columns: ["region", "premium"] }))

    expect(none()).toBeNull()
    expect(ticked()).toBe(2)
    expect(stored.writes()).toBe(0)
  })

  it("commits the column chosen after None once, and one Undo puts the selection back", () => {
    const stored = storedConfig({ selected_columns: ["policy_id"] })
    render(<Panel stored={stored} view={view} />)
    fireEvent.click(screen.getByRole("button", { name: "None" }))
    fireEvent.click(screen.getAllByRole("checkbox")[1])
    expect(stored.writes()).toBe(1)
    expect(stored.get()).toEqual({ selected_columns: ["region"] })

    act(() => stored.undo())

    expect(none()).toBeNull()
    expect(stored.get()).toEqual({ selected_columns: ["policy_id"] })
    expect(ticked()).toBe(1)
  })
})

describe("the colour picker, while each step of a pick is previewed", () => {
  // The owner shows each step in the store without recording it, as the workbench's
  // sheets do, and commits the pick as the picker is left.
  function Previewed({ stored }: { stored: Stored }) {
    const config = useSyncExternalStore(stored.subscribe, stored.get)
    return (
      <ColourSwatches
        name="Fill"
        value={config.colour as string | null}
        onCommit={(v) => stored.update("colour", v)}
        onPreview={(v) => stored.moveTo({ ...stored.get(), colour: v })}
      />
    )
  }
  const picker = () => screen.getByLabelText("Custom colour for Fill") as HTMLInputElement

  it("lets the pick go when the colour moves under it from outside, and commits nothing of it", () => {
    const stored = storedConfig({ colour: "#112233" })
    render(<Previewed stored={stored} />)
    fireEvent.change(picker(), { target: { value: "#aa0000" } })
    expect(stored.get()).toEqual({ colour: "#AA0000" })

    act(() => stored.moveTo({ colour: "#445566" }))

    expect(picker().value).toBe("#445566")
    fireEvent.blur(picker())
    expect(stored.get()).toEqual({ colour: "#445566" })
    expect(stored.writes()).toBe(0)
  })

  it("commits the previewed pick once as the picker is left", () => {
    const stored = storedConfig({ colour: "#112233" })
    render(<Previewed stored={stored} />)
    fireEvent.change(picker(), { target: { value: "#aa0000" } })
    fireEvent.change(picker(), { target: { value: "#bb0000" } })
    fireEvent.blur(picker())
    expect(stored.writes()).toBe(1)
    expect(stored.get()).toEqual({ colour: "#BB0000" })
  })
})
