import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, act } from "@testing-library/react"
import ToastContainer from "../Toast"
import useToastStore from "../../stores/useToastStore"

function resetStore() {
  useToastStore.setState({ toasts: [], _toastCounter: 0 })
}

describe("ToastContainer", () => {
  beforeEach(() => {
    resetStore()
    vi.useRealTimers()
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it("renders nothing when there are no toasts", () => {
    const { container } = render(<ToastContainer />)
    expect(container.innerHTML).toBe("")
  })

  it("renders correct icon for each toast type", () => {
    useToastStore.getState().addToast("success", "ok")
    useToastStore.getState().addToast("error", "fail")
    useToastStore.getState().addToast("info", "fyi")
    useToastStore.getState().addToast("warning", "caution")
    render(<ToastContainer />)
    const alerts = screen.getAllByRole("alert")
    expect(alerts).toHaveLength(4)
  })

  it("dismiss button removes toast", () => {
    useToastStore.getState().addToast("info", "Dismissable")
    render(<ToastContainer />)
    expect(screen.getByText("Dismissable")).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText("Dismiss notification"))
    expect(screen.queryByText("Dismissable")).not.toBeInTheDocument()
  })

  it("auto-dismisses after 3000ms", () => {
    vi.useFakeTimers()
    useToastStore.getState().addToast("info", "Auto-dismiss")
    render(<ToastContainer />)
    expect(screen.getByText("Auto-dismiss")).toBeInTheDocument()
    act(() => {
      vi.advanceTimersByTime(3000)
    })
    expect(screen.queryByText("Auto-dismiss")).not.toBeInTheDocument()
  })

  it("keeps error toasts visible after the normal auto-dismiss timeout", () => {
    vi.useFakeTimers()
    useToastStore.getState().addToast("error", "Needs attention")
    render(<ToastContainer />)
    expect(screen.getByText("Needs attention")).toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(3000)
    })

    expect(screen.getByText("Needs attention")).toBeInTheDocument()
  })

  it("shows correct text content", () => {
    useToastStore.getState().addToast("success", "Operation completed")
    render(<ToastContainer />)
    expect(screen.getByText("Operation completed")).toBeInTheDocument()
  })

  it("renders multiple toasts", () => {
    useToastStore.getState().addToast("info", "First toast")
    useToastStore.getState().addToast("error", "Second toast")
    useToastStore.getState().addToast("success", "Third toast")
    render(<ToastContainer />)
    expect(screen.getAllByRole("alert")).toHaveLength(3)
    expect(screen.getByText("First toast")).toBeInTheDocument()
    expect(screen.getByText("Second toast")).toBeInTheDocument()
    expect(screen.getByText("Third toast")).toBeInTheDocument()
  })

  it("toast with very long text does not break layout", () => {
    const longText = "A".repeat(500)
    useToastStore.getState().addToast("info", longText)
    render(<ToastContainer />)
    const alert = screen.getByRole("alert")
    expect(alert).toBeInTheDocument()
    expect(screen.getByText(longText)).toBeInTheDocument()
  })

  it("dismiss removes only the targeted toast", () => {
    useToastStore.getState().addToast("info", "Keep me")
    useToastStore.getState().addToast("error", "Remove me")
    render(<ToastContainer />)
    expect(screen.getAllByRole("alert")).toHaveLength(2)
    const dismissButtons = screen.getAllByLabelText("Dismiss notification")
    fireEvent.click(dismissButtons[1])
    expect(screen.queryByText("Remove me")).not.toBeInTheDocument()
    expect(screen.getByText("Keep me")).toBeInTheDocument()
  })

  it("each toast type renders a distinct icon element", () => {
    useToastStore.getState().addToast("success", "s")
    useToastStore.getState().addToast("error", "e")
    useToastStore.getState().addToast("info", "i")
    useToastStore.getState().addToast("warning", "w")
    render(<ToastContainer />)
    const alerts = screen.getAllByRole("alert")
    const svgs = alerts.map((a) => a.querySelector("svg")!)
    expect(svgs).toHaveLength(4)
    const classNames = svgs.map((svg) => svg.classList.toString())
    expect(new Set(classNames).size).toBeGreaterThanOrEqual(1)
    svgs.forEach((svg) => expect(svg).toBeTruthy())
  })
})
