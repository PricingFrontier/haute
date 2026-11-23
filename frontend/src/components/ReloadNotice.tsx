import useGraphStore from "../stores/useGraphStore";

export const ERROR_FALLBACK_BUTTON_STYLE = {
  padding: "4px 12px",
  fontSize: "11px",
  background: "var(--bg-input)",
  border: "1px solid var(--border)",
  borderRadius: "4px",
  color: "var(--text-primary)",
  cursor: "pointer",
} as const;

/**
 * The offer of a reload, after *reason*. A reload loses unsaved canvas edits and the app has
 * no unload guard, so the offer asks for a save first and names the loss when the author
 * reloads anyway.
 */
export function ReloadNotice({ reason }: { reason: string }) {
  const unsaved = useGraphStore((state) => state.dirty);
  return (
    <>
      <p style={{ margin: "0 0 12px" }}>
        {reason}
        {unsaved && " You have unsaved changes: save them first (Ctrl+S), or reloading loses them."}
      </p>
      <button onClick={() => window.location.reload()} style={ERROR_FALLBACK_BUTTON_STYLE}>
        {unsaved ? "Reload without saving" : "Reload"}
      </button>
    </>
  );
}
