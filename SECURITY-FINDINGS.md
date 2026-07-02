# Security review — findings & disposition

On **2026-06-21**, as part of bringing the WDGoWars feeder family onto a common
gated CI pipeline (pytest + coverage → SonarCloud → Snyk), `heimdall.py` was
reviewed for the same classes of issue that SonarCloud's SAST flagged in the
sibling **adsb-to-wdgwars (Muninn)** repo — path traversal, command/argument
injection in scheduler artifacts, insecure temp-directory use, and unsafe
database opens.

**Original outcome (2026-06-21): no remediation needed.** Like wigle-to-wdgwars
(and unlike Muninn, which carried 21 accepted findings), Heimdall was already
defended against every category. This document records that review, and the
posture is backed by regression tests
([`tests/test_security.py`](tests/test_security.py)).

**Update (2026-07-02):** a later change added offline-JSON parsing and a
sniff-based `parse_file()`, whose `read_text()` sites reached the untrusted argv
capture path unvalidated. SonarCloud flagged **3 × S8707** on them. Now
**fixed** — see [the 2026-07-02 remediation](#2026-07-02--s8707-re-flagged-after-offline-json-parsing) below.

## Why the Muninn finding classes don't apply here

| Muninn finding class | Status in Heimdall |
|---|---|
| **S2083** — path traversal into a watch state file | **N/A** — Heimdall has no watch mode and writes no state file. It uploads a CSV the operator names; it never derives a second path from a watched directory. |
| **S5443** — use of a publicly-writable / `/tmp` directory | **N/A** — no `tempfile`, `gettempdir`, or hardcoded `/tmp` path anywhere. Config lives under the per-OS user config dir (`%APPDATA%\heimdall` / XDG). |
| **S8706** — SQLite connection built from a filename | **N/A** — Heimdall has no SQLite support. |
| **S6350 / S8705** — command / OS-command argument from untrusted data | **Already defended** — the scheduler renderers interpolate the user-supplied CSV path *and* the interpreter/script paths, but every argv element is passed through `_shell_quote()` for systemd/cron, and the time is validated by `_validate_hhmm()`. The Windows `schtasks` action double-quotes paths that contain spaces; Windows paths cannot contain a literal `"`. |
| **S8707 / S6549** — path construction from CLI args | **Fixed (2026-07-02)** — the CLI path inputs are the positional `csv` and `--schedule-csv PATH`. Both are now canonicalised at the boundary via `_user_path()` (control-char rejection + `expanduser()` + `resolve()`) before any read or before being shell-quoted into a unit. As documented in `SECURITY.md`, this is a local operator CLI with no sandbox root to confine to, so the normalise (not confine) disposition matches Muninn's. |

## Existing defenses this review confirmed (now under test)

- **The CSV path is shell-quoted into the schedule.** `render_systemd_units` /
  `render_cron_line` run every argv element — including the operator's CSV
  path — through `_shell_quote()`, so a path containing a shell metacharacter
  can never execute. Locked by `RendererCsvPathQuotingTests`.
- **The API key never hits the command line.** `_schedule_argv()` builds
  `[python, script, csv]` only — no `--key` / `--api-key` — so the secret never
  lands in a unit file, crontab line, or schtasks action. Locked by
  `ScheduleArgvSecretTests`.
- **The key file is written safely.** `save_key()` refuses to write through a
  symlink (dotfile-redirect defence) and creates the file with
  `O_CREAT | O_TRUNC` and mode `0o600` from the start (no world-readable race).
  Locked by `SaveKeyTests`.
- **Schedule time is validated.** `_validate_hhmm()` rejects out-of-range and
  non-numeric input before it can reach a rendered command. Locked by
  `RendererCsvPathQuotingTests.test_time_is_validated_before_rendering`.

## 2026-07-02 — S8707 re-flagged after offline-JSON parsing

When the repo's `ci-quality-gates` SonarCloud step first ran green against a
valid org token, the scanner flagged **3 × `pythonsecurity:S8707` (MAJOR)** on
`heimdall.py`:

| Line | Read site | Taint path |
|---|---|---|
| `parse_meshmapper_csv` | `path.read_text(...)` | argv `csv` → `parse_file` → here |
| `parse_offline_json` | `json.loads(path.read_text(...))` | argv `csv` → `parse_file` → here |
| `parse_file` | `path.read_text(...)` (format sniff) | argv `csv` → here |

These read sites post-date the original 2026-06-21 review (offline-JSON support
and the sniff-based dispatcher were added later), so the untrusted argv path
reached `read_text` without passing the boundary check the scheduler path
already had.

**Fix:** ported Muninn's `_UnsafeInput` / `_reject_control_chars` / `_user_path`
helpers and canonicalise `args.csv` **once at the boundary** in `main()` before
the existence check and `parse_file()` call (and switched `--schedule-csv` to
the same helper). Downstream parsers now receive a validated, absolute path, so
the taint no longer reaches `read_text`. Locked by
`UserPathValidationTests` in [`tests/test_security.py`](tests/test_security.py).

The disposition remains **normalise, not confine**: this is a local operator CLI
with no sandbox root, so the canonical fix is to reject control chars and
collapse traversal, not to jail reads under a fixed directory.

## A note on remaining SonarCloud hotspots

SonarCloud may still raise **security hotspots** (review-required, not
vulnerabilities) on the read-only CLI path inputs and the `subprocess` calls in
the installers. The dispositions above are the rationale to mark those *Safe* /
*Accepted*: the inputs are operator-controlled and trusted, no `shell=True` is
used, and the scheduler arguments are quoted.
