import { lazy, Suspense } from "react"
import type { EditorView } from "@codemirror/view"

export type CodeEditorProps = {
  defaultValue: string
  onChange: (value: string) => void
  placeholder?: string
  errorLine?: number | null
  /** Column names for in-string autocomplete */
  availableColumns?: string[]
  /** Callback to expose the EditorView for external operations (e.g. text insertion) */
  onEditorView?: (view: EditorView | null) => void
}

const LazyCodeMirrorEditor = lazy(() => import("./CodeMirrorEditor"))

export function CodeEditor(props: CodeEditorProps) {
  return (
    <Suspense fallback={<div data-testid="code-editor-wrapper" className="flex-1 min-h-[120px]" />}>
      <LazyCodeMirrorEditor {...props} />
    </Suspense>
  )
}
