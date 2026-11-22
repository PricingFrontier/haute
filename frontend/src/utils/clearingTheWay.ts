/**
 * The one answer to an operation that replaces the project's files refused over files
 * changed outside Haute (specs/frontend-git-ui): a switch, opening a new branch, Catch up,
 * Spin off a copy, an archive. The server names the files (409 `unrecorded_files`); the
 * page's question asks whether to record them as a save or put them back, and the
 * operation is asked for again with that choice, which the server settles first under the
 * same lock. Cancel leaves the refusal standing, for the caller to report as any other.
 */
import type { ReplacingFilesOptions } from "../api/client"
import { apiErrorCode, apiErrorRawDetail } from "../api/errors"
import { documentPart } from "../stores/useDocumentStatusStore"
import useQuestionStore from "../stores/useQuestionStore"
import { answer, type Settled } from "./answers"

export async function clearingTheWay<T>(
  operation: (...settled: [] | [ReplacingFilesOptions]) => Promise<T>,
): Promise<T> {
  // How the operation, asked again or not, came out: the caller's own answer writes from it.
  let outcome: Settled<T> | null = null
  const settle = (settled: Settled<T>) => {
    outcome = settled
  }
  await answer([documentPart()], operation(), {
    ok: (value) => settle({ value }),
    failed: (error, asked) => {
      const files = unrecordedFiles(error)
      if (files === null) return settle({ error })
      const detail = apiErrorRawDetail(error)
      // What the files were when the question was asked: the server settles none that changed since.
      const token = typeof detail?.token === "string" ? detail.token : ""
      return asked.answer(useQuestionStore.getState().ask({
        title: "Files changed outside Haute",
        text: typeof detail?.message === "string" ? detail.message : undefined,
        list: files,
        confirm: "Put back",
        danger: true,
        other: "Record",
      }), {
        ok: (choice) => {
          if (choice === null) return settle({ error })
          // Asked first exactly as the caller would ask; again, with the choice, only once made.
          const settled = { settle: { choice: choice.choice === "other" ? "record" as const : "put-back" as const, files, token } }
          return asked.answer(operation(settled), {
            ok: (value) => settle({ value }),
            failed: (again) => settle({ error: again }),
            stale: settle,
          })
        },
        stale: () => settle({ error }),
      })
    },
    stale: settle,
  })
  const done = outcome as Settled<T> | null
  if (done === null) throw new Error("The operation came to no answer.")
  if ("error" in done) throw done.error
  return done.value
}

/** The files a refusal over files changed outside Haute names; null for any other error. */
function unrecordedFiles(err: unknown): string[] | null {
  if (apiErrorCode(err) !== "unrecorded_files") return null
  const files = apiErrorRawDetail(err)?.files
  if (!Array.isArray(files)) return null
  const named = files.filter((file): file is string => typeof file === "string")
  return named.length > 0 ? named : null
}
