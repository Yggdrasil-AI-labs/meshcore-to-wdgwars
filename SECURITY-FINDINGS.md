# Security review — findings & disposition

On **2026-06-21**, as part of bringing the WDGoWars feeder family onto a common
gated CI pipeline (pytest + coverage → SonarCloud → Snyk), `heimdall.py` was
reviewed for the same classes of issue that SonarCloud's SAST flagged in the
sibling **adsb-to-wdgwars (Muninn)** repo — path traversal, command/argument
injection in scheduler artifacts, insecure temp-directory use, and unsafe
database opens.

**Outcome: no remediation needed.** Like wigle-to-wdgwars (and unlike Muninn,
which carried 21 accepted findings), Heimdall was already defended against every
category. This document records that review, and the posture is now backed by
regression tests ([`tests/test_security.py`](tests/test_security.py)).

## Why the Muninn finding classes don't apply here

| Muninn finding class | Status in Heimdall |
|---|---|
| **S2083** — path traversal into a watch state file | **N/A** — Heimdall has no watch mode and writes no state file. It uploads a CSV the operator names; it never derives a second path from a watched directory. |
| **S5443** — use of a publicly-writable / `/tmp` directory | **N/A** — no `tempfile`, `gettempdir`, or hardcoded `/tmp` path anywhere. Config lives under the per-OS user config dir (`%APPDATA%\heimdall` / XDG). |
| **S8706** — SQLite connection built from a filename | **N/A** — Heimdall has no SQLite support. |
| **S6350 / S8705** — command / OS-command argument from untrusted data | **Already defended** — the scheduler renderers interpolate the user-supplied CSV path *and* the interpreter/script paths, but every argv element is passed through `_shell_quote()` for systemd/cron, and the time is validated by `_validate_hhmm()`. The Windows `schtasks` action double-quotes paths that contain spaces; Windows paths cannot contain a literal `"`. |
| **S8707 / S6549** — path construction from CLI args | **Accept-by-design** — the CLI path inputs are the positional `csv` and `--schedule-csv PATH`. The upload path is **read-only** and chosen by the operator; the schedule path is `resolve()`d and then shell-quoted into the unit. As documented in `SECURITY.md`, this is a local operator CLI: there is no sandbox root to confine to. |

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

## A note for when SonarCloud is enabled

This repo is not yet imported into SonarCloud. Once it is (and the `SONAR_TOKEN`
/ `SNYK_TOKEN` secrets are added — see [CI.md](CI.md)), the scanner may still
raise **security hotspots** (review-required, not vulnerabilities) on the
read-only CLI path inputs and the `subprocess` calls in the installers. The
disposition above is the rationale to mark those *Safe* / *Accepted*: the inputs
are operator-controlled and trusted, no `shell=True` is used, and the scheduler
arguments are quoted.
