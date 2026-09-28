/**
 * Keeps a saved config value visible in a `<select>` whose options lack it.
 *
 * Column lists arrive with the first preview, so a node opened before then has
 * no options yet: the saved value shows as itself rather than a blank select.
 * Once the options are known, a value they lack is labelled as missing.
 */
export default function SavedValueOption({
  value,
  options,
  missingLabel = (saved) => `${saved} (not in input)`,
}: {
  value: string
  /** The option values the select offers; empty while they are not known. */
  options: readonly string[]
  missingLabel?: (value: string) => string
}) {
  if (!value || options.includes(value)) return null
  return <option value={value}>{options.length === 0 ? value : missingLabel(value)}</option>
}
