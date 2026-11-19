import { useId, type CSSProperties, type InputHTMLAttributes, type Ref } from "react"

import type { NumberRule } from "../../utils/numberRule"
import { DraftTextField } from "./CommittedTextField"
import { useNumberDraft } from "./useNumberDraft"
import type { CommitResult } from "./useCommittedDraft"

// ─── The shared number field ──────────────────────────────────────
//
// Every number box of the editor and the Workbench (specs/frontend-shared high-level,
// "Typed numbers"). It is a text field on the shared draft, never the browser's own number
// input, which reads the text by the browser's language and drops a character that language
// does not use: `1,5` became 15 in an English browser and 1.5 in a German one. What is typed
// is read on blur or Enter by the one grammar (`utils/typedNumbers.ts`), the server's too;
// text it refuses, a decimal comma among it, stays on screen with its reason and is not
// committed. `typedNumbersByHand.test.ts` fails for a box that reads its own text. The draft
// and its refusal are `useNumberDraft`'s, which a grid's cell draws on too.

type NumberFieldProps = NumberRule &
  Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "type" | "min" | "max" | "step" | "inputMode" | "required"> & {
    ref?: Ref<HTMLInputElement>
    /** The stored number, or null or undefined for none. */
    value: number | null | undefined
    /** Called once per commit with the number read, or null for a blank optional field. */
    onCommit: (next: number | null) => CommitResult | void
    /** What the value belongs to (a row's identity), when the field is reused for another. */
    owner?: unknown
    /** The look of the input while its text is refused, merged over `style`. */
    problemStyle?: CSSProperties
    /** The refusal's test id; without it the refusal has none. */
    problemTestId?: string
    /** The refusal's class. */
    problemClassName?: string
    /** A box to hold the field and its refusal, when the field sits in a row of others. */
    containerClassName?: string
  }

/**
 * A number box: the stored number until the analyst types, the typed text read by the one
 * grammar on blur or Enter, the number committed, and text refused said beneath it.
 */
export function NumberField({
  value,
  onCommit,
  owner,
  integer,
  min,
  max,
  exclusiveMin,
  exclusiveMax,
  required,
  unit,
  style,
  problemStyle = { borderColor: "var(--danger)" },
  problemTestId,
  problemClassName = "text-[10px]",
  containerClassName,
  ...rest
}: NumberFieldProps) {
  const { draft, problem } = useNumberDraft(value, onCommit, { integer, min, max, exclusiveMin, exclusiveMax, required, unit }, { owner })
  const problemId = useId()
  const field = (
    <>
      <DraftTextField
        {...rest}
        draft={draft}
        type="text"
        inputMode={integer ? "numeric" : "decimal"}
        aria-invalid={problem !== null ? true : rest["aria-invalid"]}
        aria-describedby={problem !== null ? problemId : rest["aria-describedby"]}
        style={problem !== null ? { ...style, ...problemStyle } : style}
      />
      {problem !== null && (
        <span id={problemId} role="alert" data-testid={problemTestId} className={problemClassName} style={{ color: "var(--danger)" }}>
          {problem}
        </span>
      )}
    </>
  )
  return containerClassName === undefined ? field : <div className={containerClassName}>{field}</div>
}
