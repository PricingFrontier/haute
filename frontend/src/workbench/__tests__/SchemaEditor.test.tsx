/**
 * The schema editor (specs/workbench): tables and columns defined in the view, each
 * field an edit of the form store once it commits, with what a sheet shows protected
 * by a confirmation.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import type { FormSpec, SchemaColumn, SchemaTable } from "../../api/types"
import useDocumentStatusStore from "../../stores/useDocumentStatusStore"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import SchemaEditor from "../SchemaEditor"

const column = (id: string, name: string, overrides: Partial<SchemaColumn> = {}): SchemaColumn => ({
  id,
  name,
  type: "str",
  label: "",
  key: false,
  required: false,
  min: null,
  max: null,
  options: [],
  index: false,
  ...overrides,
})

const table = (id: string, name: string, overrides: Partial<SchemaTable> = {}): SchemaTable => ({
  id,
  name,
  role: "input",
  rows: "one",
  columns: [],
  ...overrides,
})

const form = (...tables: SchemaTable[]): FormSpec => ({
  version: 1,
  name: "motor",
  schema: { tables },
  pages: [{ id: "page_1", title: "Sheet 1", widgets: [] }],
  sample: {},
})

const POLICY = table("t_policy", "policy_details", {
  columns: [column("c_state", "state", { options: ["CA", "NY"] }), column("c_exposure", "exposure", { type: "float" })],
})
const LAYERS = table("t_layers", "layers", {
  rows: "many",
  columns: [column("c_layer", "layer", { type: "int", key: true }), column("c_limit", "limit", { type: "float" })],
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
const columnNames = (name: string) =>
  within(tableNamed(name)).queryAllByRole("textbox", { name: "Column name" }).map((input) => (input as HTMLInputElement).value)

/** Type into a committed field and commit with blur. */
function commit(input: HTMLElement, value: string): void {
  fireEvent.change(input, { target: { value } })
  fireEvent.blur(input)
}

describe("SchemaEditor", () => {
  beforeEach(() => {
    load(form(POLICY, LAYERS))
  })

  afterEach(() => {
    cleanup()
    useWorkbenchFormStore.setState({ form: null, status: "idle", undoStack: [], redoStack: [] })
    useDocumentStatusStore.setState({ capabilities: null })
  })

  it("lists the tables with their columns' names and types, each type shown as its marker", () => {
    render(<SchemaEditor />)

    expect(columnNames("policy_details")).toEqual(["state", "exposure"])
    expect(screen.getByRole("combobox", { name: "Type of exposure" })).toHaveValue("float")
    expect(screen.getByRole("combobox", { name: "Role of layers" })).toHaveValue("input")
    expect(screen.getByRole("combobox", { name: "Rows of layers" })).toHaveValue("many")
    expect(within(tableNamed("policy_details")).getByText("2 cols")).toBeInTheDocument()
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

  it("changes a table's role and rows, dropping its keys when it has one row", () => {
    render(<SchemaEditor />)

    fireEvent.change(screen.getByRole("combobox", { name: "Role of policy_details" }), { target: { value: "output" } })
    fireEvent.change(screen.getByRole("combobox", { name: "Rows of layers" }), { target: { value: "one" } })

    expect(current().schema.tables[0].role).toBe("output")
    expect(current().schema.tables[1].rows).toBe("one")
    expect(current().schema.tables[1].columns.map((c) => c.key)).toEqual([false, false])
  })

  it("picks a column's type from its marker and marks a key in a many-row table", () => {
    render(<SchemaEditor />)

    fireEvent.change(screen.getByRole("combobox", { name: "Type of state" }), { target: { value: "bool" } })
    const key = screen.getByRole("button", { name: "Key: limit" })
    expect(key).toHaveAttribute("aria-pressed", "false")
    fireEvent.click(key)

    expect(current().schema.tables[0].columns[0].type).toBe("bool")
    expect(current().schema.tables[1].columns[1].key).toBe(true)
    expect(screen.queryByRole("button", { name: "Key: state" })).not.toBeInTheDocument()
  })

  it("adds the next column on Enter in a column's name, and moves one with Alt+Down", () => {
    render(<SchemaEditor />)
    const [state] = within(tableNamed("policy_details")).getAllByRole("textbox", { name: "Column name" })

    fireEvent.keyDown(state, { key: "Enter" })
    expect(columnNames("policy_details")).toEqual(["state", "column", "exposure"])
    const added = within(tableNamed("policy_details")).getAllByRole("textbox", { name: "Column name" })[1]
    expect(added).toBe(document.activeElement)

    fireEvent.keyDown(added, { key: "ArrowDown", altKey: true })
    expect(columnNames("policy_details")).toEqual(["state", "exposure", "column"])
  })

  it("shows a column's label and rules, each saved as it commits", () => {
    render(<SchemaEditor />)

    fireEvent.click(screen.getByRole("button", { name: "Label and rules of exposure" }))
    commit(screen.getByRole("textbox", { name: "Label of exposure" }), "Exposure ($)")
    fireEvent.click(screen.getByRole("checkbox", { name: "exposure is required" }))
    commit(screen.getByTestId("schema-column-c_exposure-min"), "0")
    commit(screen.getByTestId("schema-column-c_exposure-max"), "lots")
    commit(screen.getByRole("textbox", { name: "Allowed values of exposure" }), "100, 250 ,")

    expect(current().schema.tables[0].columns[1]).toMatchObject({
      label: "Exposure ($)",
      required: true,
      min: 0,
      max: null,
      options: ["100", "250"],
    })
    expect(screen.getByTestId("schema-column-c_exposure-max-error")).toHaveTextContent("Enter a number.")
    expect(within(tableNamed("policy_details")).getByText("required · 0 to … · 2 values")).toBeInTheDocument()
  })

  it("asks before removing a column or a table a sheet shows, and takes it off the sheet", () => {
    const shown = form(POLICY, LAYERS)
    shown.pages[0].widgets = [
      { id: "w", type: "collection", x: 0, y: 0, w: 400, h: 100, title: "Policy", columns: 2, fields: [{ table: "t_policy", column: "c_state" }] },
    ]
    load(shown)
    render(<SchemaEditor />)

    fireEvent.click(screen.getByRole("button", { name: "Remove column exposure" }))
    expect(columnNames("policy_details")).toEqual(["state"])

    fireEvent.click(screen.getByRole("button", { name: "Remove column state" }))
    expect(screen.getByRole("alert")).toHaveTextContent("Shown in 1 widget; removing it takes it out of that too.")
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }))
    expect(columnNames("policy_details")).toEqual(["state"])
    fireEvent.click(screen.getByRole("button", { name: "Remove column state" }))
    fireEvent.click(screen.getByRole("button", { name: "Remove" }))
    expect(columnNames("policy_details")).toEqual([])
    expect(current().pages[0].widgets[0].fields).toEqual([])

    fireEvent.click(screen.getByRole("button", { name: "Delete table layers" }))
    expect(screen.queryByRole("region", { name: "Table layers" })).not.toBeInTheDocument()
  })

  it("adds a many-row table's index first, an Integer whose type cannot change", () => {
    render(<SchemaEditor />)

    fireEvent.click(screen.getByRole("checkbox", { name: "Add index to layers" }))

    expect(columnNames("layers")).toEqual(["row_number", "layer", "limit"])
    expect(current().schema.tables[1].columns[0]).toMatchObject({ name: "row_number", type: "int", index: true })
    expect(screen.queryByRole("combobox", { name: "Type of row_number" })).not.toBeInTheDocument()
    expect(screen.queryByRole("checkbox", { name: "Add index to policy_details" })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole("checkbox", { name: "Add index to layers" }))
    expect(columnNames("layers")).toEqual(["layer", "limit"])
  })

  it("says what would stop the schema being the pipeline's tables", () => {
    useDocumentStatusStore.setState({
      capabilities: { reserved_api_input_frame_labels: ["class"] } as unknown as NonNullable<
        ReturnType<typeof useDocumentStatusStore.getState>["capabilities"]
      >,
    })
    load(form(table("t_class", "class", { rows: "many", columns: [column("c_a", "a")] })))
    render(<SchemaEditor />)

    const block = tableNamed("class")
    expect(within(block).getByText("1 col, 2 problems")).toBeInTheDocument()
    expect(within(block).getByText(/cannot be a Python hard keyword/)).toBeInTheDocument()
    expect(within(block).getByText('Table "class" has many rows, so it needs a key column')).toBeInTheDocument()
  })

  it("collapses a table's columns", () => {
    render(<SchemaEditor />)

    fireEvent.click(screen.getByRole("button", { name: "Collapse policy_details" }))

    expect(within(tableNamed("policy_details")).queryAllByRole("textbox", { name: "Column name" })).toEqual([])
    expect(columnNames("layers")).toEqual(["layer", "limit"])
    fireEvent.click(screen.getByRole("button", { name: "Expand policy_details" }))
    expect(columnNames("policy_details")).toEqual(["state", "exposure"])
  })
})
