export const DEFAULT_TARGET_HANDLE = "__default_target"
/** Interaction-only target for the single visible input socket on a submodel. */
export const SUBMODEL_INPUT_HANDLE = "__submodel_inputs__"
export const INPUT_ORIGIN_HANDLE_CLASS = "input-origin-handle"
export const OUTPUT_ORIGIN_HANDLE_CLASS = "output-origin-handle"

export function normalizeDefaultTargetHandle(handleId: string | null | undefined): string | null {
  return handleId === DEFAULT_TARGET_HANDLE ? null : handleId ?? null
}
