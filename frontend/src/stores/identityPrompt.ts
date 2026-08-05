/**
 * Session-scoped memory of whether the user has waved away the git-identity
 * prompt.
 *
 * A save on a restored container reports `identity_required` on EVERY save, so
 * without this flag a user who chose not to set an identity would be nagged
 * after every keystroke-driven autosave. The save warning still surfaces each
 * time; only the modal is suppressed, and only until the page reloads.
 */
let dismissed = false

export function isIdentityPromptDismissed(): boolean {
  return dismissed
}

export function dismissIdentityPrompt(): void {
  dismissed = true
}

/** Test-only: module state outlives a single test otherwise. */
export function resetIdentityPromptForTests(): void {
  dismissed = false
}
