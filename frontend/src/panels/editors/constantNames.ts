/** Each name two or more of a Constant node's values share, which execution
 *  refuses; an empty name is skipped, as execution skips it. */
export function duplicateConstantNames(names: string[]): Set<string> {
  const seen = new Set<string>()
  const repeated = new Set<string>()
  for (const name of names) {
    if (!name) continue
    if (seen.has(name)) repeated.add(name)
    seen.add(name)
  }
  return repeated
}

/** The first `constant_<n>` no value uses yet. */
export function nextConstantName(names: string[]): string {
  const taken = new Set(names)
  let n = 1
  while (taken.has(`constant_${n}`)) n += 1
  return `constant_${n}`
}
