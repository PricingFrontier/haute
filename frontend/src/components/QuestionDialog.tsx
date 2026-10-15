/**
 * The question the page is asking (specs/frontend-shared), on `ModalShell`: its title and
 * text, the names it lists, the reason it asks for, focused, and Cancel, its other way on
 * and its yes, each named for what it does, the yes in the danger colour for an action
 * that deletes or discards. Escape, a press outside it and Cancel answer no; a way on
 * waits for the reason when one is asked for.
 */
import { useEffect, useRef, useState } from "react"
import useQuestionStore, { type Answer, type Question } from "../stores/useQuestionStore"
import ModalShell from "./ModalShell"
import { ModalFormHeader, ModalTextInput } from "./ModalForm"

const BUTTON = "px-3 py-1.5 text-[12px] font-medium rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed"

export default function QuestionDialog() {
  const question = useQuestionStore((s) => s.question)
  const answer = useQuestionStore((s) => s.answer)
  if (question === null) return null
  return <QuestionForm key={question.id} question={question} answer={answer} />
}

function QuestionForm({ question, answer }: { question: Question; answer: (answer: Answer | null) => void }) {
  const [reason, setReason] = useState("")
  const needsReason = question.reason !== undefined
  const ready = !needsReason || reason.trim() !== ""
  const go = (choice: Answer["choice"]) => {
    if (ready) answer({ choice, reason: reason.trim() })
  }
  // After the shell has taken the focus, and noted where it was, the reason takes it.
  const reasonInput = useRef<HTMLInputElement>(null)
  useEffect(() => reasonInput.current?.focus(), [])
  return (
    <ModalShell ariaLabel={question.title} onClose={() => answer(null)} width="w-[420px]" testId="question">
      <form
        onSubmit={(event) => {
          event.preventDefault()
          go("confirm")
        }}
      >
        <ModalFormHeader title={question.title}>{question.text}</ModalFormHeader>
        {question.list !== undefined && question.list.length > 0 && (
          <ul className="max-h-40 overflow-y-auto px-4 pt-2 text-[12px]" data-testid="question-list">
            {question.list.map((name) => (
              <li key={name} className="truncate">
                {name}
              </li>
            ))}
          </ul>
        )}
        {needsReason && (
          <div className="flex flex-col gap-1 px-4 pt-3">
            <label className="text-[12px]" style={{ color: "var(--text-secondary)" }} htmlFor="question-reason">
              {question.reason?.label}
            </label>
            <ModalTextInput ref={reasonInput} id="question-reason" value={reason} onChange={(event) => setReason(event.target.value)} maxLength={1000} />
          </div>
        )}
        <div className="flex justify-end gap-2 p-4">
          <button type="button" data-testid="question-cancel" className={BUTTON} style={{ color: "var(--text-secondary)" }} onClick={() => answer(null)}>
            Cancel
          </button>
          {question.other !== undefined && (
            <button
              type="button"
              data-testid="question-other"
              className={BUTTON}
              style={{ color: "var(--text-primary)", border: "1px solid var(--border)" }}
              disabled={!ready}
              onClick={() => go("other")}
            >
              {question.other}
            </button>
          )}
          <button
            type="submit"
            data-testid="question-confirm"
            className={BUTTON}
            style={{ background: question.danger === true ? "var(--danger)" : "var(--structure-action)", color: "var(--text-on-accent)" }}
            disabled={!ready}
          >
            {question.confirm}
          </button>
        </div>
      </form>
    </ModalShell>
  )
}
