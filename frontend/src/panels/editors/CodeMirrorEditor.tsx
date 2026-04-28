import { useCallback, useEffect, useRef } from "react"
import { EditorView, placeholder as cmPlaceholder, keymap, lineNumbers, highlightActiveLine, highlightActiveLineGutter, drawSelection, rectangularSelection } from "@codemirror/view"
import { EditorState, Compartment, Annotation } from "@codemirror/state"
import { python } from "@codemirror/lang-python"
import { syntaxHighlighting, indentOnInput, bracketMatching, foldGutter, foldKeymap, HighlightStyle, indentUnit, syntaxTree } from "@codemirror/language"
import { defaultKeymap, indentWithTab, history, historyKeymap } from "@codemirror/commands"
import { closeBrackets, closeBracketsKeymap, autocompletion, completionKeymap, type CompletionContext, type CompletionResult } from "@codemirror/autocomplete"
import { searchKeymap, highlightSelectionMatches } from "@codemirror/search"
import { lintGutter, setDiagnostics } from "@codemirror/lint"
import { tags } from "@lezer/highlight"
import { SYNTAX_COLORS } from "../../theme/colors"

const LOCAL_CHANGE_DEBOUNCE_MS = 150

// ─── CodeEditor (CodeMirror 6) ────────────────────────────────────

// Dark theme matching Haute's CSS variables
const hauteTheme = EditorView.theme({
  "&": {
    backgroundColor: "var(--bg-input)",
    color: "var(--text-primary)",
    fontSize: "12px",
    fontFamily: "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, monospace",
    flex: "1",
    minHeight: "120px",
  },
  "&.cm-focused": {
    outline: "none",
  },
  ".cm-scroller": {
    fontFamily: "inherit",
    lineHeight: "1.625",
    overflow: "auto",
  },
  ".cm-content": {
    caretColor: "var(--accent)",
    padding: "10px 0",
  },
  ".cm-line": {
    padding: "0 12px 0 4px",
  },
  ".cm-gutters": {
    backgroundColor: "var(--bg-elevated)",
    color: "var(--text-muted)",
    border: "none",
    borderRight: "1px solid var(--border)",
  },
  ".cm-gutter.cm-lineNumbers .cm-gutterElement": {
    padding: "0 8px 0 4px",
    minWidth: "28px",
  },
  ".cm-activeLineGutter": {
    backgroundColor: "transparent",
    color: "var(--text-secondary)",
  },
  ".cm-activeLine": {
    backgroundColor: "rgba(255,255,255,.03)",
  },
  ".cm-selectionBackground": {
    backgroundColor: "var(--accent-selection) !important",
  },
  "&.cm-focused .cm-selectionBackground": {
    backgroundColor: "var(--accent-ring) !important",
  },
  ".cm-cursor": {
    borderLeftColor: "var(--accent)",
  },
  ".cm-matchingBracket": {
    backgroundColor: "var(--accent-selection)",
    outline: "1px solid var(--accent-outline)",
  },
  ".cm-selectionMatch": {
    backgroundColor: "var(--accent-soft-strong)",
  },
  ".cm-searchMatch": {
    backgroundColor: "var(--warning-search)",
    outline: "1px solid var(--warning-search-outline)",
  },
  ".cm-searchMatch.cm-searchMatch-selected": {
    backgroundColor: "var(--warning-search-selected)",
  },
  ".cm-foldGutter .cm-gutterElement": {
    color: "var(--text-muted)",
    padding: "0 4px",
  },
  ".cm-tooltip": {
    backgroundColor: "var(--bg-elevated)",
    border: "1px solid var(--border)",
    color: "var(--text-primary)",
  },
  ".cm-tooltip.cm-tooltip-autocomplete > ul > li": {
    padding: "4px 8px",
  },
  ".cm-tooltip.cm-tooltip-autocomplete > ul > li[aria-selected]": {
    backgroundColor: "var(--accent-soft)",
    color: "var(--text-primary)",
  },
  ".cm-panels": {
    backgroundColor: "var(--bg-elevated)",
    color: "var(--text-primary)",
    borderTop: "1px solid var(--border)",
  },
  ".cm-panels.cm-panels-bottom": {
    borderTop: "1px solid var(--border)",
  },
  ".cm-panel input": {
    backgroundColor: "var(--bg-input)",
    color: "var(--text-primary)",
    border: "1px solid var(--border)",
    borderRadius: "4px",
    padding: "2px 6px",
    fontSize: "12px",
  },
  ".cm-panel button": {
    backgroundColor: "var(--bg-hover)",
    color: "var(--text-secondary)",
    border: "1px solid var(--border)",
    borderRadius: "4px",
    padding: "2px 8px",
    fontSize: "12px",
  },
  ".cm-placeholder": {
    color: "var(--text-muted)",
    fontStyle: "italic",
  },
  // Lint diagnostics
  ".cm-lintRange-error": {
    backgroundImage: "none",
    backgroundColor: "var(--danger-soft-strong)",
    borderBottom: "2px solid var(--danger)",
  },
  ".cm-gutter-lint": {
    width: "14px",
  },
  ".cm-gutter-lint .cm-gutterElement": {
    padding: "0 2px",
  },
  ".cm-lint-marker-error": {
    width: "8px !important",
    height: "8px !important",
    borderRadius: "50%",
    backgroundColor: "var(--danger)",
    display: "inline-block",
    marginTop: "6px",
  },
  ".cm-tooltip-lint": {
    backgroundColor: "var(--bg-elevated)",
    border: "1px solid var(--border)",
    color: "var(--text-primary)",
  },
}, { dark: true })

// Syntax highlighting colours for Python
const hauteHighlighting = HighlightStyle.define([
  { tag: tags.keyword, color: SYNTAX_COLORS.keyword },             // def, return, if, for, import
  { tag: tags.controlKeyword, color: SYNTAX_COLORS.keyword },
  { tag: tags.definitionKeyword, color: SYNTAX_COLORS.keyword },
  { tag: tags.operatorKeyword, color: SYNTAX_COLORS.keyword },      // and, or, not, in, is
  { tag: tags.modifier, color: SYNTAX_COLORS.keyword },
  { tag: tags.self, color: SYNTAX_COLORS.self },
  { tag: tags.bool, color: SYNTAX_COLORS.literal },
  { tag: tags.null, color: SYNTAX_COLORS.literal },
  { tag: tags.number, color: SYNTAX_COLORS.literal },
  { tag: tags.string, color: SYNTAX_COLORS.string },
  { tag: tags.special(tags.string), color: SYNTAX_COLORS.string },  // f-strings
  { tag: tags.regexp, color: "var(--syntax-warning)" },
  { tag: tags.comment, color: "var(--text-muted)", fontStyle: "italic" },
  { tag: tags.function(tags.definition(tags.variableName)), color: "var(--syntax-accent)" },  // function defs
  { tag: tags.function(tags.variableName), color: SYNTAX_COLORS.function },  // function calls
  { tag: tags.className, color: "var(--syntax-warning)" },
  { tag: tags.definition(tags.className), color: "var(--syntax-warning)" },
  { tag: tags.propertyName, color: SYNTAX_COLORS.property },          // .method / .attr
  { tag: tags.operator, color: SYNTAX_COLORS.operator },
  { tag: tags.punctuation, color: SYNTAX_COLORS.operator },
  { tag: tags.bracket, color: SYNTAX_COLORS.bracket },
  { tag: tags.meta, color: SYNTAX_COLORS.meta },                  // decorators
  { tag: tags.variableName, color: "var(--text-primary)" },
  { tag: tags.typeName, color: "var(--syntax-warning)" },
])

// Focus ring: border change on focus/blur
const focusRingTheme = EditorView.theme({
  "&": {
    borderRadius: "8px",
    border: "1px solid var(--border)",
    overflow: "hidden",
    transition: "border-color 0.15s, box-shadow 0.15s",
  },
  "&.cm-focused": {
    borderColor: "var(--accent-ring)",
    boxShadow: "0 0 0 2px var(--accent-soft)",
  },
})

// ─── Column-aware autocomplete source ─────────────────────────────

function columnCompletionSource(columns: string[]) {
  return (context: CompletionContext): CompletionResult | null => {
    if (columns.length === 0) return null

    // Walk the syntax tree to check if cursor is inside a string literal
    const node = syntaxTree(context.state).resolveInner(context.pos, -1)
    const isString = node.name === "String" || node.name === "FormatString"

    if (!isString) return null

    // Find the opening quote and extract the partial text after it
    const nodeText = context.state.sliceDoc(node.from, context.pos)
    // Match opening quote(s): single, double, triple-single, triple-double, with optional f/r/b prefix
    const quoteMatch = nodeText.match(/^[fFrRbBuU]{0,2}("""|'''|"|')/)
    if (!quoteMatch) return null

    const quoteLen = quoteMatch[0].length
    const partialFrom = node.from + quoteLen
    const partial = context.state.sliceDoc(partialFrom, context.pos)

    // Filter columns by case-insensitive prefix match
    const lower = partial.toLowerCase()
    const options = columns
      .filter((col) => col.toLowerCase().startsWith(lower))
      .map((col) => ({ label: col, type: "variable" as const }))

    if (options.length === 0) return null

    return {
      from: partialFrom,
      to: context.pos,
      options,
      filter: false, // we already filtered
    }
  }
}

export default function CodeMirrorEditor({
  defaultValue,
  onChange,
  placeholder,
  errorLine,
  availableColumns,
  onEditorView,
}: {
  defaultValue: string
  onChange: (value: string) => void
  placeholder?: string
  errorLine?: number | null
  /** Column names for in-string autocomplete */
  availableColumns?: string[]
  /** Callback to expose the EditorView for external operations (e.g. text insertion) */
  onEditorView?: (view: EditorView | null) => void
}) {
  const externalSyncAnnotation = useRef(Annotation.define<boolean>())
  const containerRef = useRef<HTMLDivElement>(null)
  const viewRef = useRef<EditorView | null>(null)
  const onChangeRef = useRef(onChange)
  const onEditorViewRef = useRef(onEditorView)
  const notifiedEditorViewCallbackRef = useRef<((view: EditorView | null) => void) | undefined>(undefined)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const diagnosticsClearRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const placeholderCompartment = useRef(new Compartment())
  const columnCompartment = useRef(new Compartment())
  const lastPropValueRef = useRef(defaultValue)
  const pendingLocalValueRef = useRef<string | null>(null)
  const pendingExternalValueRef = useRef<string | null>(null)

  // Keep onChange ref fresh without recreating the editor
  useEffect(() => { onChangeRef.current = onChange }, [onChange])
  useEffect(() => {
    const previousCallback = notifiedEditorViewCallbackRef.current
    if (previousCallback && previousCallback !== onEditorView) {
      previousCallback(null)
      notifiedEditorViewCallbackRef.current = undefined
    }

    onEditorViewRef.current = onEditorView
    if (viewRef.current && onEditorView) {
      onEditorView(viewRef.current)
      notifiedEditorViewCallbackRef.current = onEditorView
    }
  }, [onEditorView])

  const clearPendingLocalChangeTimer = useCallback(() => {
    if (debounceRef.current) {
      clearTimeout(debounceRef.current)
      debounceRef.current = undefined
    }
  }, [])

  const discardPendingLocalChange = useCallback(() => {
    clearPendingLocalChangeTimer()
    pendingLocalValueRef.current = null
  }, [clearPendingLocalChangeTimer])

  const flushPendingLocalChange = useCallback(() => {
    clearPendingLocalChangeTimer()
    const value = pendingLocalValueRef.current
    if (value === null) return
    pendingLocalValueRef.current = null
    if (value === lastPropValueRef.current) return
    lastPropValueRef.current = value
    onChangeRef.current(value)
  }, [clearPendingLocalChangeTimer])

  const applyExternalValue = useCallback((view: EditorView, value: string) => {
    flushPendingLocalChange()
    const currentDoc = view.state.doc.toString()
    if (value === currentDoc) {
      lastPropValueRef.current = value
      pendingExternalValueRef.current = null
      return
    }
    view.dispatch({
      changes: { from: 0, to: currentDoc.length, insert: value },
      annotations: externalSyncAnnotation.current.of(true),
    })
    lastPropValueRef.current = value
    pendingExternalValueRef.current = null
  }, [flushPendingLocalChange])

  // Sync external value changes into the editor. Focused editors still accept
  // updates when their buffer matches the last committed prop value, which
  // keeps websocket-driven refreshes visible without clobbering active edits.
  useEffect(() => {
    const view = viewRef.current
    if (!view) {
      lastPropValueRef.current = defaultValue
      return
    }
    const currentDoc = view.state.doc.toString()
    if (defaultValue === currentDoc) {
      lastPropValueRef.current = defaultValue
      pendingExternalValueRef.current = null
      discardPendingLocalChange()
      return
    }
    if (!view.hasFocus || currentDoc === lastPropValueRef.current) {
      applyExternalValue(view, defaultValue)
      return
    }
    pendingExternalValueRef.current = defaultValue
  }, [applyExternalValue, defaultValue, discardPendingLocalChange])

  // Create the editor once on mount
  useEffect(() => {
    if (!containerRef.current) return

    const updateListener = EditorView.updateListener.of((update) => {
      if (update.docChanged) {
        const isExternalSync = update.transactions.some((transaction) =>
          transaction.annotation(externalSyncAnnotation.current) === true,
        )
        if (isExternalSync) return
        const value = update.state.doc.toString()
        pendingLocalValueRef.current = value
        clearPendingLocalChangeTimer()
        debounceRef.current = setTimeout(() => {
          flushPendingLocalChange()
        }, LOCAL_CHANGE_DEBOUNCE_MS)
        // Clear diagnostics after the current CodeMirror transaction completes.
        if (diagnosticsClearRef.current) clearTimeout(diagnosticsClearRef.current)
        const view = update.view
        diagnosticsClearRef.current = setTimeout(() => {
          diagnosticsClearRef.current = undefined
          if (viewRef.current === view) {
            view.dispatch(setDiagnostics(view.state, []))
          }
        }, 0)
      }
    })

    const state = EditorState.create({
      doc: defaultValue,
      extensions: [
        // Core editing
        history(),
        drawSelection(),
        rectangularSelection(),
        indentOnInput(),
        bracketMatching(),
        closeBrackets(),
        // Column-aware completions (reconfigurable via compartment)
        columnCompartment.current.of(
          availableColumns?.length
            ? autocompletion({ override: [columnCompletionSource(availableColumns)] })
            : autocompletion(),
        ),
        highlightActiveLine(),
        highlightActiveLineGutter(),
        highlightSelectionMatches(),
        indentUnit.of("    "),

        // Gutters
        lineNumbers(),
        foldGutter(),
        lintGutter(),

        // Python language
        python(),
        syntaxHighlighting(hauteHighlighting),

        // Theme
        hauteTheme,
        focusRingTheme,

        // Placeholder
        placeholderCompartment.current.of(
          placeholder ? cmPlaceholder(placeholder) : [],
        ),

        // Keymaps — order matters: specific before general
        keymap.of([
          ...closeBracketsKeymap,
          ...searchKeymap,
          ...historyKeymap,
          ...foldKeymap,
          ...completionKeymap,
          indentWithTab,
          ...defaultKeymap,
        ]),

        // Focus tracking for external sync
        EditorView.domEventHandlers({
          blur: (_event, view) => {
            const pendingValue = pendingExternalValueRef.current
            if (pendingValue !== null && view.state.doc.toString() === lastPropValueRef.current) {
              applyExternalValue(view, pendingValue)
            }
          },
        }),

        // Change listener
        updateListener,

        // Prevent the editor from growing wider than the panel
        EditorView.lineWrapping,
      ],
    })

    const view = new EditorView({
      state,
      parent: containerRef.current,
    })

    viewRef.current = view
    if (onEditorViewRef.current) {
      onEditorViewRef.current(view)
      notifiedEditorViewCallbackRef.current = onEditorViewRef.current
    }

    return () => {
      flushPendingLocalChange()
      if (diagnosticsClearRef.current) {
        clearTimeout(diagnosticsClearRef.current)
        diagnosticsClearRef.current = undefined
      }
      if (viewRef.current === view) viewRef.current = null
      notifiedEditorViewCallbackRef.current?.(null)
      notifiedEditorViewCallbackRef.current = undefined
      view.destroy()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- mount once: defaultValue is initial content only, onChange is tracked via ref
  }, [])

  // Update column completions when availableColumns changes
  useEffect(() => {
    if (!viewRef.current) return
    viewRef.current.dispatch({
      effects: columnCompartment.current.reconfigure(
        availableColumns?.length
          ? autocompletion({ override: [columnCompletionSource(availableColumns)] })
          : autocompletion(),
      ),
    })
  }, [availableColumns])

  // Push error diagnostics when errorLine changes
  useEffect(() => {
    const view = viewRef.current
    if (!view) return

    if (errorLine != null && errorLine >= 1) {
      const doc = view.state.doc
      const lineNum = Math.min(errorLine, doc.lines)
      const line = doc.line(lineNum)
      view.dispatch(setDiagnostics(view.state, [{
        from: line.from,
        to: line.to,
        severity: "error",
        message: `Error on line ${errorLine}`,
      }]))
    } else {
      view.dispatch(setDiagnostics(view.state, []))
    }
  }, [errorLine])

  // Update placeholder if it changes
  useEffect(() => {
    if (!viewRef.current) return
    viewRef.current.dispatch({
      effects: placeholderCompartment.current.reconfigure(
        placeholder ? cmPlaceholder(placeholder) : [],
      ),
    })
  }, [placeholder])

  return <div ref={containerRef} data-testid="code-editor-wrapper" className="flex-1 min-h-[120px]" />
}

