# G08 — Branch switch (and Create & Move) silently discards unsaved in-memory edits

**Severity: HIGH · Confidence: CONFIRMED · Class: silent data loss, contradicts the README promise**
**Files: `frontend/src/components/BranchManager.tsx`; `frontend/src/components/MoveConfirmModal.tsx` (pattern donor); `frontend/src/App.tsx`**
**Origin: U-2 (UX reviewer). Independently verified at `BranchManager.tsx:137-147`.**

## The defect

README (`README.md:143`): *"Switching between versions saves your current work first."*

`BranchManager.switchNow` (`BranchManager.tsx:137-147`) calls `setWorkingBranch(...)` then reloads
(`run(..., { reloadOnDone: true })`). It never imports `useGraphStore`, never reads the in-memory
`dirty` flag, never calls `handleSave`. There is no `beforeunload` guard anywhere in the app, so
the forced reload gives no native prompt either. The switch confirm copy (`BranchManager.tsx:355`)
— *"Switch to `X`? Your editor reloads onto that branch."* — says nothing about unsaved work. The
same applies to **Create & Move** (`doCreate`, `:115-123`, reloads on `res.switched`) and to
archive/delete of the current branch (`reloadOnDone: b.is_current`).

The only dirty check BranchManager has (`:156-164`, keyed off `has_uncommitted_changes`) reads the
**on-disk git tree** — which is clean precisely when the unsaved edits are still in the editor's
memory. So: user edits nodes, clicks Switch, confirms a dialog that mentions no risk, app reloads
onto the other branch — edits gone, no undo.

The correct pattern already exists one file over: `MoveConfirmModal.tsx:22-35,58-67` reads
`useGraphStore(s => s.dirty)` and forces an explicit **Save & move / Discard & move** choice, with
the save awaited before the checkout (`App.tsx:359-382`).

## Fix design

1. Give BranchManager the in-memory dirty signal + a save function (the same
   `useGraphStore(s => s.dirty)` selector MoveConfirmModal uses; `handleSave` passed down from App
   or exposed via the store — match however MoveConfirmModal is wired).
2. When `dirty`, the switch confirm becomes three-way, mirroring MoveConfirmModal:
   - primary **"Save & switch"** — `await handleSave()`; only on success call
     `setWorkingBranch(...)` and reload;
   - secondary (danger-styled) **"Switch without saving"**;
   - **Cancel**.
   Proposed copy: *"You have unsaved changes. Save them to `<current>` before switching?"*
3. Apply the same gate to Create & Move (it relocates the tree and reloads) and to archive/delete
   of the **current** branch (both reload; delete additionally force-discards server-side —
   `_switch_away_if_active(..., discard=True)` — so the dialog must say the edits are lost, not
   imply they're kept).
4. Belt-and-braces: register a `beforeunload` handler while `dirty` (native prompt) so *any*
   future reload path is covered. Keep it silent during haute-initiated reloads that already
   saved (set a one-shot flag before `reloadApp()`).
5. The "don't ask again" pref (`skipSwitchConfirm`) must **not** skip the dirty gate — it skips
   only the informational confirm. Losing work is never a "don't ask again" class.

## TDD plan (vitest/RTL)

1. `test_switch_with_dirty_editor_offers_save_first` — set graph store dirty; click Switch;
   assert "Save & switch" affordance renders; click it; assert `handleSave` resolved **before**
   `setWorkingBranch` was called (ordering via mock call log).
2. `test_switch_without_saving_requires_explicit_choice` — dirty; assert plain "Switch" is not the
   primary/default action and the danger label is explicit.
3. `test_skip_switch_confirm_pref_does_not_bypass_dirty_gate` — pref on + dirty → gate still shown.
4. `test_create_and_move_with_dirty_editor_gates` — same for the move-fork path.
5. `test_clean_editor_switch_unchanged` — not dirty → existing one-step confirm (+pref) behaviour
   preserved (existing BranchManager tests stay green).

## Notes

Data-loss class → full dev/reviewer pair. Coordinate copy with G14 (README truth): after this
lands, the README sentence becomes true again for switches; the move flow already honoured it.
