/**
 * Tests for the per-session-global non-canonical path warning (PATH_GRAMMAR.md
 * §4): the trigger predicate, the modal render, and the per-session-GLOBAL
 * sessionStorage dismissal (one dismissal silences BOTH editors for the session).
 */
import { describe, it, expect, beforeEach } from "vitest"
import { render, screen, fireEvent, cleanup, act } from "@testing-library/react"
import { afterEach } from "vitest"
import { PathWarningProvider } from "../../panels/editors/PathWarningModal"
import {
  usePathWarning,
  pathWarningTarget,
  isPathWarningDismissed,
  dismissPathWarning,
} from "../../panels/editors/pathCanonicalWarning"

afterEach(cleanup)
beforeEach(() => {
  sessionStorage.clear()
})

// A minimal commit boundary: a button that "commits" a path through the hook,
// exactly as the editors' CommittedTextInput does after a valid commit.
function CommitButton({ path, label }: { path: string; label: string }) {
  const notify = usePathWarning()
  return (
    <button data-testid={`commit-${label}`} onClick={() => notify(path)}>
      commit {label}
    </button>
  )
}

describe("pathWarningTarget — the trigger predicate (§4 / §5)", () => {
  it("returns the canonical form for a valid non-canonical path with a safe rewrite", () => {
    expect(pathWarningTarget("$[:]['a']")).toBe("$[:].a")
    expect(pathWarningTarget("$.a")).toBe("$[:].a")
  })

  it("returns null for an already-canonical path (no warning)", () => {
    expect(pathWarningTarget("$[:].a.b")).toBeNull()
  })

  it("returns null for the §5 non-identifier case (no safe rewrite — no warning)", () => {
    expect(pathWarningTarget("$[:]['first.last']")).toBeNull()
  })

  it("returns null for an invalid path", () => {
    expect(pathWarningTarget("$[*]")).toBeNull()
  })

  it("returns null once the session-global dismissal is set", () => {
    expect(pathWarningTarget("$[:]['a']")).toBe("$[:].a")
    dismissPathWarning()
    expect(pathWarningTarget("$[:]['a']")).toBeNull()
  })
})

describe("NonCanonicalPathModal via the provider", () => {
  it("pops the modal on a non-canonical commit, showing both spellings", () => {
    render(
      <PathWarningProvider>
        <CommitButton path="$[:]['a']" label="x" />
      </PathWarningProvider>,
    )
    expect(screen.queryByTestId("path-canonical-warning")).toBeNull()
    fireEvent.click(screen.getByTestId("commit-x"))
    expect(screen.getByTestId("path-canonical-warning")).toBeTruthy()
    expect(screen.getByTestId("path-canonical-warning-user").textContent).toBe("$[:]['a']")
    expect(screen.getByTestId("path-canonical-warning-canonical").textContent).toBe("$[:].a")
  })

  it("does NOT pop the modal on a canonical commit", () => {
    render(
      <PathWarningProvider>
        <CommitButton path="$[:].a" label="x" />
      </PathWarningProvider>,
    )
    fireEvent.click(screen.getByTestId("commit-x"))
    expect(screen.queryByTestId("path-canonical-warning")).toBeNull()
  })

  it("does NOT pop the modal for the §5 non-identifier case", () => {
    render(
      <PathWarningProvider>
        <CommitButton path="$[:]['first.last']" label="x" />
      </PathWarningProvider>,
    )
    fireEvent.click(screen.getByTestId("commit-x"))
    expect(screen.queryByTestId("path-canonical-warning")).toBeNull()
  })

  it("'Got it' closes the modal without dismissing the session warning", () => {
    render(
      <PathWarningProvider>
        <CommitButton path="$[:]['a']" label="x" />
      </PathWarningProvider>,
    )
    fireEvent.click(screen.getByTestId("commit-x"))
    fireEvent.click(screen.getByTestId("path-canonical-warning-ok"))
    expect(screen.queryByTestId("path-canonical-warning")).toBeNull()
    expect(isPathWarningDismissed()).toBe(false)
    // A subsequent non-canonical commit pops it again (not dismissed).
    fireEvent.click(screen.getByTestId("commit-x"))
    expect(screen.getByTestId("path-canonical-warning")).toBeTruthy()
  })

  it("checking 'Don't show again' on close persists a session-global dismissal", () => {
    render(
      <PathWarningProvider>
        <CommitButton path="$[:]['a']" label="x" />
      </PathWarningProvider>,
    )
    fireEvent.click(screen.getByTestId("commit-x"))
    fireEvent.click(screen.getByTestId("path-canonical-warning-dismiss"))
    fireEvent.click(screen.getByTestId("path-canonical-warning-ok"))
    expect(isPathWarningDismissed()).toBe(true)
    // It no longer pops in THIS provider…
    fireEvent.click(screen.getByTestId("commit-x"))
    expect(screen.queryByTestId("path-canonical-warning")).toBeNull()
  })

  it("the dismissal is per-session-GLOBAL — silences a SECOND provider too", () => {
    // First provider: dismiss.
    const first = render(
      <PathWarningProvider>
        <CommitButton path="$[:]['a']" label="x" />
      </PathWarningProvider>,
    )
    fireEvent.click(screen.getByTestId("commit-x"))
    fireEvent.click(screen.getByTestId("path-canonical-warning-dismiss"))
    fireEvent.click(screen.getByTestId("path-canonical-warning-ok"))
    first.unmount()

    // A fresh, independent provider (the OTHER editor) stays silent.
    render(
      <PathWarningProvider>
        <CommitButton path="$[:]['a']" label="y" />
      </PathWarningProvider>,
    )
    fireEvent.click(screen.getByTestId("commit-y"))
    expect(screen.queryByTestId("path-canonical-warning")).toBeNull()
  })

  it("a fresh session (cleared storage) re-surfaces the warning", () => {
    dismissPathWarning()
    expect(isPathWarningDismissed()).toBe(true)
    act(() => {
      sessionStorage.clear()
    })
    expect(isPathWarningDismissed()).toBe(false)
  })
})
