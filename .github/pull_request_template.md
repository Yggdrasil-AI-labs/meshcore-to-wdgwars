<!--
Reviewer's verification checklist. Fill out each box before merging.
Reasoning: today's pattern was shipping fixes that surfaced new issues
the moment a user touched the change. Slow-down at merge time prevents
the next round of that.
-->

## Summary

<!-- 1-3 sentences describing the change and its motivation. -->

## What changed (user-facing)

<!-- What an end user notices, if anything. New flag? New default? New
prompt? "Internal only" is a valid answer for refactors. -->

## Verification

- [ ] Tests pass locally (`python -m unittest discover tests/`).
- [ ] If the change touches `setup.sh` / `run.sh` / `update.sh`: ran the affected script on a fresh clone (or in a `/tmp` copy) without an existing `.venv/`.
- [ ] If the change touches the upload path: live-tested with `HEIMDALL_TEST_ALLOW_LIVE_KEY=1` on a host with a sacrificial key, OR with `--dry-run`.
- [ ] CHANGELOG.md has an entry for this change.
- [ ] `__version__` in `heimdall.py` is bumped if user-visible behavior changed (skip for docs-only / CI-only changes).
- [ ] No `Co-Authored-By: Claude` trailer in any commit (per public-repo convention).
- [ ] No `zhn*` hostnames, real names, or lab-internal references in code/commits/README.

## Notes for reviewer

<!-- Anything that needs context, links to related PRs, links to user
reports that motivated this, etc. -->
