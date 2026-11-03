import { useCallback, useLayoutEffect, useRef } from "react"

import { confirmed } from "../../stores/useQuestionStore"
import type { OnUpdateConfig } from "./_shared"

type ConfirmQuestion = Parameters<typeof confirmed>[0]

/** An editor's config and update handler as they stand now. */
export type CurrentEditorConfig = {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
}

/**
 * Asks a question, then hands back the editor's config and update handler as
 * they stand once it is answered. Props a callback closed over are a render old
 * by then: writing a list built from them would undo every edit made while the
 * dialog was open. Answers null when the question is declined; a question open
 * when another document loads is declined for it (`useQuestionStore`), so the
 * change is never written into that document.
 */
export default function useConfirmedConfigUpdate(
  config: Record<string, unknown>,
  onUpdate: OnUpdateConfig,
): (question: ConfirmQuestion) => Promise<CurrentEditorConfig | null> {
  const current = useRef<CurrentEditorConfig>({ config, onUpdate })
  useLayoutEffect(() => {
    current.current = { config, onUpdate }
  })
  return useCallback(
    async (question: ConfirmQuestion) => ((await confirmed(question)) ? current.current : null),
    [],
  )
}
