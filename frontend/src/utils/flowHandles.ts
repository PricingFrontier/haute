export const DEFAULT_TARGET_HANDLE = "__default_target"

export function normalizeDefaultTargetHandle(handleId: string | null | undefined): string | null {
  return handleId === DEFAULT_TARGET_HANDLE ? null : handleId ?? null
}
