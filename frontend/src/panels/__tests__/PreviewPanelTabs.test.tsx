import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { useState } from "react"
import { afterEach, describe, expect, it, vi } from "vitest"

import PreviewPanelTabs from "../PreviewPanelTabs"

type Tab = "first" | "disabled" | "second" | "third"

const tabs = [
  { key: "first", label: "First" },
  { key: "disabled", label: "Disabled", disabled: true },
  { key: "second", label: "Second" },
  { key: "third", label: "Third" },
] as const

function Tabs({ initialTab = "first", onChange = vi.fn() }: { initialTab?: Tab; onChange?: (tab: Tab) => void }) {
  const [activeTab, setActiveTab] = useState<Tab>(initialTab)

  return (
    <PreviewPanelTabs
      tabs={tabs}
      activeTab={activeTab}
      ariaLabel="Preview tabs"
      onChange={(tab) => {
        setActiveTab(tab)
        onChange(tab)
      }}
    />
  )
}

describe("PreviewPanelTabs", () => {
  afterEach(cleanup)

  it("uses a roving tabindex, including when the active tab is disabled", () => {
    const rendered = render(<Tabs initialTab="second" />)

    expect(screen.getByRole("tab", { name: "Second" })).toHaveAttribute("tabindex", "0")
    expect(screen.getByRole("tab", { name: "First" })).toHaveAttribute("tabindex", "-1")
    expect(screen.getByRole("tab", { name: "Disabled" })).toBeDisabled()
    expect(screen.getByRole("tab", { name: "Disabled" })).toHaveAttribute("tabindex", "-1")

    rendered.unmount()
    render(<Tabs initialTab="disabled" />)
    expect(screen.getByRole("tab", { name: "First" })).toHaveAttribute("tabindex", "0")
  })

  it("moves focus and activation with arrows, wrapping and skipping disabled tabs", () => {
    const onChange = vi.fn()
    render(<Tabs onChange={onChange} />)

    const first = screen.getByRole("tab", { name: "First" })
    const second = screen.getByRole("tab", { name: "Second" })
    const third = screen.getByRole("tab", { name: "Third" })
    first.focus()

    fireEvent.keyDown(first, { key: "ArrowRight" })
    expect(second).toHaveFocus()
    expect(second).toHaveAttribute("aria-selected", "true")

    fireEvent.keyDown(second, { key: "ArrowLeft" })
    expect(first).toHaveFocus()
    fireEvent.keyDown(first, { key: "ArrowLeft" })
    expect(third).toHaveFocus()
    fireEvent.keyDown(third, { key: "ArrowRight" })
    expect(first).toHaveFocus()
    expect(onChange).toHaveBeenCalledWith("second")
    expect(onChange).toHaveBeenCalledWith("third")
  })

  it("moves focus and activation to the first and last enabled tabs with Home and End", () => {
    render(<Tabs initialTab="second" />)

    const first = screen.getByRole("tab", { name: "First" })
    const second = screen.getByRole("tab", { name: "Second" })
    const third = screen.getByRole("tab", { name: "Third" })
    second.focus()

    fireEvent.keyDown(second, { key: "Home" })
    expect(first).toHaveFocus()
    expect(first).toHaveAttribute("aria-selected", "true")

    fireEvent.keyDown(first, { key: "End" })
    expect(third).toHaveFocus()
    expect(third).toHaveAttribute("aria-selected", "true")
  })
})
