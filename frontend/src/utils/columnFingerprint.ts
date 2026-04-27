export type ColumnFingerprintInput = readonly { name: string; dtype: string }[]

const UNDEFINED_COLUMNS_FINGERPRINT = ""
const EMPTY_COLUMNS_FINGERPRINT = "0:"
const FIELD_SEPARATOR = "\u0002"
const COLUMN_SEPARATOR = "\u0003"

/**
 * Produce an order-sensitive, collision-safe fingerprint for column schemas.
 *
 * Each field is length-prefixed before joining, so names or dtypes containing
 * separator characters cannot collide with a different schema. The helper
 * intentionally reads `name.length` and `dtype.length` directly: malformed
 * column records should fail loudly instead of being silently normalized.
 */
export function columnFingerprint(columns: ColumnFingerprintInput | undefined): string {
  if (columns === undefined) return UNDEFINED_COLUMNS_FINGERPRINT
  if (columns.length === 0) return EMPTY_COLUMNS_FINGERPRINT

  const parts = new Array<string>(columns.length)
  for (let index = 0; index < columns.length; index += 1) {
    const { name, dtype } = columns[index]
    parts[index] = `${name.length}:${name}${FIELD_SEPARATOR}${dtype.length}:${dtype}`
  }
  return parts.join(COLUMN_SEPARATOR)
}

export function columnsEqualByFingerprint(
  a: ColumnFingerprintInput | undefined,
  b: ColumnFingerprintInput | undefined,
): boolean {
  return columnFingerprint(a) === columnFingerprint(b)
}
