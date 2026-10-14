/**
 * The schema editor (specs/workbench): tables and columns defined in the view, each
 * field an edit of the form store once it commits, with what a sheet shows protected
 * by a confirmation.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { ApiError } from "../../api/client"
import { listProjectFiles } from "../../api/projectFiles"
import type { FormSpec, ProjectFolderResponse, SchemaColumn, SchemaTable } from "../../api/types"
import { fetchParamFile } from "../../api/workbench"
import useDocumentStatusStore from "../../stores/useDocumentStatusStore"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import { BLANK_THEME, COLLECTION_OPTIONS } from "../../utils/workbenchForm"
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
  options_source: null,
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

const POLICY = table("t_policy", "policy_details", {
  columns: [
    column("c_state", "state", { options: ["CA", "NY"], options_source: { file: "states.csv", column: "state" } }),
    column("c_exposure", "exposure", { type: "float" }),
  ],
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
    vi.mocked(listProjectFiles).mockResolvedValue(paramsFolder())
    vi.mocked(fetchParamFile).mockResolvedValue({ columns: ["state"], values: ["CA", "NY"] })
  })

  afterEach(() => {
    cleanup()
    useWorkbenchFormStore.setState({ form: null, status: "idle", undoStack: [], redoStack: [] })
    useDocumentStatusStore.setState({ capabilities: null })
    vi.mocked(fetchParamFile).mockReset()
  })

  it("lists the tables with their columns' names and types, each type shown as its marker", () => {
    render(<SchemaEditor />)

    expect(columnNames("policy_details")).toEqual(["state", "exposure"])
    expect(screen.getByRole("combobox", { name: "Type of exposure" })).toHaveValue("float")
    expect(screen.getByRole("combobox", { name: "Role of layers" })).toHaveValue("input")
    expect(screen.getByRole("combobox", { name: "Rows of layers" })).toHaveValue("many")
    expect(within(tableNamed("policy_details")).getByText("2 cols")).toBeInTheDocument()
  })

  it("keeps Haute's quote_details table as it is: labels and help, and rules only where the underwriter types", () => {
    load(form(detailsTable(), POLICY))
    render(<SchemaEditor />)
    const details = tableNamed("quote_details")

    expect(within(details).getByRole("textbox", { name: "Table name" })).toHaveAttribute("readonly")
    expect(within(details).getByRole("combobox", { name: "Role of quote_details" })).toBeDisabled()
    expect(within(details).getByRole("combobox", { name: "Rows of quote_details" })).toBeDisabled()
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
    expect(current().schema.tables[0].columns.find((column) => column.name === "insured")).toMatchObject({
      label: "Client",
      required: true,
      default_value: "Acme Ltd",
    })
    expect(current().schema.tables[0].columns.find((column) => column.name === "stage")).toMatchObject({
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

    expect(current().schema.tables[0].columns[1]).toMatchObject({
      label: "Exposure ($)",
      required: true,
      min: 0,
      max: null,
    })
    expect(screen.getByTestId("schema-column-c_exposure-max-error")).toHaveTextContent("Enter a number.")
    expect(within(tableNamed("policy_details")).getByText("required · 0 to …")).toBeInTheDocument()
    // Allowed values come from a params file alone: none are typed in.
    expect(screen.queryByRole("textbox", { name: "Allowed values of exposure" })).toBeNull()
  })

  it("reads a column's allowed values from a CSV in the params folder, its file then its column, as one undo step", async () => {
    load(form(table("t_policy", "policy_details", { columns: [column("c_state", "state")] })))
    vi.mocked(listProjectFiles).mockResolvedValue(paramsFolder("sic_codes.csv", "notes.txt", "old", "LEGACY.CSV"))
    vi.mocked(fetchParamFile).mockImplementation(async (_file, column) => ({
      columns: ["occupancy", "sic_name"],
      values: column === "sic_name" ? ["Food", "Cloth"] : [],
    }))
    render(<SchemaEditor />)

    fireEvent.click(screen.getByRole("button", { name: "Label and rules of state" }))
    const from = await screen.findByRole("combobox", { name: "Where the allowed values of state come from" })
    expect(within(from).getAllByRole("option").map((option) => option.textContent)).toEqual(["", "sic_codes.csv", "LEGACY.CSV"])
    fireEvent.change(from, { target: { value: "sic_codes.csv" } })
    // The form changes once a column is picked.
    const columnSelect = await screen.findByRole("combobox", { name: "Column the allowed values of state come from" })
    await waitFor(() => expect(within(columnSelect).getAllByRole("option").map((option) => option.textContent)).toEqual(["", "occupancy", "sic_name"]))
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(0)
    fireEvent.change(columnSelect, { target: { value: "sic_name" } })

    await waitFor(() =>
      expect(current().schema.tables[0].columns[0]).toMatchObject({
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

    expect(current().schema.tables[0].columns[0]).toMatchObject({ options: [], options_source: null })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
    expect(screen.queryByRole("combobox", { name: "Column the allowed values of state come from" })).toBeNull()
  })

  it("names allowed values typed in, as an older form holds them, among its table's problems", () => {
    load(form(table("t_policy", "policy_details", { columns: [column("c_state", "state", { options: ["CA", "NY"] })] })))
    render(<SchemaEditor />)

    expect(
      within(tableNamed("policy_details")).getByText('Column "state" in Table "policy_details" has allowed values typed in, not read from a params file'),
    ).toBeInTheDocument()
  })

  it("reads a column's values from its file again, and says why the file cannot give them", async () => {
    load(form(table("t_policy", "policy_details", {
      columns: [column("c_state", "state", { options: ["Food"], options_source: { file: "sic_codes.csv", column: "sic_name" } })],
    })))
    vi.mocked(fetchParamFile).mockResolvedValue({ columns: ["sic_name"], values: ["Food", "Hangars"] })
    render(<SchemaEditor />)

    fireEvent.click(screen.getByRole("button", { name: "Label and rules of state" }))
    fireEvent.click(await screen.findByRole("button", { name: "Read the allowed values of state again" }))
    await waitFor(() => expect(current().schema.tables[0].columns[0].options).toEqual(["Food", "Hangars"]))

    const gone = "rating/files/params/sic_codes.csv doesn't exist."
    vi.mocked(fetchParamFile).mockRejectedValue(new ApiError("HTTP 409", 409, gone))
    fireEvent.click(screen.getByRole("button", { name: "Read the allowed values of state again" }))

    expect(await screen.findByRole("alert")).toHaveTextContent(gone)
    expect(current().schema.tables[0].columns[0].options).toEqual(["Food", "Hangars"])
  })

  it("shows a column's help, placeholder and number format, each saved as it commits", () => {
    load(form(POLICY, table("t_out", "pricing", { role: "output", columns: [column("c_premium", "premium", { type: "float" })] })))
    render(<SchemaEditor />)

    fireEvent.click(screen.getByRole("button", { name: "Label and rules of exposure" }))
    commit(screen.getByRole("textbox", { name: "Help of exposure" }), "Sum insured")
    commit(screen.getByRole("textbox", { name: "Placeholder of exposure" }), "e.g. 250,000")
    // Separators ticked under General make it a Number, as an Explore pivot does.
    fireEvent.click(screen.getByRole("checkbox", { name: "Separators (1,000)" }))
    expect(current().schema.tables[0].columns[1]).toMatchObject({ number_format: "number", use_grouping: true })
    fireEvent.change(screen.getByRole("combobox", { name: "Format of exposure" }), { target: { value: "currency_gbp" } })
    fireEvent.change(screen.getByRole("combobox", { name: "Decimal places of exposure" }), { target: { value: "0" } })
    fireEvent.click(screen.getByRole("checkbox", { name: "Separators (1,000)" }))
    expect(current().schema.tables[0].columns[1]).toMatchObject({
      help: "Sum insured",
      placeholder: "e.g. 250,000",
      number_format: "currency_gbp",
      decimal_places: 0,
      use_grouping: false,
    })
    fireEvent.change(screen.getByRole("combobox", { name: "Decimal places of exposure" }), { target: { value: "automatic" } })
    expect(current().schema.tables[0].columns[1].decimal_places).toBeNull()

    // A text column has no format; an output column takes a format but no placeholder.
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of state" }))
    expect(screen.queryByRole("combobox", { name: "Format of state" })).not.toBeInTheDocument()
    expect(screen.getByRole("textbox", { name: "Placeholder of state" })).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of premium" }))
    expect(screen.getByRole("combobox", { name: "Format of premium" })).toBeInTheDocument()
    expect(screen.queryByRole("textbox", { name: "Placeholder of premium" })).not.toBeInTheDocument()

    // A True/false or a Date column takes help but no placeholder.
    cleanup()
    load(form(table("t_flags", "flags", { columns: [column("c_ok", "ok", { type: "bool" }), column("c_on", "on", { type: "date" })] })))
    render(<SchemaEditor />)
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of ok" }))
    expect(screen.getByRole("textbox", { name: "Help of ok" })).toBeInTheDocument()
    expect(screen.queryByRole("textbox", { name: "Placeholder of ok" })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of on" }))
    expect(screen.getByRole("textbox", { name: "Help of on" })).toBeInTheDocument()
    expect(screen.queryByRole("textbox", { name: "Placeholder of on" })).not.toBeInTheDocument()
  })

  it("asks before removing a column or a table a sheet shows, and takes it off the sheet", () => {
    const shown = form(POLICY, LAYERS)
    shown.pages[0].sections[0].widgets = [
      { id: "w", type: "collection", x: 0, y: 0, w: 400, h: 100, title: "Policy", columns: 2, ...COLLECTION_OPTIONS, fields: [{ table: "t_policy", column: "c_state" }], show_when: { match: "all", conditions: [] } },
    ]
    load(shown)
    render(<SchemaEditor />)

    fireEvent.click(screen.getByRole("button", { name: "Remove column exposure" }))
    expect(columnNames("policy_details")).toEqual(["state"])

    fireEvent.click(screen.getByRole("button", { name: "Remove column state" }))
    expect(screen.getByRole("alert")).toHaveTextContent("Shown in 1 component; removing it takes it out of that too.")
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }))
    expect(columnNames("policy_details")).toEqual(["state"])
    fireEvent.click(screen.getByRole("button", { name: "Remove column state" }))
    fireEvent.click(screen.getByRole("button", { name: "Remove" }))
    expect(columnNames("policy_details")).toEqual([])
    expect(current().pages[0].sections[0].widgets[0]).toMatchObject({ fields: [] })

    fireEvent.click(screen.getByRole("button", { name: "Delete table layers" }))
    expect(screen.queryByRole("region", { name: "Table layers" })).not.toBeInTheDocument()
  })

  it("asks before removing a column or a table a condition tests, and takes the condition with it", () => {
    const tested = form(POLICY, LAYERS)
    const condition = { table: "t_policy", column: "c_state", op: "filled" as const, values: [] }
    tested.pages[0].show_when = { match: "all", conditions: [condition] }
    tested.pages[0].sections[0].widgets = [
      { id: "w", type: "collection", x: 0, y: 0, w: 400, h: 100, title: "Policy", columns: 2, ...COLLECTION_OPTIONS, fields: [{ table: "t_policy", column: "c_state" }], show_when: { match: "all", conditions: [] } },
    ]
    load(tested)
    render(<SchemaEditor />)

    fireEvent.click(screen.getByRole("button", { name: "Remove column state" }))
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Shown in 1 component and tested by the conditions of 1 sheet, section or component; removing it takes it out of them too.",
    )
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }))
    fireEvent.click(screen.getByRole("button", { name: "Delete table policy_details" }))
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Its columns are shown in 1 component and tested by the conditions of 1 sheet, section or component; deleting it takes them out of them too.",
    )
    fireEvent.click(screen.getByRole("button", { name: "Delete" }))
    expect(current().pages[0].show_when.conditions).toEqual([])
  })

  it("counts a section whose conditions test a column or a table in the confirmation, and takes those conditions out of it", () => {
    const tested = form(POLICY, LAYERS)
    const state = { table: "t_policy", column: "c_state", op: "filled" as const, values: [] }
    const exposure = { table: "t_policy", column: "c_exposure", op: "above" as const, values: ["100"] }
    tested.pages[0].show_when = { match: "all", conditions: [state] }
    tested.pages[0].sections.push({ id: "section_2", title: "Section 2", height: 160, show_when: { match: "all", conditions: [state, exposure] }, widgets: [] })
    load(tested)
    render(<SchemaEditor />)
    const sectionConditions = () => current().pages[0].sections[1].show_when.conditions

    // The state is tested by the sheet's conditions and the section's.
    fireEvent.click(screen.getByRole("button", { name: "Remove column state" }))
    expect(screen.getByRole("alert")).toHaveTextContent("Tested by the conditions of 2 sheets, sections or components; removing it takes it out of them too.")
    fireEvent.click(screen.getByRole("button", { name: "Remove" }))
    expect(current().pages[0].show_when.conditions).toEqual([])
    expect(sectionConditions()).toEqual([exposure])

    // What is left of the table is tested by the section's alone.
    fireEvent.click(screen.getByRole("button", { name: "Delete table policy_details" }))
    expect(screen.getByRole("alert")).toHaveTextContent("Its columns are tested by the conditions of 1 sheet, section or component; deleting it takes them out of it too.")
    fireEvent.click(screen.getByRole("button", { name: "Delete" }))
    expect(sectionConditions()).toEqual([])
    expect(current().pages[0].sections.map((section) => section.id)).toEqual(["section_1", "section_2"])
  })

  it("adds a many-row table's index first, an Integer whose type cannot change", () => {
    render(<SchemaEditor />)

    fireEvent.click(screen.getByRole("checkbox", { name: "Add index to layers" }))

    expect(columnNames("layers")).toEqual(["row_number", "layer", "limit"])
    expect(current().schema.tables[1].columns[0]).toMatchObject({ name: "row_number", type: "int", index: true })
    expect(screen.queryByRole("combobox", { name: "Type of row_number" })).not.toBeInTheDocument()
    expect(within(tableNamed("layers")).queryByText("row number")).toBeNull()
    // Nothing is typed into the index, so it has no format or default to show.
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of row_number" }))
    expect(screen.getByRole("textbox", { name: "Label of row_number" })).toBeInTheDocument()
    expect(screen.queryByRole("combobox", { name: "Format of row_number" })).not.toBeInTheDocument()
    expect(screen.queryByLabelText("Default of row_number")).not.toBeInTheDocument()
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

  it("holds no hint text in a column's details", () => {
    render(<SchemaEditor />)

    for (const name of ["exposure", "state", "limit"]) {
      fireEvent.click(screen.getByRole("button", { name: `Label and rules of ${name}` }))
      expect(screen.getByRole("textbox", { name: `Label of ${name}` })).toBeInTheDocument()
      expect(document.querySelectorAll("[placeholder]")).toHaveLength(0)
    }
  })

  it("puts a column in a group, offering the groups its table's columns are in", () => {
    render(<SchemaEditor />)

    fireEvent.click(screen.getByRole("button", { name: "Label and rules of exposure" }))
    commit(screen.getByRole("combobox", { name: "Group of exposure" }), " Cover ")
    expect(current().schema.tables[0].columns[1].group).toBe("Cover")
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)

    fireEvent.click(screen.getByRole("button", { name: "Label and rules of state" }))
    const group = screen.getByRole("combobox", { name: "Group of state" })
    const offered = document.getElementById(group.getAttribute("list") ?? "")
    expect([...(offered?.querySelectorAll("option") ?? [])].map((option) => option.value)).toEqual(["Cover"])
  })

  it("starts a new quote with a one-row input column's default, taken as its cells take a value", () => {
    const flags = table("t_flags", "flags", { columns: [column("c_ok", "ok", { type: "bool" }), column("c_on", "on", { type: "date" })] })
    load(form(POLICY, LAYERS, flags, table("t_out", "pricing", { role: "output", columns: [column("c_premium", "premium", { type: "float" })] })))
    render(<SchemaEditor />)

    // Typed, trimmed; and named in the table's problems while its rules refuse it.
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of exposure" }))
    commit(screen.getByRole("textbox", { name: "Default of exposure" }), " lots ")
    expect(current().schema.tables[0].columns[1].default_value).toBe("lots")
    expect(
      within(tableNamed("policy_details")).getByText('Column "exposure" in Table "policy_details" has a default its rules refuse: Not a number'),
    ).toBeInTheDocument()
    commit(screen.getByRole("textbox", { name: "Default of exposure" }), "250,000")
    expect(within(tableNamed("policy_details")).queryByText(/has a default its rules refuse/)).toBeNull()

    // One of the allowed values, or none.
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of state" }))
    const state = screen.getByRole("combobox", { name: "Default of state" })
    expect([...state.querySelectorAll("option")].map((option) => option.getAttribute("value"))).toEqual(["", "CA", "NY"])
    fireEvent.change(state, { target: { value: "NY" } })
    expect(current().schema.tables[0].columns[0].default_value).toBe("NY")

    // Ticked or not: a tick, or none.
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of ok" }))
    fireEvent.click(screen.getByRole("checkbox", { name: "ok starts ticked" }))
    expect(current().schema.tables[2].columns[0].default_value).toBe(true)
    fireEvent.click(screen.getByRole("checkbox", { name: "ok starts ticked" }))
    expect(current().schema.tables[2].columns[0].default_value).toBe("")

    // A day.
    fireEvent.click(screen.getByRole("button", { name: "Label and rules of on" }))
    const day = screen.getByLabelText("Default of on")
    expect(day).toHaveAttribute("type", "date")
    commit(day, "2026-10-13")
    expect(current().schema.tables[2].columns[1].default_value).toBe("2026-10-13")

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
})
