/**
 * Pure helpers for the modelling Export pane. The export-field keys and the
 * training identity that omits them live in utils/modellingExportConfig.
 */
import { modelFileSuffixForAlgorithm } from "../../utils/modelFamilies"
import type { ModellingAlgorithm } from "./algorithmCapabilities"

export type ExportableAlgorithm = ModellingAlgorithm

/** The extension "Save model to file" adds when a filename has none. */
export function modelFileExtension(algorithm: ExportableAlgorithm): string {
  return modelFileSuffixForAlgorithm(algorithm)
}
