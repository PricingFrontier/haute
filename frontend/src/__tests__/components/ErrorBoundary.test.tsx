/**
 * Integration tests for ErrorBoundary component.
 *
 * Tests: error catching with fallback rendering, "Try again" button reset,
 * custom fallback prop, named boundary logging, and recovery after reset.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import { ErrorBoundary } from "../../components/ErrorBoundary"

// ── Helpers ──────────────────────────────────────────────────────

/** A component that throws on render when `shouldThrow` is true. */
function ThrowingChild({ shouldThrow = true }: { shouldThrow?: boolean }) {
  if (shouldThrow) {
    throw new Error("Test explosion")
  }
  return <div data-testid="child-ok">All good</div>
}

/**
 * A component that reads from a shared flag to decide whether to throw.
 * The flag is toggled externally (not via React state) so that after
 * ErrorBoundary resets, the re-render succeeds.
 */
let shouldThrowFlag = true
function ConditionalThrowChild() {
  if (shouldThrowFlag) {
    throw new Error("Conditional explosion")
  }
  return <div data-testid="child-ok">Recovered</div>
}

// ── Test suites ──────────────────────────────────────────────────

describe("ErrorBoundary", () => {
  let consoleErrorSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    // Suppress React's default error boundary logging during tests
    consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {})
    shouldThrowFlag = true
  })

  afterEach(() => {
    cleanup()
    consoleErrorSpy.mockRestore()
  })

  // ────────────────────────────────────────────────────────────────
  // Rendering children when no error
  // ────────────────────────────────────────────────────────────────

  describe("happy path", () => {
    it("renders children when no error is thrown", () => {
      render(
        <ErrorBoundary>
          <ThrowingChild shouldThrow={false} />
        </ErrorBoundary>,
      )
      expect(screen.getByTestId("child-ok")).toBeTruthy()
      expect(screen.getByText("All good")).toBeTruthy()
    })
  })

  // ────────────────────────────────────────────────────────────────
  // Error catching & default fallback
  // ────────────────────────────────────────────────────────────────

  describe("error catching", () => {
    it("catches errors and renders the default fallback UI", () => {
      render(
        <ErrorBoundary>
          <ThrowingChild />
        </ErrorBoundary>,
      )
      // Default fallback shows "Something went wrong" and the error message
      expect(screen.getByText("Something went wrong")).toBeTruthy()
      expect(screen.getByText("Test explosion")).toBeTruthy()
    })

    it("renders the 'Try again' button in default fallback", () => {
      render(
        <ErrorBoundary>
          <ThrowingChild />
        </ErrorBoundary>,
      )
      const tryAgainButton = screen.getByText("Try again")
      expect(tryAgainButton).toBeTruthy()
      expect(tryAgainButton.tagName).toBe("BUTTON")
    })

    it("marks the default fallback (role=alert + testid) so it is detectable", () => {
      render(
        <ErrorBoundary>
          <ThrowingChild />
        </ErrorBoundary>,
      )
      const fallback = screen.getByTestId("error-boundary-fallback")
      expect(fallback).toBeTruthy()
      expect(fallback.getAttribute("role")).toBe("alert")
    })
  })

  // ────────────────────────────────────────────────────────────────
  // "Try again" button resets state
  // ────────────────────────────────────────────────────────────────

  describe("Try again button", () => {
    it("resets error state and re-renders children", () => {
      // ConditionalThrowChild reads `shouldThrowFlag` — initially true, so it throws.
      // After we flip the flag and click "Try again", it renders successfully.
      render(
        <ErrorBoundary>
          <ConditionalThrowChild />
        </ErrorBoundary>,
      )

      // Should show fallback after the throw
      expect(screen.getByText("Something went wrong")).toBeTruthy()

      // Flip the flag so the child won't throw on re-render
      shouldThrowFlag = false

      // Click "Try again" -- ErrorBoundary resets state, child re-renders
      fireEvent.click(screen.getByText("Try again"))

      // After reset, child renders successfully
      expect(screen.getByTestId("child-ok")).toBeTruthy()
      expect(screen.getByText("Recovered")).toBeTruthy()
      // Fallback should be gone
      expect(screen.queryByText("Something went wrong")).toBeNull()
    })

    it("shows fallback again if child throws again after reset", () => {
      // shouldThrowFlag stays true, so child always throws
      render(
        <ErrorBoundary>
          <ThrowingChild shouldThrow={true} />
        </ErrorBoundary>,
      )

      expect(screen.getByText("Something went wrong")).toBeTruthy()

      // Click "Try again" -- child will throw again
      fireEvent.click(screen.getByText("Try again"))

      // Fallback should still be shown (new error caught)
      expect(screen.getByText("Something went wrong")).toBeTruthy()
      expect(screen.getByText("Test explosion")).toBeTruthy()
    })
  })

  // ────────────────────────────────────────────────────────────────
  // Console.error logging
  // ────────────────────────────────────────────────────────────────

  describe("console.error logging", () => {
    it("logs to console.error when an error is caught", () => {
      render(
        <ErrorBoundary>
          <ThrowingChild />
        </ErrorBoundary>,
      )

      // ErrorBoundary's componentDidCatch calls console.error with
      // the boundary prefix, the error, and the component stack
      const boundaryLogCall = consoleErrorSpy.mock.calls.find(
        (args: unknown[]) => typeof args[0] === "string" && args[0].includes("[ErrorBoundary]"),
      )
      expect(boundaryLogCall).toBeTruthy()
      // Second argument should be the error object
      expect(boundaryLogCall![1]).toBeInstanceOf(Error)
      expect(boundaryLogCall![1].message).toBe("Test explosion")
    })

    it("includes the boundary name in the log when provided", () => {
      render(
        <ErrorBoundary name="TestPanel">
          <ThrowingChild />
        </ErrorBoundary>,
      )

      const boundaryLogCall = consoleErrorSpy.mock.calls.find(
        (args: unknown[]) => typeof args[0] === "string" && args[0].includes("[ErrorBoundary: TestPanel]"),
      )
      expect(boundaryLogCall).toBeTruthy()
    })

    it("uses generic prefix when no name is provided", () => {
      render(
        <ErrorBoundary>
          <ThrowingChild />
        </ErrorBoundary>,
      )

      const boundaryLogCall = consoleErrorSpy.mock.calls.find(
        (args: unknown[]) => typeof args[0] === "string" && args[0] === "[ErrorBoundary]",
      )
      expect(boundaryLogCall).toBeTruthy()
    })
  })

  // ────────────────────────────────────────────────────────────────
  // Custom fallback prop
  // ────────────────────────────────────────────────────────────────

  describe("custom fallback", () => {
    it("renders the custom fallback instead of default when provided", () => {
      render(
        <ErrorBoundary fallback={<div data-testid="custom-fallback">Custom error view</div>}>
          <ThrowingChild />
        </ErrorBoundary>,
      )

      expect(screen.getByTestId("custom-fallback")).toBeTruthy()
      expect(screen.getByText("Custom error view")).toBeTruthy()
      // Default fallback should NOT be present
      expect(screen.queryByText("Something went wrong")).toBeNull()
      expect(screen.queryByText("Try again")).toBeNull()
    })
  })

  // ────────────────────────────────────────────────────────────────
  // Error message display
  // ────────────────────────────────────────────────────────────────

  describe("error message display", () => {
    it("displays the error message in the default fallback", () => {
      const SpecificError = () => {
        throw new Error("Connection timeout on /api/preview")
      }

      render(
        <ErrorBoundary>
          <SpecificError />
        </ErrorBoundary>,
      )

      expect(screen.getByText("Connection timeout on /api/preview")).toBeTruthy()
    })
  })
})
