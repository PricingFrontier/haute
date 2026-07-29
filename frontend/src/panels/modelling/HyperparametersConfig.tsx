import { useEffect, useMemo, useRef, useState } from "react"

import { MODEL_COLORS } from "../../theme/colors"
import type { OnUpdateConfig } from "../editors"
import {
  formatHyperparameters,
  mergeReservedKeys,
  parseHyperparameters,
} from "./hyperparameters"

type Props = {
  algorithmLabel: string
  params: Record<string, unknown>
  defaultParams?: Record<string, unknown>
  reservedKeys?: readonly string[]
  reservedKeysHelp?: string
  onUpdate: OnUpdateConfig
  draft: string
  setDraft: (draft: string) => void
}

export function HyperparametersConfig({
  algorithmLabel,
  params,
  defaultParams = {},
  reservedKeys = [],
  reservedKeysHelp = "",
  onUpdate,
  draft,
  setDraft,
}: Props) {
  const stored = useMemo(
    () => formatHyperparameters(params, defaultParams, reservedKeys),
    [defaultParams, params, reservedKeys],
  )
  const previousStored = useRef(stored)
  const [jsonError, setJsonError] = useState<string | null>(null)

  useEffect(() => {
    if (stored === previousStored.current) return
    if (draft === previousStored.current) {
      setDraft(stored)
      setJsonError(null)
    }
    previousStored.current = stored
  }, [draft, setDraft, stored])

  const dirty = draft !== stored

  const applyDraft = () => {
    try {
      const projection = parseHyperparameters(
        draft,
        reservedKeys,
        reservedKeysHelp,
      )
      const merged = mergeReservedKeys(params, projection, reservedKeys)
      // Sync the draft to the post-apply stored presentation: for an emptied
      // projection that is the defaults draft, so the editor never reads as
      // dirty against its own just-applied state.
      setDraft(formatHyperparameters(merged, defaultParams, reservedKeys))
      onUpdate("params", merged)
      setJsonError(null)
    } catch (cause) {
      setJsonError(cause instanceof Error ? cause.message : "Invalid JSON")
    }
  }

  return (
    <section className="space-y-2.5">
      <div>
        <h3
          className="text-[11px] font-bold uppercase tracking-[0.08em]"
          style={{ color: "var(--text-muted)" }}
        >
          Hyperparameters
        </h3>
      </div>

      <label
        className="block text-[11px]"
        style={{ color: "var(--text-secondary)" }}
      >
        Parameters JSON
        <textarea
          aria-label={`${algorithmLabel} hyperparameters JSON`}
          aria-invalid={jsonError ? true : undefined}
          value={draft}
          onChange={(event) => {
            setDraft(event.target.value)
            setJsonError(null)
          }}
          spellCheck={false}
          rows={Math.min(24, Math.max(10, draft.split("\n").length + 1))}
          className="mt-1.5 w-full rounded-lg px-2.5 py-2 font-mono text-xs leading-5"
          style={{
            background: "var(--bg-input)",
            border: `1px solid ${jsonError ? "var(--danger)" : "var(--border)"}`,
            color: "var(--text-primary)",
            resize: "vertical",
          }}
        />
      </label>

      <div className="flex gap-2">
        <button
          type="button"
          disabled={!dirty}
          onClick={applyDraft}
          className="rounded-md px-3 py-1.5 text-xs font-medium transition-opacity disabled:cursor-default disabled:opacity-40"
          style={{ background: MODEL_COLORS.accent, color: "white" }}
        >
          Apply
        </button>
        <button
          type="button"
          disabled={!dirty}
          onClick={() => {
            setDraft(stored)
            setJsonError(null)
          }}
          className="rounded-md px-3 py-1.5 text-xs font-medium transition-opacity disabled:cursor-default disabled:opacity-40"
          style={{
            background: "var(--chrome-hover)",
            border: "1px solid var(--border)",
            color: "var(--text-secondary)",
          }}
        >
          Revert
        </button>
      </div>

      {jsonError && (
        <p className="text-[10px]" style={{ color: "var(--danger)" }} role="alert">
          {jsonError}
        </p>
      )}
    </section>
  )
}
