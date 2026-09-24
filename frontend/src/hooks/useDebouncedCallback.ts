import { useEffect, useLayoutEffect, useRef, useState } from "react"

/** A debounced call: at most one waits at a time, with its latest arguments. */
export interface DebouncedCallback<A extends unknown[], R> {
  /** Run the callback with `args` once `delayMs` (default: the hook's delay)
   *  passes without another schedule; a later schedule replaces the waiting
   *  arguments and restarts the wait. */
  schedule: (args: A, delayMs?: number) => void
  /** Run the waiting call now and return its result; undefined when none waits. */
  flush: () => R | undefined
  /** Drop the waiting call. */
  cancel: () => void
  /** The waiting call's arguments, or null when none waits. */
  pending: () => A | null
}

/**
 * The one debounce for a scheduled call. The returned object is stable, and
 * the call always runs the latest `callback`. On unmount the waiting call is
 * dropped, or run when `onUnmount` is `"flush"` (an edit that must not be lost).
 */
export function useDebouncedCallback<A extends unknown[], R>(
  callback: (...args: A) => R,
  delayMs: number,
  { onUnmount = "cancel" }: { onUnmount?: "cancel" | "flush" } = {},
): DebouncedCallback<A, R> {
  const callbackRef = useRef(callback)
  useLayoutEffect(() => {
    callbackRef.current = callback
  })
  const delayRef = useRef(delayMs)
  useLayoutEffect(() => {
    delayRef.current = delayMs
  })
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const pendingRef = useRef<A | null>(null)

  const [debounced] = useState<DebouncedCallback<A, R>>(() => {
    const cancel = () => {
      clearTimeout(timerRef.current)
      timerRef.current = undefined
      pendingRef.current = null
    }
    const flush = () => {
      const args = pendingRef.current
      cancel()
      return args === null ? undefined : callbackRef.current(...args)
    }
    return {
      schedule: (args, delay) => {
        cancel()
        pendingRef.current = args
        timerRef.current = setTimeout(flush, delay ?? delayRef.current)
      },
      flush,
      cancel,
      pending: () => pendingRef.current,
    }
  })

  const onUnmountRef = useRef(onUnmount)
  useEffect(() => () => {
    if (onUnmountRef.current === "flush") debounced.flush()
    else debounced.cancel()
  }, [debounced])

  return debounced
}
