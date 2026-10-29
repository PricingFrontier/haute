import { useEffect, useRef, useState, type DragEvent } from "react"
import { ChevronRight, Download, File as FileIcon, Folder, FolderGit2, FolderOpen, RefreshCw, Trash2, Upload, X } from "lucide-react"
import PanelShell from "./PanelShell"
import { deleteProjectFile, listProjectFiles, projectFileDownloadUrl, uploadProjectFile } from "../api/projectFiles"
import { apiErrorMessage } from "../api/errors"
import type { ProjectFileEntry, ProjectFilePlace, ProjectFolderResponse } from "../api/types"
import { reportSaveCapture } from "../stores/saveCapture"
import useDataEpochStore from "../stores/useDataEpochStore"
import useNodeDataStore from "../stores/useNodeDataStore"
import useQuestionStore, { confirmed } from "../stores/useQuestionStore"
import useSettingsStore from "../stores/useSettingsStore"
import useToastStore from "../stores/useToastStore"
import { formatBytes } from "../utils/formatBytes"
import { relativeTime } from "../utils/relativeTime"

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

function joinPath(folder: string, name: string): string {
  return folder ? `${folder}/${name}` : name
}

/** The folder a place-relative *path* is in: "" for the place's root. */
function folderOf(path: string): string {
  const slash = path.lastIndexOf("/")
  return slash < 0 ? "" : path.slice(0, slash)
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
  // The listing of the folder shown: until it answers, another folder's rows, and what they
  // offer, are not shown, so nothing acts on a folder that is no longer open.
  const shown = listing !== null && listing.place === place && listing.path === path ? listing : null
  const shownFolder = (folder: string) => (folder ? joinPath(root, folder) : root) || "the project folder"
  // The open folder's own listing says whether it takes files (a pipeline folder
  // .gitignore ignores does not); any other folder goes by its place.
  const takesFiles = (folder: string) =>
    folder === path && shown !== null ? shown.writable : folderTakesFiles(place, folder)
  const refusal = (folder: string) =>
    place === "pipeline"
      ? `${shownFolder(folder)} is ignored by .gitignore, so files added there couldn't be versioned.`
      : `${shownFolder(folder)} only downloads: files can't be added to it.`

  async function uploadAll(folder: string, files: File[], revisions: Map<string, string>) {
    let replaced = false
    let wrote = false
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
        wrote = true
      } catch (caught) {
        if (!isAbort(caught)) addToast("error", `${file.name}: ${apiErrorMessage(caught, "The file was not added.")}`)
      } finally {
        setUploads((rows) => rows.filter((row) => row.id !== id))
      }
    }
    useSettingsStore.getState().clearFileListCache()
    // A displayed preview may have read the file just replaced.
    if (replaced) useNodeDataStore.getState().bumpEpoch()
    // What was priced on the data before is stale: a file the pipeline reads may be new.
    if (wrote) useDataEpochStore.getState().moved()
    refresh()
  }

  async function addFiles(folder: string, files: File[]) {
    if (files.length === 0) return
    if (!takesFiles(folder)) {
      addToast("error", refusal(folder))
      return
    }
    // A clash is checked against a listing of the folder written to, never another's.
    let entries = folder === path && shown !== null ? shown.entries : null
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
    const clashing = files.filter((file) => revisions.has(file.name))
    if (clashing.length === 0) {
      await uploadAll(folder, files, new Map())
      return
    }
    // Asked first, saying what the replace loses; the files that clash nothing may go alone.
    const lost = clashing.filter((file) => (statuses.get(file.name) ?? null) !== null).length
    const fresh = files.filter((file) => !revisions.has(file.name))
    const answer = await useQuestionStore.getState().ask({
      title: clashing.length === 1 ? `Replace ${clashing[0].name} in ${shownFolder(folder)}?` : `Replace ${clashing.length} files in ${shownFolder(folder)}?`,
      text: clashing.length === 1 ? fateOf(place, statuses.get(clashing[0].name) ?? null, "The version it replaces") : fateOfMany(place, clashing.length, lost),
      confirm: "Replace",
      ...(fresh.length > 0 ? { other: "Skip these" } : {}),
      ...(clashing.length > 1 ? { list: clashing.map((file) => file.name) } : {}),
    })
    if (answer === null) return
    if (answer.choice === "other") await uploadAll(folder, fresh, new Map())
    else await uploadAll(folder, files, revisions)
  }

  /** Delete a file once asked, saying whether it can be recovered. */
  async function askToDelete(entry: ProjectFileEntry) {
    const yes = await confirmed({
      title: `Delete ${entry.name} from ${shownFolder(folderOf(entry.path))}?`,
      text: fateOf(place, entry.status, "It"),
      confirm: "Delete",
      danger: true,
    })
    if (yes) await deleteFile(entry)
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
      useDataEpochStore.getState().moved()
    } catch (caught) {
      addToast("error", `${entry.name}: ${apiErrorMessage(caught, "The file was not deleted.")}`)
    }
    refresh()
  }

  const crumbs = path ? path.split("/") : []
  const openFolder = dropFolder(place, path)
  const writable = shown?.writable ?? false
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
            disabled={shown === null || !takesFiles(openFolder)}
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
        ) : shown === null ? (
          <div className="px-3 py-2 text-[12px]" style={MUTED} data-testid="files-loading">Loading…</div>
        ) : shown.entries.length === 0 && uploads.length === 0 ? (
          <div className="px-3 py-2 text-[12px]" style={MUTED}>Empty folder</div>
        ) : (
          <ul aria-label={TITLES[place]}>
            {shown.entries.map((entry) =>
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
                    {entry.modified !== null ? relativeTime(new Date(entry.modified * 1000), now) : ""}
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
                        onClick={() => void askToDelete(entry)}
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

