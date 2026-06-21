# Security Notes

## What this tool does

- Reads a local CSV file (your MeshMapper export, or a sibling format
  once those land).
- Normalises each row to the WDGoWars meshcore schema.
- Optionally POSTs the records to `https://wdgwars.pl/api/upload/` as
  an HMAC-signed JSON envelope.

That's the whole footprint of the v0.1.0 CLI.

## What this tool **does not** do

- ❌ **No telemetry or analytics.** The only outbound traffic is to
  `https://wdgwars.pl/api/upload/`, and only when you invoke an upload
  command without `--dry-run`. Override the endpoint with `--endpoint`.
- ❌ **No version-check, no auto-update.** Heimdall does not phone home,
  not even to GitHub's releases API. (Muninn has an opt-out daily HEAD
  to releases for update notifications; Heimdall may add the same later,
  with the same opt-out, but does not today.)
- ❌ **No `eval`, `exec`, `os.system`, or `shell=True` subprocess calls.**
  No command-injection paths.
- ❌ **No remote code download/execution.** Pure stdlib Python; nothing
  is pulled from PyPI or any other index at runtime.
- ❌ **No data sent anywhere except the configured WDGoWars endpoint
  when you explicitly opt in.**

## API key handling

- Resolution: `--api-key` flag → `$WDGWARS_API_KEY` env var.
- The key is **not persisted to disk** in v0.1.0. Every invocation
  re-reads it. A future `--save-key` flag (mirroring Muninn) will
  introduce persistence with `mode 0600` and symlink-refusal, same
  shape as Muninn's `~/.config/muninn/api.key`.
- The key is sent over HTTPS only, in the `X-API-Key` request header
  to `wdgwars.pl`. The TLS context is Python's `ssl.create_default_context()`
  default — system trust store, hostname verification on, TLS 1.2+.
- Heimdall does not print the API key in any output, success or
  failure. If it ever shows up in a server-response body or stack
  trace, that's a bug — please open an issue.

## What the API key can do

The WDGoWars API key authorises you to submit observations under your
account. If it leaks, an attacker could:

- Submit fake mesh / WiFi / BLE / aircraft captures under your name.
- Read your account stats via `GET /api/me`.

It cannot (as far as we know):

- Change your password.
- Withdraw money / make purchases.
- Affect other users' accounts.

If you suspect your key has leaked, rotate it on the WDGoWars site and
re-run Heimdall with the new key.

## HMAC envelope

The envelope shape is byte-identical to Muninn's:

```python
payload   = {"networks": [], "aircraft": [], "meshcore_nodes": chunk}
body_json = json.dumps(payload, separators=(",", ":"))
data_b64  = base64.b64encode(body_json.encode()).decode()
nonce     = secrets.token_hex(8)
sig       = hmac.new(api_key.encode(),
                     (nonce + data_b64).encode(),
                     hashlib.sha256).hexdigest()
envelope  = {"data": data_b64, "nonce": nonce, "sig": sig}
```

`json.dumps(..., separators=(",", ":"))` and `ensure_ascii=True`
(Python's default) are load-bearing — different whitespace or
non-ASCII handling produces a different signature.

## Static-analysis review

A review of Heimdall against the SonarCloud SAST finding classes (path
traversal, command/argument injection, insecure temp use, unsafe DB opens)
found nothing to remediate — the scheduler arguments (including the CSV path)
are shell-quoted, the API key never reaches the command line, and `save_key`
refuses symlinks and uses mode 600. The full write-up is in
[SECURITY-FINDINGS.md](SECURITY-FINDINGS.md); the posture is locked by
`tests/test_security.py`.

## Reporting issues

Open a GitHub issue, or DM the maintainer on the WDGoWars community
channels. For anything potentially exploitable upstream (in WDGoWars
itself), please disclose privately to LOCOSP first rather than filing
a public issue here.
