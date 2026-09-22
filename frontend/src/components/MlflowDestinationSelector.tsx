/**
 * The per-node MLflow destination control.
 *
 * Where a node logs (or browses) is a *node* decision, so this control lives
 * on the node rather than on the toolbar. It reads the node's stored value
 * (`databricks` or `server`, anything else = the local folder) and reports the
 * chosen destination through `onChange`; callers store it with
 * `mlflowDestinationConfigValue`, which removes the key for Local folder. It
 * never reads or writes node config itself, and it never rewrites an
 * unconfigured or unrecognised stored value — the only store mutations it
 * makes are the inventory's own `fetchMlflow`/`invalidateMlflow`.
 *
 * Per `specs/frontend-shared/low-level.md` ("MLflow destination selector").
 */
import { useEffect } from "react"
import { RefreshCw, Settings } from "lucide-react"

import Tooltip from "./Tooltip"
import useSettingsStore, { useMlflowDestinations } from "../stores/useSettingsStore"
import useUIStore from "../stores/useUIStore"
import type { MlflowDestinationEntry, MlflowDestinationKey } from "../api/types"
import {
  MLFLOW_DESTINATION_KEYS,
  MLFLOW_DESTINATION_LABELS,
  effectiveMlflowDestination,
  mlflowDestinationEntry,
  mlflowLight,
  mlflowLogAvailability,
  type MlflowLight,
} from "../utils/mlflowDestinations"

export interface MlflowDestinationSelectorProps {
  /** The node's stored `mlflow_destination`; absent or `""` is the local folder. */
  value: string
  onChange: (value: MlflowDestinationKey) => void
  disabled?: boolean
  /** Radio-group `name`, so two mounted selectors stay independent. */
  idPrefix?: string
}

/** Light colours come from theme tokens only — never a literal. */
const LIGHT_COLOURS: Record<Exclude<MlflowLight, "none">, string> = {
  green: "var(--success)",
  amber: "var(--warning-strong)",
  grey: "var(--text-muted)",
  pending: "var(--text-muted)",
}

/**
 * What a remote's light means in words. A missing entry has no detail of its
 * own, so the inventory's detail is the only reason anybody has.
 */
function remoteTooltip(
  light: MlflowLight,
  entry: MlflowDestinationEntry | undefined,
  storeDetail: string,
): string {
  const detail = entry ? entry.detail : storeDetail
  switch (light) {
    case "pending":
      return "Checking connection…"
    case "grey":
      return `Not configured: ${detail} Click to configure.`
    case "amber":
      return detail
    default:
      return entry ? entry.destination : ""
  }
}

export default function MlflowDestinationSelector({
  value,
  onChange,
  disabled = false,
  idPrefix = "mlflow-destination",
}: MlflowDestinationSelectorProps) {
  const state = useMlflowDestinations()
  const fetchMlflow = useSettingsStore((s) => s.fetchMlflow)
  const invalidateMlflow = useSettingsStore((s) => s.invalidateMlflow)
  const setMlflowSettingsOpen = useUIStore((s) => s.setMlflowSettingsOpen)

  const loading = state.status === "loading"
  useEffect(() => {
    if (loading) fetchMlflow()
  }, [loading, fetchMlflow])

  const effective = effectiveMlflowDestination(value)
  const availability = mlflowLogAvailability(state, value)
  const resolved = availability.available
    ? `${availability.label} - ${availability.destination}`
    : availability.reason

  return (
    <div className="space-y-1">
      <div
        role="radiogroup"
        aria-label="MLflow destination"
        className="flex flex-wrap items-center gap-1"
      >
        {MLFLOW_DESTINATION_KEYS.map((key) => {
          const entry = mlflowDestinationEntry(state.destinations, key)
          // Local is never probed, so it carries no light even before the
          // inventory arrives — `mlflowLight` can only recognise local from an
          // entry it has, and while loading there is none.
          const light = key === "local" ? "none" : mlflowLight(entry, state.status)
          const selected = effective === key
          // Unconfigured remotes stay operable (aria-disabled, not disabled)
          // so activating one can offer the fix instead of silently doing
          // nothing — but they never become the node's choice.
          const blocked = light === "grey"

          // A render function, so a remote's tooltip describes the radio that
          // takes focus rather than the label wrapped around it.
          const option = (describedBy?: string) => (
            <label
              className="flex cursor-pointer items-center gap-1 rounded px-1.5 py-0.5 text-[11px]"
              style={{
                background: selected ? "var(--accent-soft-subtle)" : "var(--bg-input)",
                border: `1px solid ${selected ? "var(--accent-ring)" : "var(--border)"}`,
                color: blocked ? "var(--text-muted)" : "var(--text-primary)",
                opacity: disabled || loading ? 0.6 : 1,
              }}
            >
              <input
                type="radio"
                className="sr-only"
                name={idPrefix}
                id={`${idPrefix}-${key}`}
                checked={selected}
                disabled={disabled || loading}
                aria-disabled={blocked ? true : undefined}
                aria-describedby={describedBy}
                onClick={(event) => {
                  if (blocked) {
                    // Cancel the radio's activation behaviour: an unconfigured
                    // destination is offered as a fix, never selected.
                    event.preventDefault()
                    setMlflowSettingsOpen(true)
                  }
                }}
                onChange={() => {
                  // Also reached by keyboard navigation, which no click can
                  // cancel — hence the guard rather than the preventDefault.
                  if (blocked) return
                  onChange(key)
                }}
              />
              {light !== "none" && (
                <span
                  data-testid={`mlflow-light-${key}`}
                  data-light={light}
                  aria-hidden="true"
                  className="inline-block h-1.5 w-1.5 shrink-0 rounded-full"
                  style={{ background: LIGHT_COLOURS[light] }}
                />
              )}
              <span>{MLFLOW_DESTINATION_LABELS[key]}</span>
            </label>
          )

          return (
            <span key={key} data-testid={`mlflow-option-${key}`} className="inline-flex">
              {light === "none" ? (
                option()
              ) : (
                <Tooltip label={remoteTooltip(light, entry, state.detail)}>{option}</Tooltip>
              )}
            </span>
          )
        })}
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        <p
          data-testid="mlflow-destination-resolved"
          className="min-w-0 flex-1 break-all text-[10px]"
          style={{ color: "var(--text-muted)" }}
        >
          {resolved}
        </p>
        <button
          type="button"
          aria-label="Re-check MLflow connections"
          title="Re-check MLflow connections"
          className="shrink-0 rounded p-0.5"
          style={{ color: "var(--text-muted)" }}
          disabled={loading}
          onClick={() => invalidateMlflow()}
        >
          <RefreshCw size={12} aria-hidden="true" />
        </button>
        <button
          type="button"
          aria-label="MLflow settings"
          title="MLflow settings"
          className="shrink-0 rounded p-0.5"
          style={{ color: "var(--text-muted)" }}
          onClick={() => setMlflowSettingsOpen(true)}
        >
          <Settings size={12} aria-hidden="true" />
        </button>
      </div>
    </div>
  )
}
