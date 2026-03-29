import { describe, it, expect, beforeEach } from "vitest"
import useToastStore from "../useToastStore"

function reset() {
  useToastStore.setState({
    toasts: [],
    _toastCounter: 0,
  })
}

describe("useToastStore", () => {
  beforeEach(reset)

  describe("addToast / dismissToast", () => {
    it("adds a toast with incrementing id", () => {
      useToastStore.getState().addToast("info", "Hello")
      const { toasts, _toastCounter } = useToastStore.getState()
      expect(toasts).toHaveLength(1)
      expect(toasts[0]).toEqual({ id: "1", type: "info", text: "Hello" })
      expect(_toastCounter).toBe(1)
    })

    it("accumulates multiple toasts", () => {
      const { addToast } = useToastStore.getState()
      addToast("info", "First")
      addToast("error", "Second")
      addToast("success", "Third")
      const { toasts } = useToastStore.getState()
      expect(toasts).toHaveLength(3)
      expect(toasts.map((t) => t.type)).toEqual(["info", "error", "success"])
      expect(toasts.map((t) => t.id)).toEqual(["1", "2", "3"])
    })

    it("dismisses a toast by id", () => {
      const { addToast } = useToastStore.getState()
      addToast("info", "Keep")
      addToast("error", "Remove")
      useToastStore.getState().dismissToast("2")
      const { toasts } = useToastStore.getState()
      expect(toasts).toHaveLength(1)
      expect(toasts[0].text).toBe("Keep")
    })

    it("dismissing non-existent id is a no-op", () => {
      useToastStore.getState().addToast("info", "Only")
      useToastStore.getState().dismissToast("999")
      expect(useToastStore.getState().toasts).toHaveLength(1)
    })

    it("counter keeps incrementing after dismiss", () => {
      const { addToast } = useToastStore.getState()
      addToast("info", "A")
      useToastStore.getState().dismissToast("1")
      addToast("info", "B")
      expect(useToastStore.getState().toasts[0].id).toBe("2")
    })

    it("counter keeps incrementing across multiple dismiss cycles", () => {
      const { addToast } = useToastStore.getState()
      addToast("info", "A")
      addToast("info", "B")
      useToastStore.getState().dismissToast("1")
      useToastStore.getState().dismissToast("2")
      addToast("info", "C")
      expect(useToastStore.getState()._toastCounter).toBe(3)
      expect(useToastStore.getState().toasts[0].id).toBe("3")
    })

    it("adding toast at max capacity slices to keep last 10", () => {
      const { addToast } = useToastStore.getState()
      for (let i = 0; i < 10; i++) {
        addToast("info", `Toast ${i + 1}`)
      }
      expect(useToastStore.getState().toasts).toHaveLength(10)

      addToast("info", "Toast 11")
      const { toasts } = useToastStore.getState()
      expect(toasts).toHaveLength(10)
      expect(toasts[0].id).toBe("2")
      expect(toasts[toasts.length - 1].id).toBe("11")
    })

    it("slice keeps newest toasts when adding beyond capacity", () => {
      const { addToast } = useToastStore.getState()
      for (let i = 0; i < 15; i++) {
        addToast("info", `Toast ${i + 1}`)
      }
      const { toasts } = useToastStore.getState()
      expect(toasts).toHaveLength(10)
      expect(toasts[0].text).toBe("Toast 6")
      expect(toasts[toasts.length - 1].text).toBe("Toast 15")
    })
  })
})
