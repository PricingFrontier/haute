# Platform Divergence — Windows vs macOS/Linux

This is the contributor-facing catalogue of behaviour that differs across platform stacks in this codebase. It is **incident-derived**: every entry is a divergence that actually bit us, with the fix pattern the repository now uses and what — if anything — enforces it mechanically.

It is deliberately not generic portability advice. The point is that a contributor touching path, encoding, or subprocess code can look up the *specific* failure mode rather than rediscover it.

For the *user-facing* side of filename portability — which spellings resolve to which file on which filesystem — see [Filesystem Portability](building-models/filesystem-portability.md). This page is about the code.

Per entry: the pattern in plain English → the portable alternative → the enforcement.

## 1. Catalogue

### P1. Backslash path separators leak into generated Python source

**Pattern.** Haute round-trips pipelines through generated Python source: the code generator writes `config="config/…"` strings and `scan_parquet("data/…")` calls, and the parser reads them back. On Windows, `str(Path(...))` produces backslash separators — and a backslash inside a Python string literal is an *escape sequence*. `config\batch.json` parses fine, but `data\nb_batch.parquet` contains a real newline and `config\rating\x.json` contains a carriage return. The path does not just look wrong; it silently *is* a different string, and which paths break depends on which letter follows the backslash.

The same class one level up: user-typed node *descriptions* containing Windows paths raised `SyntaxError` when interpolated into generated docstrings.

**Portable alternative.** Any path serialised into a persisted artefact — generated `.py`, config JSON, schema mapping — is POSIX-relative: build it with `pathlib`, emit it with `.as_posix()`, never `str()` or f-string interpolation of a `Path`. Any *user text* interpolated into generated source goes through the escape sanitiser — `_sanitize_description` in `src/haute/_codegen_builders.py`, which doubles every backslash so escape sequences stay literal.

**Enforcement.** No lint rule sees this — `str(some_path)` is legal and the damage happens at the serialisation boundary. Review line: **does any `Path` reach a persisted string via `str()`/f-string instead of `.as_posix()`, and does any user text reach generated source without the sanitiser?**

### P2. Unspecified text encoding (the locale default is cp1252 on Windows)

**Pattern.** `open()`, `Path.read_text()`, and `Path.write_text()` without `encoding=` use the locale preferred encoding — UTF-8 on macOS/Linux, cp1252 on most Windows installs. UTF-8 JSON with any non-ASCII content then fails to decode, or writes mojibake, only on Windows. Haute's configs are written with `ensure_ascii=False`, so non-ASCII content in configs is expected, not hypothetical.

**Portable alternative.** `encoding="utf-8"` on every text-mode `open`/`read_text`/`write_text`. The atomic-write helper — `atomic_write_text` in `src/haute/_file_ops.py` — already defaults to UTF-8; route writes through it where atomicity is wanted anyway (P5).

**Enforcement.** Ruff `PLW1514` (unspecified-encoding), enforced repository-wide.

### P3. A CRLF checkout breaks byte-identity fixtures

**Pattern.** Golden tests that compare file bytes assume the fixture bytes in git are the bytes on disk. A Windows clone with `core.autocrlf=true` — the Git-for-Windows installer default — rewrites text-classified files to CRLF at checkout, so byte-for-byte comparisons fail only on Windows checkouts, while the repository content never changed.

**Portable alternative.** Every directory of byte-compared fixtures gets a `.gitattributes` `eol=lf` pin. Where the comparison can tolerate it, normalise line endings before comparing instead — but where byte identity *is* the contract, the pin is the fix.

**Enforcement.** Existing `.gitattributes` patterns cover the current fixture roots only. Review line: **new golden or byte-identity fixture location → is it covered by a `.gitattributes` `eol=lf` pattern?**

### P4. `subprocess` cannot find `npm` on Windows

**Pattern.** On Windows, npm is not an executable — it is `npm.cmd`. `subprocess.run(["npm", …])` uses `CreateProcess`, which does not apply `PATHEXT`, so the bare name fails with `FileNotFoundError` even though `npm` works in every shell. `shutil.which("npm")` *does* apply `PATHEXT` and returns the full path `CreateProcess` accepts.

A hardcoded install-path fallback was tried and then deliberately removed: a silent guess at a specific machine layout hides "npm isn't on PATH" from the user. The current contract is `shutil.which` only, failing loud with an install hint.

**Portable alternative.** Resolve every external binary through `shutil.which()` at the tool's chokepoint (§3); never hardcode install paths; fail loud when resolution fails.

**Enforcement.** No lint rule. The chokepoint rule plus a review line: **does any new invocation of a named tool go through that tool's chokepoint module, and does that chokepoint resolve via `shutil.which`?** Tests pin the fail-loud contract for the CLI helpers.

### P5. Rename is atomic on POSIX, not on Windows

**Pattern.** The temp-then-rename atomic-write idiom relies on rename being atomic, which it is on POSIX even under open readers. On Windows it fails under contention: a concurrent reader holding the target open with Python's default share mode produces a sharing violation, and concurrent renames to the same target race.

**Portable alternative.** Keep temp-then-replace — it is still the best available on both platforms — but treat replace failure as a loud error, never a silent retry, and do not hold long-lived open readers on files that get atomically replaced.

**Enforcement.** Mechanised: the `platform-smoke` lane (Windows and macOS) runs `tests/test_file_ops.py`, which pins the *real* observed Windows failure (no partial write lands, no stray temp file lingers, the original is intact) so a future change in Windows semantics breaks tests rather than assumptions. Review line for new writers: **does this write go through the atomic-write helpers rather than a hand-rolled temp-and-rename?**

### P6. `chmod` executable bits do not exist on NTFS

**Pattern.** `Path.chmod(0o755)` is a silent no-op on NTFS, and the Windows git shim ignores the POSIX executable bit anyway — so "make this script executable" code succeeds while doing nothing, and the intended bit never reaches a commit made from Windows.

**Portable alternative.** Platform-guard chmod calls. Where the executable bit matters in-repository, set it with `git update-index --chmod=+x`, which works from any platform, rather than through the filesystem.

**Enforcement.** Ruff `PTH101` flags `os.chmod` but cannot see the semantic no-op. Review line: **is any chmod platform-guarded, and does anything downstream actually depend on the bit on Windows?**

### P7. Platform-only APIs versus the type checker

**Pattern.** Windows-only and POSIX-only APIs exist only on their platform, but code that branches on the platform at runtime still has to typecheck on *both* type-checker platform targets, and each target rejects the other branch's attribute access.

**Portable alternative.** Confine platform branches to small, documented helper functions; use typed casts or platform-literal narrowing rather than blanket type-ignore comments.

**Enforcement.** Not mechanised today: CI runs the type checker for a single platform target, so the other branch's attribute access is caught only when the checker is run for the other target as well (`mypy --platform win32` or `--platform linux`) — a second target is what surfaced the original incident. Review line: **does a new platform branch pass the type checker under both platform targets, and is it confined to one helper?**

### P8. Whitespace and BOM semantics diverge between JavaScript and Python

**Pattern.** Haute mirrors backend name sanitisation in the frontend, and the two languages' whitespace definitions disagree at the edges. JavaScript's `trim()` strips the byte-order mark — which Windows tooling loves to prepend — and Python's `strip()` does not; Python strips several control characters that `trim()` does not. A label pasted with a BOM therefore produced different identifiers on the two sides.

**Portable alternative.** When mirroring Python string semantics in TypeScript, enumerate the exact codepoint set. Never reach for the host language's whitespace default (`trim()`, `\s`). State the parity contract at both sites.

**Enforcement.** The frontend lint gate includes `no-irregular-whitespace` (and has demonstrably fired on this). Review line: **does frontend code claiming parity with a Python function enumerate its character class rather than use an idiomatic default, and is there a parity test?**

### P9. Platform-conditional native dependencies

**Pattern.** Optional and native packages resolve differently per platform. A package with per-OS binaries can install fine on the authoring OS and break installation or runtime on another — npm's long-standing lockfile behaviour omits other-OS optional binaries when the lock is regenerated on one platform.

**Portable alternative.** Default to not taking the dependency at all. Where a native dependency is necessary, verify installation on all three operating systems via the existing lanes before landing.

**Enforcement.** The package-smoke and platform-smoke lanes catch install-time breakage off Linux. Review line: **new dependency with native or optional components → checked on the Windows and macOS lanes before landing?**

### P10. WSL and Windows fight over `.venv` in a shared checkout

**Pattern.** A checkout on a Windows drive mounted into WSL is one directory with two operating systems. A virtualenv created by either side is unusable by — and silently clobberable from — the other, because its configuration points at a platform-specific interpreter path.

**Portable alternative and enforcement.** Already mechanised in the preflight scripts: the shell script detects WSL and diverts to a separate environment directory, then refuses to run against a virtualenv belonging to the other platform; the PowerShell script has the mirror-image check. Review line: **does a new script that creates or assumes a virtualenv respect the environment override and the WSL split?**

## 2. Git on Windows

Haute shells out to git as a *product* feature, which makes git-on-Windows behaviour product behaviour rather than only contributor ergonomics. These are anticipated divergences, catalogued before they bite:

- **Installer-set `core.autocrlf=true`** rewrites text files at checkout and commit. Repository-side `.gitattributes` pins beat user config — extend them to any file class compared byte-wise (P3).
- **Index-lock contention.** Antivirus and search indexers briefly open files inside `.git/`, making lock acquisition fail transiently. Because haute's save flow commits frequently and automatically, this surfaces as a *save failure* to a user who ran nothing. The chokepoint module is the right place for a bounded retry on that specific signature — silent retry is wrong for push rejections (P5's lesson) but right for lock contention, which is self-clearing.
- **Case-insensitive filesystems.** Two node labels differing only in case are one file on Windows and macOS, two on Linux. A rename changing only case is a no-op to the filesystem but a rename to git, needing a two-step move. Cheap to test on the macOS lane — no Windows runner required.
- **Symlinks** require Developer Mode or elevation on Windows; otherwise clone materialises them as plain files containing the target path. Keep symlink-dependent tests POSIX-gated.
- **Path length.** The 260-character limit applies unless long paths are enabled both in Windows and in git. Deep pipeline layouts under long user directories can cross it; the failure names the file, not the limit.
- **Credential managers** pop interactive dialogs and SSH prompts block. **No git subprocess may block on credentials** — disable terminal and SSH prompting, bound the call with a timeout, and surface "authentication needed" as a structured error.
- **Executable resolution.** `git` ships as `git.exe`, so the bare name happens to resolve — unlike npm (P4). Do not generalise from git's luck: every new tool invocation goes through `shutil.which` at its chokepoint.

## 3. The chokepoint-module rule

**Every external tool haute invokes gets exactly one module that owns its subprocess calls.** That module owns executable resolution, platform quirks (prompt suppression, timeouts, retry policy), error taxonomy — wrapping raw stderr, which may contain absolute paths and remote URLs, into typed errors — and auditability, so that one grep target answers "what do we run, and with what arguments".

This is already the de facto structure for git, npm/node, docker, and GPU queries. Route handlers contain no subprocess calls.

**Enforcement.** A source-scan test asserts that `subprocess` is imported only in the allowlisted chokepoint modules. Review line: **new external interface → its own chokepoint module, its own section in this catalogue, and non-Linux lane relevance assessed.**

## 4. Review checklist

The lines this catalogue justifies that no linter can enforce, in one place:

1. A `Path` reaches a persisted string only via `.as_posix()`; user text reaches generated source only via the sanitiser (P1).
2. A new byte-identity fixture root gets a `.gitattributes` `eol=lf` pin (P3).
3. A new subprocess call goes through its tool's chokepoint, resolves via `shutil.which`, and fails loud (P4, §3).
4. A new file writer uses the atomic-write helpers, not a hand-rolled temp-and-rename, and reports replace failures loudly (P5).
5. Any chmod is platform-guarded, with the Windows story stated (P6).
6. A new platform branch passes both type-checker targets and is confined to a helper (P7).
7. Frontend/backend parity code enumerates its codepoint set and has a parity test (P8).
8. A new native or optional dependency is green on all OS lanes before landing (P9).
9. A new virtualenv-touching script respects the environment override and the WSL split (P10).
10. A new network-touching git call is prompt-suppressed and timeout-bounded (§2).
