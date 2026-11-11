/**
 * The schema editor (specs/workbench): tables and their columns under Keys, Inputs
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
import { BLANK_THEME, buttonUses, COLLECTION_OPTIONS, conditionUses, createWidget, fieldUses, TABLE_OPTIONS, tablePorts, updateSchemaColumn } from "../../utils/workbenchForm"
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
  required: false,
  min: null,
  max: null,
  options: [],
  options_source: null,
  index: null,
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

/** A many-row table's index: each row's number, from 1. */
const INDEX = column("table_index", { type: "int", index: "numbered" })
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
  outputs: [column("layer", { type: "int", index: "typed" }), column("limit", { type: "float" })],
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
/** A table's group of columns under its heading. */
const blockOf = (name: string, title: "Keys" | "Inputs" | "Outputs") => within(tableNamed(name)).getByRole("group", { name: `${name} ${title}` })
/** A block's columns' names, the quote's identity aside; `everyName` with it. */
const everyName = (name: string, title: "Keys" | "Inputs" | "Outputs"): string[] => nameFields(blockOf(name, title))
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
    expect(everyName("layers", "Inputs")).toEqual([])
    expect(everyName("layers", "Keys")).toEqual(["layer"])
    expect(blockNames("layers", "Outputs")).toEqual(["limit"])
    // Empty sides retain their headings and Add column while building.
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

    // Only fields the underwriter types have configuration controls.
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of insured" }))
    commit(screen.getByRole("textbox", { name: "Label of insured" }), "Client")
    commit(screen.getByRole("textbox", { name: "Default of insured" }), "Acme Ltd")
    fireEvent.click(screen.getByRole("checkbox", { name: "insured is required" }))
    expect(screen.queryByRole("button", { name: "Label and rules of stage" })).toBeNull()
    expect(current().schema.tables[0].inputs.find((column) => column.name === "insured")).toMatchObject({
      label: "Client",
      required: true,
      default_value: "Acme Ltd",
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
    for (const title of ["Inputs", "Outputs"] as const) {
      expect(within(blockOf("table", title)).getByText(title, { exact: true })).toBeInTheDocument()
      expect(everyName("table", title)).toEqual([])
      expect(within(blockOf("table", title)).getByRole("button", { name: "Add column" })).toBeInTheDocument()
    }
    expect(within(added).queryByRole("button", { name: /^Add (inputs|outputs)$/ })).toBeNull()
    expect(tablePorts(current().schema.tables[2])).toEqual([])
    fireEvent.click(within(blockOf("table", "Inputs")).getByRole("button", { name: "Add column" }))
    expect(everyName("table", "Keys")).toEqual(["quote_reference", "quote_option"])
    expect(everyName("table", "Inputs")).toEqual(["column"])
    expect(within(added).getByDisplayValue("column")).toHaveFocus()
    expect(current().schema.tables[2].inputs.at(-1)).toMatchObject({ name: "column", type: "str" })
    expect(tablePorts(current().schema.tables[2])).toEqual(["table_input"])
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(2)
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

  it("gives a table its numbered index on many rows and removes it on one row, each one edit", () => {
    render(<SchemaEditor />)

    fireEvent.change(screen.getByRole("combobox", { name: "Rows of policy_details" }), { target: { value: "many" } })
    fireEvent.change(screen.getByRole("combobox", { name: "Rows of layers" }), { target: { value: "one" } })

    expect(current().schema.tables[0]).toMatchObject({ rows: "many" })
    expect(own(current().schema.tables[0].inputs).map((c) => [c.name, c.index])).toEqual([
      ["table_index", "numbered"],
      ["state", null],
      ["exposure", null],
    ])
    // The index's kind is configured through its details, with no key mark.
    const index = screen.getByTestId("schema-column-table_index")
    fireEvent.click(within(index).getByRole("button", { name: "Label and rules of table_index" }))
    expect(within(index).getByRole("combobox", { name: "How table_index gets its values" })).toHaveValue("numbered")
    expect(within(index).queryByRole("img", { name: /key/ })).toBeNull()
    expect(within(index).queryByRole("button", { name: /^Key:/ })).toBeNull()
    expect(screen.queryByRole("button", { name: "Key: state" })).toBeNull()
    expect(current().schema.tables[1].rows).toBe("one")
    expect(own(current().schema.tables[1].outputs).map((c) => [c.name, c.index])).toEqual([["limit", null]])
    // One edit each.
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(2)

    fireEvent.change(screen.getByRole("combobox", { name: "Rows of policy_details" }), { target: { value: "one" } })
    expect(own(current().schema.tables[0].inputs).map((c) => c.name)).toEqual(["state", "exposure"])
  })

  it("picks an ordinary column's type and the index's Integer or Text, with no key controls", () => {
    render(<SchemaEditor />)

    fireEvent.change(screen.getByRole("combobox", { name: "Type of state" }), { target: { value: "bool" } })
    const indexType = screen.getByRole("combobox", { name: "Type of layer" })
    expect(within(indexType).getAllByRole("option").map((option) => option.getAttribute("value")).sort()).toEqual(["int", "str"])
    fireEvent.change(indexType, { target: { value: "str" } })

    expect(current().schema.tables[0].inputs[0].type).toBe("bool")
    expect(current().schema.tables[1].outputs[0]).toMatchObject({ name: "layer", type: "str", index: "typed" })
    // An input is never a key, nor an output of a one-row table; a one-row table shows no key at all.
    expect(screen.queryByRole("button", { name: "Key: state" })).not.toBeInTheDocument()
    expect(within(screen.getByTestId("schema-column-state")).queryByRole("img", { name: /key/ })).toBeNull()

    // Gaining its first input moves the same typed index to the inputs.
    fireEvent.click(within(blockOf("layers", "Inputs")).getByRole("button", { name: "Add column" }))
    expect(own(current().schema.tables[1].inputs).map((c) => c.name)).toEqual(["layer", "column"])
    expect(own(current().schema.tables[1].outputs).map((c) => c.name)).toEqual(["limit"])
    expect(screen.queryByRole("button", { name: /^Key: / })).toBeNull()
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of layer" }))
    expect(within(blockOf("layers", "Keys")).getByRole("combobox", { name: "How layer gets its values" })).toHaveValue("typed")
  })

  it("shows a values index's kind only through its config button and no key marks on any column", () => {
    const perils = { options: ["AOP", "EQ"], options_source: { file: "perils.csv", column: "peril" }, index: "values" as const }
    load(
      form(
        table("coverage", {
          rows: "many",
          inputs: [...IDENTITY, column("peril", perils), column("limit", { type: "float" })],
          outputs: [column("premium", { type: "float" })],
        }),
      ),
    )
    render(<SchemaEditor />)

    const config = screen.getByRole("button", { name: "Label and rules of peril" })
    expect(config).toHaveAttribute("aria-expanded", "false")
    expect(screen.queryByRole("combobox", { name: "How peril gets its values" })).toBeNull()
    fireEvent.click(config)
    expect(config).toHaveAttribute("aria-expanded", "true")
    expect(screen.getByRole("combobox", { name: "How peril gets its values" })).toHaveValue("values")
    expect(screen.getByRole("combobox", { name: "Where the allowed values of peril come from" })).toBeInTheDocument()
    fireEvent.click(config)
    expect(screen.queryByRole("combobox", { name: "How peril gets its values" })).toBeNull()
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(0)
    expect(current().schema.tables[0].inputs[2]).toMatchObject(perils)
    for (const name of ["peril", "limit", "premium"]) {
      expect(within(screen.getByTestId(`schema-column-${name}`)).queryByRole("img", { name: /key/ })).toBeNull()
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

    // Moving an output to the inputs brings its table's typed index with it.
    dragOnto(columnGrip("limit")!, headingOf("layers", "Inputs"))
    expect(own(current().schema.tables[1].inputs).map((c) => [c.name, c.index])).toEqual([["layer", "typed"], ["limit", null]])
    expect(everyName("layers", "Outputs")).toEqual([])
    expect(within(blockOf("layers", "Outputs")).getByRole("button", { name: "Add column" })).toBeInTheDocument()
    expect(tablePorts(current().schema.tables[1])).toEqual(["layers_input"])
  })

  it("moves a renamed index to the outputs with its last typed input, keeping its kind and restricted type choice", () => {
    const itemNumber = column("item_number", { type: "int", index: "numbered" })
    load(form(table("items", { rows: "many", inputs: [...IDENTITY, itemNumber, column("value", { type: "float" })], outputs: [column("premium", { type: "float" })] })))
    render(<SchemaEditor />)
    expect(screen.queryByRole("button", { name: /^Key: / })).toBeNull()

    dragOnto(columnGrip("value")!, headingOf("items", "Outputs"))

    const [items] = current().schema.tables
    expect(items.inputs).toEqual([])
    expect(items.outputs.map((c) => c.name)).toEqual(["quote_reference", "quote_option", "item_number", "premium", "value"])
    expect(everyName("items", "Inputs")).toEqual([])
    expect(everyName("items", "Keys")).toEqual(["quote_reference", "quote_option", "item_number"])
    expect(everyName("items", "Outputs")).toEqual(["premium", "value"])
    expect(screen.queryByRole("button", { name: /^Key:/ })).toBeNull()
    // Keys stay in their shared section as their canonical placement changes.
    expect(screen.queryByRole("button", { name: "Key: quote_reference" })).toBeNull()
    const index = screen.getByTestId("schema-column-item_number")
    expect(within(index).queryByRole("button", { name: /^Key:/ })).toBeNull()
    expect(within(index).queryByRole("img", { name: /key/ })).toBeNull()
    fireEvent.click(within(index).getByRole("button", { name: "Label and rules of item_number" }))
    expect(within(index).getByRole("combobox", { name: "How item_number gets its values" })).toHaveValue("numbered")
    expect(within(index).getByRole("combobox", { name: "Type of item_number" })).toHaveValue("int")
    expect(columnGrip("item_number")).toBeNull()
    expect(within(index).queryByRole("button", { name: "Remove column item_number" })).toBeNull()
    expect(within(index).getByRole("button", { name: "Label and rules of item_number" })).toBeInTheDocument()
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)

    // Renamed as any column is, as one edit.
    commit(within(index).getByRole("textbox", { name: "Column name" }), "line_number")
    expect(everyName("items", "Keys")).toEqual(["quote_reference", "quote_option", "line_number"])
    expect(everyName("items", "Outputs")).toEqual(["premium", "value"])
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(2)

    // An input added takes it, with the identity, back to the inputs' head.
    fireEvent.click(within(blockOf("items", "Inputs")).getByRole("button", { name: "Add column" }))
    expect(everyName("items", "Keys")).toEqual(["quote_reference", "quote_option", "line_number"])
    expect(everyName("items", "Inputs")).toEqual(["column"])
    expect(blockNames("items", "Outputs")).toEqual(["premium", "value"])
    expect(screen.queryByRole("button", { name: /^Key: / })).toBeNull()
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
    expect(columnNames("layers")).toEqual(["table_index", "limit", "layer"])
    // The index has no grip, and nothing dropped on it moves above it.
    expect(columnGrip("table_index")).toBeNull()
    dragOnto(columnGrip("layer")!, screen.getByTestId("schema-column-table_index"))
    expect(columnNames("layers")).toEqual(["table_index", "limit", "layer"])
    // A column dropped on another table's column stays in its own table.
    dragOnto(columnGrip("exposure")!, screen.getByTestId("schema-column-layer"))
    expect(columnNames("policy_details")).toEqual(["state", "exposure"])
    expect(columnNames("layers")).toEqual(["table_index", "limit", "layer"])
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

  it("changes the index's kind in its config panel in one undo step and shows allowed values there", () => {
    const perils = { options: ["AOP", "EQ", "NW"], options_source: { file: "perils.csv", column: "peril" } }
    load(
      form(
        POLICY,
        table("coverage", {
          rows: "many",
          inputs: [column("peril", { ...perils, index: "values" }), column("limit", { type: "float" })],
        }),
      ),
    )
    render(<SchemaEditor />)
    const cover = tableNamed("coverage")
    expect(within(cover).getByText("3 values from perils.csv · a row each")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of peril" }))
    const kind = screen.getByRole("combobox", { name: "How peril gets its values" })
    fireEvent.change(kind, { target: { value: "numbered" } })
    expect(current().schema.tables[1].inputs[0]).toMatchObject({ index: "numbered", options: [], options_source: null })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
    expect(screen.getByRole("textbox", { name: "Label of peril" })).toBeInTheDocument()
    expect(screen.queryByRole("combobox", { name: "Where the allowed values of peril come from" })).toBeNull()
    fireEvent.change(kind, { target: { value: "values" } })
    expect(current().schema.tables[1].inputs[0].index).toBe("values")
    expect(screen.getByRole("textbox", { name: "Label of peril" })).toBeInTheDocument()
    expect(screen.getByRole("combobox", { name: "Where the allowed values of peril come from" })).toBeInTheDocument()
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(2)
    expect(within(cover).getByText("coverage.peril gives a row for each value, so it needs allowed values from a params file")).toBeInTheDocument()
    expect(screen.queryByRole("checkbox", { name: "A row for each" })).toBeNull()
    fireEvent.change(kind, { target: { value: "typed" } })
    expect(current().schema.tables[1].inputs[0].index).toBe("typed")
    expect(screen.getByRole("textbox", { name: "Placeholder of peril" })).toBeInTheDocument()
    expect(screen.queryByRole("combobox", { name: "Where the allowed values of peril come from" })).toBeNull()
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(3)
    act(() => useWorkbenchFormStore.getState().undo())
    expect(kind).toHaveValue("values")
    expect(screen.getByRole("combobox", { name: "Where the allowed values of peril come from" })).toBeInTheDocument()
  })

  it("returns an index of values to numbered when its source is cleared, in one edit", async () => {
    const perils = { options: ["AOP", "EQ"], options_source: { file: "perils.csv", column: "peril" }, index: "values" as const }
    load(form(table("coverage", { rows: "many", inputs: [column("peril", perils), column("limit", { type: "float" })] })))
    render(<SchemaEditor />)

    fireEvent.click(screen.getByRole("button", { name: "Label and rules of peril" }))
    fireEvent.change(await screen.findByRole("combobox", { name: "Where the allowed values of peril come from" }), { target: { value: "" } })

    expect(current().schema.tables[0].inputs[0]).toMatchObject({ options: [], options_source: null, index: "numbered" })
    expect(screen.getByRole("combobox", { name: "How peril gets its values" })).toHaveValue("numbered")
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
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
    dragOnto(columnGrip("exposure")!, within(blockOf("policy_details", "Outputs")).getByRole("button", { name: "Add column" }))
    expect(current().schema.tables[0].outputs[0]).toMatchObject({ name: "exposure", required: true, min: null })
    expect(screen.getByRole("checkbox", { name: "exposure is required" })).toBeChecked()
    expect(screen.queryByTestId("schema-column-exposure-min")).toBeNull()
  })

  it.each([false, true])("shows only form fields and fixed keys in quote_details without changing the form (readOnly=%s)", (readOnly) => {
    const details = detailsTable()
    details.inputs.find((each) => each.name === "modified_date")!.label = "Last changed"
    const original = form(details, table("audit", { inputs: [column("modified_date")] }))
    load(original)
    useWorkbenchStore.setState({ storeColumns: { quote_details: [
      { name: "quote_version", kind: "int" }, { name: "saved_at", kind: "datetime" },
      { name: "saved_by", kind: "str" }, { name: "priced_release", kind: "str" },
      { name: "source_reference", kind: "str" }, { name: "source_option", kind: "int" },
      { name: "source_relation", kind: "str" }, { name: "handover_note", kind: "str" },
      { name: "note_by", kind: "str" }, { name: "note_at", kind: "datetime" },
    ] } })
    render(<SchemaEditor readOnly={readOnly} />)
    const shown = within(tableNamed("quote_details"))
    expect(nameFields(tableNamed("quote_details"))).toEqual([
      "quote_reference", "quote_option", "quote_name", "insured", "broker",
      "inception_date", "expiry_date", "is_renewal",
    ])
    expect(shown.getByText("8 cols")).toBeInTheDocument()
    expect(shown.queryByTestId(/^schema-store-column-/)).toBeNull()
    expect(shown.getAllByRole("button", { name: /^Label and rules of / })).toHaveLength(6)
    for (const name of ["quote_reference", "quote_option"]) {
      const key = within(shown.getByTestId(`schema-column-${name}`))
      expect(key.getByRole("textbox", { name: "Column name" })).toHaveAttribute("readonly")
      expect(key.queryByRole("button", { name: `Label and rules of ${name}` })).toBeNull()
    }
    // User fields in other tables are not hidden by their name.
    fireEvent.click(within(tableNamed("audit")).getByRole("button", { name: "Label and rules of modified_date" }))
    expect(screen.getByRole("textbox", { name: "Label of modified_date" })).toBeInTheDocument()
    const search = screen.getByRole("textbox", { name: "Search fields" })
    for (const query of ["Last changed", "saved_at", "haute_version", "stage"]) {
      fireEvent.change(search, { target: { value: query } })
      expect(screen.getByText("No fields match")).toBeInTheDocument()
      expect(screen.queryByRole("region", { name: "Table quote_details" })).toBeNull()
    }
    fireEvent.change(search, { target: { value: "insured" } })
    expect(nameFields(tableNamed("quote_details"))).toEqual(["insured"])
    expect(current()).toEqual(original)
    expect(current().schema.tables[0].inputs).toHaveLength(16)
    expect(useWorkbenchFormStore.getState().undoStack).toEqual([])
  })

  it.each([false, true])("shows shared keys once before inputs and outputs (readOnly=%s)", (readOnly) => {
    const items = table("items", {
      rows: "many",
      inputs: [...IDENTITY, column("line_number", { type: "int", index: "numbered", label: "Line identifier" }), column("value", { type: "float" })],
      outputs: [column("premium", { type: "float" })],
    })
    const policy = table("policy_details", { inputs: [...IDENTITY, column("table_index")], outputs: [column("rate", { type: "float" })] })
    const inputOnly = table("cover", { inputs: [...IDENTITY, column("state")] })
    const outputOnly = { ...LAYERS, outputs: [...IDENTITY, ...LAYERS.outputs] }
    const original = form(detailsTable(), items, policy, inputOnly, outputOnly)
    load(original)
    render(<SchemaEditor readOnly={readOnly} />)

    for (const each of original.schema.tables) {
      const keys = blockOf(each.name, "Keys")
      const index = [...each.inputs, ...each.outputs].find((column) => column.index !== null)
      expect(nameFields(keys)).toEqual(["quote_reference", "quote_option", ...(index ? [index.name] : [])])
      expect(within(keys).getByText("Keys")).toBeInTheDocument()
      expect(within(keys).queryByRole("button", { name: /^Add / })).toBeNull()
      expect(within(keys).queryByTitle("Drag to move")).toBeNull()
      const groups = within(tableNamed(each.name)).getAllByRole("group")
      expect(groups[0]).toBe(keys)
      // Every column has exactly one row, including keys shared by the two sides.
      expect(within(tableNamed(each.name)).getAllByTestId(/^schema-column-[a-z_]+$/)).toHaveLength(each.name === "quote_details" ? 8 : each.inputs.length + each.outputs.length)
    }
    expect(everyName("items", "Inputs")).toEqual(["value"])
    expect(everyName("items", "Outputs")).toEqual(["premium"])
    expect(everyName("policy_details", "Inputs")).toEqual(["table_index"])
    expect(everyName("policy_details", "Outputs")).toEqual(["rate"])
    expect(everyName("cover", "Inputs")).toEqual(["state"])
    expect(everyName("layers", "Outputs")).toEqual(["limit"])
    expect(everyName("quote_details", "Inputs")).toEqual(["quote_name", "insured", "broker", "inception_date", "expiry_date", "is_renewal"])
    expect(screen.queryByTestId(/^schema-output-carried-/)).toBeNull()

    const search = screen.getByRole("textbox", { name: "Search fields" })
    fireEvent.change(search, { target: { value: "quote_reference" } })
    expect(screen.getAllByRole("group").map((group) => group.getAttribute("aria-label"))).toEqual(original.schema.tables.map((each) => `${each.name} Keys`))
    expect(everyName("items", "Keys")).toEqual(["quote_reference"])
    fireEvent.change(search, { target: { value: "identifier" } })
    expect(screen.getAllByRole("group")).toEqual([blockOf("items", "Keys")])
    expect(everyName("items", "Keys")).toEqual(["line_number"])
    fireEvent.change(search, { target: { value: "premium" } })
    expect(screen.getAllByRole("group")).toEqual([blockOf("items", "Outputs")])
    expect(current()).toEqual(original)
    expect(useWorkbenchFormStore.getState().undoStack).toEqual([])
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

  it.each([false, true])("lists where a column is used, sheet by sheet, as Remove counts it (readOnly=%s)", (readOnly) => {
    const spec = form(POLICY, LAYERS)
    const tests = { match: "all" as const, conditions: [{ table: "policy_details", column: "state", op: "filled" as const, values: [], field: null, days: 0 }] }
    const always = { match: "all" as const, conditions: [] }
    const rect = { x: 0, y: 0, w: 160, h: 40 }
    spec.pages[0].sections[0].widgets = [
      { ...createWidget("heading", rect, spec), name: "title_1" },
      { name: "policy_box", id: "w_box", type: "collection", x: 0, y: 48, w: 400, h: 100, title: "Policy", columns: 2, ...COLLECTION_OPTIONS, fields: [{ table: "policy_details", column: "state" }], show_when: tests },
      { ...createWidget("import", rect, spec), id: "w_in", name: "import_policy", table: "policy_details" },
    ]
    spec.pages.push(
      {
        id: "page_2",
        title: "Rating",
        width: 1280,
        show_when: tests,
        sections: [
          {
            id: "section_2",
            title: "Extras",
            height: 560,
            show_when: tests,
            widgets: [
              {
                ...createWidget("import", rect, spec),
                id: "w_sql",
                name: "import_layers",
                table: "layers",
                source: { format: "sql", connection: "rating", query: "select 1", parameters: [{ name: "st", table: "policy_details", column: "state" }] },
              },
              { ...createWidget("export", rect, spec), id: "w_out", name: "export_all", tables: ["policy_details"] },
            ],
          },
        ],
      },
      { id: "page_3", title: "Notes", width: 1280, show_when: always, sections: [{ id: "section_3", title: "Section 3", height: 560, show_when: always, widgets: [] }] },
    )
    load(spec)
    render(<SchemaEditor readOnly={readOnly} />)
    const eye = screen.getByRole("button", { name: "Where state is used" })
    const uses = () => screen.queryByTestId("schema-column-state-uses")
    /** Each line of a sheet's uses: the holder's name, then its kind and ways. */
    const lines = (sheet: string) =>
      within(within(uses()!).getByRole("list", { name: `Uses on ${sheet}` }))
        .getAllByRole("listitem")
        .map((item) => [...item.querySelectorAll("span")].map((part) => part.textContent).join(" | "))
    const heading = (sheet: string) => within(uses()!).getByText(sheet).closest("p")?.textContent

    expect(eye).toHaveAttribute("aria-expanded", "false")
    fireEvent.click(eye)
    expect(eye).toHaveAttribute("aria-expanded", "true")
    // Sheets in their order, a sheet nothing on uses it left out; each component once, with all its ways.
    expect(heading("Sheet 1")).toBe("Sheet 1")
    expect(lines("Sheet 1")).toEqual(["policy_box | Collection · Shows it, Show when", "import_policy | Import · Imports it"])
    expect(heading("Rating")).toBe("RatingShow when")
    expect(lines("Rating")).toEqual(["Extras | Section · Show when", "import_layers | Import · Gives a parameter", "export_all | Export · Exports it"])
    expect(within(uses()!).queryByText("Notes")).toBeNull()
    // What it lists is what removing the column takes it out of.
    const f = current()
    const removed = new Set([...fieldUses(f, "policy_details", "state"), ...conditionUses(f, "policy_details", "state"), ...buttonUses(f, "policy_details", "state")])
    expect(removed.size).toBe(lines("Sheet 1").length + lines("Rating").length + 1)

    // One row's details at a time: Label and rules closes it, and it closes Label and rules.
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of state" }))
    expect(uses()).toBeNull()
    expect(eye).toHaveAttribute("aria-expanded", "false")
    fireEvent.click(eye)
    expect(screen.queryByRole("textbox", { name: "Label of state" })).toBeNull()
    expect(uses()).not.toBeNull()
    fireEvent.click(eye)
    expect(uses()).toBeNull()
    expect(useWorkbenchFormStore.getState().undoStack).toEqual([])

    // Every column has one, the index's too; one nothing uses says so.
    expect(screen.getByRole("button", { name: "Where layer is used" })).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Where limit is used" }))
    expect(screen.getByTestId("schema-column-limit-uses")).toHaveTextContent(/^Not used on any sheet$/)
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

  it("gives a many-row table its numbered index and keeps it as its last input goes", () => {
    load(form(POLICY, table("layers", { rows: "many" })))
    render(<SchemaEditor />)
    // No index control: the operations give and take it.
    expect(screen.queryByRole("checkbox", { name: /^Add index/ })).toBeNull()

    fireEvent.click(within(blockOf("layers", "Inputs")).getByRole("button", { name: "Add column" }))

    expect(everyName("layers", "Keys")).toEqual(["quote_reference", "quote_option", "table_index"])
    expect(blockNames("layers", "Inputs")).toEqual(["column"])
    expect(tablePorts(current().schema.tables[1])).toEqual(["layers_input"])
    expect(own(current().schema.tables[1].inputs)[0]).toMatchObject({ name: "table_index", type: "int", index: "numbered" })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
    expect(screen.getByRole("combobox", { name: "Type of table_index" })).toHaveValue("int")
    // Never dragged.
    expect(columnGrip("table_index")).toBeNull()
    // Nothing is typed into the index, so it has no format or default to show.
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of table_index" }))
    expect(screen.getByRole("textbox", { name: "Label of table_index" })).toBeInTheDocument()
    expect(screen.queryByRole("combobox", { name: "Format of table_index" })).not.toBeInTheDocument()
    expect(screen.queryByLabelText("Default of table_index")).not.toBeInTheDocument()

    // Alt+Up never passes it.
    fireEvent.keyDown(within(blockOf("layers", "Inputs")).getByRole("textbox", { name: "Column name" }), { key: "ArrowUp", altKey: true })
    expect(blockNames("layers", "Inputs")).toEqual(["column"])
    expect(own(current().schema.tables[1].inputs).map((column) => column.name)).toEqual(["table_index", "column"])

    fireEvent.click(screen.getByRole("button", { name: "Remove column column" }))
    expect(columnNames("layers")).toEqual(["table_index"])
    expect(everyName("layers", "Inputs")).toEqual([])
    expect(within(blockOf("layers", "Inputs")).getByRole("button", { name: "Add column" })).toBeInTheDocument()
    expect(own(current().schema.tables[1].inputs)).toEqual([expect.objectContaining({ name: "table_index", index: "numbered" })])
    expect(tablePorts(current().schema.tables[1])).toEqual([])
  })

  it("gives the index no remove: the operations alone take it away", () => {
    load(form(POLICY, table("layers", { rows: "many", inputs: [INDEX, column("layer", { type: "int" })] })))
    render(<SchemaEditor />)

    expect(screen.getByTestId("schema-column-table_index")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Remove column table_index" })).toBeNull()
  })

  it("keeps empty blocks inactive with shared keys, including output-first and undo", () => {
    render(<SchemaEditor />)

    fireEvent.click(screen.getByRole("button", { name: "Add table" }))
    fireEvent.change(screen.getByRole("combobox", { name: "Rows of table" }), { target: { value: "many" } })

    // Many rows give it its index at once, though it holds no column of its own yet.
    const added = tableNamed("table")
    expect(nameFields(added)).toEqual(["quote_reference", "quote_option", "table_index"])
    expect(everyName("table", "Keys")).toEqual(["quote_reference", "quote_option", "table_index"])
    expect(everyName("table", "Inputs")).toEqual([])
    expect(everyName("table", "Outputs")).toEqual([])
    expect(tablePorts(current().schema.tables[2])).toEqual([])
    expect(within(blockOf("table", "Inputs")).getByRole("button", { name: "Add column" })).toBeInTheDocument()
    expect(within(blockOf("table", "Outputs")).getByRole("button", { name: "Add column" })).toBeInTheDocument()
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

    // Renaming the index with Enter does not choose Inputs implicitly.
    const indexName = within(within(added).getByTestId("schema-column-table_index")).getByRole("textbox", { name: "Column name" })
    fireEvent.change(indexName, { target: { value: "line_number" } })
    fireEvent.keyDown(indexName, { key: "Enter" })
    expect(nameFields(added)).toEqual(["quote_reference", "quote_option", "line_number"])
    expect(within(blockOf("table", "Inputs")).getByRole("button", { name: "Add column" })).toBeInTheDocument()
    expect(tablePorts(current().schema.tables[2])).toEqual([])
    const edits = useWorkbenchFormStore.getState().undoStack.length

    fireEvent.click(within(blockOf("table", "Outputs")).getByRole("button", { name: "Add column" }))
    expect(current().schema.tables[2].inputs).toEqual([])
    expect(everyName("table", "Keys")).toEqual(["quote_reference", "quote_option", "line_number"])
    expect(everyName("table", "Outputs")).toEqual(["column"])
    expect(current().schema.tables[2].outputs.at(-1)).toMatchObject({ name: "column", type: "str" })
    expect(tablePorts(current().schema.tables[2])).toEqual(["table_output"])
    expect(within(added).getByDisplayValue("column")).toHaveFocus()
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(edits + 1)
    expect(within(blockOf("table", "Inputs")).getByRole("button", { name: "Add column" })).toBeInTheDocument()

    act(() => useWorkbenchFormStore.getState().undo())
    expect(nameFields(added)).toEqual(["quote_reference", "quote_option", "line_number"])
    expect(everyName("table", "Keys")).toEqual(["quote_reference", "quote_option", "line_number"])
    expect(everyName("table", "Outputs")).toEqual([])
    expect(tablePorts(current().schema.tables[2])).toEqual([])
    expect(within(blockOf("table", "Outputs")).getByRole("button", { name: "Add column" })).toBeInTheDocument()
    fireEvent.click(within(blockOf("table", "Inputs")).getByRole("button", { name: "Add column" }))
    expect(everyName("table", "Keys")).toEqual(["quote_reference", "quote_option", "line_number"])
    expect(everyName("table", "Inputs")).toEqual(["column"])
    expect(tablePorts(current().schema.tables[2])).toEqual(["table_input"])
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(edits + 1)
  })

  it("takes the index away with what shows it as its table switches to one row", () => {
    const indexed = form(POLICY, table("layers", { rows: "many", inputs: [INDEX, column("layer", { type: "int" })] }))
    indexed.pages[0].sections[0].widgets = [
      { name: "w", id: "w", type: "tableInput", x: 0, y: 0, w: 400, h: 100, title: "Layers", rows: 3, ...TABLE_OPTIONS, fields: [{ table: "layers", column: "table_index" }, { table: "layers", column: "layer" }], show_when: { match: "all", conditions: [] } },
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
    load(form(table("class", { outputs: [column("a")] })))
    render(<SchemaEditor />)

    const block = tableNamed("class")
    expect(within(block).getByText("1 col, 1 problem")).toBeInTheDocument()
    // Its port, the name with _output, is held to the port rules.
    expect(within(block).getByText(/^Table "class"'s port class_output: .*cannot be a Python hard keyword/)).toBeInTheDocument()
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
          inputs: [column("table_index", { type: "int", index: "numbered", number_format: "number" }), column("value", { type: "float", default_value: "5" })],
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
    expect(summary("table_index")).toBeNull()
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
    expect(screen.queryByRole("button", { name: /^Add (inputs|outputs)$/ })).toBeNull()
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
    expect(within(layers).queryByRole("combobox", { name: "How layer gets its values" })).toBeNull()
    expect(within(layers).queryByRole("button", { name: "Delete table layers" })).toBeNull()
    expect(within(layers).queryByRole("button", { name: /^Remove column/ })).toBeNull()
    expect(within(layers).queryByRole("button", { name: "Add column" })).toBeNull()
    expect(screen.queryByRole("button", { name: /^Add (inputs|outputs)$/ })).toBeNull()
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
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of layer" }))
    expect(within(layers).getByRole("combobox", { name: "How layer gets its values" })).toHaveValue("typed")
    expect(within(layers).getByRole("combobox", { name: "How layer gets its values" })).toBeDisabled()
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of limit" }))
    expect(screen.getByRole("textbox", { name: "Label of limit" })).toBeDisabled()
    fireEvent.click(screen.getByRole("button", { name: "Collapse policy_details" }))
    expect(within(tableNamed("policy_details")).queryAllByRole("textbox", { name: "Column name" })).toEqual([])
    expect(useWorkbenchFormStore.getState().undoStack).toEqual([])
  })
})
