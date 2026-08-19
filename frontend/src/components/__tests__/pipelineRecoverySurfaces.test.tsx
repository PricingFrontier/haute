import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import PipelineLoadFailureView from "../PipelineLoadFailureView"
import PipelineRecoveryBanner from "../PipelineRecoveryBanner"
import SourceRecoveryView from "../SourceRecoveryView"
import StalePipelineReferenceBanner from "../StalePipelineReferenceBanner"
import useDocumentStatusStore from "../../stores/useDocumentStatusStore"
import { makePipelineEditorDocument } from "../../testSupport/pipelineDocumentFixture"

const diagnostic = {
  diagnostic_id: "python-syntax-error",
  code: "python_syntax_error",
  severity: "error" as const,
  scope: "pipeline" as const,
  message: "Pipeline source contains invalid Python syntax.",
  element_id: null,
  source_file: "rating/main.py",
  source_span: { start_line: 7, start_column: 4, end_line: 7, end_column: 5 },
  remediation: "Correct the syntax at this location.",
  incident_id: null,
}

describe("pipeline recovery surfaces", () => {
  beforeEach(() => useDocumentStatusStore.getState().reset())
  afterEach(cleanup)

  it("summarises a degraded document without treating it as a load failure", () => {
    useDocumentStatusStore.getState().loadDocumentStatus(makePipelineEditorDocument({
      load_status: "degraded",
      diagnostics: [diagnostic],
      diagnostics_omitted: 2,
    }))

    render(<PipelineRecoveryBanner />)

    expect(screen.getByTestId("pipeline-recovery-banner")).toHaveTextContent(
      "Pipeline opened in recovery mode",
    )
    expect(screen.getByTestId("pipeline-recovery-banner")).toHaveTextContent("3 issues")
  })

  it("navigates from a node diagnostic without hiding document issues", () => {
    const onSelectElement = vi.fn()
    useDocumentStatusStore.getState().loadDocumentStatus(makePipelineEditorDocument({
      load_status: "degraded",
      diagnostics: [{ ...diagnostic, element_id: "broken-node", scope: "node" }],
    }))

    render(<PipelineRecoveryBanner onSelectElement={onSelectElement} />)
    fireEvent.click(screen.getByText("Review detected issues"))
    fireEvent.click(screen.getByRole("button", { name: /Pipeline source contains/ }))

    expect(onSelectElement).toHaveBeenCalledWith("broken-node")
    expect(screen.getByLabelText("Pipeline issues")).toBeInTheDocument()
  })

  it("shows current source and diagnostics for a source-only document", () => {
    useDocumentStatusStore.getState().loadDocumentStatus(makePipelineEditorDocument({
      load_status: "source_only",
      source_file: "rating/main.py",
      source_text: "def broken(:\n    pass\n",
      diagnostics: [diagnostic],
    }))

    render(<SourceRecoveryView />)

    expect(screen.getByTestId("source-recovery-view")).toHaveTextContent(
      "Pipeline source needs repair",
    )
    expect(screen.getByLabelText("Current pipeline source")).toHaveTextContent("def broken(")
    expect(screen.getByText("rating/main.py:7")).toBeInTheDocument()
  })

  it("labels a retained canvas with distinct stale and current revisions", () => {
    const sourceOnly = makePipelineEditorDocument({
      load_status: "source_only",
      source_file: "rating/main.py",
      source_revision: "current-r2",
      source_text: "def broken(:\n",
      diagnostics: [diagnostic],
    })
    useDocumentStatusStore.getState().loadLiveDocumentStatus(sourceOnly, {
      kind: "last_renderable",
      sourceRevision: "last-good-r1",
      loadStatus: "ready",
    }, false)

    render(<StalePipelineReferenceBanner />)

    expect(screen.getByTestId("stale-pipeline-reference-banner")).toHaveTextContent(
      "stale read-only reference",
    )
    expect(screen.getByTestId("stale-pipeline-reference-banner")).toHaveTextContent(
      "canvas last-good-r1; current current-r2",
    )
    fireEvent.click(screen.getByText("Review current source and diagnostics"))
    expect(screen.getByLabelText("Current pipeline source")).toHaveTextContent("def broken(")
    expect(screen.getByLabelText("Current source diagnostics")).toHaveTextContent(
      "invalid Python syntax",
    )
  })

  it("distinguishes a system load failure from authored recovery", () => {
    render(<PipelineLoadFailureView detail="HTTP 403" />)

    expect(screen.getByTestId("pipeline-load-failure")).toHaveTextContent(
      "Haute could not open this pipeline",
    )
    expect(screen.getByTestId("pipeline-load-failure")).toHaveTextContent("HTTP 403")
  })
})
