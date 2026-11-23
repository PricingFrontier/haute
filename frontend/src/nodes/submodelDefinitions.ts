import { createContext, useContext } from "react"
import useGraphStore from "../stores/useGraphStore"

/**
 * The submodel definitions a canvas draws its occurrences from, when they are not the
 * document's: a comparison's two canvases each give the version they show (FEG-09). An
 * occurrence drawn from another version's definition would show ports its own document
 * never had.
 */
const SubmodelDefinitionsContext = createContext<Record<string, unknown> | null>(null)

export const SubmodelDefinitionsProvider = SubmodelDefinitionsContext.Provider

/** The definitions the canvas being drawn holds: the provided ones, else the document's. */
export function useSubmodelDefinitions(): Record<string, unknown> {
  const provided = useContext(SubmodelDefinitionsContext)
  const document = useGraphStore((state) => state.submodels)
  return provided ?? document
}
