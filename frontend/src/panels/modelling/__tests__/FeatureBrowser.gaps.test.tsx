import { describe, it, expect, afterEach, vi } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import { FeatureBrowser, type FeatureItem } from "../FeatureBrowser"

const FEATURES: FeatureItem[] = [
  { feature: "age", importance: 10 },
  { feature: "income", importance: -5 },
  { feature: "region", importance: 0 },
]

describe("FeatureBrowser", () => {
  afterEach(cleanup)

  it("renders all features unfiltered", () => {
    render(<FeatureBrowser features={FEATURES} selected={null} onSelect={() => {}} />)
    expect(screen.getByText("age")).toBeInTheDocument()
    expect(screen.getByText("income")).toBeInTheDocument()
    expect(screen.getByText("region")).toBeInTheDocument()
  })

  it("filters features by case-insensitive search", () => {
    render(<FeatureBrowser features={FEATURES} selected={null} onSelect={() => {}} />)
    fireEvent.change(screen.getByPlaceholderText("Search features..."), {
      target: { value: "INCO" },
    })
    expect(screen.getByText("income")).toBeInTheDocument()
    expect(screen.queryByText("age")).not.toBeInTheDocument()
    expect(screen.queryByText("region")).not.toBeInTheDocument()
  })

  it("shows empty state when search matches nothing", () => {
    render(<FeatureBrowser features={FEATURES} selected={null} onSelect={() => {}} />)
    fireEvent.change(screen.getByPlaceholderText("Search features..."), {
      target: { value: "zzz" },
    })
    expect(screen.getByText("No features found")).toBeInTheDocument()
  })

  it("shows empty state when given no features", () => {
    render(<FeatureBrowser features={[]} selected={null} onSelect={() => {}} />)
    expect(screen.getByText("No features found")).toBeInTheDocument()
  })

  it("calls onSelect with the clicked feature", () => {
    const onSelect = vi.fn()
    render(<FeatureBrowser features={FEATURES} selected={null} onSelect={onSelect} />)
    fireEvent.click(screen.getByText("income"))
    expect(onSelect).toHaveBeenCalledWith("income")
  })

  it("marks the selected feature with accent color", () => {
    render(<FeatureBrowser features={FEATURES} selected="age" onSelect={() => {}} />)
    const label = screen.getByText("age")
    expect(label.style.color).toContain("accent")
    // Non-selected uses secondary text color
    expect(screen.getByText("income").style.color).toContain("secondary")
  })

  it("normalises importance bars against the max absolute importance", () => {
    const { container } = render(
      <FeatureBrowser features={FEATURES} selected={null} onSelect={() => {}} />,
    )
    const bars = container.querySelectorAll(".h-full.rounded-full")
    // age: |10|/10 = 100%, income: |-5|/10 = 50%, region: 0/10 = 0%
    expect((bars[0] as HTMLElement).style.width).toBe("100%")
    expect((bars[1] as HTMLElement).style.width).toBe("50%")
    expect((bars[2] as HTMLElement).style.width).toBe("0%")
  })

  it("yields zero-width bars when all importances are zero", () => {
    const { container } = render(
      <FeatureBrowser
        features={[{ feature: "a", importance: 0 }]}
        selected={null}
        onSelect={() => {}}
      />,
    )
    const bar = container.querySelector(".h-full.rounded-full") as HTMLElement
    expect(bar.style.width).toBe("0%")
  })

  it("respects a custom width prop", () => {
    const { container } = render(
      <FeatureBrowser features={FEATURES} selected={null} onSelect={() => {}} width={240} />,
    )
    const root = container.firstChild as HTMLElement
    expect(root.style.width).toBe("240px")
  })
})
