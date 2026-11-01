/**
 * The utility panel with the real code editor, which reports an edit only once typing
 * pauses (150 ms): text typed while a save waits must not be lost to that pause.
 */
import { EditorView } from "@codemirror/view"
import { act, cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import useGitStore from "../../stores/useGitStore"
import useToastStore from "../../stores/useToastStore"
import useUIStore from "../../stores/useUIStore"
import UtilityPanel from "../UtilityPanel"

const { mockListFiles, mockReadFile, mockUpdateFile } = vi.hoisted(() => ({
  mockListFiles: vi.fn(),
  mockReadFile: vi.fn(),
  mockUpdateFile: vi.fn(),
}))

vi.mock("../../api/client", () => ({
  ApiError: class extends Error {},
  listUtilityFiles: (...args: unknown[]) => mockListFiles(...args),
  readUtilityFile: (...args: unknown[]) => mockReadFile(...args),
  createUtilityFile: vi.fn(),
  updateUtilityFile: (...args: unknown[]) => mockUpdateFile(...args),
  deleteUtilityFile: vi.fn(),
}))

const saved = (content: string, revision: string) => ({
  status: "ok", name: "features.py", module: "features", import_line: "", content, revision,
  warnings: [], git_sha: null, identity_required: false, uncaptured: [],
})

function RightPanel() {
  const utilityOpen = useUIStore((s) => s.utilityOpen)
  if (!utilityOpen) return <div data-testid="no-panel" />
  return (
    <UtilityPanel
      onClose={() => useUIStore.getState().setUtilityOpen(false)}
      onImportAdded={vi.fn()}
      preamble=""
      onPreambleChange={vi.fn()}
    />
  )
}

async function liveEditor(): Promise<EditorView> {
  let view: EditorView | null = null
  await waitFor(() => {
    const dom = document.querySelector<HTMLElement>(".cm-editor")
    view = dom ? EditorView.findFromDOM(dom) : null
    expect(view).not.toBeNull()
  })
  return view!
}

/** Type *text* at the end, as a keystroke would: the editor reports it 150 ms later. */
function typeAtEnd(view: EditorView, text: string) {
  act(() => view.dispatch({ changes: { from: view.state.doc.length, insert: text } }))
}

describe("UtilityPanel with the live editor", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useToastStore.setState({ toasts: [] })
    useGitStore.setState({ uncaptured: [] })
    useUIStore.setState({
      utilityOpen: true, gitOpen: false, utilityUnsaved: false, utilityLeave: null,
      utilityLeaveActions: [],
    })
    mockListFiles.mockResolvedValue({ files: [{ name: "features.py", module: "features" }] })
    mockReadFile.mockResolvedValue({ name: "features.py", module: "features", content: "x = 1\n", revision: "1-1" })
  })

  afterEach(cleanup)

  it("saves text typed while a close waits on a save before closing", async () => {
    let finish: (value: unknown) => void = () => {}
    mockUpdateFile
      .mockReturnValueOnce(new Promise((resolve) => { finish = resolve }))
      .mockResolvedValueOnce(saved("x = 1\ny = 2\nz = 3\n", "3-3"))
    render(<RightPanel />)
    const view = await liveEditor()

    typeAtEnd(view, "y = 2\n")
    act(() => useUIStore.getState().setUtilityOpen(false))
    await waitFor(() => expect(mockUpdateFile).toHaveBeenCalledWith("features", "x = 1\ny = 2\n", "1-1"))
    typeAtEnd(view, "z = 3\n")
    await act(async () => { finish(saved("x = 1\ny = 2\n", "2-2")) })

    await waitFor(() => expect(screen.getByTestId("no-panel")).toBeInTheDocument())
    expect(mockUpdateFile).toHaveBeenLastCalledWith("features", "x = 1\ny = 2\nz = 3\n", "2-2")
  })

  it("stays open when the file changed during that save", async () => {
    let finish: (value: unknown) => void = () => {}
    mockUpdateFile.mockReturnValueOnce(new Promise((resolve) => { finish = resolve }))
    render(<RightPanel />)
    const view = await liveEditor()

    typeAtEnd(view, "y = 2\n")
    act(() => useUIStore.getState().setUtilityOpen(false))
    await waitFor(() => expect(mockUpdateFile).toHaveBeenCalledOnce())
    typeAtEnd(view, "z = 3\n")
    await act(async () => { finish(saved("x = 1\ny = 2\n# formatted\n", "2-2")) })

    expect(await screen.findByText(/changed while it was saved/)).toBeInTheDocument()
    expect(screen.getByTestId("utility-panel")).toBeInTheDocument()
    expect(view.state.doc.toString()).toBe("x = 1\ny = 2\nz = 3\n")
    expect(mockUpdateFile).toHaveBeenCalledOnce()
  })

  it("saves on Ctrl+S what was typed just now, and shows a hook's formatting in place", async () => {
    mockUpdateFile.mockResolvedValueOnce(saved("x = 1\ny = 2\n# formatted\n", "2-2"))
    render(<RightPanel />)
    const view = await liveEditor()

    typeAtEnd(view, "y = 2\n")
    act(() => {
      view.contentDOM.dispatchEvent(new KeyboardEvent("keydown", { key: "s", ctrlKey: true, bubbles: true }))
    })

    await waitFor(() => expect(view.state.doc.toString()).toBe("x = 1\ny = 2\n# formatted\n"))
    expect(mockUpdateFile).toHaveBeenCalledExactlyOnceWith("features", "x = 1\ny = 2\n", "1-1")
    expect(useUIStore.getState().utilityUnsaved).toBe(false)
  })
})
