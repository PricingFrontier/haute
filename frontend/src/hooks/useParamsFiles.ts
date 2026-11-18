import { useEffect, useRef, useState } from "react"
import { apiErrorMessage } from "../api/errors"
import { listProjectFiles } from "../api/projectFiles"
import { fetchParamFile } from "../api/workbench"
import { ask, newest } from "../utils/answers"
import { useRequestKind } from "./useRequestKind"

/** Whether a file in the params folder is a CSV, whatever the case of its extension. */
const isCsv = (name: string): boolean => name.toLowerCase().endsWith(".csv")

/**
 * The CSV files in the pipeline folder's `files/params/` (specs/frontend-shared), listed as
 * the caller mounts and again by `retry`, and the header of `file` ("" for none), read while
 * it is not yet held and then kept, per file, while the caller is mounted. A header read
 * that fails hands its message to `onHeaderProblem` only while `file` is still the one it
 * was read for and its header is still unread, so a read the caller has moved on from says
 * nothing.
 */
export function useParamsFiles(file: string, onHeaderProblem: (message: string) => void) {
  // The CSV files' names; null while they are listed.
  const [files, setFiles] = useState<readonly string[] | null>(null)
  // Why the params folder could not be listed, and the listing asked for again by `retry`.
  const [unlisted, setUnlisted] = useState<string | null>(null)
  const [listing, setListing] = useState(0)
  // Each file's header as read, its columns to pick from.
  const [headers, setHeaders] = useState<Readonly<Record<string, readonly string[]>>>({})
  // The caller's handler when a read fails, rather than when it was asked.
  const onHeaderProblemRef = useRef(onHeaderProblem)
  useEffect(() => {
    onHeaderProblemRef.current = onHeaderProblem
  }, [onHeaderProblem])

  const listings = useRequestKind()
  useEffect(() => {
    const asked = ask([newest(listings)])
    void asked.answer(listProjectFiles("pipeline", "params"), {
      ok: (folder) => setFiles(folder.entries.filter((entry) => entry.kind === "file" && isCsv(entry.name)).map((entry) => entry.name)),
      failed: (error) => setUnlisted(apiErrorMessage(error, "The params files could not be listed.")),
    })
    return () => asked.release()
  }, [listing, listings])

  const header = file === "" ? undefined : headers[file]
  const headerReads = useRequestKind()
  useEffect(() => {
    if (file === "" || header !== undefined) return
    const asked = ask([newest(headerReads)])
    const keep = (columns: readonly string[]) => setHeaders((read) => ({ ...read, [file]: columns }))
    void asked.answer(fetchParamFile(file), {
      ok: ({ columns }) => keep(columns),
      failed: (error) => onHeaderProblemRef.current(apiErrorMessage(error)),
      // A file's header is that file's whichever file is chosen now: it is kept.
      stale: (settled) => {
        if ("value" in settled) keep(settled.value.columns)
      },
    })
    return () => asked.release()
  }, [file, header, headerReads])

  return {
    /** The CSV files' names; null until the folder is listed. */
    files,
    /** Why the folder could not be listed; null once `retry` asks again. */
    unlisted,
    retry: () => {
      setUnlisted(null)
      setListing((count) => count + 1)
    },
    /** `file`'s columns; undefined while they are read, or after a read that failed. */
    header,
    /** Keeps the header of a file that a read of its values brought back. */
    keepHeader: (read: string, columns: readonly string[]) => setHeaders((kept) => ({ ...kept, [read]: columns })),
  }
}
