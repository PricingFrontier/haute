/**
 * Preview held steady under the underwriter (specs/workbench): what is drawn held from a
 * primary press on the sheet's viewport until a turn after the press ends, so the release's
 * click lands on what was pressed, and the viewport scrolled by as much as the section
 * holding the focus, or else the first reaching below its top, moved as sections open and
 * close, between two draws of one sheet at one zoom.
 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { useState } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { SHEET_PADDING, stackSections, type Stacked } from "../../utils/sheetGeometry"
import { useHeldWhilePressed, useKeptPlace } from "../steadyPreview"

/** What the hook gives for `value`, on a button in a viewport with two fields, a button outside it; a click reports what it showed. */
function Held({ value, active = true, onClick }: { value: string; active?: boolean; onClick?: (shown: string) => void }) {
  const [viewport, setViewport] = useState<HTMLElement | null>(null)
  const shown = useHeldWhilePressed(value, active, viewport)
  return (
    <>
      <div ref={setViewport} data-testid="viewport">
        <button type="button" data-testid="shown" onClick={() => onClick?.(shown)}>
          {shown}
        </button>
        <input aria-label="A field" />
        <input aria-label="Another field" />
        <select aria-label="A select">
          <option value="a">A</option>
        </select>
      </div>
      <button type="button">Outside</button>
    </>
  )
}

type Band = Stacked<{ id: string; height: number }>

/** Sections of these heights, by id, stacked one under the next as the sheet draws them. */
const stack = (...sections: [id: string, height: number][]): Band[] => stackSections(sections.map(([id, height]) => ({ id, height })))

/** A viewport drawing `sections` of `sheet` at `zoom`, a field in each, its place kept by the hook. */
function Kept({ sections, zoom = 1, sheet = "p1" }: { sections: Band[]; zoom?: number; sheet?: string | null }) {
  const [viewport, setViewport] = useState<HTMLElement | null>(null)
  useKeptPlace(viewport, sections, zoom, sheet)
  return (
    <div ref={setViewport} data-testid="viewport">
      {sections.map(({ section }) => (
        <div key={section.id} data-section-id={section.id}>
          <input aria-label={`A field in ${section.id}`} />
        </div>
      ))}
    </div>
  )
}

/**
 * The drawn viewport, its `scrollTo` (which jsdom lacks) moving its scroll position as a
 * browser's does: `scroll` the underwriter scrolling, which its scroll event reports, and
 * `scrolls` the positions the hook scrolled it to, in turn.
 */
function scrollable() {
  const viewport = screen.getByTestId("viewport")
  const scrollTo = vi.fn((options: ScrollToOptions) => {
    viewport.scrollTop = options.top ?? viewport.scrollTop
  })
  Object.defineProperty(viewport, "scrollTo", { configurable: true, value: scrollTo })
  const scroll = (top: number) => {
    viewport.scrollTop = top
    fireEvent.scroll(viewport)
  }
  const scrolls = () => scrollTo.mock.calls.map(([options]) => options.top)
  return { viewport, scroll, scrolls }
}

describe("useHeldWhilePressed", () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  /** What the button in the viewport shows. */
  const shown = () => screen.getByTestId("shown").textContent
  /** A turn passes. */
  const turn = () => {
    act(() => {
      vi.advanceTimersByTime(0)
    })
  }

  it("holds the value as it was when pressed on the viewport until a turn after the release, the release's click landing first", () => {
    const clicked = vi.fn()
    const { rerender } = render(<Held value="before" onClick={clicked} />)

    fireEvent.pointerDown(screen.getByTestId("shown"))
    rerender(<Held value="after" onClick={clicked} />)
    expect(shown()).toBe("before")

    fireEvent.pointerUp(screen.getByTestId("shown"))
    fireEvent.click(screen.getByTestId("shown"))
    expect(clicked).toHaveBeenCalledWith("before")
    expect(shown()).toBe("before")

    turn()
    expect(shown()).toBe("after")
  })

  it("lets go a turn after the press is cancelled, a press on the viewport itself held too", () => {
    const { rerender } = render(<Held value="before" />)

    fireEvent.pointerDown(screen.getByTestId("viewport"))
    rerender(<Held value="after" />)
    fireEvent.pointerCancel(window)
    expect(shown()).toBe("before")

    turn()
    expect(shown()).toBe("after")
  })

  it("lets go a turn after a context menu opens on the press, which takes its release", () => {
    const { rerender } = render(<Held value="before" />)

    fireEvent.pointerDown(screen.getByTestId("shown"))
    rerender(<Held value="after" />)
    expect(shown()).toBe("before")

    // No release follows: the menu has it.
    fireEvent.contextMenu(screen.getByTestId("shown"))
    expect(shown()).toBe("before")

    turn()
    expect(shown()).toBe("after")
  })

  it("lets go a turn after the window loses the focus, its release going elsewhere", () => {
    const { rerender } = render(<Held value="before" />)

    fireEvent.pointerDown(screen.getByTestId("shown"))
    rerender(<Held value="after" />)
    fireEvent.blur(window)
    expect(shown()).toBe("before")

    turn()
    expect(shown()).toBe("after")
  })

  it("holds through a field in the viewport blurring as the press moves the focus to another, until a turn after the release", () => {
    const { rerender } = render(<Held value="before" />)
    const typed = screen.getByLabelText("A field")
    const pressed = screen.getByLabelText("Another field")
    typed.focus()

    // The press leaves the field typed in, which commits as it blurs: what is drawn holds.
    fireEvent.pointerDown(pressed)
    pressed.focus()
    rerender(<Held value="after" />)
    expect(pressed).toHaveFocus()
    turn()
    expect(shown()).toBe("before")

    fireEvent.pointerUp(pressed)
    turn()
    expect(shown()).toBe("after")
  })

  it("holds a press made before the last one's turn is out until a turn after its own release", () => {
    const { rerender } = render(<Held value="first" />)
    fireEvent.pointerDown(screen.getByTestId("shown"))
    rerender(<Held value="second" />)
    fireEvent.pointerUp(window)

    // Pressed again before the release's turn: the value as it is now is held.
    fireEvent.pointerDown(screen.getByTestId("shown"))
    rerender(<Held value="third" />)
    turn()
    expect(shown()).toBe("second")

    fireEvent.pointerUp(window)
    turn()
    expect(shown()).toBe("third")
  })

  it("holds nothing for a press outside the viewport", () => {
    const { rerender } = render(<Held value="before" />)

    fireEvent.pointerDown(screen.getByRole("button", { name: "Outside" }))
    rerender(<Held value="after" />)
    expect(shown()).toBe("after")
  })

  it("holds nothing for a press on a select, whose menu takes the release and opens where it is", () => {
    const { rerender } = render(<Held value="before" />)

    // In Chromium the release goes to the select's menu, never reaching the window.
    fireEvent.pointerDown(screen.getByRole("combobox", { name: "A select" }))
    rerender(<Held value="after" />)
    expect(shown()).toBe("after")
  })

  it("holds nothing for a press of a button other than the primary", () => {
    const { rerender } = render(<Held value="before" />)

    // The secondary button, as opens a context menu.
    fireEvent.pointerDown(screen.getByTestId("shown"), { button: 2 })
    rerender(<Held value="after" />)
    expect(shown()).toBe("after")
  })

  it("holds nothing while not active, and lets go of a press at once as it stops being active", () => {
    const { rerender } = render(<Held value="before" active={false} />)
    fireEvent.pointerDown(screen.getByTestId("shown"))
    rerender(<Held value="after" active={false} />)
    expect(shown()).toBe("after")
    fireEvent.pointerUp(window)

    rerender(<Held value="after" />)
    fireEvent.pointerDown(screen.getByTestId("shown"))
    rerender(<Held value="later" />)
    expect(shown()).toBe("after")

    // Back to building with the pointer still pressed: the value as it is.
    rerender(<Held value="later" active={false} />)
    expect(shown()).toBe("later")
  })
})

describe("useKeptPlace", () => {
  afterEach(cleanup)

  it("keeps the first section reaching below the viewport's top in place as a section opens and closes above it", () => {
    const { rerender } = render(<Kept sections={stack(["a", 100], ["b", 200], ["c", 200])} />)
    const { scroll, scrolls } = scrollable()
    // a ends above the viewport's top, the sheet's padding over it; b reaches below.
    scroll(SHEET_PADDING + 150)

    rerender(<Kept sections={stack(["a", 100], ["x", 160], ["b", 200], ["c", 200])} />)
    expect(scrolls()).toEqual([SHEET_PADDING + 150 + 160])

    // From where it scrolled, which the browser has yet to report.
    rerender(<Kept sections={stack(["a", 100], ["b", 200], ["c", 200])} />)
    expect(scrolls()).toEqual([SHEET_PADDING + 150 + 160, SHEET_PADDING + 150])
  })

  it("measures the sections and scrolls by the move at the zoom", () => {
    const { rerender } = render(<Kept sections={stack(["a", 100], ["b", 100], ["c", 200])} zoom={0.5} />)
    const { scroll, scrolls } = scrollable()
    // At half size, under the padding, a ends 82 pixels down the viewport's content and b 132:
    // b is the first reaching below 100.
    scroll(100)

    rerender(<Kept sections={stack(["a", 100], ["x", 120], ["b", 100], ["c", 200])} zoom={0.5} />)
    expect(scrolls()).toEqual([100 + 120 * 0.5])
  })

  it("keeps the section holding the focus in place before the first in view", () => {
    const { rerender } = render(<Kept sections={stack(["a", 100], ["b", 100], ["c", 200])} />)
    const { scroll, scrolls } = scrollable()
    scroll(SHEET_PADDING + 150)
    const field = screen.getByLabelText("A field in c")
    field.focus()

    // b, the first in view, stays where it is; c, holding the focus, moves down.
    rerender(<Kept sections={stack(["a", 100], ["b", 100], ["x", 120], ["c", 200])} />)
    expect(field).toHaveFocus()
    expect(scrolls()).toEqual([SHEET_PADDING + 150 + 120])
  })

  it("scrolls from the position its scroll listener recorded before the draw, not the one the browser clamped it to", () => {
    const { rerender } = render(<Kept sections={stack(["a", 300], ["b", 200], ["c", 200])} />)
    const { viewport, scroll, scrolls } = scrollable()
    scroll(600)
    // The sheet shortening as a closes, the browser clamps the scroll, reporting it only later.
    viewport.scrollTop = 400

    rerender(<Kept sections={stack(["b", 200], ["c", 200])} />)
    expect(scrolls()).toEqual([600 - 300])
  })

  it("scrolls nothing while the section in view stays where it was, whatever moves under it", () => {
    const { rerender } = render(<Kept sections={stack(["a", 100], ["b", 100], ["c", 200])} />)
    const { scroll, scrolls } = scrollable()
    // a's last few pixels, the sheet's padding over it, are in view still.
    scroll(SHEET_PADDING + 90)

    rerender(<Kept sections={stack(["a", 100], ["x", 120], ["b", 100], ["c", 200])} />)
    rerender(<Kept sections={stack(["a", 100], ["x", 120], ["b", 100], ["c", 300])} />)
    expect(scrolls()).toEqual([])
  })

  it("scrolls nothing for a draw of another sheet or at another zoom, then keeps the place again", () => {
    const { rerender } = render(<Kept sections={stack(["a", 100], ["b", 100], ["c", 200])} />)
    const { scroll, scrolls } = scrollable()
    scroll(SHEET_PADDING + 150)

    // Each draw opens a section at the top, moving every section under it down by its height.
    rerender(<Kept sections={stack(["x", 80], ["a", 100], ["b", 100], ["c", 200])} sheet="p2" />)
    rerender(<Kept sections={stack(["y", 40], ["x", 80], ["a", 100], ["b", 100], ["c", 200])} sheet="p2" zoom={0.5} />)
    expect(scrolls()).toEqual([])

    rerender(<Kept sections={stack(["z", 60], ["y", 40], ["x", 80], ["a", 100], ["b", 100], ["c", 200])} sheet="p2" zoom={0.5} />)
    expect(scrolls()).toEqual([SHEET_PADDING + 150 + 60 * 0.5])
  })

  it("keeps nothing while building, nor on the first draw in Preview", () => {
    const { rerender } = render(<Kept sections={stack(["a", 100], ["b", 100])} sheet={null} />)
    const { scroll, scrolls } = scrollable()
    scroll(SHEET_PADDING + 150)

    rerender(<Kept sections={stack(["x", 80], ["a", 100], ["b", 100])} sheet={null} />)
    rerender(<Kept sections={stack(["y", 40], ["x", 80], ["a", 100], ["b", 100])} />)
    expect(scrolls()).toEqual([])

    rerender(<Kept sections={stack(["z", 60], ["y", 40], ["x", 80], ["a", 100], ["b", 100])} />)
    expect(scrolls()).toEqual([SHEET_PADDING + 150 + 60])
  })

  it("scrolls nothing as the section in view closes, though one above it moves up", () => {
    const { rerender } = render(<Kept sections={stack(["a", 100], ["b", 100], ["c", 300])} />)
    const { scroll, scrolls } = scrollable()
    // c alone reaches below the viewport's top.
    scroll(400)

    rerender(<Kept sections={stack(["b", 100])} />)
    expect(scrolls()).toEqual([])
  })

  it("scrolls nothing as the first section in view closes, though the one under it moves up into its place", () => {
    const { rerender } = render(<Kept sections={stack(["a", 100], ["b", 100], ["c", 300])} />)
    const { scroll, scrolls } = scrollable()
    // b is the first reaching below the viewport's top, the place kept; once it is drawn no
    // more there is no place to keep, and c moving up into it scrolls nothing.
    scroll(SHEET_PADDING + 150)

    rerender(<Kept sections={stack(["a", 100], ["c", 300])} />)
    expect(scrolls()).toEqual([])
  })
})
