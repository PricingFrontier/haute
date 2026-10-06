import { Check, X } from "lucide-react"

/** What a new chat offers as a starting point; each fills the composer. */
const STARTER_PROMPTS = [
  "Band driver age into groups and add a rating step that applies a factor per band.",
  "Join the vehicle table onto the quotes by vehicle_id and keep its columns.",
  "Explain what this pipeline does, node by node.",
] as const

const CAN = [
  "Build and edit nodes, writing their logic as steps",
  "Set up banding, rating tables and joins",
  "Configure outputs and the quote response",
  "Set up model training and scoring nodes",
]

const CANNOT = [
  "Run the pipeline",
  "Train models",
  "Deploy",
  "Use Git (each change it saves is recorded like your saves)",
]

interface AssistantIntroProps {
  /** The canvas pipeline's source file this chat edits. */
  sourceFile: string | null
  onChoosePrompt: (prompt: string) => void
}

/** The empty state of a new chat: what the assistant can and cannot do here. */
export default function AssistantIntro({ sourceFile, onChoosePrompt }: AssistantIntroProps) {
  return (
    <div data-testid="assistant-empty" className="space-y-3 px-1 py-2 text-[11px]" style={{ color: "var(--text-muted)" }}>
      <p style={{ color: "var(--text-primary)" }}>
        {sourceFile === null
          ? "Ask the assistant to change this pipeline."
          : `Ask the assistant to change ${sourceFile}.`}
      </p>
      <div>
        <p className="mb-1 font-medium" style={{ color: "var(--text-primary)" }}>It can</p>
        <ul data-testid="assistant-can" className="space-y-0.5">
          {CAN.map((item) => (
            <li key={item} className="flex items-start gap-1.5">
              <Check size={12} aria-hidden="true" className="mt-0.5 shrink-0" style={{ color: "var(--success)" }} />
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </div>
      <div>
        <p className="mb-1 font-medium" style={{ color: "var(--text-primary)" }}>It cannot</p>
        <ul data-testid="assistant-cannot" className="space-y-0.5">
          {CANNOT.map((item) => (
            <li key={item} className="flex items-start gap-1.5">
              <X size={12} aria-hidden="true" className="mt-0.5 shrink-0" style={{ color: "var(--text-muted)" }} />
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </div>
      <div>
        <p className="mb-1 font-medium" style={{ color: "var(--text-primary)" }}>Try</p>
        <div className="flex flex-col gap-1">
          {STARTER_PROMPTS.map((prompt) => (
            <button
              key={prompt}
              type="button"
              data-testid="assistant-starter-prompt"
              onClick={() => onChoosePrompt(prompt)}
              className="rounded-md px-2 py-1.5 text-left transition-colors hover:bg-[var(--bg-hover)]"
              style={{ border: "1px solid var(--border)", color: "var(--text-primary)" }}
            >
              {prompt}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
