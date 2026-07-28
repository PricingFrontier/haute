import { useEffect, useState } from "react"
import { InputSourcesBar, INPUT_STYLE } from "./_shared"
import type { InputSource, OnUpdateConfig } from "./_shared"
import { configField } from "../../utils/configField"
import { CommittedTextField } from "../../components/form"

type ScenarioRangeNumberField = "min_value" | "max_value"
type ScenarioRangeDraftState = {
  config: Record<string, unknown>
  committedText: string
  draft: string | null
  pendingConfigEcho: boolean
}

function numberConfigText(value: unknown): string {
  return value === null || value === undefined ? "" : String(value)
}

function parseScenarioNumber(raw: string): number | null {
  const trimmed = raw.trim()
  if (!trimmed) return null
  const parsed = Number(trimmed)
  return Number.isFinite(parsed) ? parsed : null
}

function isBlankScenarioNumber(raw: string): boolean {
  return raw.trim() === ""
}

function activeRangeDraft(
  state: ScenarioRangeDraftState,
  committedText: string,
  config: Record<string, unknown>,
): string | null {
  if (state.draft === null) return null
  const draftNumber = parseScenarioNumber(state.draft)
  if (state.config === config) return state.draft
  if (!state.pendingConfigEcho) return null
  if (state.committedText === committedText) {
    return draftNumber !== null ? state.draft : null
  }
  const committedNumber = parseScenarioNumber(committedText)
  return draftNumber !== null && committedNumber !== null && draftNumber === committedNumber
    ? state.draft
    : null
}

export default function ScenarioExpanderEditor({
  config,
  onUpdate,
  inputSources,
  onDeleteInput,
  upstreamColumns,
}: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  inputSources: InputSource[]
  onDeleteInput?: (edgeId: string) => void
  upstreamColumns: { name: string; dtype: string }[]
}) {
  const quoteId = configField(config, "quote_id", "")
  const columnName = configField(config, "column_name", "")
  const minValue = configField(config, "min_value", "")
  const maxValue = configField(config, "max_value", "")
  const steps = configField(config, "steps", "")
  const stepColumn = configField(config, "step_column", "")
  const committedMinText = numberConfigText(minValue)
  const committedMaxText = numberConfigText(maxValue)
  const [minDraftState, setMinDraftState] = useState<ScenarioRangeDraftState>({
    config,
    committedText: committedMinText,
    draft: null,
    pendingConfigEcho: false,
  })
  const [maxDraftState, setMaxDraftState] = useState<ScenarioRangeDraftState>({
    config,
    committedText: committedMaxText,
    draft: null,
    pendingConfigEcho: false,
  })

  const minDraft = activeRangeDraft(minDraftState, committedMinText, config)
  const maxDraft = activeRangeDraft(maxDraftState, committedMaxText, config)
  const setMinDraft = (draft: string | null, pendingConfigEcho = false) => {
    setMinDraftState({ config, committedText: committedMinText, draft, pendingConfigEcho })
  }
  const setMaxDraft = (draft: string | null, pendingConfigEcho = false) => {
    setMaxDraftState({ config, committedText: committedMaxText, draft, pendingConfigEcho })
  }

  useEffect(() => {
    if (minDraftState.draft === null) return
    if (minDraft === null) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- derived draft reset: drop stale range text when config ownership changes
      setMinDraftState({ config, committedText: committedMinText, draft: null, pendingConfigEcho: false })
      return
    }
    if (minDraftState.pendingConfigEcho) {
      setMinDraftState({ config, committedText: committedMinText, draft: minDraft, pendingConfigEcho: false })
    }
  }, [committedMinText, config, minDraft, minDraftState.draft, minDraftState.pendingConfigEcho])

  useEffect(() => {
    if (maxDraftState.draft === null) return
    if (maxDraft === null) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- derived draft reset: drop stale range text when config ownership changes
      setMaxDraftState({ config, committedText: committedMaxText, draft: null, pendingConfigEcho: false })
      return
    }
    if (maxDraftState.pendingConfigEcho) {
      setMaxDraftState({ config, committedText: committedMaxText, draft: maxDraft, pendingConfigEcho: false })
    }
  }, [committedMaxText, config, maxDraft, maxDraftState.draft, maxDraftState.pendingConfigEcho])

  const shownMinText = minDraft ?? committedMinText
  const shownMaxText = maxDraft ?? committedMaxText
  const parsedMin = parseScenarioNumber(shownMinText)
  const parsedMax = parseScenarioNumber(shownMaxText)
  const minInvalid = minDraft !== null && parseScenarioNumber(minDraft) === null
  const maxInvalid = maxDraft !== null && parseScenarioNumber(maxDraft) === null
  const numberInputStyle = (invalid: boolean) => invalid
    ? { ...INPUT_STYLE, border: "1px solid var(--danger-border-strong)" }
    : INPUT_STYLE

  // Buffer keystrokes locally and commit ONCE at the blur boundary —
  // committing per parseable keystroke pushed one undo snapshot per
  // character (BUGS undo-atomicity class; same rationale as
  // CommittedTextField, which these fields don't use directly because
  // they carry their own numeric-validity styling and draft machinery).
  const updateRangeDraft = (
    next: string,
    setDraft: (draft: string | null, pendingConfigEcho?: boolean) => void,
  ) => {
    setDraft(next)
  }

  const commitRangeNumber = (
    field: ScenarioRangeNumberField,
    draft: string | null,
    committedText: string,
    clearDraft: (next: string | null) => void,
  ) => {
    if (draft === null) return
    if (draft === committedText) {
      clearDraft(null)
      return
    }
    const parsed = parseScenarioNumber(draft)
    if (parsed !== null) {
      onUpdate(field, parsed)
      clearDraft(null)
      return
    }
    if (isBlankScenarioNumber(draft)) {
      onUpdate(field, null)
      clearDraft(null)
    }
    // Invalid non-blank drafts stay in place with the error styling so
    // the user can fix or revert — nothing invalid reaches config.
  }

  return (
    <div className="px-4 py-3 space-y-4">
      <InputSourcesBar inputSources={inputSources} onDeleteInput={onDeleteInput} />

      {/* Row key */}
      <div>
        <label className="text-[11px] font-bold uppercase tracking-[0.08em] block mb-1.5" style={{ color: 'var(--text-muted)' }}>
          Row Key
          <span className="ml-1.5 normal-case tracking-normal font-normal">unique column per input row</span>
        </label>
        {upstreamColumns.length > 0 ? (
          <select
            className="w-full px-2.5 py-1.5 rounded-md text-[12px] font-mono appearance-none cursor-pointer"
            style={INPUT_STYLE}
            value={quoteId}
            onChange={(e) => onUpdate("quote_id", e.target.value)}
          >
            <option value="">-- select column --</option>
            {upstreamColumns.map((c) => (
              <option key={c.name} value={c.name}>{c.name}</option>
            ))}
          </select>
        ) : (
          <CommittedTextField
            type="text"
            className="w-full px-2.5 py-1.5 rounded-md text-[12px] font-mono"
            style={INPUT_STYLE}
            value={quoteId}
            onCommit={(v) => onUpdate("quote_id", v)}
          />
        )}
      </div>

      {/* Index column name */}
      <div>
        <label className="text-[11px] font-bold uppercase tracking-[0.08em] block mb-1.5" style={{ color: 'var(--text-muted)' }}>
          Index Column
          <span className="ml-1.5 normal-case tracking-normal font-normal">0-based step index column</span>
        </label>
        <CommittedTextField
          type="text"
          className="w-full px-2.5 py-1.5 rounded-md text-[12px] font-mono"
          style={INPUT_STYLE}
          value={stepColumn}
          onCommit={(v) => onUpdate("step_column", v)}
        />
      </div>

      {/* Steps — shown standalone when no value column is set */}
      {!columnName && (
        <div>
          <label className="text-[11px] font-bold uppercase tracking-[0.08em] block mb-1.5" style={{ color: 'var(--text-muted)' }}>
            Steps
            <span className="ml-1.5 normal-case tracking-normal font-normal">rows generated per input row</span>
          </label>
          <CommittedTextField
            type="number"
            min={1}
            className="w-full px-2.5 py-1.5 rounded-md text-[12px] font-mono"
            style={INPUT_STYLE}
            value={String(steps)}
            onCommit={(v) => onUpdate("steps", Math.max(1, parseInt(v) || 1))}
          />
        </div>
      )}

      {/* Value column (optional) */}
      <div>
        <label className="text-[11px] font-bold uppercase tracking-[0.08em] block mb-1.5" style={{ color: 'var(--text-muted)' }}>
          Value Column
          <span className="ml-1.5 normal-case tracking-normal font-normal">(optional)</span>
        </label>
        <CommittedTextField
          type="text"
          className="w-full px-2.5 py-1.5 rounded-md text-[12px] font-mono"
          style={INPUT_STYLE}
          value={columnName}
          onCommit={(v) => onUpdate("column_name", v)}
        />
      </div>

      {/* Value range — only shown when value column is set */}
      {columnName && (
        <div>
          <label className="text-[11px] font-bold uppercase tracking-[0.08em] block mb-1.5" style={{ color: 'var(--text-muted)' }}>
            Value Range
          </label>
          {parsedMin !== null && parsedMax !== null && parsedMin >= parsedMax && (
            <div className="mb-1.5 px-2 py-1 rounded text-[10px]" style={{ background: 'var(--warning-soft-strong)', color: 'var(--warning-strong)', border: '1px solid var(--warning-border)' }}>
              Warning: min value should be less than max value
            </div>
          )}
          <div className="grid grid-cols-4 gap-2">
            <div>
              <label className="text-[10px] block mb-0.5" style={{ color: 'var(--text-muted)' }}>Min</label>
              <input
                type="text"
                inputMode="decimal"
                className="w-full px-2 py-1.5 rounded-md text-[12px] font-mono"
                style={numberInputStyle(minInvalid)}
                value={shownMinText}
                aria-invalid={minInvalid ? true : undefined}
                onChange={(e) => updateRangeDraft(e.target.value, setMinDraft)}
                onBlur={() => commitRangeNumber("min_value", minDraft, committedMinText, setMinDraft)}
              />
            </div>
            <div>
              <label className="text-[10px] block mb-0.5" style={{ color: 'var(--text-muted)' }}>Max</label>
              <input
                type="text"
                inputMode="decimal"
                className="w-full px-2 py-1.5 rounded-md text-[12px] font-mono"
                style={numberInputStyle(maxInvalid)}
                value={shownMaxText}
                aria-invalid={maxInvalid ? true : undefined}
                onChange={(e) => updateRangeDraft(e.target.value, setMaxDraft)}
                onBlur={() => commitRangeNumber("max_value", maxDraft, committedMaxText, setMaxDraft)}
              />
            </div>
            <div>
              <label className="text-[10px] block mb-0.5" style={{ color: 'var(--text-muted)' }}>Steps</label>
              <CommittedTextField
                type="number"
                min={1}
                className="w-full px-2 py-1.5 rounded-md text-[12px] font-mono"
                style={INPUT_STYLE}
                value={String(steps)}
                onCommit={(v) => onUpdate("steps", Math.max(1, parseInt(v) || 1))}
              />
            </div>
            <div>
              <label className="text-[10px] block mb-0.5" style={{ color: 'var(--text-muted)' }}>Step Size</label>
              <div
                className="w-full px-2 py-1.5 rounded-md text-[12px] font-mono"
                style={{ ...INPUT_STYLE, opacity: 0.7 }}
                data-testid="step-size"
              >
                {steps && Number(steps) > 1 && parsedMin !== null && parsedMax !== null ? +((parsedMax - parsedMin) / Math.max(Number(steps) - 1, 1)).toFixed(4) : "—"}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
