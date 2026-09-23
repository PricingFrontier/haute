/**
 * Pure helpers for the modelling Export pane. The export-field keys and the
 * training identity that omits them live in utils/modellingExportConfig.
 */

/** Native model file extension per algorithm, as written by training. */
const MODEL_FILE_EXTENSIONS = {
  catboost: ".cbm",
  glm: ".rsglm",
  xgboost: ".ubj",
  lightgbm: ".lgbm",
  ebm: ".ebm",
} as const

export type ExportableAlgorithm = keyof typeof MODEL_FILE_EXTENSIONS

/** The extension "Save model to file" adds when a filename has none. */
export function modelFileExtension(algorithm: ExportableAlgorithm): string {
  return MODEL_FILE_EXTENSIONS[algorithm]
}
