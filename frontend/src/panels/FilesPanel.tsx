import { useEffect, useRef, useState, type DragEvent } from "react"
import { ChevronRight, Download, File as FileIcon, Folder, FolderGit2, FolderOpen, RefreshCw, Trash2, Upload, X } from "lucide-react"
import PanelShell from "./PanelShell"
import ModalShell from "../components/ModalShell"
import { ModalFormHeader } from "../components/ModalForm"
import { deleteProjectFile, listProjectFiles, projectFileDownloadUrl, uploadProjectFile } from "../api/projectFiles"
import { apiErrorMessage } from "../api/errors"
import type { ProjectFileEntry, ProjectFilePlace, ProjectFolderResponse } from "../api/types"
import { reportSaveCapture } from "../stores/saveCapture"
import useNodeDataStore from "../stores/useNodeDataStore"
import useSettingsStore from "../stores/useSettingsStore"
import useToastStore from "../stores/useToastStore"
import { formatBytes } from "../utils/formatBytes"
import { formatRelativeTime } from "../utils/formatTime"

const TITLES: Record<ProjectFilePlace, string> = { pipeline: "Pipeline files", local: "Local files" }
/** The one local folder that takes files; outputs/ only downloads. */
const LOCAL_WRITABLE_FOLDER = "data"

const MUTED = { color: "var(--text-muted)" }
const ICON_BUTTON = "p-1 rounded shrink-0 transition-colors disabled:opacity-40 hover:bg-[var(--bg-hover)]"

interface UploadRow {
  id: number
  name: string
  loaded: number
  total: number
  abort: () => void
}

type FileStatus = ProjectFileEntry["status"]

type Confirmation =
  | { kind: "replace"; folder: string; files: File[]; revisions: Map<string, string>; statuses: Map<string, FileStatus> }
  | { kind: "delete"; entry: ProjectFileEntry }

function joinPath(folder: string, name: string): string {
  return folder ? `${folder}/${name}` : name
}

/** Whether files can be added to *folder* (relative to the place) and deleted from it. */
function folderTakesFiles(place: ProjectFilePlace, folder: string): boolean {
  if (place === "pipeline") return true
  return folder === LOCAL_WRITABLE_FOLDER || folder.startsWith(`${LOCAL_WRITABLE_FOLDER}/`)
}

/** Where a drop on the open folder lands: Local files' root sends it to data/. */
function dropFolder(place: ProjectFilePlace, path: string): string {
  return place === "local" && path === "" ? LOCAL_WRITABLE_FOLDER : path
}

/**
 * What deleting or replacing one file means for what goes. Only a pipeline file with no
 * status is in version history exactly as it is.
 */
function fateOf(place: ProjectFilePlace, status: FileStatus, what: string): string {
  if (place === "local") return `${what} can't be recovered.`
  if (status === null) return `${what} stays in version history.`
  if (status === "changed") return `${what} has changes that aren't in version history yet, so they can't be recovered.`
  if (status === "ignored") return `${what} isn't in version history, so it can't be recovered.`
  return `${what} isn't in version history yet, so it can't be recovered.`
}

/** The same, for several replaced files, *lost* of them not in version history as they are. */
function fateOfMany(place: ProjectFilePlace, count: number, lost: number): string {
  if (place === "local") return "The files they replace can't be recovered."
  if (lost === 0) return "The versions they replace stay in version history."
  if (lost === count) return "None of them is in version history as it is now, so what they hold can't be recovered."
  return lost === 1
    ? "One of them isn't in version history as it is now, so what it holds can't be recovered."
    : `${lost} of them aren't in version history as they are now, so what they hold can't be recovered.`
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError"
}

function carriesFiles(event: DragEvent): boolean {
  return Array.from(event.dataTransfer.types).includes("Files")
}

/** The dropped files, and the names of any folders, which are not added. */
function droppedFiles(event: DragEvent): { files: File[]; folders: string[] } {
  const files: File[] = []
  const folders: string[] = []
  const items = Array.from(event.dataTransfer.items ?? [])
  if (items.length === 0) return { files: Array.from(event.dataTransfer.files), folders }
  for (const item of items) {
    if (item.kind !== "file") continue
    const entry = item.webkitGetAsEntry?.() ?? null
    if (entry?.isDirectory) {
      folders.push(entry.name)
      continue
    }
    const file = item.getAsFile()
    if (file) files.push(file)
  }
  return { files, folders }
}

interface FilesPanelProps {
  place: ProjectFilePlace
  onClose: () => void
}

/**
 * The Files panel (specs/frontend-shared): one folder of a place at a time, under a
 * breadcrumb. Files are added by dropping them on the list or a folder row, or through
 * Upload, one at a time; each file downloads, and is deleted where files can be added.
 */
export default function FilesPanel({ place, onClose }: FilesPanelProps) {
  const [path, setPath] = useState("")
  const [listing, setListing] = useState<ProjectFolderResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)
  // The listing request last answered; the folder is loading until it is this one.
  const request = `${place}|${path}|${nonce}`
  const [answered, setAnswered] = useState<string | null>(null)
  const loading = answered !== request
  const [uploads, setUploads] = useState<UploadRow[]>([])
  const [dropTarget, setDropTarget] = useState<string | null>(null)
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const nextUpload = useRef(0)
  const addToast = useToastStore((s) => s.addToast)
  const refresh = () => setNonce((n) => n + 1)

  useEffect(() => {
    const controller = new AbortController()
    listProjectFiles(place, path, { signal: controller.signal })
      .then((folder) => {
        setListing(folder)
        setError(null)
        setAnswered(request)
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return
        setListing(null)
        setError(apiErrorMessage(caught, "The folder could not be read."))
        setAnswered(request)
      })
    return () => controller.abort()
  }, [place, path, request])

  const root = listing?.root ?? ""
  const shownFolder = (folder: string) => (folder ? joinPath(root, folder) : root) || "the project folder"
  // The open folder's own listing says whether it takes files (a pipeline folder
  // .gitignore ignores does not); any other folder goes by its place.
  const takesFiles = (folder: string) =>
    folder === path && listing !== null ? listing.writable : folderTakesFiles(place, folder)
  const refusal = (folder: string) =>
    place === "pipeline"
      ? `${shownFolder(folder)} is ignored by .gitignore, so files added there couldn't be versioned.`
      : `${shownFolder(folder)} only downloads: files can't be added to it.`

  async function uploadAll(folder: string, files: File[], revisions: Map<string, string>) {
    let replaced = false
    for (const file of files) {
      const id = nextUpload.current++
      const job = uploadProjectFile(place, joinPath(folder, file.name), file, {
        replaceRevision: revisions.get(file.name) ?? null,
        onProgress: (loaded, total) => setUploads((rows) => rows.map((row) => (row.id === id ? { ...row, loaded, total } : row))),
      })
      setUploads((rows) => [...rows, { id, name: file.name, loaded: 0, total: file.size, abort: job.abort }])
      try {
        const written = await job.promise
        if (place === "pipeline") reportSaveCapture(written)
        replaced ||= written.replaced
      } catch (caught) {
        if (!isAbort(caught)) addToast("error", `${file.name}: ${apiErrorMessage(caught, "The file was not added.")}`)
      } finally {
        setUploads((rows) => rows.filter((row) => row.id !== id))
      }
    }
    useSettingsStore.getState().clearFileListCache()
    // A displayed preview may have read the file just replaced.
    if (replaced) useNodeDataStore.getState().bumpEpoch()
    refresh()
  }

  async function addFiles(folder: string, files: File[]) {
    if (files.length === 0) return
    if (!takesFiles(folder)) {
      addToast("error", refusal(folder))
      return
    }
    let entries = folder === path && listing ? listing.entries : null
    if (entries === null) {
      try {
        entries = (await listProjectFiles(place, folder)).entries
      } catch (caught) {
        addToast("error", apiErrorMessage(caught, "The folder could not be read."))
        return
      }
    }
    const revisions = new Map<string, string>()
    // How each file stands against version history: whether a replace keeps what it replaces.
    const statuses = new Map<string, FileStatus>()
    for (const entry of entries) {
      if (entry.kind !== "file" || entry.revision === null) continue
      revisions.set(entry.name, entry.revision)
      statuses.set(entry.name, entry.status)
    }
    if (files.some((file) => revisions.has(file.name))) {
      setConfirmation({ kind: "replace", folder, files, revisions, statuses })
      return
    }
    await uploadAll(folder, files, new Map())
  }

  function onDrop(event: DragEvent, folder: string) {
    if (!carriesFiles(event)) return
    event.preventDefault()
    event.stopPropagation()
    setDropTarget(null)
    const { files, folders } = droppedFiles(event)
    for (const name of folders) addToast("warning", `${name} is a folder: drop the files inside it instead.`)
    void addFiles(folder, files)
  }

  /** *folder* is where the drop would land; *highlight* the row (or list) outlined meanwhile. */
  function onDragOver(event: DragEvent, folder: string, highlight: string = folder) {
    if (!carriesFiles(event)) return
    event.preventDefault()
    event.stopPropagation()
    event.dataTransfer.dropEffect = takesFiles(folder) ? "copy" : "none"
    setDropTarget(highlight)
  }

  async function deleteFile(entry: ProjectFileEntry) {
    try {
      const removed = await deleteProjectFile(place, entry.path, entry.revision ?? "")
      if (place === "pipeline") reportSaveCapture(removed)
      useSettingsStore.getState().clearFileListCache()
    } catch (caught) {
      addToast("error", `${entry.name}: ${apiErrorMessage(caught, "The file was not deleted.")}`)
    }
    refresh()
  }

  const crumbs = path ? path.split("/") : []
  const openFolder = dropFolder(place, path)
  const writable = listing?.writable ?? false
  const now = new Date()

  return (
    <PanelShell
      testId="files-panel"
      title={
        <span className="flex items-center gap-2">
          <span className="side-panel-title">{TITLES[place]}</span>
          {place === "local" && (
            <span className="rounded px-1 text-[10px] font-medium" style={{ ...MUTED, border: "1px solid var(--border)" }}>
              Not versioned
            </span>
          )}
        </span>
      }
      onClose={onClose}
      maxWidth={640}
      icon={place === "pipeline" ? <FolderGit2 size={14} style={{ color: "var(--success)" }} /> : <FolderOpen size={14} style={MUTED} />}
      subtitle={
        <nav aria-label="Folder" className="flex min-w-0 items-center gap-0.5 text-[11px]" style={MUTED}>
          <button type="button" className="truncate hover:underline" onClick={() => setPath("")} data-testid="files-crumb-root">
            {place === "pipeline" ? root || "files" : "project"}
          </button>
          {crumbs.map((part, index) => (
            <span key={crumbs.slice(0, index + 1).join("/")} className="flex min-w-0 items-center gap-0.5">
              <ChevronRight size={10} aria-hidden="true" />
              <button type="button" className="truncate hover:underline" onClick={() => setPath(crumbs.slice(0, index + 1).join("/"))}>
                {part}
              </button>
            </span>
          ))}
        </nav>
      }
      actions={
        <>
          <input
            ref={inputRef}
            type="file"
            multiple
            hidden
            data-testid="files-upload-input"
            onChange={(event) => {
              const picked = Array.from(event.target.files ?? [])
              event.target.value = ""
              void addFiles(openFolder, picked)
            }}
          />
          <button
            type="button"
            data-testid="files-upload"
            onClick={() => inputRef.current?.click()}
            disabled={listing === null || !takesFiles(openFolder)}
            className={ICON_BUTTON}
            style={MUTED}
            title="Upload"
            aria-label="Upload"
          >
            <Upload size={12} />
          </button>
          <button type="button" data-testid="files-refresh" onClick={refresh} disabled={loading} className={ICON_BUTTON} style={MUTED} title="Refresh" aria-label="Refresh">
            <RefreshCw size={12} className={loading ? "animate-spin" : undefined} />
          </button>
        </>
      }
    >
      <div
        data-testid="files-drop-zone"
        className="relative flex-1 min-h-0 overflow-y-auto py-1"
        style={dropTarget === path ? { outline: "1px solid var(--accent)", outlineOffset: -2 } : undefined}
        onDragOver={(event) => onDragOver(event, openFolder, path)}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDropTarget(null)
        }}
        onDrop={(event) => onDrop(event, openFolder)}
      >
        {uploads.map((row) => (
          <div key={row.id} className="flex h-6 items-center gap-1.5 px-3 text-[12px]" data-testid="files-upload-row">
            <Upload size={13} aria-hidden="true" style={MUTED} />
            <span className="min-w-0 flex-1 truncate">{row.name}</span>
            <span className="h-1 w-20 overflow-hidden rounded" style={{ background: "var(--border)" }}>
              <span className="block h-full" style={{ width: `${row.total ? Math.round((row.loaded / row.total) * 100) : 0}%`, background: "var(--accent)" }} />
            </span>
            <button type="button" className={ICON_BUTTON} style={MUTED} onClick={row.abort} title="Cancel" aria-label={`Cancel ${row.name}`}>
              <X size={12} />
            </button>
          </div>
        ))}
        {error !== null ? (
          <div role="alert" className="flex items-center gap-2 px-3 py-2 text-[12px]" style={{ color: "var(--danger)" }}>
            <span className="flex-1">{error}</span>
            <button type="button" onClick={refresh} className="underline">Try again</button>
          </div>
        ) : listing !== null && listing.entries.length === 0 && uploads.length === 0 ? (
          <div className="px-3 py-2 text-[12px]" style={MUTED}>Empty folder</div>
        ) : (
          <ul aria-label={TITLES[place]}>
            {listing?.entries.map((entry) =>
              entry.kind === "folder" ? (
                <li
                  key={entry.path}
                  className="group flex h-6 items-center px-3 text-[12px] hover:bg-[var(--chrome-hover)]"
                  style={dropTarget === entry.path ? { outline: "1px solid var(--accent)", outlineOffset: -1 } : undefined}
                  onDragOver={(event) => onDragOver(event, entry.path)}
                  onDrop={(event) => onDrop(event, entry.path)}
                >
                  <button type="button" className="flex min-w-0 flex-1 items-center gap-1.5 text-left" onClick={() => setPath(entry.path)}>
                    <Folder size={13} aria-hidden="true" style={MUTED} />
                    <span className="truncate">{entry.name}</span>
                    {entry.status === "ignored" && <Marker label="Ignored" title="Ignored by .gitignore, so not versioned" />}
                  </button>
                </li>
              ) : (
                <li key={entry.path} className="group flex h-6 items-center gap-1.5 px-3 text-[12px] hover:bg-[var(--chrome-hover)]">
                  <FileIcon size={13} aria-hidden="true" style={MUTED} />
                  <span className="min-w-0 flex-1 truncate" title={joinPath(root, entry.path)}>{entry.name}</span>
                  {entry.status === "new" && <Marker label="New" title="Not in version history yet: Commit adds it" />}
                  {entry.status === "changed" && <Marker label="Changed" title="Changed since it went into version history: Commit records it" />}
                  {entry.status === "ignored" && <Marker label="Ignored" title="Ignored by .gitignore, so not versioned" />}
                  <span className="w-16 shrink-0 text-right text-[11px] tabular-nums" style={MUTED}>
                    {entry.size !== null ? formatBytes(entry.size) : ""}
                  </span>
                  <span
                    className="w-20 shrink-0 truncate text-right text-[11px]"
                    style={MUTED}
                    title={entry.modified !== null ? new Date(entry.modified * 1000).toLocaleString() : undefined}
                  >
                    {entry.modified !== null ? formatRelativeTime(entry.modified, now) : ""}
                  </span>
                  <span className="flex shrink-0 items-center opacity-0 group-hover:opacity-100 group-focus-within:opacity-100">
                    <a
                      href={projectFileDownloadUrl(place, entry.path)}
                      download={entry.name}
                      className={ICON_BUTTON}
                      style={MUTED}
                      title="Download"
                      aria-label={`Download ${entry.name}`}
                    >
                      <Download size={12} />
                    </a>
                    {writable && (
                      <button
                        type="button"
                        className={ICON_BUTTON}
                        style={MUTED}
                        onClick={() => setConfirmation({ kind: "delete", entry })}
                        title="Delete"
                        aria-label={`Delete ${entry.name}`}
                      >
                        <Trash2 size={12} />
                      </button>
                    )}
                  </span>
                </li>
              ),
            )}
          </ul>
        )}
      </div>
      {confirmation !== null && (
        <ConfirmDialog
          place={place}
          confirmation={confirmation}
          shownFolder={shownFolder}
          onCancel={() => setConfirmation(null)}
          onReplace={(filesToSend, revisions) => {
            setConfirmation(null)
            void uploadAll(confirmation.kind === "replace" ? confirmation.folder : path, filesToSend, revisions)
          }}
          onDelete={(entry) => {
            setConfirmation(null)
            void deleteFile(entry)
          }}
        />
      )}
    </PanelShell>
  )
}

function Marker({ label, title }: { label: string; title: string }) {
  return (
    <span className="shrink-0 rounded px-1 text-[10px] font-medium" style={{ ...MUTED, border: "1px solid var(--border)" }} title={title}>
      {label}
    </span>
  )
}

const DIALOG_BUTTON = "px-3 py-1.5 text-[12px] font-medium rounded-md transition-colors"

function ConfirmDialog({
  place,
  confirmation,
  shownFolder,
  onCancel,
  onReplace,
  onDelete,
}: {
  place: ProjectFilePlace
  confirmation: Confirmation
  shownFolder: (folder: string) => string
  onCancel: () => void
  onReplace: (files: File[], revisions: Map<string, string>) => void
  onDelete: (entry: ProjectFileEntry) => void
}) {
  if (confirmation.kind === "delete") {
    return (
      <ModalShell ariaLabel={`Delete ${confirmation.entry.name}`} onClose={onCancel} testId="files-delete-dialog">
        <ModalFormHeader title={`Delete ${confirmation.entry.name}?`}>
          {fateOf(place, confirmation.entry.status, "It")}
        </ModalFormHeader>
        <div className="flex justify-end gap-2 p-4">
          <button type="button" className={DIALOG_BUTTON} style={{ color: "var(--text-secondary)" }} onClick={onCancel}>Cancel</button>
          <button
            type="button"
            data-testid="files-delete-confirm"
            className={DIALOG_BUTTON}
            style={{ background: "var(--danger)", color: "var(--text-on-accent)" }}
            onClick={() => onDelete(confirmation.entry)}
          >
            Delete
          </button>
        </div>
      </ModalShell>
    )
  }
  const { folder, files, revisions, statuses } = confirmation
  const clashing = files.filter((file) => revisions.has(file.name))
  const lost = clashing.filter((file) => (statuses.get(file.name) ?? null) !== null).length
  const fate = clashing.length === 1
    ? fateOf(place, statuses.get(clashing[0].name) ?? null, "The version it replaces")
    : fateOfMany(place, clashing.length, lost)
  const fresh = files.filter((file) => !revisions.has(file.name))
  const title = clashing.length === 1
    ? `Replace ${clashing[0].name} in ${shownFolder(folder)}?`
    : `Replace ${clashing.length} files in ${shownFolder(folder)}?`
  return (
    <ModalShell ariaLabel={title} onClose={onCancel} width="w-[420px]" testId="files-replace-dialog">
      <ModalFormHeader title={title}>
        {fate}
      </ModalFormHeader>
      {clashing.length > 1 && (
        <ul className="max-h-40 overflow-y-auto px-4 pt-2 text-[12px]">
          {clashing.map((file) => <li key={file.name} className="truncate">{file.name}</li>)}
        </ul>
      )}
      <div className="flex justify-end gap-2 p-4">
        <button type="button" className={DIALOG_BUTTON} style={{ color: "var(--text-secondary)" }} onClick={onCancel}>Cancel</button>
        {fresh.length > 0 && (
          <button type="button" data-testid="files-replace-skip" className={DIALOG_BUTTON} style={{ color: "var(--text-primary)", border: "1px solid var(--border)" }} onClick={() => onReplace(fresh, new Map())}>
            Skip these
          </button>
        )}
        <button
          type="button"
          data-testid="files-replace-confirm"
          className={DIALOG_BUTTON}
          style={{ background: "var(--structure-action)", color: "var(--text-on-accent)" }}
          onClick={() => onReplace(files, revisions)}
        >
          Replace
        </button>
      </div>
    </ModalShell>
  )
}
