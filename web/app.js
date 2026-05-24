// Heimdall — web frontend. Loads Pyodide, runs heimdall.py's parser
// client-side against a dropped MeshMapper CSV, then offers download
// or upload.

const $ = (id) => document.getElementById(id);
const statusEl = $("status");
const resultsEl = $("results");
const summaryEl = $("summary");
const previewBody = $("preview").querySelector("tbody");
const dropEl = $("drop");
const fileInput = $("file");
const downloadBtn = $("download");
const uploadBtn = $("upload");
const uploadStatusEl = $("upload-status");
const dryrunEl = $("dryrun");
const dryOutputEl = $("dry-output");

// Public GitHub Pages deploys can't direct-upload (CORS), so the upload UI
// is hidden when we're on *.github.io. Everywhere else (localhost, self-
// hosted via serve.py, custom domain) the upload path is available.
const IS_PUBLIC_DEPLOY = document.documentElement.classList.contains("public-deploy");
const apikeyEl = $("apikey");
const apiurlEl = $("apiurl");
const versionPill = $("version-pill");

apikeyEl.value = localStorage.getItem("heimdall.apikey") || "";
apiurlEl.value = localStorage.getItem("heimdall.apiurl") || "https://wdgwars.pl/api/upload/";
apikeyEl.addEventListener("change", () => localStorage.setItem("heimdall.apikey", apikeyEl.value));
apiurlEl.addEventListener("change", () => localStorage.setItem("heimdall.apiurl", apiurlEl.value));

let pyodide = null;
let lastPayload = null;
let heimdallVersion = "?";

function setStatus(msg, cls = "") {
  statusEl.className = "status " + cls;
  statusEl.textContent = msg;
}

function setUploadStatus(msg, cls = "") {
  uploadStatusEl.className = "status " + cls;
  uploadStatusEl.textContent = msg;
}

async function bootPyodide() {
  setStatus("Loading Pyodide runtime (one-time, ~10 MB)...", "");
  pyodide = await loadPyodide({
    indexURL: "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/",
  });

  // Pyodide unvendors `ssl` from stdlib; heimdall.py imports urllib which
  // pulls it transitively. Web frontend uses fetch() so the urllib path is
  // dead, but top-level import has to resolve.
  setStatus("Loading runtime modules...", "");
  await pyodide.loadPackage(["ssl"]);

  // Pull heimdall.py from the same directory and write into Pyodide's FS.
  const src = await fetch("./heimdall.py").then(r => {
    if (!r.ok) throw new Error("could not fetch heimdall.py (" + r.status + ")");
    return r.text();
  });
  pyodide.FS.writeFile("/heimdall.py", src);
  try { pyodide.FS.mkdir("/tmp"); } catch (_) {}

  heimdallVersion = pyodide.runPython("import sys; sys.path.insert(0, '/'); import heimdall; heimdall.__version__");
  versionPill.textContent = "heimdall " + heimdallVersion;
  setStatus("Ready. Drop a MeshMapper CSV above.", "ok");
}

async function handleFile(file) {
  if (!pyodide) {
    setStatus("Runtime still loading, hold on a moment.", "warn");
    return;
  }
  resultsEl.classList.remove("show");
  setStatus(`Reading ${file.name}...`, "");
  const buf = await file.arrayBuffer();
  const bytes = new Uint8Array(buf);
  const safeName = file.name.replace(/[^a-zA-Z0-9._-]/g, "_") || "dropped.csv";
  const dst = "/tmp/" + safeName;
  pyodide.FS.writeFile(dst, bytes);
  setStatus(`Parsing ${file.name}...`, "");

  let result;
  try {
    result = pyodide.runPython(`
import json
from pathlib import Path
import heimdall

p = Path(${JSON.stringify(dst)})
records = heimdall.parse_meshmapper_csv(p)
json.dumps({"records": records, "format": "meshmapper-csv"})
`);
  } catch (e) {
    const raw = String(e.message || e);
    console.error("[heimdall] parse error:", raw);
    const lines = raw.trim().split("\n").map(l => l.trim()).filter(Boolean);
    const last = lines[lines.length - 1] || raw;
    let friendly = `Couldn't parse ${file.name}. `;
    if (/UnicodeDecodeError|codec can't decode/i.test(raw)) {
      friendly += "File looks binary — Heimdall expects a text CSV export.";
    } else {
      friendly += `(${last})`;
    }
    setStatus(friendly, "err");
    return;
  }

  const parsed = JSON.parse(result);
  if (!parsed.records.length) {
    setStatus(
      `No meshcore nodes found in ${file.name}. ` +
      `Check that the file is a MeshMapper "Logs → Copy CSV" export with the header ` +
      `'timestamp,repeater_id,snr,rssi,path_length,header,latitude,longitude,path_hops'.`,
      "warn",
    );
    return;
  }

  lastPayload = {
    records: parsed.records,
    filename: file.name.replace(/\.[^.]+$/, "") + ".wdgwars.json",
    format: parsed.format,
  };

  // Build a dump1090-fa-shaped download payload — same envelope shape
  // as the upload, useful for offline inspection.
  lastPayload.web = {
    "networks": [],
    "aircraft": [],
    "meshcore_nodes": parsed.records,
  };

  renderResults(parsed, file.name);
}

function renderResults(parsed, filename) {
  const n = parsed.records.length;
  summaryEl.innerHTML =
    `<span class="pill ok">${n} meshcore nodes</span>` +
    `<span class="pill">format: ${parsed.format}</span>` +
    `<span class="pill">${filename}</span>`;

  previewBody.innerHTML = "";
  for (const r of parsed.records.slice(0, 6)) {
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td>${r.node_id || ""}</td>` +
      `<td>${r.type || ""}</td>` +
      `<td>${(r.lat ?? "").toString().slice(0,9)}</td>` +
      `<td>${(r.lon ?? "").toString().slice(0,9)}</td>` +
      `<td>${r.rssi ?? ""}</td>` +
      `<td>${r.snr ?? ""}</td>`;
    previewBody.appendChild(tr);
  }
  if (n > 6) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="6" style="color:var(--muted)">... and ${n - 6} more</td>`;
    previewBody.appendChild(tr);
  }
  resultsEl.classList.add("show");
  setStatus(`Parsed ${n} meshcore nodes from ${filename}.`, "ok");
  setUploadStatus("");
}

downloadBtn.addEventListener("click", () => {
  if (!lastPayload) return;
  const blob = new Blob([JSON.stringify(lastPayload.web, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = lastPayload.filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
});

uploadBtn.addEventListener("click", async () => {
  if (!lastPayload) return;
  const key = apikeyEl.value.trim();
  const url = apiurlEl.value.trim();
  if (!key) {
    setUploadStatus("Set an API key in Settings below first.", "warn");
    return;
  }
  if (!url) {
    setUploadStatus("Set an upload URL.", "warn");
    return;
  }

  const BATCH = 1000;
  const records = lastPayload.records;
  const chunks = Math.ceil(records.length / BATCH);
  const dryRun = dryrunEl.checked;
  uploadBtn.disabled = true;
  let totalImported = 0, totalSeen = 0;
  let dryLog = "";
  if (dryRun) {
    dryOutputEl.textContent = "";
    dryOutputEl.classList.remove("show");
  }
  try {
    for (let i = 0; i < records.length; i += BATCH) {
      const chunk = records.slice(i, i + BATCH);
      const idx = Math.floor(i / BATCH) + 1;
      setUploadStatus(
        (dryRun ? "[DRY] Building " : "Uploading ") +
        `chunk ${idx}/${chunks} (${chunk.length} nodes)...`,
        dryRun ? "warn" : "",
      );

      // Build envelope via Python so signature is byte-identical to the CLI.
      pyodide.globals.set("_chunk_records", chunk);
      pyodide.globals.set("_api_key", key);
      const envelope = pyodide.runPython(`
import json, base64, hmac, hashlib, secrets
_payload = {"networks": [], "aircraft": [], "meshcore_nodes": _chunk_records.to_py()}
_body_json = json.dumps(_payload, separators=(",", ":"))
_data_b64 = base64.b64encode(_body_json.encode()).decode()
_nonce = secrets.token_hex(8)
_sig = hmac.new(_api_key.encode(), (_nonce + _data_b64).encode(), hashlib.sha256).hexdigest()
json.dumps({"data": _data_b64, "nonce": _nonce, "sig": _sig})
`);

      if (dryRun) {
        const env = JSON.parse(envelope);
        const keyMask = key.length > 8
          ? key.slice(0, 4) + "..." + key.slice(-4)
          : "***";
        dryLog +=
          `─── CHUNK ${idx}/${chunks} (${chunk.length} nodes) ─────────────\n` +
          `POST ${url}\n` +
          `Content-Type: application/json\n` +
          `X-API-Key:    ${keyMask}\n` +
          `User-Agent:   heimdall-web/${heimdallVersion}\n` +
          `Accept:       application/json\n\n` +
          `body bytes:   ${envelope.length}\n` +
          `nonce:        ${env.nonce}\n` +
          `sig (sha256): ${env.sig}\n` +
          `data (b64, first 80): ${env.data.slice(0,80)}${env.data.length > 80 ? "..." : ""}\n\n`;
        continue;
      }

      const resp = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-API-Key": key,
          "Accept": "application/json",
          "User-Agent": "heimdall-web/" + heimdallVersion,
        },
        body: envelope,
      });
      const txt = await resp.text();
      if (!resp.ok) {
        setUploadStatus(`Chunk ${idx} failed: HTTP ${resp.status} ${txt.slice(0,200)}`, "err");
        return;
      }
      try {
        const data = JSON.parse(txt);
        totalImported += data.meshcore_imported || 0;
        totalSeen += data.meshcore_already_seen || 0;
      } catch (_) { /* server returned non-JSON; ignore counters */ }
    }
    if (dryRun) {
      dryOutputEl.textContent = dryLog;
      dryOutputEl.classList.add("show");
      setUploadStatus(
        `[DRY] Built ${chunks} chunk(s) — ${records.length} nodes. ` +
        `Nothing sent. Inspect the request below.`,
        "warn",
      );
    } else {
      setUploadStatus(
        `Done — ${records.length} nodes sent, ${totalImported} imported, ${totalSeen} already-seen.`,
        "ok",
      );
    }
  } catch (e) {
    const msg = (e.message || String(e));
    const looksLikeCors =
      /failed to fetch/i.test(msg) ||
      /networkerror/i.test(msg) ||
      /load failed/i.test(msg);
    if (looksLikeCors && !apiurlEl.value.startsWith("/")) {
      setUploadStatus(
        "Direct upload blocked by the WDG server's CORS policy. " +
        "Click 'Download JSON' and upload via wdgwars.pl, or run Heimdall " +
        "self-hosted (see docs for the local-proxy setup).",
        "warn",
      );
    } else {
      setUploadStatus("Upload error: " + msg, "err");
    }
  } finally {
    uploadBtn.disabled = false;
  }
});

// Drag-and-drop wiring.
["dragenter", "dragover"].forEach(ev =>
  dropEl.addEventListener(ev, e => { e.preventDefault(); dropEl.classList.add("drag"); }));
["dragleave", "drop"].forEach(ev =>
  dropEl.addEventListener(ev, e => { e.preventDefault(); dropEl.classList.remove("drag"); }));
dropEl.addEventListener("drop", e => {
  const f = e.dataTransfer.files[0];
  if (f) handleFile(f);
});
dropEl.addEventListener("click", () => fileInput.click());
dropEl.addEventListener("keydown", e => {
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); }
});
fileInput.addEventListener("change", () => {
  if (fileInput.files[0]) handleFile(fileInput.files[0]);
});

bootPyodide().catch(e => setStatus("Failed to load runtime: " + (e.message || e), "err"));
