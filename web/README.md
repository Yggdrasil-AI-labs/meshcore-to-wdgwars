# Heimdall — web frontend

A static, single-page version of Heimdall that runs entirely in the
browser. The Python parser from `../heimdall.py` executes client-side
via [Pyodide](https://pyodide.org/), so a dropped MeshMapper CSV never
leaves the user's machine until they click **Upload**.

**Headless / CLI users:** ignore this directory. The CLI in the repo
root (`heimdall.py`) is completely independent — no shared runtime,
no shared deps, no display required.

## What's here

- `index.html` — the page itself (inline CSS, matches the WDGoWars cyan-on-black aesthetic)
- `app.js` — drop-zone wiring + Pyodide bootstrap + HMAC upload
- `heimdall.py` — build-time copy of the root `heimdall.py` (the parser runs as-is)
- `serve.py` — optional self-hosted server that proxies `/api/upload/` to wdgwars.pl (so direct browser upload works on a local install)

## Local preview (read-only — parse / download only)

Pyodide and ES modules require an HTTP server — opening `index.html`
directly via `file://` will not work.

```bash
cd web
python3 -m http.server 8000
# then visit http://localhost:8000
```

`http.server` only serves static files, so the in-browser **Direct
upload** button will still fail with CORS — the upload UI shows up
because the page is no longer on `*.github.io`, but the actual POST
to `wdgwars.pl` is blocked. For local preview / debugging this is fine.

## Self-hosted with direct upload (`serve.py`)

For self-hosters who want the in-browser upload button to actually
work, the repo ships a small stdlib-only server that serves the static
files **and proxies `/api/upload/` to `wdgwars.pl`**.

```bash
cd web
python3 serve.py          # binds 127.0.0.1:8765 by default
# python3 serve.py --port 8000 --host 0.0.0.0   # bind everywhere
```

Open the page, then in **Settings → Endpoint** change the value from
`https://wdgwars.pl/api/upload/` to the relative path:

```
/api/upload/
```

Now Direct upload routes through the local proxy and reaches WDG. Your
API key never leaves your machine in the page — it travels to `serve.py`
over loopback and then to `wdgwars.pl` server-to-server.

## Public deploy: parse-only

When the site is served from `*.github.io`, the upload UI is hidden
entirely (button, dry-run toggle, uplink config). The page becomes a
pure converter: drop → preview → download. Players take the downloaded
JSON to wdgwars.pl's normal upload form.

## Keeping `web/heimdall.py` in sync

The web bundle ships a copy of the root parser. Before deploying:

```bash
cp heimdall.py web/heimdall.py
```

The GitHub Actions workflow at `.github/workflows/pages.yml` does this
copy automatically on every push to `main` that touches `web/**` or
`heimdall.py`, so production is always in sync.

## Deployment

Static. Any static host works — GitHub Pages, Netlify, Cloudflare
Pages, an `nginx` block on your own box. There is no server-side
component (the optional `serve.py` is for self-hosters who want the
direct-upload feature without the public deploy's CORS hiccup).
