/** A column's allowed values, read from a params file's column (specs/workbench). */
import { RefreshCw } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { apiErrorMessage } from "../../api/errors"
import { listProjectFiles } from "../../api/projectFiles"
import type { SchemaColumn } from "../../api/types"
import { fetchParamFile } from "../../api/workbench"
import { COMPACT_FIELD_CLASS, INPUT_STYLE } from "../../components/form"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import { latest } from "../../utils/latest"
import { IconButton } from "./IconButton"

/** Whether a file in the params folder is a CSV, whatever the case of its extension. */
const isCsv = (name: string): boolean => name.toLowerCase().endsWith(".csv")

/** The Allowed choice standing for values typed in, as an older form holds them: never a CSV's name. */
const TYPED_IN = ":typed"

/**
 * A column's allowed values: the distinct values of a column of a CSV in the pipeline's params
 * folder, its file chosen and then its column, copied in and read from the file again by
 * Refresh. The blank choice takes them away, with a row for each, values typed in as an
 * older form holds them included, which show as a choice of their own. A read answers only
 * while the column's source in the form is the one it began from, so one an undo overtakes
 * changes nothing. Why a file or a column cannot give values shows under them.
 */
export function AllowedField({ tableId, column, set }: { tableId: string; column: SchemaColumn; set: (patch: Partial<SchemaColumn>) => void }) {
  const source = column.options_source
  const typedIn = source === null && column.options.length > 0
  // The CSV files in the params folder, read as the details open.
  const [files, setFiles] = useState<readonly string[]>([])
  // Each file's header as read, its columns to pick from.
  const [headers, setHeaders] = useState<Readonly<Record<string, readonly string[]>>>({})
  // A file chosen whose column is still to be picked: the form changes once it is.
  const [pendingFile, setPendingFile] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  // Why the params folder could not be listed (a project with no pipeline folder says so),
  // and the listing asked for again by Retry.
  const [unlisted, setUnlisted] = useState<string | null>(null)
  const [listing, setListing] = useState(0)
  // A read answers while the details are open, and a read of values only while it is the latest.
  const mounted = useRef(false)
  const [reads] = useState(latest)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])
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
  const file = pendingFile ?? source?.file ?? ""
  const header = headers[file]
  useEffect(() => {
    if (file === "" || header !== undefined) return
    void fetchParamFile(file)
      .then(({ columns }) => {
        if (mounted.current) setHeaders((read) => ({ ...read, [file]: columns }))
      })
      .catch((error: unknown) => {
        if (mounted.current) setProblem(apiErrorMessage(error))
      })
  }, [file, header])

  // The column as the form holds it now, which an undo, a redo or a sync may have changed
  // while a read was under way.
  const columnNow = () =>
    useWorkbenchFormStore
      .getState()
      .form?.schema.tables.find((other) => other.id === tableId)
      ?.columns.find((other) => other.id === column.id)
  const sourceNow = () => JSON.stringify(columnNow()?.options_source ?? null)
  const readValues = (from: string, name: string, then: (values: string[]) => void) => {
    const ticket = reads.take()
    const began = sourceNow()
    // A read answers only while it is the latest and the column's source is the one it began from.
    const current = () => mounted.current && reads.is(ticket) && sourceNow() === began
    setProblem(null)
    void fetchParamFile(from, name)
      .then(({ columns, values }) => {
        if (!current()) return
        setHeaders((read) => ({ ...read, [from]: columns }))
        then(values)
      })
      .catch((error: unknown) => {
        if (current()) setProblem(apiErrorMessage(error))
      })
  }
  const chooseFile = (chosen: string) => {
    // A pick or a refresh still being read no longer answers.
    reads.bump()
    setProblem(null)
    if (chosen === TYPED_IN) setPendingFile(null)
    else if (chosen !== "") setPendingFile(chosen === source?.file ? null : chosen)
    else {
      setPendingFile(null)
      if (source !== null || column.options.length > 0) set({ options: [], options_source: null, row_per_value: false })
    }
  }
  const chooseColumn = (name: string) =>
    readValues(file, name, (values) => {
      setPendingFile(null)
      set({ options_source: { file, column: name }, options: values })
    })
  const refresh = () => {
    if (source === null) return
    readValues(source.file, source.column, (values) => {
      if (JSON.stringify(values) !== JSON.stringify(columnNow()?.options ?? column.options)) set({ options: values })
    })
  }

  const fileChoices = file === "" || files.includes(file) ? files : [...files, file]
  // The column the values came from stays offered when the file no longer has it.
  const columnChoices =
    pendingFile === null && source !== null && !(header ?? []).includes(source.column) ? [...(header ?? []), source.column] : (header ?? [])
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <div className="flex min-w-0 items-center gap-1.5">
        <select
          aria-label={`Where the allowed values of ${column.name} come from`}
          value={typedIn && pendingFile === null ? TYPED_IN : file}
          onChange={(e) => chooseFile(e.target.value)}
          disabled={fileChoices.length === 0 && !typedIn}
          className={`${COMPACT_FIELD_CLASS} ${file === "" ? "flex-1" : "max-w-[50%]"}`}
          style={INPUT_STYLE}
        >
          <option value="" />
          {typedIn && (
            <option value={TYPED_IN}>
              {column.options.length} {column.options.length === 1 ? "value" : "values"} typed in
            </option>
          )}
          {fileChoices.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
        {file !== "" && (
          <>
            <select
              aria-label={`Column the allowed values of ${column.name} come from`}
              value={pendingFile === null ? (source?.column ?? "") : ""}
              onChange={(e) => chooseColumn(e.target.value)}
              className={`${COMPACT_FIELD_CLASS} min-w-0 flex-1`}
              style={INPUT_STYLE}
            >
              {pendingFile !== null && <option value="" />}
              {columnChoices.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
            {pendingFile === null && (
              <IconButton
                icon={RefreshCw}
                onClick={refresh}
                aria-label={`Read the allowed values of ${column.name} again`}
                title={`Read the values from ${file} again`}
                style={{ color: "var(--text-muted)" }}
              />
            )}
          </>
        )}
      </div>
      {problem !== null && (
        <p role="alert" className="text-[11px]" style={{ color: "var(--danger)" }}>
          {problem}
        </p>
      )}
      {unlisted !== null && (
        <p role="alert" className="flex items-center gap-2 text-[11px]" style={{ color: "var(--danger)" }} data-testid="params-unlisted">
          <span className="flex-1">{unlisted}</span>
          <button
            type="button"
            className="underline"
            onClick={() => {
              setUnlisted(null)
              setListing((count) => count + 1)
            }}
          >
            Retry
          </button>
        </p>
      )}
    </div>
  )
}
