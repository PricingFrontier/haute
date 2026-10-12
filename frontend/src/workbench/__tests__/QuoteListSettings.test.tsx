/**
 * The quote list setting (specs/workbench): what the underwriter app's Quotes page shows of
 * each quote, picked in the schema editor from the columns that can be shown, kept right as
 * the schema changes, and what a deploy would refuse said.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import type { FormSpec, SchemaColumn, SchemaTable } from "../../api/types"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import { quoteListChoices, quoteListProblems, removeSchemaColumn, removeSchemaTable, setQuoteList, setSchemaTableRows } from "../../utils/workbenchForm"
import QuoteListSettings from "../QuoteListSettings"

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
  help: "",
  placeholder: "",
  number_format: "general",
  decimal_places: null,
  use_grouping: true,
  ...overrides,
})

const table = (id: string, name: string, overrides: Partial<SchemaTable> = {}): SchemaTable => ({ id, name, role: "input", rows: "one", columns: [], ...overrides })

const POLICY = table("t_policy", "policy_details", { columns: [column("c_name", "assured_name"), column("c_class", "class_of_business", { label: "Class" })] })
const LAYERS = table("t_layers", "layers", { rows: "many", columns: [column("c_limit", "limit", { type: "float" })] })
const PRICING = table("t_price", "pricing", { role: "output", columns: [column("c_premium", "charged_premium", { type: "float" }), column("c_band", "band")] })

const form = (quoteList: FormSpec["quote_list"] = { title: null, fields: [], headline: null }): FormSpec => ({
  version: 1,
  name: "motor",
  schema: { tables: [POLICY, LAYERS, PRICING] },
  pages: [{ id: "p", title: "Sheet 1", show_when: { match: "all", conditions: [] }, sections: [{ id: "s", title: "s", height: 560, show_when: { match: "all", conditions: [] }, widgets: [] }] }],
  sample: {},
  quote_list: quoteList,
})

const NAME = { table: "t_policy", column: "c_name" }
const CLASS = { table: "t_policy", column: "c_class" }
const PREMIUM = { table: "t_price", column: "c_premium" }

describe("the quote list's form operations", () => {
  it("offers one-row input columns for the title and fields, one-row output numbers for the headline", () => {
    expect(quoteListChoices(form(), "field").map((choice) => choice.label)).toEqual(["Policy details › Assured name", "Policy details › Class"])
    expect(quoteListChoices(form(), "headline").map((choice) => choice.label)).toEqual(["Pricing › Charged premium"])
  })

  it("drops a column removed, a table removed or one switched to many rows", () => {
    const listed = setQuoteList(form(), { title: NAME, fields: [CLASS], headline: PREMIUM })
    expect(removeSchemaColumn(listed, "t_policy", "c_class").quote_list).toEqual({ title: NAME, fields: [], headline: PREMIUM })
    expect(removeSchemaTable(listed, "t_price").quote_list.headline).toBeNull()
    expect(setSchemaTableRows(listed, "t_policy", "many").quote_list).toEqual({ title: null, fields: [], headline: PREMIUM })
  })

  it("says each way it names a column the Quotes page cannot show", () => {
    const spec = form({ title: { table: "t_layers", column: "c_limit" }, fields: [CLASS, CLASS, { table: "t_x", column: "c_y" }], headline: { table: "t_price", column: "c_band" } })
    expect(quoteListProblems(spec)).toEqual([
      "The title, layers.limit, is in a many-row table: a quote's list row shows one value.",
      "The summary field 3 names a column the schema no longer has.",
      "A summary field is named twice.",
      "The headline, pricing.band, is not a number column.",
    ])
    expect(quoteListProblems(form({ title: PREMIUM, fields: [], headline: NAME }))).toEqual([
      "The title, pricing.charged_premium, is not in an input table.",
      "The headline, policy_details.assured_name, is not in an output table.",
      "The headline, policy_details.assured_name, is not a number column.",
    ])
  })
})

describe("QuoteListSettings", () => {
  const current = () => useWorkbenchFormStore.getState().form as FormSpec
  const show = () => render(<QuoteListSettings form={current()} />)
  const again = (view: ReturnType<typeof show>) => view.rerender(<QuoteListSettings form={current()} />)

  beforeEach(() => {
    useWorkbenchFormStore.setState({ form: form(), revision: "r", status: "ready", savedForm: "", dirty: false, undoStack: [], redoStack: [] })
  })

  afterEach(() => {
    cleanup()
    useWorkbenchFormStore.setState({ form: null, status: "idle", undoStack: [], redoStack: [] })
  })

  it("picks the title, summary fields and headline, each an undoable edit", () => {
    const view = show()
    expect(screen.getByRole("button", { name: /Quote list/ })).toHaveTextContent("the reference and name alone")

    fireEvent.change(screen.getByLabelText("Title"), { target: { value: JSON.stringify(["t_policy", "c_name"]) } })
    again(view)
    fireEvent.click(screen.getByRole("button", { name: "Add a summary field" }))
    again(view)
    fireEvent.change(screen.getByLabelText("Headline"), { target: { value: JSON.stringify(["t_price", "c_premium"]) } })
    again(view)

    expect(current().quote_list).toEqual({ title: NAME, fields: [NAME], headline: PREMIUM })
    expect(screen.getByRole("button", { name: /Quote list/ })).toHaveTextContent("3 of 3 set")
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(3)
  })

  it("changes and removes a summary field, offering no more than three", () => {
    useWorkbenchFormStore.setState({ form: form({ title: null, fields: [NAME, CLASS], headline: null }) })
    const view = show()

    fireEvent.change(screen.getByLabelText("Summary field 1"), { target: { value: JSON.stringify(["t_policy", "c_class"]) } })
    again(view)
    expect(current().quote_list.fields).toEqual([CLASS, CLASS])
    fireEvent.change(screen.getByLabelText("Summary field 2"), { target: { value: "" } })
    again(view)
    expect(current().quote_list.fields).toEqual([CLASS])
    fireEvent.click(screen.getByRole("button", { name: "Remove summary field 1" }))
    again(view)
    expect(current().quote_list.fields).toEqual([])

    useWorkbenchFormStore.setState({ form: form({ title: null, fields: [NAME, CLASS, NAME], headline: null }) })
    again(view)
    expect(screen.queryByRole("button", { name: "Add a summary field" })).toBeNull()
  })

  it("keeps a column it can no longer show in view, marked, with the problem said", () => {
    useWorkbenchFormStore.setState({ form: form({ title: { table: "t_layers", column: "c_limit" }, fields: [], headline: null }) })
    show()

    expect(within(screen.getByLabelText("Title")).getByRole("option", { name: "Not a column it can show" })).toBeInTheDocument()
    expect(screen.getByRole("alert")).toHaveTextContent("is in a many-row table")
    fireEvent.click(screen.getByRole("button", { name: /Quote list/ }))
    expect(screen.queryByLabelText("Title")).toBeNull()
  })
})
