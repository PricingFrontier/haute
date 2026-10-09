/**
 * The Workbench Input's panel (specs/extensions): the tables the installed extension
 * supplies, read-only, with each label's problem as a port, a way to the extension's
 * view, what previews run on, and notes when nothing updates the tables.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import WorkbenchInputEditor from "../../panels/editors/WorkbenchInputEditor"
import useExtensionsStore, { PIPELINE_VIEW } from "../../stores/useExtensionsStore"

const RESERVED = new Set(["class", "pl", "pipeline"])

const column = (table: string, name: string, type: string, many = false) => ({
  name,
  path: many ? `$[:].${table}[:].${name}` : `$[:].${table}.${name}`,
  type,
  status: "Confirmed",
  selected: true,
  levels: null,
})
const config = {
  tables: [
    { path: "$[:]", label: "policy_details", emit: true, row_id_column: null,
      columns: [column("policy_details", "state", "str"), column("policy_details", "inception_date", "date")] },
    { path: "$[:].equipment[:]", label: "equipment", emit: true, row_id_column: "item_id",
      columns: [column("equipment", "item_id", "str", true), column("equipment", "value", "float", true)] },
    { path: "$[:]", label: "pl", emit: true, row_id_column: null, columns: [column("pl", "x", "int")] },
  ],
}
const workbench = {
  name: "obverse",
  label: "Workbench",
  api_base: "/api/extensions/obverse",
  entry_url: "/extensions/obverse/obverse-embed.js",
  ready: true,
  detail: null,
  quote_tables: true,
  response_tables: false,
}

function renderEditor(props: { insideSubmodel?: boolean; sample?: Record<string, unknown> } = {}) {
  return render(
    <WorkbenchInputEditor
      config={props.sample === undefined ? config : { ...config, sample: props.sample }}
      accentColor="#E69F00"
      reservedFrameLabels={RESERVED}
      insideSubmodel={props.insideSubmodel}
    />,
  )
}

describe("WorkbenchInputEditor", () => {
  beforeEach(() => {
    useExtensionsStore.setState({ extensions: [workbench], activeView: PIPELINE_VIEW })
  })
  afterEach(() => {
    cleanup()
    useExtensionsStore.setState({ extensions: [], activeView: PIPELINE_VIEW })
  })

  it("shows the extension's tables read-only, with nothing to edit them", () => {
    renderEditor()

    const tables = screen.getByTestId("workbench-input-tables")
    expect(within(tables).getByText("Tables from Workbench")).toBeInTheDocument()
    const policy = within(tables).getByTestId("workbench-table-policy_details")
    expect(within(policy).getByText("one per quote")).toBeInTheDocument()
    expect(within(policy).getByText("state")).toBeInTheDocument()
    expect(within(policy).getByText("date")).toBeInTheDocument()
    expect(within(within(tables).getByTestId("workbench-table-equipment")).getByText("many per quote")).toBeInTheDocument()
    expect(screen.queryByText("Infer Tables")).not.toBeInTheDocument()
    expect(screen.queryAllByRole("textbox")).toEqual([])
    expect(screen.queryByRole("note")).not.toBeInTheDocument()

    // It reads no file: previews run on nulls, so there is no sample to choose.
    expect(screen.queryByText("Preview Data")).not.toBeInTheDocument()
    expect(screen.getByTestId("workbench-input-preview-note")).toHaveTextContent(
      "Previews run on one row of nulls per table until the workbench supplies values.",
    )
  })

  it("says previews run on the workbench's sample once it supplies one", () => {
    renderEditor({ sample: { policy_details: { state: "NY" } } })
    expect(screen.getByTestId("workbench-input-preview-note")).toHaveTextContent(
      "Previews run on the workbench's sample values; a table with none is one row of nulls.",
    )
    cleanup()

    renderEditor({ sample: {} })
    expect(screen.getByTestId("workbench-input-preview-note")).toHaveTextContent(
      "Previews run on one row of nulls per table until the workbench supplies values.",
    )
  })

  it("says what stops a table's label being a port", () => {
    renderEditor()

    const reserved = screen.getByTestId("workbench-table-pl")
    expect(within(reserved).getByRole("alert")).toHaveTextContent("cannot be a Python hard keyword or a name node code binds itself")
    expect(within(screen.getByTestId("workbench-table-policy_details")).queryByRole("alert")).not.toBeInTheDocument()
  })

  it("opens the extension's view to edit the tables", () => {
    renderEditor()

    fireEvent.click(screen.getByRole("button", { name: "Edit in Workbench" }))

    expect(useExtensionsStore.getState().activeView).toBe("obverse")
  })

  it("says the tables are the last copy when no installed extension supplies them", () => {
    useExtensionsStore.setState({ extensions: [{ ...workbench, quote_tables: false }] })
    renderEditor()

    expect(screen.getByRole("note")).toHaveTextContent(
      "No installed extension supplies these tables, so they are the last copy and nothing updates them.",
    )
    expect(screen.queryByRole("button", { name: /Edit in/ })).not.toBeInTheDocument()
    expect(screen.getByTestId("workbench-table-policy_details")).toBeInTheDocument()
  })

  it("says the editor updates the tables only at the top level while a submodel is open", () => {
    renderEditor({ insideSubmodel: true })

    expect(screen.getByRole("note")).toHaveTextContent(
      "The editor updates these tables only at the pipeline's top level.",
    )
  })
})
