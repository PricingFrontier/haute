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
import useQuestionStore from "../stores/useQuestionStore"

export async function clearingTheWay<T>(
  operation: (...settled: [] | [ReplacingFilesOptions]) => Promise<T>,
): Promise<T> {
  try {
    // Asked first exactly as the caller would ask; again, with the choice, only once made.
    return await operation()
  } catch (err) {
    const files = unrecordedFiles(err)
    if (files === null) throw err
    const detail = apiErrorRawDetail(err)
    const choice = await useQuestionStore.getState().ask({
      title: "Files changed outside Haute",
      text: typeof detail?.message === "string" ? detail.message : undefined,
      list: files,
      confirm: "Put back",
      danger: true,
      other: "Record",
    })
    if (choice === null) throw err
    return operation({ settle: { choice: choice.choice === "other" ? "record" : "put-back", files } })
  }
}

/** The files a refusal over files changed outside Haute names; null for any other error. */
function unrecordedFiles(err: unknown): string[] | null {
  if (apiErrorCode(err) !== "unrecorded_files") return null
  const files = apiErrorRawDetail(err)?.files
  if (!Array.isArray(files)) return null
  const named = files.filter((file): file is string => typeof file === "string")
  return named.length > 0 ? named : null
}
