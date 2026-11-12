/**
 * haute-ui's button outside the toolbar (specs/frontend-shared): the toolbar button's
 * surface in the shape its use needs, as a button or as a link, laid out by its caller.
 */
import { createRef } from "react"
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { Button, ButtonLink } from ".."
import { compileIndexCss } from "../../test-utils/compiledIndexCss"

describe("haute-ui Button", () => {
  afterEach(cleanup)

  it("is a button that does not submit, on the raised surface at the size of its label", () => {
    const onClick = vi.fn()
    render(<Button onClick={onClick}>Retry</Button>)
    const button = screen.getByRole("button", { name: "Retry" })

    expect(button).toHaveAttribute("type", "button")
    expect(button.className).toBe("toolbar-btn btn")
    fireEvent.click(button)
    expect(onClick).toHaveBeenCalledOnce()
  })

  it("submits its form when told to, and does nothing while disabled", () => {
    const onSubmit = vi.fn((event: { preventDefault: () => void }) => event.preventDefault())
    const onClick = vi.fn()
    const { rerender } = render(
      <form onSubmit={onSubmit}>
        <Button type="submit" onClick={onClick}>Save</Button>
      </form>,
    )
    fireEvent.click(screen.getByRole("button", { name: "Save" }))
    expect(onSubmit).toHaveBeenCalledOnce()

    rerender(
      <form onSubmit={onSubmit}>
        <Button type="submit" onClick={onClick} disabled>Save</Button>
      </form>,
    )
    fireEvent.click(screen.getByRole("button", { name: "Save" }))
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled()
    expect(onClick).toHaveBeenCalledOnce()
    expect(onSubmit).toHaveBeenCalledOnce()
  })

  it("draws an icon alone in square padding, named by its label", () => {
    render(
      <Button iconOnly aria-label="Refresh the list">
        <svg />
      </Button>,
    )
    expect(screen.getByRole("button", { name: "Refresh the list" }).className).toBe("toolbar-btn btn-icon")
  })

  it("fills a page's main action in the accent", () => {
    render(<Button variant="fill">New quote</Button>)
    expect(screen.getByRole("button", { name: "New quote" }).className).toBe("toolbar-fill toolbar-fill-save btn-fill")
  })

  it("takes its caller's layout classes after its own, and passes other props and its ref through", () => {
    const ref = createRef<HTMLButtonElement>()
    render(
      <Button ref={ref} className="ml-auto self-start" title="Save the note" aria-pressed data-testid="save-note">
        Save note
      </Button>,
    )
    const button = screen.getByTestId("save-note")

    expect(button.className).toBe("toolbar-btn btn ml-auto self-start")
    expect(button).toHaveAttribute("title", "Save the note")
    expect(button).toHaveAttribute("aria-pressed", "true")
    expect(ref.current).toBe(button)
  })

  it("draws a link the same way, with somewhere to go", () => {
    const onClick = vi.fn((event: { preventDefault: () => void }) => event.preventDefault())
    render(
      <>
        <ButtonLink href="/quotes" onClick={onClick}>Back to Quotes</ButtonLink>
        <ButtonLink href="/quotes/new" variant="fill" className="ml-2">New quote</ButtonLink>
      </>,
    )
    const back = screen.getByRole("link", { name: "Back to Quotes" })

    expect(back).toHaveAttribute("href", "/quotes")
    expect(back.className).toBe("toolbar-btn btn")
    expect(back).not.toHaveAttribute("type")
    fireEvent.click(back)
    expect(onClick).toHaveBeenCalledOnce()
    expect(screen.getByRole("link", { name: "New quote" }).className).toBe("toolbar-fill toolbar-fill-save btn-fill ml-2")
  })
})

// Checked by tsc, which reads the tests too: an unused @ts-expect-error fails the type check.
describe("haute-ui Button's types", () => {
  afterEach(cleanup)

  it("take each shape, and an icon alone chosen by a condition once it is named", () => {
    const compact = Math.random() < 2
    render(
      <>
        <Button>Retry</Button>
        <Button iconOnly aria-label="Refresh" />
        <Button iconOnly={compact} aria-label="Refresh">Refresh</Button>
        <Button variant="fill">New quote</Button>
        <ButtonLink href="/">Quotes</ButtonLink>
        <ButtonLink href="/" iconOnly aria-label="Quotes" />
      </>,
    )
    expect(screen.getAllByRole("button")).toHaveLength(4)
    expect(screen.getAllByRole("link")).toHaveLength(2)
  })

  it("refuse an icon alone without a name, a fill that is an icon alone, and a link to nowhere", () => {
    const compact = Math.random() < 2
    render(
      <>
        {/* @ts-expect-error an icon alone needs a name */}
        <Button iconOnly />
        {/* @ts-expect-error so does a button that may be one */}
        <Button iconOnly={compact}>Refresh</Button>
        {/* @ts-expect-error the fill is never an icon alone */}
        <Button variant="fill" iconOnly aria-label="Save" />
        {/* @ts-expect-error a link needs somewhere to go */}
        <ButtonLink>Quotes</ButtonLink>
      </>,
    )
    // What the types refuse still draws: the refusal is the compiler's.
    expect(screen.getAllByRole("button")).toHaveLength(3)
  })
})

describe("haute-ui button.css", () => {
  let css = ""
  beforeAll(async () => {
    css = (await compileIndexCss()).replace(/\/\*[\s\S]*?\*\//g, "")
  })

  // Both are in the components layer at equal specificity, so the later rule wins:
  // were button.css imported first, a fill button outside the toolbar would stretch.
  it("comes after toolbar.css, so the fill's own width outranks the toolbar's stretch", () => {
    const stretch = css.search(/\.toolbar-fill\s*\{[^}]*flex:\s*1 1 0%/)
    const ownWidth = css.search(/\.btn-fill\s*\{[^}]*flex:\s*none/)
    expect(stretch).toBeGreaterThanOrEqual(0)
    expect(ownWidth).toBeGreaterThan(stretch)
  })
})
