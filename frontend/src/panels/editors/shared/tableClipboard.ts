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
