import { useEffect, useRef, useState } from "react"
import { apiErrorMessage } from "../api/errors"
import { listProjectFiles } from "../api/projectFiles"
import { fetchParamFile } from "../api/workbench"

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

  useEffect(() => {
    let current = true
    void listProjectFiles("pipeline", "params")
      .then((folder) => {
        if (current) setFiles(folder.entries.filter((entry) => entry.kind === "file" && isCsv(entry.name)).map((entry) => entry.name))
      })
      .catch((error: unknown) => {
        if (current) setUnlisted(apiErrorMessage(error, "The params files could not be listed."))
      })
    return () => {
      current = false
    }
  }, [listing])

  const header = file === "" ? undefined : headers[file]
  useEffect(() => {
    if (file === "" || header !== undefined) return
    let current = true
    void fetchParamFile(file)
      .then(({ columns }) => setHeaders((read) => ({ ...read, [file]: columns })))
      .catch((error: unknown) => {
        if (current) onHeaderProblemRef.current(apiErrorMessage(error))
      })
    return () => {
      current = false
    }
  }, [file, header])

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
