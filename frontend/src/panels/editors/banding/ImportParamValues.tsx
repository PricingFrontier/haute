import { useEffect, useRef, useState } from "react"
import { apiErrorMessage } from "../../../api/errors"
import { fetchParamFile } from "../../../api/workbench"
import { Select } from "../../../haute-ui"
import { useParamsFiles } from "../../../hooks/useParamsFiles"
import { latest } from "../../../utils/latest"

const LABEL_CLASS = "text-[11px] font-medium block mb-0.5"

/**
 * Categorical's Import from params (specs/frontend-node-editors): a CSV in the pipeline
 * folder's `files/params/`, then one of its columns, whose distinct values are handed to
 * `onImport`, those already ruled left out. A read answers only while it is the latest and
 * the panel is open, so one the user has moved on from changes nothing.
 */
export function ImportParamValues({
  existingValues,
  onImport,
  onClose,
}: {
  /** The values the factor's rules already have, which an import leaves out. */
  existingValues: readonly string[]
  /** The values to add a rule for, never empty. */
  onImport: (values: string[]) => void
  onClose: () => void
}) {
  const [file, setFile] = useState("")
  const [column, setColumn] = useState("")
  const [problem, setProblem] = useState<string | null>(null)
  const [nothingNew, setNothingNew] = useState(false)
  const { files, unlisted, retry, header } = useParamsFiles(file, setProblem)

  // The values ruled when a read answers, and the editor's handler then, rather than when it was asked.
  const existingRef = useRef(existingValues)
  const onImportRef = useRef(onImport)
  useEffect(() => {
    existingRef.current = existingValues
    onImportRef.current = onImport
  }, [existingValues, onImport])
  const mounted = useRef(false)
  const [reads] = useState(latest)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  const chooseFile = (chosen: string) => {
    // A column of the file left still being read no longer answers.
    reads.bump()
    setFile(chosen)
    setColumn("")
    setProblem(null)
    setNothingNew(false)
  }
  const chooseColumn = (chosen: string) => {
    setColumn(chosen)
    setProblem(null)
    setNothingNew(false)
    if (chosen === "") {
      reads.bump()
      return
    }
    const ticket = reads.take()
    void fetchParamFile(file, chosen)
      .then(({ values }) => {
        if (!mounted.current || !reads.is(ticket)) return
        const ruled = new Set(existingRef.current)
        const fresh = values.filter((value) => !ruled.has(value))
        if (fresh.length === 0) setNothingNew(true)
        else onImportRef.current(fresh)
      })
      .catch((error: unknown) => {
        if (mounted.current && reads.is(ticket)) setProblem(apiErrorMessage(error))
      })
  }

  return (
    <div
      role="dialog"
      aria-label="Import from params"
      className="rounded-lg p-3 space-y-2.5"
      style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)" }}
    >
      <div className="text-xs font-semibold" style={{ color: "var(--text-secondary)" }}>
        Import from params
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label htmlFor="banding-import-file" className={LABEL_CLASS} style={{ color: "var(--text-muted)" }}>
            File
          </label>
          <Select
            id="banding-import-file"
            value={file}
            onChange={(e) => chooseFile(e.target.value)}
            disabled={!files?.length}
            className="w-full font-mono"
          >
            <option value="" />
            {(files ?? []).map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <label htmlFor="banding-import-column" className={LABEL_CLASS} style={{ color: "var(--text-muted)" }}>
            Column
          </label>
          <Select
            id="banding-import-column"
            value={column}
            onChange={(e) => chooseColumn(e.target.value)}
            disabled={header === undefined}
            className="w-full font-mono"
          >
            <option value="" />
            {(header ?? []).map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </Select>
        </div>
      </div>

      {files?.length === 0 && (
        <div className="text-[11px]" style={{ color: "var(--text-muted)" }}>
          The params folder has no CSV files.
        </div>
      )}
      {nothingNew && (
        <div className="text-[11px]" style={{ color: "var(--text-muted)" }}>
          Every value in {column} already has a rule.
        </div>
      )}
      {problem !== null && (
        <p role="alert" className="text-[11px] font-medium" style={{ color: "var(--danger)" }}>
          {problem}
        </p>
      )}
      {unlisted !== null && (
        <p role="alert" className="flex items-center gap-2 text-[11px] font-medium" style={{ color: "var(--danger)" }}>
          <span className="flex-1">{unlisted}</span>
          <button
            type="button"
            className="underline"
            onClick={retry}
          >
            Retry
          </button>
        </p>
      )}

      <div className="flex items-center justify-end">
        <button
          type="button"
          onClick={onClose}
          className="px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors"
          style={{
            background: "var(--bg-panel)",
            border: "1px solid var(--border)",
            color: "var(--text-secondary)",
          }}
        >
          Cancel
        </button>
      </div>
    </div>
  )
}
