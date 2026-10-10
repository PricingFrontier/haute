/**
 * haute-ui: the chrome kit the pipeline editor and the workbench's view are built from
 * (specs/frontend-shared): the toolbar's controls, the palette's shell and the side
 * panel's frame, so the two toolbars and palettes match by construction.
 *
 * The styles are not imported here: index.css imports `toolbar.css`, `palette.css`,
 * `side-panel.css` and `dropdowns.css` from this directory.
 */
export { PaletteColumn, PaletteHeader, PaletteItem, PaletteItems, PaletteRevealStrip } from "./PaletteShell"
export type { PaletteItemProps } from "./PaletteShell"
export { SidePanel, SidePanelHeader } from "./SidePanel"
export type { SidePanelHeaderProps, SidePanelProps } from "./SidePanel"
export { SaveCommit, ToolbarBrand, ToolbarButton, ToolbarColumn, UndoRedo, ZoomInOut } from "./ToolbarControls"
export type { ToolbarButtonProps } from "./ToolbarControls"
