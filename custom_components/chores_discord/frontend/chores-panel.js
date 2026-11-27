// Chores Discord Bot: the bin-training page in Home Assistant's sidebar.
// Plain JavaScript, no build step. Talks to the integration over websocket
// (chores_discord/*) and uses Home Assistant's theme variables throughout.

const STYLE = `
:host { display:block; background:var(--primary-background-color); color:var(--primary-text-color);
  font-family:var(--paper-font-body1_-_font-family, var(--ha-font-family-body, Roboto, sans-serif)); min-height:100%; }
.top { display:flex; align-items:center; gap:12px; padding:0 16px; height:56px; background:var(--app-header-background-color, var(--primary-color));
  color:var(--app-header-text-color, var(--text-primary-color, #fff)); font-size:20px; }
.top .grow { flex:1 }
.page { max-width:1200px; margin:0 auto; padding:16px; display:grid; gap:16px; grid-template-columns:minmax(0,1.5fr) minmax(0,1fr); }
@media (max-width:900px){ .page{ grid-template-columns:minmax(0,1fr) } }
.card { background:var(--card-background-color); border-radius:var(--ha-card-border-radius,12px); box-shadow:var(--ha-card-box-shadow,none);
  border:1px solid var(--divider-color); padding:16px; min-width:0; }
.card h2 { margin:0 0 12px; font-size:16px; font-weight:500; display:flex; gap:8px; align-items:center; }
.card h2 .grow{flex:1}
.wide { grid-column:1 / -1 }
.muted { color:var(--secondary-text-color); font-size:13px }
button { font:inherit; font-size:14px; border-radius:8px; border:1px solid var(--divider-color); background:var(--card-background-color);
  color:var(--primary-text-color); padding:6px 12px; cursor:pointer; }
button:hover { border-color:var(--primary-color) }
button.primary { background:var(--primary-color); color:var(--text-primary-color,#fff); border-color:var(--primary-color) }
button.ok { background:var(--success-color,#43a047); color:#fff; border-color:transparent }
button.danger { color:var(--error-color,#db4437) }
button:disabled { opacity:.5; cursor:default }
button:focus-visible { outline:2px solid var(--primary-color); outline-offset:2px }
.frame { position:relative; user-select:none; touch-action:none; border-radius:8px; overflow:hidden; background:#000; cursor:crosshair }
.frame img { display:block; width:100%; }
.box { position:absolute; border:3px solid #ff3b30; box-shadow:0 0 0 9999px rgb(0 0 0 / .35); pointer-events:none }
.row { display:flex; gap:8px; flex-wrap:wrap; align-items:center }
.bins { display:grid; gap:12px; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); margin-top:12px }
.bin { border:1px solid var(--divider-color); border-radius:10px; padding:12px; display:flex; flex-direction:column; gap:8px }
.bin .name { font-weight:500 }
.verdict { font-size:22px; font-weight:500 }
.bar { height:8px; border-radius:4px; background:var(--divider-color); overflow:hidden }
.bar > span { display:block; height:100%; background:var(--primary-color) }
.chip { display:inline-block; font-size:12px; border-radius:999px; padding:2px 8px; background:var(--divider-color) }
.chip.out { background:var(--success-color,#43a047); color:#fff }
.chip.in { background:var(--secondary-background-color,#e0e0e0); color:var(--primary-text-color) }
.chip.ready { background:var(--success-color,#43a047); color:#fff }
.chip.learning { background:var(--info-color,#039be5); color:#fff }
.chip.warn { background:var(--warning-color,#ffa600); color:#000 }
.stats { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:8px }
.stat { border:1px solid var(--divider-color); border-radius:8px; padding:8px }
.stat b { display:block; font-size:20px; font-variant-numeric:tabular-nums; font-weight:500 }
.grid { display:grid; gap:12px; grid-template-columns:repeat(auto-fill,minmax(180px,1fr)) }
.sample { border:1px solid var(--divider-color); border-radius:10px; overflow:hidden; display:flex; flex-direction:column }
.sample.disagree { border-color:var(--error-color,#db4437); box-shadow:0 0 0 1px var(--error-color,#db4437) }
.sample.aside { opacity:.55 }
.sample img, .sample .noimg { width:100%; aspect-ratio:16/9; object-fit:cover; display:block; background:var(--secondary-background-color) }
.sample .noimg { display:grid; place-items:center; font-size:12px; color:var(--secondary-text-color) }
.sample .body { padding:8px; display:flex; flex-direction:column; gap:6px; font-size:13px }
.sample .acts { display:flex; gap:6px } .sample .acts button{ flex:1; padding:4px 6px; font-size:12px }
.filters { display:flex; gap:6px; flex-wrap:wrap }
.filters button[aria-pressed="true"] { background:var(--primary-color); color:var(--text-primary-color,#fff); border-color:var(--primary-color) }
.pending { display:flex; flex-direction:column; gap:10px }
.pend { border-top:1px solid var(--divider-color); padding-top:8px }
.toast { position:fixed; bottom:16px; left:50%; transform:translateX(-50%); background:var(--primary-text-color); color:var(--card-background-color);
  padding:10px 16px; border-radius:8px; max-width:90vw; font-size:14px }
.err { color:var(--error-color,#db4437) }
`;

const pct = (v) => (v == null ? "—" : `${Math.round(v * 100)}%`);
const word = (l) => (l === "out" ? "Out" : l === "in" ? "Not out" : "—");
const ago = (t) => {
  if (!t) return "";
  const s = Date.now() / 1000 - t;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
};
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

class ChoresTrainingPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._overview = null;
    this._snap = null;
    this._samples = null;
    this._filter = "all";
    this._roi = null;
    this._dirtyRoi = false;
    this._busy = false;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._load();
  }
  set narrow(v) { this._narrow = v; }
  set panel(v) { this._panel = v; }

  _ws(type, extra = {}) { return this._hass.callWS({ type: `chores_discord/${type}`, ...extra }); }

  async _load() {
    try {
      this._overview = await this._ws("overview");
      this._roi = this._overview.roi.slice();
      this._render();
      this._samples = await this._ws("samples");
      this._render();
    } catch (e) {
      this._error = e.message || String(e);
      this._render();
    }
  }

  _toast(text) {
    this._toastText = text;
    this._render();
    clearTimeout(this._toastTimer);
    this._toastTimer = setTimeout(() => { this._toastText = null; this._render(); }, 4000);
  }

  async _act(fn, okText) {
    if (this._busy) return;
    this._busy = true; this._render();
    try {
      const res = await fn();
      if (okText) this._toast(typeof okText === "function" ? okText(res) : okText);
    } catch (e) {
      this._toast(`Couldn't do that: ${e.message || e}`);
    } finally {
      this._busy = false;
      try {
        this._overview = await this._ws("overview");
        this._samples = await this._ws("samples");
      } catch (e) { /* the toast already says what failed */ }
      this._render();
    }
  }

  _snapshot() {
    return this._act(async () => {
      this._snap = await this._ws("snapshot");
      this._roi = this._snap.roi.slice();
      this._dirtyRoi = false;
    });
  }

  _label(sid, bin, label) {
    return this._act(async () => {
      const r = await this._ws("label", { sid, bin, label });
      if (this._snap && this._snap.sid === sid) {
        this._snap.answered = { ...(this._snap.answered || {}), [bin]: label };
      }
      return r;
    }, (r) => r.reply);
  }

  _relabel(id, label) { return this._act(() => this._ws("relabel", { ref_id: id, label }), "Relabelled. The model was updated."); }
  _delete(id) { return this._act(() => this._ws("delete", { ref_id: id }), "Deleted."); }
  _saveRegion() {
    return this._act(async () => {
      await this._ws("set_region", { roi: this._roi });
      this._dirtyRoi = false;
      // The integration reloads with the new region; give it a moment.
      await new Promise((r) => setTimeout(r, 2500));
    }, "Region saved. Samples from the old region are set aside.");
  }

  // --- drag a new region on the live frame ---
  _bindFrame() {
    const frame = this.shadowRoot.querySelector(".frame");
    if (!frame || frame._bound) return;
    frame._bound = true;
    let start = null;
    const at = (ev) => {
      const r = frame.getBoundingClientRect();
      return [Math.min(100, Math.max(0, ((ev.clientX - r.left) / r.width) * 100)),
              Math.min(100, Math.max(0, ((ev.clientY - r.top) / r.height) * 100))];
    };
    frame.addEventListener("pointerdown", (ev) => { start = at(ev); frame.setPointerCapture(ev.pointerId); });
    frame.addEventListener("pointermove", (ev) => {
      if (!start) return;
      const p = at(ev);
      this._roi = [Math.min(start[0], p[0]), Math.min(start[1], p[1]), Math.max(start[0], p[0]), Math.max(start[1], p[1])];
      this._placeBox();
    });
    frame.addEventListener("pointerup", () => {
      if (!start) return;
      start = null;
      const [l, t, r, b] = this._roi;
      if (r - l > 2 && b - t > 2) { this._dirtyRoi = true; this._render(); }
      else { this._roi = this._overview.roi.slice(); this._placeBox(); }
    });
  }

  _placeBox() {
    const box = this.shadowRoot.querySelector(".box");
    if (!box || !this._roi) return;
    const [l, t, r, b] = this._roi;
    Object.assign(box.style, { left: `${l}%`, top: `${t}%`, width: `${r - l}%`, height: `${b - t}%` });
  }

  // --- rendering ---
  _binCard(bin, info, v, sid) {
    const answered = this._snap?.answered?.[bin];
    const verdictText = v.result === "out" ? "Out" : v.result === "not_out" ? "Not out" : v.result === "dark" ? "Too dark" : "Not sure";
    const guess = v.label ? `${word(v.label)} · ${pct(v.confidence)}` : "No labels to compare yet";
    const labels = sid && !answered
      ? `<div class="row"><button class="ok" data-label="${bin}:out">${info.emoji} Out</button><button data-label="${bin}:in">Not out</button></div>`
      : answered ? `<span class="muted">You said: ${word(answered)}</span>` : "";
    return `<div class="bin">
      <div class="row"><span class="name">${info.emoji} ${esc(info.name)}</span>
        <span class="chip ${info.ready ? "ready" : "learning"}">${info.ready ? "Trusted" : "Learning"}</span></div>
      <div class="verdict">${verdictText}</div>
      <div class="muted">Guess: ${guess}</div>
      <div class="bar" title="Confidence"><span style="width:${Math.round((v.confidence || 0) * 100)}%"></span></div>
      <div class="muted">Closest sample: ${v.nearest != null && v.compared ? v.nearest.toFixed(3) : "—"} · compared with ${v.compared}</div>
      ${labels}</div>`;
  }

  _live() {
    const o = this._overview, s = this._snap;
    const t = o.thresholds;
    if (!o.camera) return `<div class="card"><h2>Live view</h2><p>No camera picked yet. Choose one under Settings → Devices & services → Chores Discord Bot → Configure.</p></div>`;
    const img = s?.frame
      ? `<div class="frame"><img alt="Latest snapshot" src="data:image/jpeg;base64,${s.frame}"><div class="box"></div></div>`
      : `<div class="muted">Take a snapshot to see what the camera sees, the region it compares, and its guess for each bin.</div>`;
    const light = s ? `Sun ${s.elevation == null ? "—" : s.elevation.toFixed(1) + "°"} (needs ${t.min_elevation}°) · region brightness ${s.brightness} (needs ${t.min_brightness}) · ${s.daylight ? "daylight, judging" : "too dark, not judging"}` : "";
    const bins = s ? `<div class="bins">${Object.entries(o.bins).map(([b, info]) => this._binCard(b, info, s.verdicts[b], s.sid)).join("")}</div>` : "";
    return `<div class="card"><h2>Live view<span class="grow"></span>
        <button class="primary" data-act="snap" ${this._busy ? "disabled" : ""}>📸 Take snapshot</button></h2>
      ${img}
      ${s ? `<p class="muted">${light}</p>` : ""}
      ${s?.frame ? `<div class="row"><span class="muted">Drag on the picture to draw a new region.</span>
        ${this._dirtyRoi ? `<button class="primary" data-act="saveroi">Save region</button><button data-act="undoroi">Cancel</button>` : ""}</div>` : ""}
      ${s?.crop ? `<div class="row" style="margin-top:8px"><img alt="Cropped region" style="max-width:240px;border-radius:6px" src="data:image/jpeg;base64,${s.crop}">
        <span class="muted">This crop is all the model sees.</span></div>` : ""}
      ${bins}
      ${s?.sid ? `<div class="row" style="margin-top:8px"><button data-label="all:skip">🤷 Can't tell, skip this one</button></div>` : ""}
    </div>`;
  }

  _model() {
    const o = this._overview, t = o.thresholds;
    const rows = Object.entries(o.bins).map(([b, i]) => `
      <div style="margin-bottom:14px">
        <div class="row" style="margin-bottom:6px"><b>${i.emoji} ${esc(i.name)}</b>
          <span class="chip ${i.ready ? "ready" : "learning"}">${i.ready ? "Trusted: nags only when it isn't out" : i.phase === "no labels yet" ? "No labels yet" : "Learning: asks you"}</span></div>
        <div class="stats">
          <div class="stat"><span class="muted">Out</span><b>${i.out}</b></div>
          <div class="stat"><span class="muted">Not out</span><b>${i.not_out}</b></div>
          <div class="stat"><span class="muted">Self-test</span><b>${pct(i.accuracy)}</b></div>
          <div class="stat"><span class="muted">Nights</span><b style="font-size:13px">${i.nights.map((d) => d.slice(0, 3)).join(", ") || "—"}${i.weeks !== "every" ? ` (${i.weeks})` : ""}</b></div>
        </div>
        ${i.set_aside ? `<p class="muted">${i.set_aside} older samples from a previous region are set aside.</p>` : ""}
      </div>`).join("");
    return `<div class="card"><h2>Learning</h2>${rows}
      <p class="muted">Trusted at ${t.min_labels} of each answer and ${pct(t.min_accuracy)} on the self-test (each sample predicted from all the others). Once trusted, a guess under ${pct(t.confidence)} counts as unsure and it asks instead.</p></div>`;
  }

  _pending() {
    const p = this._overview.pending || [];
    if (!p.length) return "";
    return `<div class="card"><h2>Waiting for an answer</h2><div class="pending">${p.slice(0, 6).map((x) => `
      <div class="pend"><div>
        <div class="muted">${ago(x.at)}</div>
        ${Object.entries(this._overview.bins).map(([b, i]) => x.answers[b]
          ? `<div>${i.emoji} ${esc(i.name)}: ${word(x.answers[b].label)} (${esc(x.answers[b].who)})</div>`
          : `<div class="row">${i.emoji} ${esc(i.name)}: <button class="ok" data-plabel="${x.sid}:${b}:out">Out</button><button data-plabel="${x.sid}:${b}:in">Not out</button></div>`).join("")}
        <div class="row" style="margin-top:4px"><button data-plabel="${x.sid}:all:skip">Skip</button></div>
      </div></div>`).join("")}</div>
      <p class="muted">Answering here also updates the card in Discord.</p></div>`;
  }

  _samplesCard() {
    const list = this._samples;
    if (!list) return `<div class="card wide"><h2>Labelled samples</h2><p class="muted">Loading…</p></div>`;
    const disagree = (s) => s.model_says && s.model_says !== s.label;
    const filters = [["all", "All"], ["trash", "🗑️ Trash"], ["recycling", "♻️ Recycling"], ["disagree", "Model disagrees"]];
    const shown = list.filter((s) => this._filter === "all" || (this._filter === "disagree" ? disagree(s) : s.bin === this._filter));
    const nDis = list.filter(disagree).length;
    const cards = shown.map((s) => `
      <div class="sample ${disagree(s) ? "disagree" : ""} ${s.current_region ? "" : "aside"}">
        ${s.thumb ? `<img alt="" src="data:image/jpeg;base64,${s.thumb}">` : `<div class="noimg">no picture kept</div>`}
        <div class="body">
          <div class="row"><span class="chip">${s.bin === "trash" ? "🗑️ Trash" : "♻️ Recycling"}</span><span class="chip ${s.label}">${word(s.label)}</span></div>
          <div class="muted">${esc(s.by || "")} · ${ago(s.at)}</div>
          ${disagree(s) ? `<div class="err">Model says ${word(s.model_says)} (${pct(s.model_confidence)}). Mislabelled?</div>` : ""}
          ${s.current_region ? "" : `<div class="muted">From an old region, not used</div>`}
          <div class="acts"><button data-relabel="${s.id}:${s.label === "out" ? "in" : "out"}">Make it ${s.label === "out" ? "not out" : "out"}</button>
            <button class="danger" data-delete="${s.id}">${this._confirm === s.id ? "Really delete?" : "Delete"}</button></div>
        </div></div>`).join("");
    return `<div class="card wide"><h2>Labelled samples<span class="grow"></span><span class="muted">${list.length} total${nDis ? ` · ${nDis} the model disagrees with` : ""}</span></h2>
      <div class="filters" style="margin-bottom:12px">${filters.map(([k, l]) => `<button data-filter="${k}" aria-pressed="${this._filter === k}">${l}</button>`).join("")}</div>
      ${cards ? `<div class="grid">${cards}</div>` : `<p class="muted">Nothing here yet. Answer a snapshot in Discord or above.</p>`}</div>`;
  }

  _render() {
    const root = this.shadowRoot;
    let body;
    if (this._error) body = `<div class="page"><div class="card wide err">Couldn't load: ${esc(this._error)}</div></div>`;
    else if (!this._overview) body = `<div class="page"><div class="card wide muted">Loading…</div></div>`;
    else body = `<div class="page">${this._live()}<div style="display:flex;flex-direction:column;gap:16px;min-width:0">${this._model()}${this._pending()}</div>${this._samplesCard()}</div>`;
    root.innerHTML = `<style>${STYLE}</style><div class="top"><span>🗑️ Bin training</span><span class="grow"></span></div>${body}
      ${this._toastText ? `<div class="toast" role="status">${esc(this._toastText)}</div>` : ""}`;
    this._placeBox();
    this._bindFrame();
    root.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => this._click(b)));
  }

  _click(b) {
    const d = b.dataset;
    if (d.act === "snap") return this._snapshot();
    if (d.act === "saveroi") return this._saveRegion();
    if (d.act === "undoroi") { this._roi = this._overview.roi.slice(); this._dirtyRoi = false; return this._render(); }
    if (d.label) { const [bin, label] = d.label.split(":"); return this._label(this._snap.sid, bin, label); }
    if (d.plabel) { const [sid, bin, label] = d.plabel.split(":"); return this._label(sid, bin, label); }
    if (d.relabel) { const i = d.relabel.lastIndexOf(":"); return this._relabel(d.relabel.slice(0, i), d.relabel.slice(i + 1)); }
    if (d.delete) {
      if (this._confirm !== d.delete) { this._confirm = d.delete; return this._render(); }
      this._confirm = null;
      return this._delete(d.delete);
    }
    if (d.filter) { this._filter = d.filter; return this._render(); }
  }
}

customElements.define("chores-training-panel", ChoresTrainingPanel);
