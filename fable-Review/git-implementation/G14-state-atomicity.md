# G14 — `.haute/` state files: atomic writes + read-modify-write races

**Severity: MEDIUM · Confidence: CONFIRMED · Class: bounded state loss (torn writes, lost updates)**
**Files: `src/haute/_git_state.py`**
**Origin: E-6 (engine reviewer), routes reviewer's item 6, primary-read candidate #6.**

## The defect

All four writers use bare `path.write_text(...)` — non-atomic:

- `write_working_branch` (`_git_state.py:68`)
- `write_pref` (`:110`)
- `_write_forks` (`:139`)
- `record_pushed_shas` (`:197`)

and three of them are unlocked read-modify-write (`write_pref`, `set_fork` `:142-146`,
`record_pushed_shas` `:193-197`). Git routes run concurrently in the threadpool (see G01), so:

- **Torn write:** a crash/kill mid-`write_text` truncates `state.json`; `read_working_branch`
  treats malformed JSON as unset (`:55-57`) → the working-branch association silently vanishes
  and the user is re-prompted by the startup modal. Same class for forks (branch back-links
  disappear from the panel) and pushed.json (X3 rewrite detection silently degrades — it is
  designed to degrade open, but a torn write is not "never recorded", it is "recorded then lost").
- **Lost update:** two concurrent mutations (double-clicked fork; a push racing a fork) both
  read-then-write forks/pushed → last-writer-wins drops the other's entry.

Blast radius is bounded by design (the module's own contract: "reconstructable preference, not
data") — hence MEDIUM, not HIGH. But `state.json` loss is user-visible and the fix is cheap.

## Fix design

1. **Atomic writes everywhere.** The codebase already ships the idiom: `atomic_write_bytes` in
   `haute._file_ops` (used by `_save_pipeline.py`). Route all four writers through it (temp file
   in the same directory + `os.replace` — atomic on NTFS and POSIX). Encode with a trailing
   newline exactly as today so file diffs stay stable.
2. **Serialise the RMW files.** One module-level `threading.Lock` around
   read→mutate→write in `write_pref`, `set_fork`, `remove_fork`, `rename_fork`,
   `record_pushed_shas`. (After G01, engine-level races are already serialised for git-op-driven
   writers; this lock also covers the prefs route, which G01 does not wrap — cheap and total.)
3. Keep the read-side leniency exactly as is (malformed → default) — it is the right contract for
   preference files; the fix removes the *writer-side* corruption sources, not the reader's
   tolerance.

## TDD plan

1. `test_state_write_is_atomic_under_simulated_crash` — monkeypatch the underlying write to raise
   midway (or write a truncated temp then fail before replace); assert the ORIGINAL state.json
   content is intact and readable (not truncated).
2. `test_torn_state_file_reads_as_unset` — write garbage/truncated JSON directly; assert
   `read_working_branch` returns None without raising (pins the read contract; likely already
   covered by `test_git_state_coverage.py` — extend, don't duplicate).
3. `test_concurrent_set_fork_calls_both_survive` — two threads `set_fork(root, "a", sha1)` /
   `set_fork(root, "b", sha2)` behind a barrier; assert both keys present afterwards.
4. `test_concurrent_pref_and_pushed_writes_do_not_cross_corrupt` — prefs and pushed written
   concurrently; both files valid JSON with their own content.

## Notes

Mechanical, well-bounded; batch review acceptable. Reuse `atomic_write_bytes` — do not hand-roll a
second atomic-write implementation (project rule: share what's built).
