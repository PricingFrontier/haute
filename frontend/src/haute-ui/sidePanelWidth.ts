/** The widest a side panel drags to, as its `maxWidth`: half the window, never under `least`. */
export const halfWindowWidth = (least: number): number => Math.max(least, Math.floor(window.innerWidth / 2))
