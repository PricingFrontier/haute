const BYTE_UNITS = ["KB", "MB", "GB", "TB"] as const
/** `formatBytes` stops at GB. */
const LAST_FORMAT_BYTES_UNIT = BYTE_UNITS.indexOf("GB")

/**
 * Format a byte count into a human-readable string (B / KB / MB / GB).
 *
 * The unit follows the value as it would be printed, so rounding carries into
 * the next unit: 1,048,575 bytes is 1023.999 KB, which would print as
 * "1024.0 KB", so it reads "1.0 MB". A negative count keeps its sign.
 */
export function formatBytes(b: number): string {
  if (Math.abs(b) < 1024) return `${b} B`
  let value = b / 1024
  let unit = 0
  while (unit < LAST_FORMAT_BYTES_UNIT && Math.abs(Number(value.toFixed(1))) >= 1024) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(1)} ${BYTE_UNITS[unit]}`
}

/**
 * Format a byte count that can reach gigabytes, such as a cache budget.
 *
 * `formatBytes` stops at GB with one decimal, which reads badly for a limit. Values of
 * ten or more drop the decimal, so a size and its limit stay short enough to
 * sit on one line beside each other.
 */
export function formatByteSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  let value = bytes / 1024
  let unit = 0
  while (value >= 1024 && unit < BYTE_UNITS.length - 1) {
    value /= 1024
    unit += 1
  }
  // Rounding can carry the value into the next unit — 1,048,575 bytes is
  // 1023.999 KB, which would print as "1024 KB" — so carry it explicitly.
  if (value >= 10 && Math.round(value) >= 1024 && unit < BYTE_UNITS.length - 1) {
    value /= 1024
    unit += 1
  }
  // The threshold is applied to the value as it would be *printed*, not as it
  // is held: 9.96 rounds to "10.0", and "10.0 MB" beside "10 MB" would make
  // the rule look arbitrary.
  return `${Number(value.toFixed(1)) >= 10 ? Math.round(value) : value.toFixed(1)} ${BYTE_UNITS[unit]}`
}
