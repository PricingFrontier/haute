import { describe, it, expect, beforeEach, afterEach } from "vitest"
import { render, screen, cleanup, act } from "@testing-library/react"

import PreviewOutOfDateBadge from "../PreviewOutOfDateBadge"
import type { PreviewData } from "../../panels/DataPreview"
import useGraphStore from "../../stores/useGraphStore"
import useNodeDataStore from "../../stores/useNodeDataStore"
import useNodeResultsStore from "../../stores/useNodeResultsStore"
import useUIStore from "../../stores/useUIStore"

const data: PreviewData = {
  nodeId: "A",
  nodeLabel: "A",
  status: "ok",
  row_count: 1,
  column_count: 1,
  columns: [],
  preview: [],
  error: null,
}

function storeResult(structuralVersion: number, nodeDataEpoch: number) {
  useNodeResultsStore.getState().setPreview("A", data, structuralVersion, "live", 1000, nodeDataEpoch)
}

describe("PreviewOutOfDateBadge", () => {
  beforeEach(() => {
    useNodeResultsStore.setState({ previews: {}, columnCache: {} })
    useNodeDataStore.getState().reset()
    useGraphStore.setState({ structuralVersion: 0 })
    useUIStore.setState({ calculationMode: "manual" })
  })

  afterEach(() => {
    useUIStore.setState({ calculationMode: "automatic" })
    cleanup()
  })

  it("stays hidden while the shown result is current", () => {
    storeResult(0, useNodeDataStore.getState().epoch)
    render(<PreviewOutOfDateBadge data={data} />)
    expect(screen.queryByTestId("preview-out-of-date")).toBeNull()
  })

  it("appears once the pipeline changes after the result was calculated", () => {
    storeResult(0, useNodeDataStore.getState().epoch)
    render(<PreviewOutOfDateBadge data={data} />)
    act(() => useGraphStore.setState({ structuralVersion: 1 }))
    expect(screen.getByTestId("preview-out-of-date")).toHaveTextContent("Out of date")
  })

  it("appears once the node data changes after the result was calculated", () => {
    storeResult(0, useNodeDataStore.getState().epoch)
    render(<PreviewOutOfDateBadge data={data} />)
    act(() => useNodeDataStore.getState().bumpEpoch())
    expect(screen.getByTestId("preview-out-of-date")).toBeInTheDocument()
  })

  it("is never shown in automatic calculation", () => {
    useUIStore.setState({ calculationMode: "automatic" })
    storeResult(0, useNodeDataStore.getState().epoch)
    useGraphStore.setState({ structuralVersion: 1 })
    render(<PreviewOutOfDateBadge data={data} />)
    expect(screen.queryByTestId("preview-out-of-date")).toBeNull()
  })
})
