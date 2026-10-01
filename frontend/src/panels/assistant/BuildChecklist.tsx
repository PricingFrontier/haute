import { useState, type RefObject } from "react"
import { CheckCircle2, ChevronDown, ChevronRight, Circle, ListChecks } from "lucide-react"

import type { AssistantBuildPlan, AssistantBuildPlanItem } from "../../api/assistant"
import type { TranscriptEntry } from "../../stores/useAssistantStore"

/** The length of the abbreviated change id a change without a card is named by. */
const SHORT_ID = 7

type ItemState = "Done" | "In progress" | "Not started"

function itemState(item: AssistantBuildPlanItem): ItemState {
  if (item.complete) return "Done"
  return item.changes.some((change) => !change.undone) ? "In progress" : "Not started"
}

interface BuildChecklistProps {
  plan: AssistantBuildPlan
  /** The transcript entries, whose change cards name and locate each change. */
  entries: TranscriptEntry[]
  /** The transcript element, holding each change card under its `data-change-id`. */
  transcriptRef: RefObject<HTMLDivElement | null>
}

/**
 * The build checklist: each stage of the backend's plan with whether the
 * assistant marked it done, and the changes saved for it, each linked to its
 * change card. It renders only what the plan holds.
 */
export default function BuildChecklist({ plan, entries, transcriptRef }: BuildChecklistProps) {
  const [expanded, setExpanded] = useState(true)
  const done = plan.items.filter((item) => item.complete).length
  // The latest card of each change: one plan can be saved again after an undo.
  const summaries = new Map<string, string>()
  for (const entry of entries) {
    if (entry.kind === "change") summaries.set(entry.change.id, entry.change.summary)
  }

  const showCard = (changeId: string) => {
    const cards = transcriptRef.current?.querySelectorAll<HTMLElement>(
      `[data-change-id="${changeId}"]`,
    )
    const card = cards?.[cards.length - 1]
    if (card === undefined) {
      throw new Error(`Build checklist: the transcript has no card for change ${changeId}.`)
    }
    card.scrollIntoView({ block: "nearest", behavior: "smooth" })
  }

  return (
    <section
      data-testid="assistant-build-checklist"
      aria-label="Checklist"
      className="shrink-0 px-3 py-2 text-[11px]"
      style={{ background: "var(--bg-elevated)", borderTop: "1px solid var(--border)" }}
    >
      <button
        type="button"
        data-testid="assistant-build-checklist-toggle"
        aria-expanded={expanded}
        onClick={() => setExpanded((open) => !open)}
        className="flex w-full items-center gap-1.5 font-medium"
        style={{ color: "var(--text-primary)" }}
      >
        {expanded
          ? <ChevronDown size={12} aria-hidden="true" />
          : <ChevronRight size={12} aria-hidden="true" />}
        <ListChecks size={12} aria-hidden="true" style={{ color: "var(--accent)" }} />
        Checklist
        <span
          data-testid="assistant-build-checklist-count"
          className="ml-auto font-normal"
          style={{ color: "var(--text-muted)" }}
        >
          {done} of {plan.items.length} done
        </span>
      </button>
      {expanded && (
        <ol className="mt-1.5 max-h-40 space-y-1 overflow-y-auto">
          {plan.items.map((item) => {
            const state = itemState(item)
            return (
              <li key={item.id} data-testid="assistant-build-item" data-state={state}>
                <div className="flex items-start gap-1.5">
                  {item.complete ? (
                    <CheckCircle2
                      size={12}
                      aria-hidden="true"
                      className="mt-px shrink-0"
                      style={{ color: "var(--success)" }}
                    />
                  ) : (
                    <Circle
                      size={12}
                      aria-hidden="true"
                      className="mt-px shrink-0"
                      style={{ color: "var(--text-muted)" }}
                    />
                  )}
                  <span className="min-w-0 break-words" style={{ color: "var(--text-primary)" }}>
                    {item.title}
                  </span>
                  <span className="ml-auto shrink-0" style={{ color: "var(--text-muted)" }}>
                    {state}
                  </span>
                </div>
                {item.changes.length > 0 && (
                  <ul className="space-y-0.5 pl-[18px]">
                    {item.changes.map((change) => {
                      const summary = summaries.get(change.id)
                      const struck = change.undone ? "line-through" : undefined
                      return (
                        <li
                          key={change.id}
                          data-testid="assistant-build-change"
                          data-undone={change.undone}
                          className="flex items-baseline gap-1"
                          style={{ color: "var(--text-secondary)" }}
                        >
                          {summary === undefined ? (
                            <span style={{ textDecoration: struck }}>
                              An earlier change{" "}
                              <span className="font-mono">{change.id.slice(0, SHORT_ID)}</span>
                            </span>
                          ) : (
                            <button
                              type="button"
                              data-testid="assistant-build-change-link"
                              onClick={() => showCard(change.id)}
                              title="Show this change's card"
                              className="min-w-0 truncate text-left hover:underline"
                              style={{ color: "var(--accent)", textDecoration: struck }}
                            >
                              {summary}
                            </button>
                          )}
                          {change.undone && (
                            <span className="shrink-0" style={{ color: "var(--text-muted)" }}>
                              undone
                            </span>
                          )}
                        </li>
                      )
                    })}
                  </ul>
                )}
              </li>
            )
          })}
        </ol>
      )}
    </section>
  )
}
