/**
 * A short digest of a text: its length and two independent 32-bit hashes of it. The graph's
 * fingerprints compose one per node, edge and table instead of holding the text itself, so
 * comparing two fingerprints costs the same whatever the size of a node's settings (FEG-01).
 * Not a cryptographic hash: it tells apart the documents one editor makes, never an
 * adversary's.
 */
export function contentDigest(text: string): string {
  let h1 = 0xdeadbeef
  let h2 = 0x41c6ce57
  for (let index = 0; index < text.length; index += 1) {
    const code = text.charCodeAt(index)
    h1 = Math.imul(h1 ^ code, 2654435761)
    h2 = Math.imul(h2 ^ code, 1597334677)
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909)
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909)
  return `${text.length.toString(36)}.${(h1 >>> 0).toString(36)}.${(h2 >>> 0).toString(36)}`
}

/**
 * `contentDigest` of `serialize(value)`, kept for `value`'s identity in `cache`: the graph
 * replaces an object it changes and never mutates one in place, so an object reads the
 * same for as long as it lives.
 */
export function cachedDigest<K extends object>(
  cache: WeakMap<K, string>,
  value: K,
  serialize: (value: K) => string,
): string {
  const known = cache.get(value)
  if (known !== undefined) return known
  const digest = contentDigest(serialize(value))
  cache.set(value, digest)
  return digest
}
