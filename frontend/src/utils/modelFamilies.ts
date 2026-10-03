/**
 * The model family registry, as the backend registers it. `modelFamilies.json`
 * is generated from `haute._model_flavors.model_family_fixture()` and a parity
 * test (`tests/test_model_families.py`) holds the two equal.
 */
import families from "./modelFamilies.json"

export type ModelFamily = {
  label: string
  /** The Haute training algorithm whose models load as this family, if any. */
  algorithm: string | null
  /** The file suffixes the family loads from, lowercase with the dot. */
  suffixes: string[]
}

export const MODEL_FAMILIES = families as Record<string, ModelFamily>

/** Every model file suffix a family registers. */
export const MODEL_FILE_SUFFIXES: readonly string[] = Object.values(MODEL_FAMILIES).flatMap(
  (family) => family.suffixes,
)

/** The file suffix the models a Haute training algorithm writes load from. */
export function modelFileSuffixForAlgorithm(algorithm: string): string {
  const family = Object.values(MODEL_FAMILIES).find((entry) => entry.algorithm === algorithm)
  const suffix = family?.suffixes[0]
  if (suffix === undefined) {
    throw new Error(`No model family loads files trained by algorithm "${algorithm}"`)
  }
  return suffix
}
