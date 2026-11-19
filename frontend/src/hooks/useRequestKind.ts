import { useEffect, useState } from "react"
import { requestKind, type RequestKind } from "../utils/answers"

/**
 * A kind of request held for one mounted component and bumped when it unmounts, so the
 * answers its requests are still waiting for are dropped (specs/frontend-shared, "Late
 * answers"). Ask with `newest` for a lookup whose newest ask wins, `under` for work done
 * under the component's life.
 */
export function useRequestKind(): RequestKind {
  const [kind] = useState(requestKind)
  useEffect(() => () => kind.bump(), [kind])
  return kind
}
