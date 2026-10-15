import { useCallback, useState } from "react"

/**
 * The items of a list shown collapsed, by id, each toggled open or shut (specs/
 * frontend-shared): a view setting, neither saved nor undone, all open to start.
 */
export default function useCollapsedSet(): { collapsed: ReadonlySet<string>; toggle: (id: string) => void } {
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set())
  const toggle = useCallback(
    (id: string) =>
      setCollapsed((current) => {
        const next = new Set(current)
        if (next.has(id)) next.delete(id)
        else next.add(id)
        return next
      }),
    [],
  )
  return { collapsed, toggle }
}
