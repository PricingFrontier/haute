/**
 * The schema editor (specs/workbench): tables and their columns under two headings, Inputs
 * and Outputs, defined in the view, each field an edit of the form store once it commits,
 * with what a sheet shows protected by a confirmation, and the columns the quote store keeps
 * in each table shown locked.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { ApiError } from "../../api/client"
import { listProjectFiles } from "../../api/projectFiles"
import type { FormSpec, ProjectFolderResponse, SchemaColumn, SchemaTable } from "../../api/types"
import { fetchParamFile } from "../../api/workbench"
import useDocumentStatusStore from "../../stores/useDocumentStatusStore"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchStore from "../../stores/useWorkbenchStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import { answerQuestion, asked } from "../../test-utils/question"
import { BLANK_THEME, COLLECTION_OPTIONS, createWidget, TABLE_OPTIONS, updateSchemaColumn } from "../../utils/workbenchForm"
import SchemaEditor from "../SchemaEditor"
import { detailsTable } from "./fixtures"

vi.mock("../../api/projectFiles", () => ({ listProjectFiles: vi.fn() }))
vi.mock("../../api/workbench", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/workbench")>()),
  fetchParamFile: vi.fn(),
}))

/** The params folder as the Pipeline files panel lists it, holding `names`. */
const paramsFolder = (...names: string[]): ProjectFolderResponse => ({
  place: "pipeline",
  path: "params",
  root: "rating/files",
  writable: true,
  entries: names.map((name) => ({
    name,
    path: `params/${name}`,
    kind: name.includes(".") ? "file" : "folder",
    modified: null,
    revision: null,
    size: null,
    status: null,
  })),
})

const column = (name: string, overrides: Partial<SchemaColumn> = {}): SchemaColumn => ({
  name,
  type: "str",
  label: "",
  key: false,
  required: false,
  min: null,
  max: null,
  options: [],
  options_source: null,
  row_per_value: false,
  index: false,
  help: "",
  placeholder: "",
  group: "",
  default_value: "",
  number_format: "general",
  decimal_places: null,
  use_grouping: true,
  ...overrides,
})

const table = (name: string, overrides: Partial<SchemaTable> = {}): SchemaTable => ({
  name,
  rows: "one",
  inputs: [],
  outputs: [],
  ...overrides,
})

/** A many-row input table's index: each row's number, from 1. */
const INDEX = column("row_number", { type: "int", index: true })
/** The quote's identity as Haute makes it at the head of a table's first block. */
const IDENTITY = [column("quote_reference"), column("quote_option", { type: "int" })]

const form = (...tables: SchemaTable[]): FormSpec => ({
  version: 1,
  name: "motor",
  theme: { ...BLANK_THEME },
  schema: { tables },
  pages: [
    {
      id: "page_1",
      title: "Sheet 1",
      width: 1280,
      show_when: { match: "all", conditions: [] },
      sections: [{ id: "section_1", title: "Section 1", height: 560, show_when: { match: "all", conditions: [] }, widgets: [] }],
    },
  ],
  sample: {},
})

const POLICY = table("policy_details", {
  inputs: [
    column("state", { options: ["CA", "NY"], options_source: { file: "states.csv", column: "state" } }),
    column("exposure", { type: "float" }),
  ],
})
/** A many-row table of outputs alone, keyed by the layer pricing fills. */
const LAYERS = table("layers", {
  rows: "many",
  outputs: [column("layer", { type: "int", key: true }), column("limit", { type: "float" })],
})

function load(spec: FormSpec): void {
  useWorkbenchFormStore.setState({
    form: spec,
    revision: "rev-0",
    status: "ready",
    savedForm: JSON.stringify(spec),
    dirty: false,
    undoStack: [],
    redoStack: [],
  })
}

const current = () => {
  const { form: spec } = useWorkbenchFormStore.getState()
  if (spec === null) throw new Error("no form")
  return spec
}
const tableNamed = (name: string) => screen.getByRole("region", { name: `Table ${name}` })
/** The quote's identity, which every edit of a table's rows or blocks puts at the head of its first block. */
const IDENTITY_NAMES: ReadonlySet<string> = new Set(["quote_reference", "quote_option"])
/** `columns` but the quote's identity. */
const own = <T extends { name: string }>(columns: readonly T[]): T[] => columns.filter((column) => !IDENTITY_NAMES.has(column.name))
/** The names in the column name fields under `root`, in order. */
const nameFields = (root: HTMLElement): string[] =>
  within(root).queryAllByRole("textbox", { name: "Column name" }).map((input) => (input as HTMLInputElement).value)
/** A table's columns' names as the editor lists them, the quote's identity aside. */
const columnNames = (name: string): string[] => nameFields(tableNamed(name)).filter((value) => !IDENTITY_NAMES.has(value))
const tableNames = () => screen.getAllByRole("textbox", { name: "Table name" }).map((input) => (input as HTMLInputElement).value)
const tableNameField = (name: string) => within(tableNamed(name)).getByRole("textbox", { name: "Table name" })
/** The grip a table, its header's, or a column is dragged by, or null while it cannot move. */
const tableGrip = (name: string) => within(tableNamed(name)).queryAllByTitle("Drag to move")[0] ?? null
const columnGrip = (name: string) => within(screen.getByTestId(`schema-column-${name}`)).queryByTitle("Drag to move")
/** A table's block of columns under its heading, Inputs or Outputs. */
const blockOf = (name: string, title: "Inputs" | "Outputs") => within(tableNamed(name)).getByRole("group", { name: `${name} ${title}` })
/** A block's columns' names, the quote's identity aside; `everyName` with it. */
const everyName = (name: string, title: "Inputs" | "Outputs"): string[] => nameFields(blockOf(name, title))
const blockNames = (name: string, title: "Inputs" | "Outputs") => everyName(name, title).filter((value) => !IDENTITY_NAMES.has(value))
/** A block's heading, which a column carried from the other block is dropped on to go at its end. */
const headingOf = (name: string, title: "Inputs" | "Outputs") => within(blockOf(name, title)).getByText(title)

/** Drag `grip` onto `target` and let go. */
function dragOnto(grip: HTMLElement, target: HTMLElement): void {
  fireEvent.dragStart(grip)
  fireEvent.dragOver(target)
  fireEvent.drop(target)
  fireEvent.dragEnd(grip)
}

/** Type into a committed field and commit with blur. */
function commit(input: HTMLElement, value: string): void {
  fireEvent.change(input, { target: { value } })
  fireEvent.blur(input)
}

describe("SchemaEditor", () => {
  beforeEach(() => {
    load(form(POLICY, LAYERS))
    vi.mocked(listProjectFiles).mockResolvedValue(paramsFolder())
    vi.mocked(fetchParamFile).mockResolvedValue({ columns: ["state"], values: ["CA", "NY"] })
  })

  afterEach(() => {
    cleanup()
    useWorkbenchFormStore.setState({ form: null, status: "idle", undoStack: [], redoStack: [] })
    useWorkbenchViewStore.setState({ schemaCollapsed: new Set(), schemaDetails: null, schemaScroll: 0, schemaSearch: "" })
    useWorkbenchStore.setState({ storeColumns: {} })
    useDocumentStatusStore.setState({ capabilities: null })
    vi.mocked(fetchParamFile).mockReset()
  })

  it("lists the tables with their columns' names and types under Inputs and Outputs, each type shown as its marker, and no role", () => {
    render(<SchemaEditor />)

    expect(columnNames("policy_details")).toEqual(["state", "exposure"])
    expect(blockNames("policy_details", "Inputs")).toEqual(["state", "exposure"])
    expect(blockNames("layers", "Inputs")).toEqual([])
    expect(blockNames("layers", "Outputs")).toEqual(["layer", "limit"])
    // A heading over no column of its own shows Add column alone.
    expect(within(blockOf("policy_details", "Outputs")).getByRole("button", { name: "Add column" })).toBeInTheDocument()
    expect(screen.getByRole("combobox", { name: "Type of exposure" })).toHaveValue("float")
    expect(screen.queryByRole("combobox", { name: "Role of layers" })).toBeNull()
    expect(screen.getByRole("combobox", { name: "Rows of layers" })).toHaveValue("many")
    expect(within(tableNamed("policy_details")).getByText("2 cols")).toBeInTheDocument()
  })

  it("keeps Haute's quote_details table as it is: labels and help, and rules only where the underwriter types", () => {
    load(form(detailsTable(), POLICY))
    render(<SchemaEditor />)
    const details = tableNamed("quote_details")

    expect(within(details).getByRole("textbox", { name: "Table name" })).toHaveAttribute("readonly")
    expect(within(details).getByRole("combobox", { name: "Rows of quote_details" })).toBeDisabled()
    // Its inputs alone, and no output given it.
    expect(within(details).queryByRole("group", { name: "quote_details Outputs" })).toBeNull()
    expect(within(details).queryByRole("button", { name: "Delete table quote_details" })).toBeNull()
    expect(within(details).queryByRole("button", { name: /^Remove column/ })).toBeNull()
    expect(within(details).queryByRole("button", { name: "Add column" })).toBeNull()
    expect(within(details).queryByRole("combobox", { name: /^Type of/ })).toBeNull()
    for (const name of within(details).getAllByRole("textbox", { name: "Column name" })) expect(name).toHaveAttribute("readonly")
    // The columns the app sets are not marked so beside their names.
    expect(within(details).queryByText("set by the app")).toBeNull()

    // A column the underwriter types takes a label, a default and rules; one the app sets, a
    // label and a group alone.
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of insured" }))
    commit(screen.getByRole("textbox", { name: "Label of insured" }), "Client")
    commit(screen.getByRole("textbox", { name: "Default of insured" }), "Acme Ltd")
    fireEvent.click(screen.getByRole("checkbox", { name: "insured is required" }))
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of stage" }))
    commit(screen.getByRole("textbox", { name: "Label of stage" }), "Status")
    commit(screen.getByRole("combobox", { name: "Group of stage" }), "Workflow")
    expect(screen.queryByRole("checkbox", { name: "stage is required" })).toBeNull()
    expect(screen.queryByLabelText("Default of stage")).toBeNull()
    expect(current().schema.tables[0].inputs.find((column) => column.name === "insured")).toMatchObject({
      label: "Client",
      required: true,
      default_value: "Acme Ltd",
    })
    expect(current().schema.tables[0].inputs.find((column) => column.name === "stage")).toMatchObject({
      label: "Status",
      group: "Workflow",
      required: false,
      default_value: "",
    })
  })

  it("adds a table, whose name takes the focus, and records it for undo", () => {
    render(<SchemaEditor />)

    fireEvent.click(screen.getByRole("button", { name: "Add table" }))

    const added = screen.getByRole("region", { name: "Table table" })
    expect(within(added).getByRole("textbox", { name: "Table name" })).toBe(document.activeElement)
    expect(current().schema.tables.map((t) => t.name)).toEqual(["policy_details", "layers", "table"])
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
    expect(useWorkbenchFormStore.getState().dirty).toBe(true)
  })

  it("renames a table once its field commits, as one undo step", () => {
    render(<SchemaEditor />)
    const name = within(tableNamed("policy_details")).getByRole("textbox", { name: "Table name" })

    fireEvent.change(name, { target: { value: "policy" } })
    expect(current().schema.tables[0].name).toBe("policy_details")
    fireEvent.keyDown(name, { key: "Enter" })

    expect(current().schema.tables[0].name).toBe("policy")
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
  })

  it("changes a table's rows: many gives a table with inputs its index, marked as its key; one drops its keys and its index", () => {
    render(<SchemaEditor />)

    fireEvent.change(screen.getByRole("combobox", { name: "Rows of policy_details" }), { target: { value: "many" } })
    fireEvent.change(screen.getByRole("combobox", { name: "Rows of layers" }), { target: { value: "one" } })

    expect(current().schema.tables[0]).toMatchObject({ rows: "many" })
    expect(own(current().schema.tables[0].inputs).map((c) => [c.name, c.index])).toEqual([
      ["row_number", true],
      ["state", false],
      ["exposure", false],
    ])
    // The index keys the table: a mark, not a control, and no key toggle on an input.
    const index = screen.getByTestId("schema-column-row_number")
    expect(within(index).getByRole("img", { name: "row_number is the key" })).toBeInTheDocument()
    expect(within(index).queryByRole("button", { name: /^Key:/ })).toBeNull()
    expect(screen.queryByRole("button", { name: "Key: state" })).toBeNull()
    expect(current().schema.tables[1].rows).toBe("one")
    expect(own(current().schema.tables[1].outputs).map((c) => c.key)).toEqual([false, false])
    // One edit each.
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(2)

    fireEvent.change(screen.getByRole("combobox", { name: "Rows of policy_details" }), { target: { value: "one" } })
    expect(own(current().schema.tables[0].inputs).map((c) => c.name)).toEqual(["state", "exposure"])
  })

  it("picks a column's type from its marker, and marks a key only on an output of a many-row table of outputs alone", () => {
    render(<SchemaEditor />)

    fireEvent.change(screen.getByRole("combobox", { name: "Type of state" }), { target: { value: "bool" } })
    const key = screen.getByRole("button", { name: "Key: limit" })
    expect(key).toHaveAttribute("aria-pressed", "false")
    fireEvent.click(key)

    expect(current().schema.tables[0].inputs[0].type).toBe("bool")
    expect(current().schema.tables[1].outputs[1].key).toBe(true)
    // An input is never a key, nor an output of a one-row table; a one-row table shows no key at all.
    expect(screen.queryByRole("button", { name: "Key: state" })).not.toBeInTheDocument()
    expect(within(screen.getByTestId("schema-column-state")).queryByRole("img", { name: /key/ })).toBeNull()

    // Gaining its first input, the table is keyed by its index: its outputs' keys go, and their toggles.
    fireEvent.click(within(blockOf("layers", "Inputs")).getByRole("button", { name: "Add column" }))
    expect(own(current().schema.tables[1].inputs).map((c) => c.name)).toEqual(["row_number", "column"])
    expect(own(current().schema.tables[1].outputs).map((c) => c.key)).toEqual([false, false])
    expect(screen.queryByRole("button", { name: /^Key: / })).toBeNull()
    expect(within(screen.getByTestId("schema-column-row_number")).getByRole("img", { name: "row_number is the key" })).toBeInTheDocument()
  })

  it("marks the column giving a table a row for each value as its key, not its index, and every other column as not the key", () => {
    const perils = { options: ["AOP", "EQ"], options_source: { file: "perils.csv", column: "peril" }, row_per_value: true }
    load(
      form(
        table("coverage", {
          rows: "many",
          inputs: [...IDENTITY, INDEX, column("peril", perils), column("limit", { type: "float" })],
          outputs: [column("premium", { type: "float" })],
        }),
      ),
    )
    render(<SchemaEditor />)

    expect(within(screen.getByTestId("schema-column-peril")).getByRole("img", { name: "peril is the key" })).toBeInTheDocument()
    expect(within(screen.getByTestId("schema-column-row_number")).queryByRole("img", { name: "row_number is the key" })).toBeNull()
    // Grey on the rest, an output too, and still not a control; none on the quote's identity, never a key.
    for (const name of ["row_number", "limit", "premium"]) {
      expect(within(screen.getByTestId(`schema-column-${name}`)).getByRole("img", { name: `${name} is not the key` })).toBeInTheDocument()
    }
    expect(screen.queryByRole("button", { name: /^Key: / })).toBeNull()
    expect(within(screen.getByTestId("schema-column-quote_reference")).queryByRole("img", { name: /key/ })).toBeNull()
  })

  it("adds a column to either block with its own Add column, an output taking no input's rules", () => {
    render(<SchemaEditor />)

    fireEvent.click(within(blockOf("policy_details", "Outputs")).getByRole("button", { name: "Add column" }))
    expect(blockNames("policy_details", "Outputs")).toEqual(["column"])
    expect(current().schema.tables[0].outputs.map((c) => [c.name, c.type])).toEqual([["column", "str"]])
    // Named apart from every column of the table, whichever block holds it.
    fireEvent.click(within(blockOf("policy_details", "Inputs")).getByRole("button", { name: "Add column" }))
    expect(blockNames("policy_details", "Inputs")).toEqual(["state", "exposure", "column_2"])
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(2)

    fireEvent.click(screen.getByRole("button", { name: "Label and rules of column" }))
    expect(screen.getByRole("checkbox", { name: "column is required" })).toBeInTheDocument()
    expect(screen.queryByRole("textbox", { name: "Placeholder of column" })).toBeNull()
    expect(screen.queryByLabelText("Default of column")).toBeNull()
  })

  it("moves a column with Alt+Up and Alt+Down within its block alone", () => {
    load(form(table("policy_details", { inputs: [column("state"), column("exposure")], outputs: [column("premium"), column("fee")] })))
    render(<SchemaEditor />)
    const [, , premium, fee] = within(tableNamed("policy_details")).getAllByRole("textbox", { name: "Column name" })

    // The first output goes no higher than its block's head.
    fireEvent.keyDown(premium, { key: "ArrowUp", altKey: true })
    expect(blockNames("policy_details", "Inputs")).toEqual(["state", "exposure"])
    expect(blockNames("policy_details", "Outputs")).toEqual(["premium", "fee"])
    fireEvent.keyDown(fee, { key: "ArrowUp", altKey: true })
    expect(blockNames("policy_details", "Outputs")).toEqual(["fee", "premium"])
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
  })

  it("drags a column into the other block: one reaching the outputs loses what only an input takes and its sample values, one reaching the inputs its key", () => {
    const priced = form(
      table("policy_details", {
        inputs: [
          column("state", { required: true, options: ["CA", "NY"], options_source: { file: "states.csv", column: "state" } }),
          column("exposure", { type: "float", min: 0, default_value: "5" }),
        ],
        outputs: [column("premium", { type: "float" })],
      }),
      LAYERS,
    )
    priced.sample = { policy_details: [{ state: "CA", exposure: "100" }] }
    load(priced)
    render(<SchemaEditor />)

    // Dropped on the other block's row, it goes there.
    dragOnto(columnGrip("exposure")!, screen.getByTestId("schema-column-premium"))
    expect(blockNames("policy_details", "Inputs")).toEqual(["state"])
    expect(blockNames("policy_details", "Outputs")).toEqual(["exposure", "premium"])
    expect(current().schema.tables[0].outputs[0]).toMatchObject({ name: "exposure", min: null, default_value: "" })
    expect(current().sample.policy_details).toEqual([{ state: "CA" }])
    // Dropped on the other block's heading, it goes at its end, keeping Required alone.
    dragOnto(columnGrip("state")!, headingOf("policy_details", "Outputs"))
    expect(blockNames("policy_details", "Outputs")).toEqual(["exposure", "premium", "state"])
    expect(current().schema.tables[0].outputs.find((c) => c.name === "state")).toMatchObject({ required: true, options: [], options_source: null })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(2)

    // An output keyed in a table of outputs alone loses its key in the inputs.
    dragOnto(columnGrip("layer")!, headingOf("layers", "Inputs"))
    expect(current().schema.tables[1].inputs.find((c) => c.name === "layer")).toMatchObject({ key: false })
  })

  it("leaves a table whose last typed input moves to its outputs with no index, the identity heading its outputs and their key toggles offered", () => {
    load(form(table("items", { rows: "many", inputs: [...IDENTITY, INDEX, column("value", { type: "float" })], outputs: [column("premium", { type: "float" })] })))
    render(<SchemaEditor />)
    expect(screen.queryByRole("button", { name: /^Key: / })).toBeNull()

    dragOnto(columnGrip("value")!, headingOf("items", "Outputs"))

    const [items] = current().schema.tables
    expect(items.inputs).toEqual([])
    expect(items.outputs.map((c) => c.name)).toEqual(["quote_reference", "quote_option", "premium", "value"])
    expect(blockNames("items", "Inputs")).toEqual([])
    expect(everyName("items", "Outputs")).toEqual(["quote_reference", "quote_option", "premium", "value"])
    expect(screen.getByRole("button", { name: "Key: premium" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Key: value" })).toBeInTheDocument()
    // The identity, now heading the outputs, is still Haute's: no key of it.
    expect(screen.queryByRole("button", { name: "Key: quote_reference" })).toBeNull()
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
  })

  it("adds the next column on Enter in the last column's name alone, and moves one with Alt+Up", () => {
    render(<SchemaEditor />)
    const [state, exposure] = within(tableNamed("policy_details")).getAllByRole("textbox", { name: "Column name" })

    // Enter in a column with others under it commits its name and adds nothing.
    fireEvent.change(state, { target: { value: "region" } })
    fireEvent.keyDown(state, { key: "Enter" })
    expect(columnNames("policy_details")).toEqual(["region", "exposure"])

    fireEvent.keyDown(exposure, { key: "Enter" })
    expect(columnNames("policy_details")).toEqual(["region", "exposure", "column"])
    // It takes the type of the column above, Decimal.
    expect(current().schema.tables[0].inputs.find((c) => c.name === "column")?.type).toBe("float")
    // The quote's identity now heads the table, as every edit of its blocks leaves it.
    const nameOf = (column: string) => within(screen.getByTestId(`schema-column-${column}`)).getByRole("textbox", { name: "Column name" })
    const added = nameOf("column")
    expect(added).toBe(document.activeElement)

    fireEvent.keyDown(added, { key: "ArrowUp", altKey: true })
    expect(columnNames("policy_details")).toEqual(["region", "column", "exposure"])
    // The name field keeps the focus in its new place.
    expect(nameOf("column")).toBe(document.activeElement)
  })

  it("adds a column of the last column's type with Add column", () => {
    render(<SchemaEditor />)
    const addColumn = () => fireEvent.click(within(blockOf("policy_details", "Inputs")).getByRole("button", { name: "Add column" }))

    addColumn()
    fireEvent.change(screen.getByRole("combobox", { name: "Type of column" }), { target: { value: "date" } })
    addColumn()

    expect(own(current().schema.tables[0].inputs).map((c) => [c.name, c.type])).toEqual([
      ["state", "str"],
      ["exposure", "float"],
      ["column", "date"],
      ["column_2", "date"],
    ])
  })

  it("moves a table with Alt+Up and Alt+Down in its name, keeping the focus, quote_details staying first", () => {
    load(form(detailsTable(), POLICY, LAYERS))
    render(<SchemaEditor />)

    fireEvent.keyDown(tableNameField("layers"), { key: "ArrowUp", altKey: true })
    expect(tableNames()).toEqual(["quote_details", "layers", "policy_details"])
    // The name field keeps the focus in its new place.
    expect(tableNameField("layers")).toBe(document.activeElement)

    // Nothing goes above quote_details, which does not move: neither is an edit.
    fireEvent.keyDown(tableNameField("layers"), { key: "ArrowUp", altKey: true })
    fireEvent.keyDown(tableNameField("quote_details"), { key: "ArrowDown", altKey: true })
    expect(tableNames()).toEqual(["quote_details", "layers", "policy_details"])
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
  })

  it("drags a table or a column by its grip, never quote_details, a table's index or a column into another table", () => {
    const indexed = table("layers", {
      rows: "many",
      inputs: [INDEX, column("layer", { type: "int" }), column("limit", { type: "float" })],
    })
    load(form(detailsTable(), POLICY, indexed))
    render(<SchemaEditor />)

    dragOnto(tableGrip("layers")!, tableNamed("policy_details"))
    expect(tableNames()).toEqual(["quote_details", "layers", "policy_details"])
    // quote_details has no grip, and nothing dropped on it moves.
    expect(tableGrip("quote_details")).toBeNull()
    dragOnto(tableGrip("policy_details")!, tableNamed("quote_details"))
    expect(tableNames()).toEqual(["quote_details", "layers", "policy_details"])

    dragOnto(columnGrip("limit")!, screen.getByTestId("schema-column-layer"))
    expect(columnNames("layers")).toEqual(["row_number", "limit", "layer"])
    // The index has no grip, and nothing dropped on it moves above it.
    expect(columnGrip("row_number")).toBeNull()
    dragOnto(columnGrip("layer")!, screen.getByTestId("schema-column-row_number"))
    expect(columnNames("layers")).toEqual(["row_number", "limit", "layer"])
    // A column dropped on another table's column stays in its own table.
    dragOnto(columnGrip("exposure")!, screen.getByTestId("schema-column-layer"))
    expect(columnNames("policy_details")).toEqual(["state", "exposure"])
    expect(columnNames("layers")).toEqual(["row_number", "limit", "layer"])
    // quote_details' columns have no grips.
    expect(within(within(tableNamed("quote_details")).getByTestId("schema-column-quote_reference")).queryByTitle("Drag to move")).toBeNull()
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(2)
  })

  it("says why the params files could not be listed, and lists them again on Retry", async () => {
    vi.mocked(listProjectFiles)
      .mockRejectedValueOnce(new Error("This project's pipeline sits at the project root, so it has no pipeline folder to keep versioned files in."))
      .mockResolvedValueOnce(paramsFolder("states.csv"))
    render(<SchemaEditor />)

    fireEvent.click(screen.getByRole("button", { name: "Label and rules of state" }))

    const said = await screen.findByTestId("params-unlisted")
    expect(said).toHaveTextContent("This project's pipeline sits at the project root")
    const asked = vi.mocked(listProjectFiles).mock.calls.length
    fireEvent.click(within(said).getByRole("button", { name: "Retry" }))
    await waitFor(() => expect(screen.queryByTestId("params-unlisted")).toBeNull())
    expect(vi.mocked(listProjectFiles).mock.calls.length).toBeGreaterThan(asked)
  })

  it("shows a column's label and rules, each saved as it commits", () => {
    render(<SchemaEditor />)

    fireEvent.click(screen.getByRole("button", { name: "Label and rules of exposure" }))
    commit(screen.getByRole("textbox", { name: "Label of exposure" }), "Exposure ($)")
    fireEvent.click(screen.getByRole("checkbox", { name: "exposure is required" }))
    commit(screen.getByTestId("schema-column-exposure-min"), "0")
    commit(screen.getByTestId("schema-column-exposure-max"), "lots")

    expect(current().schema.tables[0].inputs[1]).toMatchObject({
      label: "Exposure ($)",
      required: true,
      min: 0,
      max: null,
    })
    expect(screen.getByTestId("schema-column-exposure-max-error")).toHaveTextContent("Enter a number.")
    expect(within(tableNamed("policy_details")).getByText("required · 0 to …")).toBeInTheDocument()
    // Allowed values come from a params file alone: none are typed in.
    expect(screen.queryByRole("textbox", { name: "Allowed values of exposure" })).toBeNull()
  })

  it("reads a column's allowed values from a CSV in the params folder, its file then its column, as one undo step", async () => {
    load(form(table("policy_details", { inputs: [column("state")] })))
    vi.mocked(listProjectFiles).mockResolvedValue(paramsFolder("sic_codes.csv", "notes.txt", "old", "LEGACY.CSV"))
    vi.mocked(fetchParamFile).mockImplementation(async (_file, column) => ({
      columns: ["occupancy", "sic_name"],
      values: column === "sic_name" ? ["Food", "Cloth"] : [],
    }))
    render(<SchemaEditor />)

    fireEvent.click(screen.getByRole("button", { name: "Label and rules of state" }))
    const from = await screen.findByRole("combobox", { name: "Where the allowed values of state come from" })
    // The choice shows before the params folder is listed: wait for the listing,
    // which lands as its own render, whenever the event loop runs it.
    await waitFor(() => expect(within(from).getAllByRole("option").map((option) => option.textContent)).toEqual(["", "sic_codes.csv", "LEGACY.CSV"]))
    fireEvent.change(from, { target: { value: "sic_codes.csv" } })
    // The form changes once a column is picked.
    const columnSelect = await screen.findByRole("combobox", { name: "Column the allowed values of state come from" })
    await waitFor(() => expect(within(columnSelect).getAllByRole("option").map((option) => option.textContent)).toEqual(["", "occupancy", "sic_name"]))
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(0)
    fireEvent.change(columnSelect, { target: { value: "sic_name" } })

    await waitFor(() =>
      expect(current().schema.tables[0].inputs[0]).toMatchObject({
        options: ["Food", "Cloth"],
        options_source: { file: "sic_codes.csv", column: "sic_name" },
      }),
    )
    expect(fetchParamFile).toHaveBeenLastCalledWith("sic_codes.csv", "sic_name")
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
    expect(within(tableNamed("policy_details")).getByText("2 values from sic_codes.csv")).toBeInTheDocument()
  })

  it("takes a column's allowed values away when the blank file is chosen, as one undo step", async () => {
    render(<SchemaEditor />)

    fireEvent.click(screen.getByRole("button", { name: "Label and rules of state" }))
    fireEvent.change(await screen.findByRole("combobox", { name: "Where the allowed values of state come from" }), { target: { value: "" } })

    expect(current().schema.tables[0].inputs[0]).toMatchObject({ options: [], options_source: null })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
    expect(screen.queryByRole("combobox", { name: "Column the allowed values of state come from" })).toBeNull()
  })

  it("gives a many-row table a row for each of a column's allowed values, one column at a time, each tick one undo step", () => {
    const perils = { options: ["AOP", "EQ", "NW"], options_source: { file: "perils.csv", column: "peril" } }
    load(
      form(
        POLICY,
        table("coverage", {
          rows: "many",
          inputs: [INDEX, column("zone", { options: ["N", "S"], options_source: { file: "zones.csv", column: "zone" }, row_per_value: true }), column("peril", perils), column("limit", { type: "float" })],
        }),
      ),
    )
    render(<SchemaEditor />)
    const cover = tableNamed("coverage")
    expect(within(cover).getByText("2 values from zones.csv · a row each")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Label and rules of peril" }))
    fireEvent.click(screen.getByRole("checkbox", { name: "A row for each" }))

    expect(current().schema.tables[1].inputs.map((c) => c.row_per_value)).toEqual([false, false, true, false])
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
    expect(within(cover).getByText("3 values from perils.csv · a row each")).toBeInTheDocument()
    expect(within(cover).getByText("2 values from zones.csv")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("checkbox", { name: "A row for each" }))
    expect(current().schema.tables[1].inputs.map((c) => c.row_per_value)).toEqual([false, false, false, false])
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(2)

    // Not offered without allowed values, nor in a one-row table.
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of limit" }))
    expect(screen.queryByRole("checkbox", { name: "A row for each" })).toBeNull()
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of state" }))
    expect(screen.queryByRole("checkbox", { name: "A row for each" })).toBeNull()
  })

  it("takes a column's row for each value away with its allowed values, and names a second column giving a table's rows", async () => {
    const perils = { options: ["AOP", "EQ"], options_source: { file: "perils.csv", column: "peril" }, row_per_value: true }
    load(form(table("coverage", { rows: "many", inputs: [INDEX, column("peril", perils), column("zone", { ...perils })] })))
    render(<SchemaEditor />)

    expect(within(tableNamed("coverage")).getByText('Table "coverage" has a row for each value of peril, so zone gives it none')).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of peril" }))
    fireEvent.change(await screen.findByRole("combobox", { name: "Where the allowed values of peril come from" }), { target: { value: "" } })

    expect(current().schema.tables[0].inputs[1]).toMatchObject({ options: [], options_source: null, row_per_value: false })
    expect(screen.queryByRole("checkbox", { name: "A row for each" })).toBeNull()
  })

  it("names allowed values typed in, as an older form holds them, among its table's problems", () => {
    load(form(table("policy_details", { inputs: [column("state", { options: ["CA", "NY"] })] })))
    render(<SchemaEditor />)

    expect(
      within(tableNamed("policy_details")).getByText('Column "state" in Table "policy_details" has allowed values typed in, not read from a params file'),
    ).toBeInTheDocument()
  })

  it("shows allowed values typed in as a choice of their own, which the blank choice takes away though no params file is there", () => {
    load(form(table("policy_details", { inputs: [column("state", { options: ["CA", "NY"] })] })))
    render(<SchemaEditor />)

    fireEvent.click(screen.getByRole("button", { name: "Label and rules of state" }))
    const from = screen.getByRole("combobox", { name: "Where the allowed values of state come from" })
    expect(from).toBeEnabled()
    expect(within(from).getByRole("option", { selected: true })).toHaveTextContent("2 values typed in")
    fireEvent.change(from, { target: { value: "" } })

    expect(current().schema.tables[0].inputs[0]).toMatchObject({ options: [], options_source: null })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
    expect(from).toBeDisabled()
  })

  it("drops a read of the values that the column's source changed under, as an undo changes it", async () => {
    load(form(table("policy_details", { inputs: [column("state", { options: ["B1"], options_source: { file: "b.csv", column: "state" } })] })))
    useWorkbenchFormStore.getState().change((spec) => updateSchemaColumn(spec, "policy_details", "state", { options: ["A1"], options_source: { file: "a.csv", column: "state" } }))
    let answer: (values: string[]) => void = () => {}
    vi.mocked(fetchParamFile).mockImplementation((_file, name) =>
      name === undefined ? Promise.resolve({ columns: ["state"], values: [] }) : new Promise((resolve) => (answer = (values) => resolve({ columns: ["state"], values }))),
    )
    render(<SchemaEditor />)

    fireEvent.click(screen.getByRole("button", { name: "Label and rules of state" }))
    fireEvent.click(await screen.findByRole("button", { name: "Read the allowed values of state again" }))
    act(() => useWorkbenchFormStore.getState().undo())
    await act(async () => answer(["A2"]))

    expect(current().schema.tables[0].inputs[0]).toMatchObject({ options: ["B1"], options_source: { file: "b.csv", column: "state" } })
    expect(useWorkbenchFormStore.getState().redoStack).toHaveLength(1)
  })

  it("reads a column's values from its file again, and says why the file cannot give them", async () => {
    load(form(table("policy_details", {
      inputs: [column("state", { options: ["Food"], options_source: { file: "sic_codes.csv", column: "sic_name" } })],
    })))
    vi.mocked(fetchParamFile).mockResolvedValue({ columns: ["sic_name"], values: ["Food", "Hangars"] })
    render(<SchemaEditor />)

    fireEvent.click(screen.getByRole("button", { name: "Label and rules of state" }))
    fireEvent.click(await screen.findByRole("button", { name: "Read the allowed values of state again" }))
    await waitFor(() => expect(current().schema.tables[0].inputs[0].options).toEqual(["Food", "Hangars"]))

    const gone = "rating/files/params/sic_codes.csv doesn't exist."
    vi.mocked(fetchParamFile).mockRejectedValue(new ApiError("HTTP 409", 409, gone))
    fireEvent.click(screen.getByRole("button", { name: "Read the allowed values of state again" }))

    expect(await screen.findByRole("alert")).toHaveTextContent(gone)
    expect(current().schema.tables[0].inputs[0].options).toEqual(["Food", "Hangars"])
  })

  it("shows a column's help, placeholder and number format, each saved as it commits", () => {
    load(form(POLICY, table("pricing", { outputs: [column("premium", { type: "float" })] })))
    render(<SchemaEditor />)

    fireEvent.click(screen.getByRole("button", { name: "Label and rules of exposure" }))
    commit(screen.getByRole("textbox", { name: "Help of exposure" }), "Sum insured")
    commit(screen.getByRole("textbox", { name: "Placeholder of exposure" }), "e.g. 250,000")
    // Separators ticked under General make it a Number, as an Explore pivot does.
    fireEvent.click(screen.getByRole("checkbox", { name: "Separators (1,000)" }))
    expect(current().schema.tables[0].inputs[1]).toMatchObject({ number_format: "number", use_grouping: true })
    fireEvent.change(screen.getByRole("combobox", { name: "Format of exposure" }), { target: { value: "currency_gbp" } })
    fireEvent.change(screen.getByRole("combobox", { name: "Decimal places of exposure" }), { target: { value: "0" } })
    fireEvent.click(screen.getByRole("checkbox", { name: "Separators (1,000)" }))
    expect(current().schema.tables[0].inputs[1]).toMatchObject({
      help: "Sum insured",
      placeholder: "e.g. 250,000",
      number_format: "currency_gbp",
      decimal_places: 0,
      use_grouping: false,
    })
    fireEvent.change(screen.getByRole("combobox", { name: "Decimal places of exposure" }), { target: { value: "automatic" } })
    expect(current().schema.tables[0].inputs[1].decimal_places).toBeNull()

    // A text column has no format; an output column takes a format but no placeholder.
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of state" }))
    expect(screen.queryByRole("combobox", { name: "Format of state" })).not.toBeInTheDocument()
    expect(screen.getByRole("textbox", { name: "Placeholder of state" })).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of premium" }))
    expect(screen.getByRole("combobox", { name: "Format of premium" })).toBeInTheDocument()
    expect(screen.queryByRole("textbox", { name: "Placeholder of premium" })).not.toBeInTheDocument()

    // A True/false or a Date column takes help but no placeholder.
    cleanup()
    load(form(table("flags", { inputs: [column("ok", { type: "bool" }), column("on", { type: "date" })] })))
    render(<SchemaEditor />)
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of ok" }))
    expect(screen.getByRole("textbox", { name: "Help of ok" })).toBeInTheDocument()
    expect(screen.queryByRole("textbox", { name: "Placeholder of ok" })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of on" }))
    expect(screen.getByRole("textbox", { name: "Help of on" })).toBeInTheDocument()
    expect(screen.queryByRole("textbox", { name: "Placeholder of on" })).not.toBeInTheDocument()
  })

  it("offers Required on an output as on an input, and keeps it as an input is moved to the outputs", () => {
    load(form(POLICY, table("pricing", { outputs: [column("premium", { type: "float" })] })))
    render(<SchemaEditor />)

    fireEvent.click(screen.getByRole("button", { name: "Label and rules of premium" }))
    fireEvent.click(screen.getByRole("checkbox", { name: "premium is required" }))
    expect(current().schema.tables[1].outputs[0].required).toBe(true)
    expect(screen.getByTestId("schema-column-premium-summary")).toHaveTextContent(/^required$/)
    // The rules only typing takes, an output takes none of.
    expect(screen.queryByTestId("schema-column-premium-min")).toBeNull()
    expect(screen.queryByRole("textbox", { name: "Placeholder of premium" })).toBeNull()

    // An input moved to the outputs keeps whether it is required, and loses the rest.
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of exposure" }))
    fireEvent.click(screen.getByRole("checkbox", { name: "exposure is required" }))
    commit(screen.getByTestId("schema-column-exposure-min"), "0")
    dragOnto(columnGrip("exposure")!, headingOf("policy_details", "Outputs"))
    expect(current().schema.tables[0].outputs[0]).toMatchObject({ name: "exposure", required: true, min: null })
    expect(screen.getByRole("checkbox", { name: "exposure is required" })).toBeChecked()
    expect(screen.queryByTestId("schema-column-exposure-min")).toBeNull()
  })

  it("shows the columns the quote store keeps in each table under the table's own, locked, with nothing to change", () => {
    load(form(detailsTable(), POLICY, LAYERS))
    useWorkbenchStore.setState({
      storeColumns: {
        quote_details: [
          { name: "quote_version", kind: "int" },
          { name: "saved_at", kind: "datetime" },
          { name: "priced_release", kind: "str" },
        ],
        layers: [{ name: "quote_row", kind: "int" }],
      },
    })
    render(<SchemaEditor />)

    const details = tableNamed("quote_details")
    const version = within(details).getByTestId("schema-store-column-quote_version")
    expect(version).toHaveTextContent("quote_version")
    expect(within(version).getByTitle("Kept by the quote store")).toHaveTextContent("quote_version")
    expect(within(version).getByTitle("Integer")).toBeInTheDocument()
    expect(within(within(details).getByTestId("schema-store-column-saved_at")).getByTitle("Date and time")).toBeInTheDocument()
    // After the table's own columns, under its inputs, and none of them.
    const inputs = within(details).getByRole("group", { name: "quote_details Inputs" })
    expect(version.previousElementSibling).toBe(inputs)
    expect(within(inputs).getAllByTestId(/^schema-column-[a-z_]+$/).at(-1)).toHaveAttribute("data-testid", "schema-column-haute_version")
    expect(columnNames("quote_details")).not.toContain("quote_version")
    // Read only: no field, button or menu of their own.
    for (const name of ["quote_version", "saved_at", "priced_release"]) {
      const row = within(details).getByTestId(`schema-store-column-${name}`)
      expect(within(row).queryByRole("textbox")).toBeNull()
      expect(within(row).queryByRole("button")).toBeNull()
      expect(within(row).queryByRole("combobox")).toBeNull()
    }
    const layers = tableNamed("layers")
    expect(within(layers).getByTestId("schema-store-column-quote_row")).toHaveTextContent("quote_row")
    expect(within(layers).getByText("2 cols")).toBeInTheDocument()
    expect(within(tableNamed("policy_details")).queryByTestId(/^schema-store-column-/)).toBeNull()

    // A search shows the form's columns alone.
    fireEvent.change(screen.getByRole("textbox", { name: "Search fields" }), { target: { value: "quote" } })
    expect(screen.queryByTestId(/^schema-store-column-/)).toBeNull()
  })

  it("asks before removing a column or a table a sheet shows, and takes it off the sheet", async () => {
    const shown = form(POLICY, LAYERS)
    shown.pages[0].sections[0].widgets = [
      { name: "w", id: "w", type: "collection", x: 0, y: 0, w: 400, h: 100, title: "Policy", columns: 2, ...COLLECTION_OPTIONS, fields: [{ table: "policy_details", column: "state" }], show_when: { match: "all", conditions: [] } },
    ]
    load(shown)
    render(<SchemaEditor />)

    fireEvent.click(screen.getByRole("button", { name: "Remove column exposure" }))
    expect(columnNames("policy_details")).toEqual(["state"])

    fireEvent.click(screen.getByRole("button", { name: "Remove column state" }))
    expect(asked()).toMatchObject({ title: "Remove column state?", text: "Shown in 1 component; removing it takes it out of that too.", confirm: "Remove", danger: true })
    await answerQuestion(null)
    expect(columnNames("policy_details")).toEqual(["state"])
    fireEvent.click(screen.getByRole("button", { name: "Remove column state" }))
    await answerQuestion("confirm")
    expect(columnNames("policy_details")).toEqual([])
    expect(current().pages[0].sections[0].widgets[0]).toMatchObject({ fields: [] })

    // Nothing shows the layers: deleted without a question.
    fireEvent.click(screen.getByRole("button", { name: "Delete table layers" }))
    await act(async () => undefined)
    expect(asked()).toBeNull()
    expect(screen.queryByRole("region", { name: "Table layers" })).not.toBeInTheDocument()
  })

  it("asks before removing a column or a table a condition tests, and takes the condition with it", async () => {
    const tested = form(POLICY, LAYERS)
    const condition = { table: "policy_details", column: "state", op: "filled" as const, values: [], field: null, days: 0 }
    tested.pages[0].show_when = { match: "all", conditions: [condition] }
    tested.pages[0].sections[0].widgets = [
      { name: "w", id: "w", type: "collection", x: 0, y: 0, w: 400, h: 100, title: "Policy", columns: 2, ...COLLECTION_OPTIONS, fields: [{ table: "policy_details", column: "state" }], show_when: { match: "all", conditions: [] } },
    ]
    load(tested)
    render(<SchemaEditor />)

    fireEvent.click(screen.getByRole("button", { name: "Remove column state" }))
    expect(asked()?.text).toBe("Shown in 1 component and tested by the conditions of 1 sheet, section or component; removing it takes it out of them too.")
    await answerQuestion(null)
    fireEvent.click(screen.getByRole("button", { name: "Delete table policy_details" }))
    expect(asked()).toMatchObject({
      title: "Delete table policy_details?",
      text: "Its columns are shown in 1 component and tested by the conditions of 1 sheet, section or component; deleting it takes them out of them too.",
      confirm: "Delete",
    })
    await answerQuestion("confirm")
    expect(current().pages[0].show_when.conditions).toEqual([])
  })

  it("asks before removing a column or a table an Import or an Export uses, and takes it out of them", async () => {
    const used = form(POLICY, LAYERS)
    const condition = { table: "policy_details", column: "state", op: "filled" as const, values: [], field: null, days: 0 }
    used.pages[0].show_when = { match: "all", conditions: [condition] }
    used.pages[0].sections[0].widgets = [
      { ...createWidget("import", { x: 0, y: 0, w: 160, h: 40 }, used), id: "w_in", table: "policy_details", column_names: [{ column: "state", name: "State" }] },
      { ...createWidget("export", { x: 176, y: 0, w: 160, h: 40 }, used), id: "w_out", tables: ["policy_details"] },
    ]
    load(used)
    render(<SchemaEditor />)
    const policyColumns = () => current().schema.tables.find((t) => t.name === "policy_details")!.inputs.map((c) => c.name)

    // The Import reads exposure by its own name and the Export writes it with its table.
    fireEvent.click(screen.getByRole("button", { name: "Remove column exposure" }))
    expect(asked()?.text).toBe("Used by 2 Imports and Exports; removing it takes it out of them too.")
    await answerQuestion(null)
    expect(policyColumns()).toContain("exposure")

    fireEvent.click(screen.getByRole("button", { name: "Remove column state" }))
    expect(asked()?.text).toBe("Tested by the conditions of 1 sheet, section or component and used by 2 Imports and Exports; removing it takes it out of them too.")
    await answerQuestion("confirm")
    expect(policyColumns()).not.toContain("state")
    expect(current().pages[0].sections[0].widgets[0]).toMatchObject({ table: "policy_details", column_names: [] })

    fireEvent.click(screen.getByRole("button", { name: "Delete table policy_details" }))
    expect(asked()?.text).toBe("Its columns are used by 2 Imports and Exports; deleting it takes them out of them too.")
    await answerQuestion("confirm")
    expect(current().pages[0].sections[0].widgets).toMatchObject([{ table: null }, { tables: [] }])
  })

  it("counts a section whose conditions test a column or a table in the confirmation, and takes those conditions out of it", async () => {
    const tested = form(POLICY, LAYERS)
    const state = { table: "policy_details", column: "state", op: "filled" as const, values: [], field: null, days: 0 }
    const exposure = { table: "policy_details", column: "exposure", op: "above" as const, values: ["100"], field: null, days: 0 }
    tested.pages[0].show_when = { match: "all", conditions: [state] }
    tested.pages[0].sections.push({ id: "section_2", title: "Section 2", height: 160, show_when: { match: "all", conditions: [state, exposure] }, widgets: [] })
    load(tested)
    render(<SchemaEditor />)
    const sectionConditions = () => current().pages[0].sections[1].show_when.conditions

    // The state is tested by the sheet's conditions and the section's.
    fireEvent.click(screen.getByRole("button", { name: "Remove column state" }))
    expect(asked()?.text).toBe("Tested by the conditions of 2 sheets, sections or components; removing it takes it out of them too.")
    await answerQuestion("confirm")
    expect(current().pages[0].show_when.conditions).toEqual([])
    expect(sectionConditions()).toEqual([exposure])

    // What is left of the table is tested by the section's alone.
    fireEvent.click(screen.getByRole("button", { name: "Delete table policy_details" }))
    expect(asked()?.text).toBe("Its columns are tested by the conditions of 1 sheet, section or component; deleting it takes them out of it too.")
    await answerQuestion("confirm")
    expect(sectionConditions()).toEqual([])
    expect(current().pages[0].sections.map((section) => section.id)).toEqual(["section_1", "section_2"])
  })

  it("gives a many-row table its index as it gains its first input, an Integer whose type cannot change, and takes it away as its last input goes", () => {
    load(form(POLICY, table("layers", { rows: "many" })))
    render(<SchemaEditor />)
    // No index control: the operations give and take it.
    expect(screen.queryByRole("checkbox", { name: /^Add index/ })).toBeNull()

    fireEvent.click(within(blockOf("layers", "Inputs")).getByRole("button", { name: "Add column" }))

    expect(blockNames("layers", "Inputs")).toEqual(["row_number", "column"])
    expect(own(current().schema.tables[1].inputs)[0]).toMatchObject({ name: "row_number", type: "int", index: true })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
    expect(screen.queryByRole("combobox", { name: "Type of row_number" })).not.toBeInTheDocument()
    // Never dragged.
    expect(columnGrip("row_number")).toBeNull()
    // Nothing is typed into the index, so it has no format or default to show.
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of row_number" }))
    expect(screen.getByRole("textbox", { name: "Label of row_number" })).toBeInTheDocument()
    expect(screen.queryByRole("combobox", { name: "Format of row_number" })).not.toBeInTheDocument()
    expect(screen.queryByLabelText("Default of row_number")).not.toBeInTheDocument()

    // Alt+Up never passes it.
    fireEvent.keyDown(within(blockOf("layers", "Inputs")).getAllByRole("textbox", { name: "Column name" })[1], { key: "ArrowUp", altKey: true })
    expect(blockNames("layers", "Inputs")).toEqual(["row_number", "column"])

    fireEvent.click(screen.getByRole("button", { name: "Remove column column" }))
    expect(blockNames("layers", "Inputs")).toEqual([])
    expect(own(current().schema.tables[1].inputs)).toEqual([])
  })

  it("gives the index no remove: the operations alone take it away", () => {
    load(form(POLICY, table("layers", { rows: "many", inputs: [INDEX, column("layer", { type: "int" })] })))
    render(<SchemaEditor />)

    expect(screen.getByTestId("schema-column-row_number")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Remove column row_number" })).toBeNull()
  })

  it("adds a table leading with the quote's reference and option under Inputs, locked, its index after them", () => {
    render(<SchemaEditor />)

    fireEvent.click(screen.getByRole("button", { name: "Add table" }))
    fireEvent.change(screen.getByRole("combobox", { name: "Rows of table" }), { target: { value: "many" } })

    expect(everyName("table", "Inputs")).toEqual(["quote_reference", "quote_option"])
    expect(blockNames("table", "Outputs")).toEqual([])
    for (const [id, name] of [["quote_reference", "quote_reference"], ["quote_option", "quote_option"]]) {
      const row = screen.getByTestId(`schema-column-${id}`)
      // Not moved, retyped, renamed, keyed, given details or removed.
      expect(columnGrip(id)).toBeNull()
      expect(within(row).queryByRole("combobox", { name: `Type of ${name}` })).toBeNull()
      expect(within(row).getByRole("textbox", { name: "Column name" })).toHaveAttribute("readonly")
      expect(within(row).queryByRole("button", { name: `Key: ${name}` })).toBeNull()
      expect(within(row).queryByRole("button", { name: `Label and rules of ${name}` })).toBeNull()
      expect(within(row).queryByRole("button", { name: `Remove column ${name}` })).toBeNull()
      expect(within(row).getByRole("textbox", { name: "Column name" })).toHaveAttribute("title", "Haute's column")
    }

    fireEvent.click(within(blockOf("table", "Inputs")).getByRole("button", { name: "Add column" }))
    expect(everyName("table", "Inputs")).toEqual(["quote_reference", "quote_option", "row_number", "column"])
  })

  it("takes the index away with what shows it as its table switches to one row", () => {
    const indexed = form(POLICY, table("layers", { rows: "many", inputs: [INDEX, column("layer", { type: "int" })] }))
    indexed.pages[0].sections[0].widgets = [
      { name: "w", id: "w", type: "tableInput", x: 0, y: 0, w: 400, h: 100, title: "Layers", rows: 3, ...TABLE_OPTIONS, fields: [{ table: "layers", column: "row_number" }, { table: "layers", column: "layer" }], show_when: { match: "all", conditions: [] } },
    ]
    load(indexed)
    render(<SchemaEditor />)

    fireEvent.change(screen.getByRole("combobox", { name: "Rows of layers" }), { target: { value: "one" } })
    expect(blockNames("layers", "Inputs")).toEqual(["layer"])
    expect(current().pages[0].sections[0].widgets[0]).toMatchObject({ fields: [{ table: "layers", column: "layer" }] })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
  })

  it("says what would stop the schema being the pipeline's tables", () => {
    useDocumentStatusStore.setState({
      capabilities: { reserved_api_input_frame_labels: ["class_output"] } as unknown as NonNullable<
        ReturnType<typeof useDocumentStatusStore.getState>["capabilities"]
      >,
    })
    load(form(table("class", { rows: "many", outputs: [column("a")] })))
    render(<SchemaEditor />)

    const block = tableNamed("class")
    expect(within(block).getByText("1 col, 2 problems")).toBeInTheDocument()
    // Its port, the name with _output, is held to the port rules.
    expect(within(block).getByText(/^Table "class"'s port class_output: .*cannot be a Python hard keyword/)).toBeInTheDocument()
    expect(within(block).getByText('Table "class" has many rows of outputs alone, so it needs a key column')).toBeInTheDocument()
  })

  it("holds no hint text in a column's details", () => {
    render(<SchemaEditor />)

    for (const name of ["exposure", "state", "limit"]) {
      fireEvent.click(screen.getByRole("button", { name: `Label and rules of ${name}` }))
      expect(screen.getByRole("textbox", { name: `Label of ${name}` })).toBeInTheDocument()
      // The tables' boxes; the search over them is named by its placeholder.
      expect(screen.getByTestId("schema-tables").querySelectorAll("[placeholder]")).toHaveLength(0)
    }
  })

  it("puts a column in a group, offering the groups its table's columns are in", () => {
    render(<SchemaEditor />)

    fireEvent.click(screen.getByRole("button", { name: "Label and rules of exposure" }))
    commit(screen.getByRole("combobox", { name: "Group of exposure" }), " Cover ")
    expect(current().schema.tables[0].inputs[1].group).toBe("Cover")
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)

    fireEvent.click(screen.getByRole("button", { name: "Label and rules of state" }))
    const group = screen.getByRole("combobox", { name: "Group of state" })
    const offered = document.getElementById(group.getAttribute("list") ?? "")
    expect([...(offered?.querySelectorAll("option") ?? [])].map((option) => option.value)).toEqual(["Cover"])
  })

  it("starts a new quote with a one-row input column's default, taken as its cells take a value", () => {
    const flags = table("flags", { inputs: [column("ok", { type: "bool" }), column("on", { type: "date" })] })
    load(form(POLICY, LAYERS, flags, table("pricing", { outputs: [column("premium", { type: "float" })] })))
    render(<SchemaEditor />)

    // Typed, trimmed; and named in the table's problems while its rules refuse it.
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of exposure" }))
    commit(screen.getByRole("textbox", { name: "Default of exposure" }), " lots ")
    expect(current().schema.tables[0].inputs[1].default_value).toBe("lots")
    expect(
      within(tableNamed("policy_details")).getByText('Column "exposure" in Table "policy_details" has a default its rules refuse: Not a number'),
    ).toBeInTheDocument()
    commit(screen.getByRole("textbox", { name: "Default of exposure" }), "250,000")
    expect(within(tableNamed("policy_details")).queryByText(/has a default its rules refuse/)).toBeNull()

    // A number in its column's format, as a cell shows it, and as typed while it is typed in.
    fireEvent.change(screen.getByRole("combobox", { name: "Format of exposure" }), { target: { value: "currency_usd" } })
    const exposure = screen.getByRole("textbox", { name: "Default of exposure" })
    expect(exposure).toHaveValue("US$250,000.00")
    fireEvent.focus(exposure)
    expect(exposure).toHaveValue("250,000")
    commit(exposure, "0.175")
    expect(current().schema.tables[0].inputs[1].default_value).toBe("0.175")
    fireEvent.change(screen.getByRole("combobox", { name: "Format of exposure" }), { target: { value: "percent" } })
    expect(exposure).toHaveValue("17.5%")
    // Its allowed values in its format too, each still the value as written.
    act(() => useWorkbenchFormStore.getState().change((spec) => updateSchemaColumn(spec, "policy_details", "exposure", { options: ["0.1", "0.175"] })))
    const allowed = screen.getByRole("combobox", { name: "Default of exposure" })
    expect([...allowed.querySelectorAll("option")].map((option) => [option.getAttribute("value"), option.textContent])).toEqual([
      ["", ""],
      ["0.1", "10%"],
      ["0.175", "17.5%"],
    ])

    // One of the allowed values, or none.
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of state" }))
    const state = screen.getByRole("combobox", { name: "Default of state" })
    expect([...state.querySelectorAll("option")].map((option) => option.getAttribute("value"))).toEqual(["", "CA", "NY"])
    fireEvent.change(state, { target: { value: "NY" } })
    expect(current().schema.tables[0].inputs[0].default_value).toBe("NY")

    // Ticked or not: a tick, or none.
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of ok" }))
    fireEvent.click(screen.getByRole("checkbox", { name: "ok starts ticked" }))
    expect(current().schema.tables[2].inputs[0].default_value).toBe(true)
    fireEvent.click(screen.getByRole("checkbox", { name: "ok starts ticked" }))
    expect(current().schema.tables[2].inputs[0].default_value).toBe("")

    // A day.
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of on" }))
    const day = screen.getByLabelText("Default of on")
    expect(day).toHaveAttribute("type", "date")
    commit(day, "2026-10-13")
    expect(current().schema.tables[2].inputs[1].default_value).toBe("2026-10-13")

    // A many-row table's columns, and an output's, take none.
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of limit" }))
    expect(screen.queryByLabelText("Default of limit")).toBeNull()
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of premium" }))
    expect(screen.queryByLabelText("Default of premium")).toBeNull()
  })

  it("collapses a table's columns", () => {
    render(<SchemaEditor />)

    fireEvent.click(screen.getByRole("button", { name: "Collapse policy_details" }))

    expect(within(tableNamed("policy_details")).queryAllByRole("textbox", { name: "Column name" })).toEqual([])
    expect(columnNames("layers")).toEqual(["layer", "limit"])
    fireEvent.click(screen.getByRole("button", { name: "Expand policy_details" }))
    expect(columnNames("policy_details")).toEqual(["state", "exposure"])
  })

  it("sums a column up under its name: its range and default in its format, and a number's format in any table", () => {
    load(
      form(
        table("cover", {
          inputs: [
            column("sum_insured", { type: "float", required: true, min: 0, max: 1000000, default_value: "250000", number_format: "currency_gbp", decimal_places: 2 }),
            column("loss_ratio", { type: "float", min: 0, max: 1, number_format: "percent", decimal_places: 1, use_grouping: false }),
            column("claims", { type: "int", default_value: "3" }),
            column("flood", { type: "bool", default_value: true }),
            column("region", { default_value: "north" }),
            column("note"),
          ],
        }),
        table("pricing", { outputs: [column("premium", { type: "float", number_format: "number", decimal_places: 2 })] }),
        table("items", {
          rows: "many",
          inputs: [column("row_number", { type: "int", index: true, number_format: "number" }), column("value", { type: "float", default_value: "5" })],
        }),
      ),
    )
    render(<SchemaEditor />)
    const summary = (id: string) => screen.queryByTestId(`schema-column-${id}-summary`)

    expect(summary("sum_insured")).toHaveTextContent("required · £0.00 to £1,000,000.00 · default £250,000.00 · £ GBP, 2 decimals")
    // A percent is the value times 100.
    expect(summary("loss_ratio")).toHaveTextContent("0.0% to 100.0% · Percentage, 1 decimal, no separators")
    // General with automatic decimals shows numbers as typed, and says nothing of its format.
    expect(summary("claims")).toHaveTextContent(/^default 3$/)
    expect(summary("flood")).toHaveTextContent(/^default ticked$/)
    expect(summary("region")).toHaveTextContent(/^default north$/)
    expect(summary("note")).toBeNull()
    // An output column has no rules, but its format.
    expect(summary("premium")).toHaveTextContent(/^Number, 2 decimals$/)
    // Neither the index's format nor a default in a many-row table, which takes none.
    expect(summary("row_number")).toBeNull()
    expect(summary("value")).toBeNull()

    // Beside the name, to its right on the name's own line, and whole in its title where the pane cuts it short.
    const name = within(screen.getByTestId("schema-column-sum_insured")).getByRole("textbox", { name: "Column name" })
    const line = summary("sum_insured")!
    expect(within(line).queryByRole("textbox")).toBeNull()
    expect(line.parentElement).toBe(name.parentElement)
    expect(name.compareDocumentPosition(line) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(line).toHaveAttribute("title", line.textContent)
  })

  it("opens again as it was left: its tables collapsed, a column's label and rules open, and scrolled where it was", () => {
    const { unmount } = render(<SchemaEditor />)
    fireEvent.click(screen.getByRole("button", { name: "Collapse policy_details" }))
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of limit" }))
    const tables = screen.getByTestId("schema-tables")
    tables.scrollTop = 120
    fireEvent.scroll(tables)
    unmount()

    render(<SchemaEditor />)
    expect(within(tableNamed("policy_details")).queryAllByRole("textbox", { name: "Column name" })).toEqual([])
    expect(screen.getByRole("textbox", { name: "Label of limit" })).toBeInTheDocument()
    expect(screen.getByTestId("schema-tables").scrollTop).toBe(120)
  })

  it("searches the fields: the columns whose name or label holds the text, whatever its case, in the tables holding one, open", () => {
    load(form(POLICY, { ...LAYERS, outputs: [...LAYERS.outputs, column("excess", { label: "Policy Excess" })] }))
    const { unmount } = render(<SchemaEditor />)
    fireEvent.click(screen.getByRole("button", { name: "Collapse policy_details" }))
    const search = screen.getByRole("textbox", { name: "Search fields" })

    fireEvent.change(search, { target: { value: "POLICY" } })
    // A label matches; a table name alone does not.
    expect(screen.queryByRole("region", { name: "Table policy_details" })).toBeNull()
    expect(columnNames("layers")).toEqual(["excess"])

    fireEvent.change(search, { target: { value: "t" } })
    // Open though collapsed, its toggle off while the search filters.
    expect(columnNames("policy_details")).toEqual(["state"])
    expect(screen.getByRole("button", { name: "Collapse policy_details" })).toBeDisabled()
    expect(columnNames("layers")).toEqual(["limit"])

    fireEvent.change(search, { target: { value: "premium" } })
    expect(screen.queryAllByRole("region", { name: /^Table / })).toEqual([])
    expect(screen.getByText("No fields match")).toBeInTheDocument()

    // Kept as the pane closes and opens again, and emptied by Clear.
    fireEvent.change(search, { target: { value: "lim" } })
    unmount()
    render(<SchemaEditor />)
    expect(screen.getByRole("textbox", { name: "Search fields" })).toHaveValue("lim")
    expect(columnNames("layers")).toEqual(["limit"])
    fireEvent.click(screen.getByRole("button", { name: "Clear search fields" }))
    expect(screen.getByRole("textbox", { name: "Search fields" })).toHaveValue("")
    fireEvent.change(screen.getByRole("textbox", { name: "Search fields" }), { target: { value: "premium" } })
    const searchAgain = screen.getByRole("textbox", { name: "Search fields" })

    // Escape empties it, and the whole schema shows as it was.
    fireEvent.keyDown(searchAgain, { key: "Escape" })
    expect(searchAgain).toHaveValue("")
    expect(within(tableNamed("policy_details")).queryAllByRole("textbox", { name: "Column name" })).toEqual([])
    expect(columnNames("layers")).toEqual(["layer", "limit", "excess"])
  })

  it("adds and moves nothing while the search filters, but edits and removes the columns it shows", () => {
    render(<SchemaEditor />)
    fireEvent.change(screen.getByRole("textbox", { name: "Search fields" }), { target: { value: "l" } })
    const layers = tableNamed("layers")

    expect(screen.queryByRole("button", { name: "Add table" })).toBeNull()
    expect(within(layers).queryByRole("button", { name: "Add column" })).toBeNull()
    expect(tableGrip("layers")).toBeNull()
    expect(columnGrip("limit")).toBeNull()
    const last = within(layers).getAllByRole("textbox", { name: "Column name" })[1]
    fireEvent.keyDown(last, { key: "Enter" })
    fireEvent.keyDown(last, { key: "ArrowUp", altKey: true })
    fireEvent.keyDown(tableNameField("layers"), { key: "ArrowUp", altKey: true })
    expect(current().schema.tables.map((table) => [...table.inputs, ...table.outputs].map((each) => each.name))).toEqual([["state", "exposure"], ["layer", "limit"]])

    commit(last, "limit_gbp")
    expect(current().schema.tables[1].outputs[1].name).toBe("limit_gbp")
  })


  it("shows the schema read only, as Preview does: a table collapses and a column's details open, but nothing in it changes", () => {
    render(<SchemaEditor readOnly />)
    const layers = tableNamed("layers")

    expect(tableNameField("layers")).toHaveAttribute("readonly")
    expect(within(layers).getByRole("combobox", { name: "Rows of layers" })).toBeDisabled()
    expect(within(layers).queryByRole("combobox", { name: /^Type of/ })).toBeNull()
    for (const name of within(layers).getAllByRole("textbox", { name: "Column name" })) expect(name).toHaveAttribute("readonly")
    expect(within(layers).getByRole("button", { name: "Key: layer" })).toBeDisabled()
    expect(within(layers).queryByRole("button", { name: "Delete table layers" })).toBeNull()
    expect(within(layers).queryByRole("button", { name: /^Remove column/ })).toBeNull()
    expect(within(layers).queryByRole("button", { name: "Add column" })).toBeNull()
    // A block holding no column shows no heading while nothing can be added to it.
    expect(within(layers).queryByRole("group", { name: "layers Inputs" })).toBeNull()
    expect(screen.queryByRole("button", { name: "Add table" })).toBeNull()
    expect(tableGrip("layers")).toBeNull()
    expect(columnGrip("limit")).toBeNull()

    // Enter in the last column's name adds none, and Alt+Up moves neither a column nor a table.
    const last = within(layers).getAllByRole("textbox", { name: "Column name" })[1]
    fireEvent.keyDown(last, { key: "Enter" })
    fireEvent.keyDown(last, { key: "ArrowUp", altKey: true })
    fireEvent.keyDown(tableNameField("layers"), { key: "ArrowUp", altKey: true })
    expect(columnNames("layers")).toEqual(["layer", "limit"])
    expect(tableNames()).toEqual(["policy_details", "layers"])

    // A column's label and rules open, each field in them disabled.
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of limit" }))
    expect(screen.getByRole("textbox", { name: "Label of limit" })).toBeDisabled()
    fireEvent.click(screen.getByRole("button", { name: "Collapse policy_details" }))
    expect(within(tableNamed("policy_details")).queryAllByRole("textbox", { name: "Column name" })).toEqual([])
    expect(useWorkbenchFormStore.getState().undoStack).toEqual([])
  })
})
