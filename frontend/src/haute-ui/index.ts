/**
 * haute-ui: the toolbar kit Haute's editor shares with its extensions
 * (specs/frontend-shared). The editor imports this directory by path; an
 * extension installs it as the `haute-ui` package and renders the same controls.
 *
 * The styles are not imported here. A consumer imports `haute-ui/toolbar.css`
 * and `haute-ui/palette.css`, and `haute-ui/tokens.css` unless it declares
 * Haute's tokens itself, into whichever document or shadow root renders the
 * controls.
 */
export { PaletteColumn, PaletteHeader, PaletteRevealStrip } from "./PaletteShell"
export { SaveCommit, ToolbarBrand, ToolbarButton, ToolbarColumn, UndoRedo, ZoomInOut } from "./ToolbarControls"
export type { ToolbarButtonProps } from "./ToolbarControls"
