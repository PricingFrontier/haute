export function parsePastedGrid(text: string): string[][] {
  const rows = text
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    .split("\n")
    .map(line => line.split("\t"))

  while (rows.length > 0 && rows[rows.length - 1].every(cell => cell.trim() === "")) {
    rows.pop()
  }

  return rows
}

export function buildTsv(rows: readonly (readonly string[])[]): string {
  return rows.map(row => row.join("\t")).join("\n")
}

export function writeClipboardText(text: string): Promise<void> {
  const clipboard = navigator.clipboard
  if (!clipboard) {
    return Promise.reject(new Error("Clipboard API is not available"))
  }
  return clipboard.writeText(text)
}

/**
 * Serialise a grid to CSV (RFC-4180-ish): cells containing a comma, quote, or
 * newline are double-quoted with embedded quotes doubled. The TSV path
 * (`buildTsv`) is the canonical paste-back form; CSV exists only for the Save
 * download affordance, where a spreadsheet is the likely destination.
 */
export function buildCsv(rows: readonly (readonly string[])[]): string {
  const escape = (cell: string): string =>
    /[",\n\r]/.test(cell) ? `"${cell.replace(/"/g, '""')}"` : cell
  return rows.map((row) => row.map(escape).join(",")).join("\n")
}

/**
 * Trigger a browser download of `text` as a file named `filename` with MIME
 * `mime`. Uses an object URL + a synthetic anchor click, the standard
 * dependency-free pattern. Returns true when the download was initiated, false
 * when the required DOM/URL APIs are unavailable (non-secure or non-browser
 * context) so callers can surface a guarded fallback rather than throwing.
 */
export function downloadTextFile(
  text: string,
  filename: string,
  mime: string,
): boolean {
  if (
    typeof document === "undefined" ||
    typeof URL === "undefined" ||
    typeof URL.createObjectURL !== "function"
  ) {
    return false
  }
  const blob = new Blob([text], { type: mime })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = filename
  anchor.style.display = "none"
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  // Revoke on the next tick so the click has a chance to start the download.
  setTimeout(() => URL.revokeObjectURL(url), 0)
  return true
}

/** True when the Clipboard write API is usable (present + secure context). The
 * spec requires a secure context for `navigator.clipboard`; some browsers also
 * gate reads on a focused document. Callers guard their copy/share affordances
 * with this so a non-secure context degrades gracefully instead of throwing. */
export function clipboardWriteAvailable(): boolean {
  if (typeof navigator === "undefined" || !navigator.clipboard) return false
  // `isSecureContext` is true for https + localhost; absent in non-browser envs.
  if (typeof isSecureContext === "boolean" && !isSecureContext) return false
  return typeof navigator.clipboard.writeText === "function"
}
