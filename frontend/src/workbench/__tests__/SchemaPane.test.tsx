/**
 * The schema's pane (specs/workbench): its SQL opens the tables' SQL while building and is
 * disabled in Preview.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"

vi.mock("../../api/workbench", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/workbench")>()),
  fetchStoreSql: vi.fn(),
}))

import { fetchStoreSql } from "../../api/workbench"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import SchemaPane from "../SchemaPane"
import { sheetForm } from "./fixtures"

describe("SchemaPane", () => {
  beforeEach(() => {
    useWorkbenchFormStore.getState().install(sheetForm(), "rev-1")
    vi.mocked(fetchStoreSql).mockResolvedValue({ branch: null, environments: [] })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it("opens the tables' SQL from its SQL while building", async () => {
    useWorkbenchViewStore.setState({ mode: "build", schemaOpen: true })
    render(<SchemaPane />)

    fireEvent.click(screen.getByRole("button", { name: "SQL" }))

    expect(await screen.findByTestId("store-sql-window")).toBeInTheDocument()
    expect(fetchStoreSql).toHaveBeenCalledTimes(1)
  })

  it("keeps SQL disabled in Preview", () => {
    useWorkbenchViewStore.setState({ mode: "preview", schemaOpen: true })
    render(<SchemaPane />)

    expect(screen.getByRole("button", { name: "SQL" })).toBeDisabled()
  })
})
