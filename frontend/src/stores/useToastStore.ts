/**
 * Zustand store for the toast notification system.
 *
 * Separated from useUIStore because toasts are a cross-cutting concern used
 * by nearly every hook and component. Having a dedicated store makes the
 * dependency explicit and keeps the toast counter/lifecycle self-contained.
 *
 * Dedup (Issue #40): if an identical (type, text) toast is already visible,
 * new additions are suppressed. The existing toast's natural lifecycle
 * (auto-dismiss at ~3s in Toast.tsx, or explicit dismissToast) provides the
 * time-window behaviour: once the old toast drops off the array a retry
 * cycle can surface a fresh notification.  This keeps the store's internal
 * state minimal and reset-friendly — no separate per-key timestamp map
 * that tests must remember to clear.
 */
import { create } from "zustand"
import type { ToastMessage } from "../components/Toast"

interface ToastState {
  toasts: ToastMessage[]
  _toastCounter: number
  addToast: (type: ToastMessage["type"], text: string) => void
  dismissToast: (id: string) => void
}

const useToastStore = create<ToastState>()((set, get) => ({
  toasts: [],
  _toastCounter: 0,
  addToast: (type, text) => {
    // Dedup: if an identical (type, text) toast is already on screen,
    // don't stack another copy — and don't advance the counter either,
    // so the absence of a new toast is fully observable.
    const currentToasts = get().toasts
    if (currentToasts.some((t) => t.type === type && t.text === text)) {
      return
    }
    const id = String(get()._toastCounter + 1)
    set((s) => ({
      _toastCounter: s._toastCounter + 1,
      toasts: [...s.toasts.slice(-9), { id, type, text }],
    }))
  },
  dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}))

export default useToastStore
