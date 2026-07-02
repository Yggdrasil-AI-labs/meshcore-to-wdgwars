# Changelog

All notable changes to Heimdall are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.4.1] - Fix false "older version available" update notice

### Fixed

- The daily update check compared the latest GitHub release tag to
  `__version__` with a plain inequality, so any mismatch — including the
  release process lagging behind a version bump already in code — was
  reported as "a newer version is available," even when the tag was
  actually older (issue #9). `_check_for_update()` now orders versions as
  int tuples and only surfaces the notice when the tag is genuinely higher.
- No GitHub Release had been published for 0.3.1 or 0.4.0, so
  `/releases/latest` was legitimately still returning `v0.3.0` — the stale
  release is also being cut alongside this fix.

## [0.4.0] - Real MeshMapper formats: multi-section CSV + offline JSON

First release driven by real-world capture data (issue #1 baseline,
contributed by @nicolasrata, 2026-06-27). The parser was written against an
assumed flat "Copy CSV" shape; a real MeshMapper export is a multi-section
file and the offline capture is JSON. The old parser returned **zero** nodes
on both.

### Added

- Multi-section MeshMapper CSV parsing: `--- TX Log ---`, `--- RX Log ---`,
  `--- DISC Log ---` blocks, each with its own header. The heard nodes are
  packed into a trailing `events` (TX) / `nodes` (DISC) column as
  `ID(snr)` / `ID(R)(snr)` tokens; one record is emitted per heard node.
- MeshCore offline ping-log JSON parsing (`pings[]` of `DISC` / `RX`).
  `DISC` pings carry full telemetry including real `local_rssi` + `local_snr`;
  `RX` pings carry a `heard_repeats` SNR token.
- `parse_file()` format dispatch (extension first, then content sniff) and a
  matching `parse_offline_json()` / `parse_meshmapper_text()` API. The CLI and
  the web dropzone both accept CSV or JSON now.
- Scrubbed fixtures: `examples/meshmapper-sections.csv`,
  `examples/offline-pings.json`. Tests covering both new formats.

### Changed

- Flat single-section "Copy CSV" exports still parse exactly as before
  (`examples/sample.csv` is unchanged) — the section logic only engages when
  `--- X Log ---` markers are present.
- Web (Pyodide) parser brought to parser parity with the root module and to
  v0.4.0; its dropzone calls `parse_file` and reports the detected format.

### Known limitation

- CSV `TX`/`RX`/`DISC` sections and JSON `RX` pings log SNR + receiver noise
  floor but no per-node RSSI, so those records carry `rssi: null`. Only
  offline-JSON `DISC` pings have a real RSSI. Node-type markers other than
  `(R)` (repeater) are normalised to the default pending a confirmed sample.

## [Unreleased] - CI quality gates + security review

Tooling and CI only — no change to `heimdall.py` behavior, so no version bump.

Brings Heimdall onto the same gated CI pipeline as the sibling
adsb-to-wdgwars (Muninn) and wigle-to-wdgwars repos: pytest + coverage →
SonarCloud quality gate → Snyk dependency scan → gated release-artifact build.
The `sonarcloud` / `snyk` jobs stay red until the repo is imported into
SonarCloud and the `SONAR_TOKEN` / `SNYK_TOKEN` Actions secrets are added (see
CI.md); the test and coverage stage is independent and passes on its own.
(Heimdall is pure stdlib, so the Snyk stage is effectively a no-op — kept for
family parity.)

A review against the SonarCloud SAST finding classes found nothing to
remediate — the scheduler arguments (including the CSV path) are shell-quoted,
the API key never reaches the command line, and `save_key` refuses symlinks
and uses mode 600. See SECURITY-FINDINGS.md.

### Added

- `.github/workflows/ci-quality-gates.yml` — gated quality + security pipeline.
- `sonar-project.properties`, `requirements-dev.txt`, `pyproject.toml`
  (pytest + coverage config with a regression floor), and `CI.md`.
- `tests/test_security.py` — regression tests locking in the existing
  defenses (shell-quoting incl. the CSV path, no-key-in-argv, safe key-file
  writes).
- `SECURITY-FINDINGS.md` — the security review write-up; pointer added to
  `SECURITY.md`.

## [0.3.1] - 2026-06-05 - Structured 413 message for the 15 MB upload cap

LOCOSP rolled out a temporary 15 MB body cap on every wdgwars.pl upload
endpoint on 2026-06-05 with a structured 413 envelope
(`{error: payload-too-large, max_bytes, received, ...}`). Heimdall does
not use gungnir (its HMAC transport is pure-stdlib local code), so the
gungnir v0.1.3 upgrade does not reach it. This release patches the same
behavior into Heimdall directly: cosmetic log-message change only, no
control-flow change.

Mesh-node payloads are kilobytes per cycle, well under the 15 MB cap,
so this is defensive insurance. If the 413 does fire, the error line
now names the cap and shows `max_bytes` + `received` instead of a
generic "rejected by wdgwars.pl (HTTP 413): payload-too-large".

### Changed

- Upload rejection branch in `main()` checks for the
  `payload-too-large` envelope shape and prints a structured line.
  Other 4xx / 5xx rejections keep the generic format.

### Not changed

- Upload control flow: 413 still returns `rc=1` and skips the batch,
  same as any other rejection. There is no auto-retry blast.
- No new tests: the 413 branch is print-only and the existing test
  suite has no upload-side coverage to extend. Sibling tools
  (gungnir v0.1.3, wigle-to-wdgwars v1.4.0) carry the contract tests
  for the envelope shape.

## [0.3.0] - 2026-06-03 - Family alignment: scheduler + naming + safety nets

Largest end-user-visible alignment of the 2026-06-03 feeder-family audit
sweep. Brings Heimdall to feature parity with Muninn + wigle-to-wdgwars
for the install / schedule / daily-run flow, and aligns the flag names
so muscle memory transfers across the family.

### Added

- `--schedule` / `--unschedule` / `--schedule-csv PATH` /
  `--schedule-time HH:MM` / `--schedule-dry-run`. Installs the right
  artifact per OS: user systemd timer on Linux-with-systemd, user
  crontab on macOS / Linux-without-systemd, scheduled task on
  Windows. Default time `03:00`. Every artifact carries a
  `# managed-by-heimdall` marker so the uninstaller can find and
  remove it cleanly. The API key is **never** baked into the unit
  file / cron line / schtasks action — the saved-on-disk key file
  is read at run-time instead.
- `--key` flag (canonical name, matches Muninn + wigle).
- `--api-url` flag (canonical name, matches Muninn).
- `scripts/smoke.sh` — pre-release smoke (README linter + AST/import
  + offline tests + `--version`/`--help` + Linux/systemd unit-write
  roundtrip + no-key-leak assertion).
- `scripts/check_readme_examples.py` — README linter ported from
  Muninn / wigle. Auto-detects the entrypoint script. Catches
  `python3 heimdall.py ...` examples that drift outside venv-teaching
  blocks. Heimdall is stdlib-only and works without a venv, so two
  intentional bootstrap examples are annotated
  `# direct invocation` to keep the linter quiet.
- README `## Running on a schedule` section with per-OS mechanism
  table.
- README `## Troubleshooting` section.
- `tests/test_scheduler.py` — 17 new tests covering the pure
  renderers (`render_systemd_units` / `render_cron_line` /
  `render_schtasks_create`), HH:MM validation, the schedule mechanism
  selector, and a no-key-leak assertion that the renderers never
  bake credentials into the artifact.

### Changed

- `_prompt_yes_no` now emits an explicit newline after consuming a
  piped-stdin answer. Interactive TTY input gets one from the
  terminal; piped input doesn't, which used to glue the next
  section header onto the prompt line in scripted runs.
- `setup.sh` / `run.sh` / `update.sh` now `[ -t 0 ]`-gate the
  trailing `Press any key to close...`. Piped / non-TTY invocations
  used to hang indefinitely on that line.
- README code-block examples rewritten from `python3 heimdall.py
  ...` to `./run.sh ...` (the venv-aware shim).

### Deprecated

- `--api-key` flag: replaced by `--key`. The old name still works
  and now emits a one-line deprecation note on stderr. Will be
  removed in `v0.4`.
- `--endpoint` flag: replaced by `--api-url`. Same one-release
  deprecation treatment.

### Not changed (out of audit scope)

- gungnir extraction: Heimdall is still pure stdlib with an inlined
  HMAC envelope. The `v0.2-gungnir` branch from earlier never landed
  on `main`. Architectural refactor, not alignment work — filed as
  a tracked note for a separate session.

## [0.2.2] - 2026-06-01 - setup.sh: PEP 668 / Bookworm fix

`setup.sh`, `run.sh`, and `update.sh` now install Heimdall into a
project-local `.venv/` instead of the system Python.

On Raspberry Pi OS Bookworm, Debian 12+, Ubuntu 23.04+, and Homebrew
Python, the previous `python3 -m pip install -r requirements.txt` line
errored out with `error: externally-managed-environment` (PEP 668).
The script crashed before saving the API key. Same flaw as Muninn's
v2.0.8 fix, found by sweeping the feeder family after a Pi24 user
reported the Muninn crash in the WDGoWars Discord.

The wrappers now `python3 -m venv .venv` on first run and call
`.venv/bin/python` for every subsequent step. `run.sh` and `update.sh`
detect the venv and reuse it. If `python3 -m venv` itself fails
(the `python3-venv` apt package missing on some Pi images), the
script prints the exact `sudo apt install -y python3-venv python3-full`
line and exits cleanly instead of leaving a half-installed state.

Heimdall has no third-party deps today, so this is mostly future-proofing
the wrapper — but it removes the Bookworm crash that bit anyone running
`./setup.sh` to save their API key.

### Fixed

- `setup.sh` no longer fails with `externally-managed-environment` on
  PEP 668 distros. Installs into `.venv/` instead.
- `run.sh` and `update.sh` now use `.venv/bin/python` when present.

## [0.2.1] - 2026-05-29 - Harden install/update path (preventive)

Heimdall has no third-party dependencies today and isn't broken by
the install issue that hit Muninn 2.0.1 and wigle-to-wdgwars 1.1.0
(see those changelogs for context). This release applies the same
hardening pattern preventively, so that when Heimdall eventually
migrates its inline HMAC code to the shared `gungnir` library
(matching its siblings), the bootstrap is already robust and no
user hits a `ModuleNotFoundError` on first install or first update
after the dep is added.

### Fixed

- **`heimdall.py --update` now refreshes `requirements.txt` and runs
  `python -m pip install --upgrade -r requirements.txt` against
  `sys.executable` after updating the script.** Today this is a no-op
  (requirements.txt is comment-only); the helper exits early without
  printing a misleading "installing deps" banner when there's nothing
  to install. The plumbing is in place so a future dep-bumping release
  self-heals without needing another wrapper-script revision.

### Added

- **`setup.bat` / `setup.sh` / `update.bat` / `update.sh` now check
  for Python ≥ 3.10 first, fetch `requirements.txt` from `main`, run
  `pip install --upgrade -r requirements.txt`, then invoke
  `heimdall.py`.** Order matters across versions that add or bump a
  dep — pip has to know about the new dep before heimdall.py tries to
  import it. Previously the wrappers just ran `python heimdall.py
  --setup` (or `--update`) with no dep management at all.

- `_fetch_raw(path, dest)` and `_pip_install_requirements(script_dir)`
  helpers in `heimdall.py` — used by `--update` to refresh sibling
  files atomically and invoke pip against the currently-running
  interpreter.

## [0.2.0]

### Added
- **`--setup` wizard.** Interactive one-time API-key flow, validates the
  key against `/api/me`, saves it to `~/.config/heimdall/api.key` (mode
  `0600` on Unix) or `%APPDATA%\heimdall\api.key` on Windows. Refuses to
  write through a symlink.
- **`--save-key KEY`.** Non-interactive equivalent for scripted installs.
- **`--whoami`.** Hit `/api/me` and print username + node counts to
  confirm a stored key is good.
- **Persistent API-key resolution.** `--api-key` flag, then
  `$WDGWARS_API_KEY`, then the saved key file. After `--setup`, no key
  flags are needed for daily use.
- **`--update`.** Self-update via `git pull --ff-only` for clones, or
  raw-GitHub fetch + atomic replace for ZIP installs. Syntax-validates
  the downloaded file before swapping.
- **Daily version-check banner.** Quiet GitHub release-API ping cached
  for 24h. Disable per-run with `--no-version-check` or globally with
  `--quiet`. Three-second timeout, never blocks an upload.
- **Helper scripts.** `setup.sh` / `setup.bat`, `run.sh` / `run.bat`,
  `update.sh` / `update.bat` in the repo root for double-click users.
- **Explicit SSL context** on every outbound request (defense in depth).

### Changed
- README rewritten to cover both git-clone and ZIP-download install
  paths, the new key-persistence flow, and the `--update` workflow.


## [0.1.0] — initial alpha

### Added
- **CLI** (`heimdall.py`) that parses a MeshMapper "Logs → Copy CSV"
  export, normalises each row to the WDGoWars meshcore schema
  (`timestamp,node_id,type,name,lat,lon,rssi,snr`), and uploads via the
  same HMAC envelope and `/api/upload/` endpoint Muninn uses for
  aircraft.
- **Three modes:** `--preview` (print the first six normalised rows),
  `--dry-run` (build the signed envelope without POSTing), default
  (real upload, in batches of 1000).
- **API key resolution:** `--api-key` flag or `$WDGWARS_API_KEY` env var.
- **Scrubbed sample** at `examples/sample.csv` so the parser can be
  exercised without exposing real GPS data. All `lat,lon` zeroed; the
  upstream ingest rejects `0,0` so the file cannot accidentally cause
  a real upload.

### Known limitations
- No web / Pyodide frontend yet. Coming in v0.2.0.
- Only one input format supported (MeshMapper CSV). Meshcore Companion
  serial, MQTT, and Cardputer ADV log support are planned once sample
  data lands.
- The `type` field defaults to `"repeater"` and the `name` field
  defaults to empty string. These are best guesses pending confirmation
  with the WDGoWars maintainers; revisit once we have a verdict.
- No `--save-key` persistence yet. Pass the key every invocation or use
  the env var.
- No version-check, no telemetry, no analytics. May add an opt-in
  update check later, mirroring Muninn's daily HEAD-to-releases pattern.
