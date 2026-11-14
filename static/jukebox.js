// Earth Spins (a NASA data jukebox) — front end (no build step).
// Layout: a fixed 1920x1080 stage scaled to the window. Left = jukebox, right = blackboard.
const $ = (sel) => document.querySelector(sel);
const PER_FLOOR = 6;
const PARTS = {
  lyria: { n: 1, source: "Google · Lyria 2", lcd: "GOOGLE LYRIA 2" },
  musicgen: { n: 2, source: "FB · Meta MusicGen", lcd: "META MUSICGEN" },
};
const ORDER = ["lyria", "musicgen"];
const HIST_COOKIE = "jbx_plays";
const HIST_MAX = 60;

const el = {
  live: $("#sr-live"), viewport: $("#viewport"), stage: $("#stage"),
  dome: $("#dome"), rack: $("#rack"), showcase: $("#showcase"), showImg: $("#show-img"), showNum: $("#show-num"), showOpen: $("#show-open"),
  showTitle: $("#show-title"), showMeta: $("#show-meta"), backdrop: $("#dome-backdrop"),
  eq: $("#eq"), pressPlay: $("#press-play"),
  play: $("#btn-play"), lcdTop: $("#lcd-top"), lcdTitle: $("#lcd-title"), genBar: $("#gen-bar"), genFill: $("#gen-fill"),
  seek: $("#seek"), time: $("#lcd-time"), dl: $("#btn-dl"), parts: [...document.querySelectorAll(".part")],
  shareBtn: $("#btn-share"), eject: $("#btn-eject"),
  pop: $("#share-pop"), shareUrl: $("#share-url"), copy: $("#btn-copy"), json: $("#json-link"),
  spCurrent: $("#sp-current"), spNone: $("#sp-none"), spHistory: $("#sp-history"), spClose: $("#sp-close"),
  keys: $("#keys"), cards: $("#cards"), floorUp: $("#floor-up"), floorDown: $("#floor-down"),
  floorNum: $("#floor-num"), floorOf: $("#floor-of"),
  dialPrev: $("#dial-prev"), dialNext: $("#dial-next"),
  digitsBtn: $("#btn-digits"), digits: [...document.querySelectorAll("#digit-boxes span")], clear: $("#btn-clear"),
  random: $("#btn-random"),
  start: $("#btn-start"),
  nd: $("#numdlg"), ndForm: $("#nd-form"), ndInput: $("#nd-input"), ndMsg: $("#nd-msg"), ndCancel: $("#nd-cancel"), ndOk: $("#nd-ok"),
  chalk: $("#chalk"), tilt: $("#tilt"), tiltMsg: $("#tilt-msg"), tiltOk: $("#tilt-ok"), tiltH: $("#tilt-h"),
  audio: $("#audio"),
};

const state = {
  topics: [], topicIdx: 0, page: 0, pages: 1, total: 0, results: [], searchSeq: 0,
  num: "", selected: null, lookupSeq: 0, lookupPromise: null, starting: false, searchTimer: null, ndBusy: false,
  code: null, data: null, track: "lyria", loadedSrc: null, autoplay: false, pollTimer: null,
  synced: false, boardTop: null, boardAnalysis: false, finished: false,
  audioCtx: null, analyser: null, peaks: [],
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const pad5 = (n) => String(n).padStart(5, "0");
function announce(msg) { el.live.textContent = ""; setTimeout(() => { el.live.textContent = msg; }, 40); }
function fmtTime(s) { return Number.isFinite(s) ? `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}` : "0:00"; }
function hues(id) { const h = (id * 47) % 360; return { "--sl1": `hsl(${h} 55% 32%)`, "--sl2": `hsl(${(h + 70) % 360} 60% 38%)`, "--lbl": `hsl(${(h + 180) % 360} 85% 58%)` }; }
function h(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "style" && typeof v === "object") Object.entries(v).forEach(([p, val]) => node.style.setProperty(p, val));
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return node;
}
function coverImg(id, url, alt = "") {
  const img = h("img", { alt, loading: "lazy", decoding: "async" });
  img.addEventListener("error", () => img.remove()); // the ♪ fallback behind it shows instead
  img.src = url || `/api/cover/${id}`;
  return img;
}

/* ===================== stage scaling ===================== */
function fit() {
  const w = window.innerWidth, hgt = window.innerHeight;
  // Side by side only when the window is wide enough (16:10, 16:9, wider); squarer or
  // narrower windows stack the blackboard under the jukebox so the text stays readable.
  const stacked = w / hgt < 1.45 || w < 900;
  document.body.classList.toggle("stacked", stacked);
  const SW = stacked ? 1152 : 1920, SH = stacked ? 2160 : 1080;
  let s, x = 0, y = 0;
  if (stacked) {
    // The jukebox (top half) fits the window; the blackboard scrolls in below it.
    s = Math.min(w / SW, Math.max(hgt, 480) / 1080);
    x = (w - SW * s) / 2;
    el.viewport.style.height = `${SH * s}px`;
  }
  else { s = Math.min(w / SW, hgt / SH); x = (w - SW * s) / 2; y = (hgt - SH * s) / 2; el.viewport.style.height = ""; }
  el.stage.style.transform = `translate(${x}px, ${y}px) scale(${s})`;
}

/* ===================== topics + floors ===================== */
/* The topic dial: a wheel of topics. The one in the middle window is the selected topic. */
const DIAL_SPAN = 2.6;  // two neighbours on each side
const DIAL_STEP = 156;  // px between topic centres (key is 140px wide)  // offsets beyond this are hidden behind the bezel
function renderKeys() {
  el.keys.innerHTML = "";
  state.topics.forEach((t, i) => {
    el.keys.append(h("button", {
      class: "key", type: "button", title: t.query ? `Search NASA SVS for “${t.query}”` : "Newest Earth Information Center visualizations",
      "aria-pressed": "false", onclick: () => { if (i !== state.topicIdx) selectTopic(i); },
    }, h("span", {}, t.label)));
  });
  layoutDial();
}
function dialOffset(i) {
  const n = state.topics.length;
  let off = (i - state.topicIdx) % n;
  if (off > n / 2) off -= n;
  if (off < -n / 2) off += n;
  return off;
}
function layoutDial() {
  [...el.keys.children].forEach((b, i) => {
    const off = dialOffset(i);
    // Evenly spaced, never overlapping: the middle one plus two on each side are fully visible.
    const visible = Math.abs(off) <= DIAL_SPAN;
    const x = off * DIAL_STEP;
    const scale = 1 - Math.min(Math.abs(off), 3) * 0.06;
    b.style.transform = `translate(-50%, -50%) translateX(${x}px) rotateY(${(-off * 8).toFixed(1)}deg) scale(${scale.toFixed(2)})`;
    b.style.opacity = visible ? String([1, 0.85, 0.6][Math.round(Math.abs(off))] ?? 0.6) : "0";
    b.style.zIndex = String(20 - Math.round(Math.abs(off) * 2));
    b.style.pointerEvents = visible ? "" : "none";
    b.tabIndex = off === 0 ? 0 : -1;
    b.classList.toggle("center", off === 0);
    b.setAttribute("aria-pressed", String(off === 0));
    b.setAttribute("aria-hidden", String(!visible));
  });
}
function selectTopic(i, { now = false } = {}) {
  const n = state.topics.length;
  state.topicIdx = ((i % n) + n) % n;
  layoutDial();
  el.keys.classList.remove("click"); void el.keys.offsetWidth; el.keys.classList.add("click");
  const t = state.topics[state.topicIdx];
  announce(`Topic: ${t.label}`);
  // Spinning several notches quickly only searches where the dial stops.
  clearTimeout(state.searchTimer);
  state.searchTimer = setTimeout(() => loadFloor(0), now ? 0 : 320);
}
function stepDial(d) { selectTopic(state.topicIdx + d); }
async function loadFloor(page) {
  const seq = ++state.searchSeq;
  const topic = state.topics[state.topicIdx];
  el.cards.classList.remove("flip-in"); el.cards.classList.add("flip-out");
  el.floorUp.disabled = el.floorDown.disabled = true;
  try {
    const [resp] = await Promise.all([fetch(`/api/search?q=${encodeURIComponent(topic.query)}&page=${page}`), sleep(230)]);
    const body = await resp.json();
    if (!resp.ok) throw new Error(body.detail || resp.statusText);
    if (seq !== state.searchSeq) return;
    Object.assign(state, { results: body.results, page: body.page, pages: body.pages, total: body.total });
    renderCards(); renderRack(); renderFloor();
    announce(body.total
      ? `${topic.label}: floor ${body.page + 1} of ${body.pages}, ${body.results.length} records.`
      : `No records for ${topic.label}.`);
  } catch (err) {
    if (seq !== state.searchSeq) return;
    state.results = [];
    renderCards(`Signal lost · ${err.message}`); renderRack(); renderFloor();
  }
}
function renderFloor() {
  el.floorNum.textContent = state.total ? String(state.page + 1).padStart(2, "0") : "--";
  el.floorOf.textContent = `of ${state.total ? String(state.pages).padStart(2, "0") : "--"}`;
  el.floorUp.disabled = state.page <= 0;
  el.floorDown.disabled = state.page >= state.pages - 1;
}
function renderCards(emptyText = "No records on this floor") {
  el.cards.innerHTML = "";
  for (let i = 0; i < PER_FLOOR; i++) {
    const r = state.results[i];
    let card;
    if (r) {
      const label = `Record ${pad5(r.id)}: ${r.title}. ${r.eic ? "Made for the Earth Information Center. " : ""}Released ${r.release_date}.`;
      card = h("button", {
        class: "card" + (state.selected && state.selected.id === r.id ? " picked" : ""), type: "button",
        style: { "--i": i, ...hues(r.id) }, "aria-label": label, title: r.description || r.title,
        onclick: () => pickResult(i),
      },
      h("span", { class: "cover" }, coverImg(r.id), h("span", { "aria-hidden": "true" }, "♪")),
      h("span", { class: "txt" },
        h("span", { class: "no" }, `No. ${pad5(r.id)}`),
        h("span", { class: "ttl" }, r.title),
        h("span", { class: "ft" }, r.release_date || "", r.eic ? h("span", { class: "eic", title: "Made for NASA's Earth Information Center" }, "EIC") : null)));
    } else {
      card = h("button", { class: "card blank", type: "button", disabled: true, style: { "--i": i } }, i === 0 ? emptyText : "");
    }
    el.cards.append(h("li", {}, card));
  }
  el.cards.classList.remove("flip-out"); void el.cards.offsetWidth; el.cards.classList.add("flip-in");
}
function renderRack() {
  el.rack.innerHTML = "";
  for (let i = 0; i < PER_FLOOR; i++) {
    const r = state.results[i];
    const off = i - (PER_FLOOR - 1) / 2;
    const id = r ? r.id : 1000 + i * 37 + state.page * 11;
    const t = `translateX(${off * 132}px) rotate(${off * 4.5}deg) translateY(${off * off * 5}px)`;
    const rec = h("div", {
      class: "rack-rec" + (r && state.selected && state.selected.id === r.id ? " lift" : ""),
      style: { left: "405px", "--t": t, ...hues(id) },
      onclick: r ? () => pickResult(i) : null,
    },
    h("div", { class: "disc" }),
    h("div", { class: "sleeve" }, r ? coverImg(r.id) : null, h("span", { class: "sleeve-fallback" }, "♪")),
    r ? h("span", { class: "tag" }, pad5(r.id)) : null);
    el.rack.append(rec);
  }
}

/* ===================== selection: cards, number, showcase ===================== */
function pickResult(i) {
  const r = state.results[i];
  if (!r) return;

  state.lookupSeq++;
  setNumber(r.id);
  select({ id: r.id, title: r.title, release_date: r.release_date, eic: r.eic, page_type: r.type, image_url: null, fromTopic: true });
}
function select(sel) {
  state.selected = sel;
  [...el.cards.querySelectorAll(".card")].forEach((c, i) => c.classList.toggle("picked", Boolean(state.results[i] && state.results[i].id === sel?.id)));
  [...el.rack.children].forEach((c, i) => c.classList.toggle("lift", Boolean(state.results[i] && state.results[i].id === sel?.id)));
  if (!sel) { hideShowcase(); el.start.classList.remove("ready"); return; }
  showcase(sel);
  el.start.classList.add("ready");
  announce(`Record ${sel.id}, ${sel.title}, is ready. Press Start.`);
}
function showcase(sel) {
  const same = !el.showcase.hidden && el.showcase.dataset.id === String(sel.id);
  el.showcase.dataset.id = String(sel.id);
  Object.entries(hues(sel.id)).forEach(([k, v]) => el.showcase.style.setProperty(k, v));
  el.showNum.textContent = `No. ${pad5(sel.id)}`;
  el.showOpen.href = `https://svs.gsfc.nasa.gov/${sel.id}/`;
  el.showOpen.setAttribute("aria-label", `Open NASA SVS page ${sel.id} in a new window`);
  el.showTitle.textContent = sel.title || "";
  el.showMeta.innerHTML = "";
  el.showMeta.append([sel.release_date, sel.page_type].filter(Boolean).join(" · "));
  if (sel.eic) el.showMeta.append(" · ", h("span", { class: "eic" }, "Earth Information Center"));
  const src = sel.image_url || `/api/cover/${sel.id}`;
  if (!same) {
    el.showImg.hidden = false;
    el.showImg.onerror = () => { el.showImg.hidden = true; };
    el.showImg.src = src;
    el.showImg.alt = sel.title ? `Cover: ${sel.title}` : "";
    el.backdrop.style.backgroundImage = `url("${src}")`;
    el.showcase.hidden = true; void el.showcase.offsetWidth; el.showcase.hidden = false;
  }
  el.dome.classList.add("has-pick");
}
function hideShowcase() {
  el.showcase.hidden = true; el.showcase.dataset.id = "";
  el.dome.classList.remove("has-pick");
  el.backdrop.style.backgroundImage = "";
}
function renderDigits(value, animate = true) {
  const s = pad5(value || "");
  el.digits.forEach((span, i) => {
    if (span.textContent !== s[i]) {
      span.textContent = s[i];
      if (animate) { span.classList.remove("roll"); void span.offsetWidth; span.classList.add("roll"); }
    }
    span.classList.toggle("lead", i < 5 - (value || "").length);
  });
  el.digitsBtn.setAttribute("aria-label", `Record number: ${value ? Number(value) : "empty"}. Press to type a number.`);
}
function setNumber(id) {
  state.num = String(id).slice(-5).replace(/^0+/, "");
  renderDigits(state.num);
}
function clearNumber() {
  state.lookupSeq++;
  setNumber("");
  select(null);
}
/* Look a number up on NASA SVS. Resolves to {ok} or {ok:false, message}. */
function lookup(id) {
  const seq = ++state.lookupSeq;
  state.lookupPromise = (async () => {
    try {
      const resp = await fetch(`/api/svs/${id}`);
      if (seq !== state.lookupSeq) return { ok: false, stale: true };
      if (!resp.ok) {
        return { ok: false, message: resp.status === 404
          ? `No. ${pad5(id)} is not a page on the NASA SVS site. Try another number.`
          : `Lookup failed (${resp.status}). Try again.` };
      }
      const it = await resp.json();
      if (seq !== state.lookupSeq) return { ok: false, stale: true };
      setNumber(it.id);
      select({
        id: it.id, title: it.title, release_date: it.release_date, page_type: it.page_type, image_url: it.image_url,
        eic: (it.keywords || []).includes("Earth Information Center"), fromTopic: false,
      });
      return { ok: true };
    } catch {
      return { ok: false, message: "Lookup failed. Check the connection and try again." };
    }
  })();
  return state.lookupPromise;
}

/* ===================== type-a-number dialog ===================== */
function openNumberDialog(prefill = "") {
  if (!el.nd.hidden) return;
  closePop();
  el.ndInput.value = prefill;
  ndMessage("");
  el.nd.hidden = false;
  el.ndInput.focus();
  el.ndInput.setSelectionRange(el.ndInput.value.length, el.ndInput.value.length);
}
function closeNumberDialog() {
  el.nd.hidden = true;
  state.ndBusy = false;
  el.digitsBtn.focus();
}
function ndMessage(text, kind = "") { el.ndMsg.textContent = text; el.ndMsg.className = "nd-msg " + kind; }
async function submitNumber() {
  if (state.ndBusy) return;
  const v = el.ndInput.value.trim();
  if (!/^\d{1,5}$/.test(v) || Number(v) === 0) {
    ndMessage("Please type a number with 1 to 5 digits (not 0).", "bad");
    el.ndInput.focus();
    return;
  }
  const id = Number(v);
  const i = state.results.findIndex((r) => r.id === id);
  if (i >= 0) { pickResult(i); closeNumberDialog(); el.start.focus(); return; }
  state.ndBusy = true; el.ndOk.disabled = true;
  ndMessage(`Looking up No. ${pad5(id)} on NASA SVS…`, "wait");
  const res = await lookup(id);
  state.ndBusy = false; el.ndOk.disabled = false;
  if (el.nd.hidden || res.stale) return;
  if (res.ok) { closeNumberDialog(); el.start.focus(); announce(`Record ${id}, ${state.selected.title}, is ready. Press Start.`); }
  else { ndMessage(res.message, "bad"); el.ndInput.select(); }
}

/* ===================== Random record ===================== */
// SVS page IDs live in two blocks (sampled Nov 2026: ~75% of IDs in them are real pages;
// 6000-9999 and everything above 15120 are almost all empty).
const RANDOM_RANGES = [[1, 5690], [10000, 15120]];
const RANDOM_TRIES = 8;
function randomSvsId() {
  let k = Math.floor(Math.random() * RANDOM_RANGES.reduce((n, [lo, hi]) => n + hi - lo + 1, 0));
  for (const [lo, hi] of RANDOM_RANGES) {
    if (k <= hi - lo) return lo + k;
    k -= hi - lo + 1;
  }
}
async function playRandom() {
  if (state.starting || state.randomBusy) return;
  ensureAudioGraph(); // inside the click, so the browser lets us play later
  state.randomBusy = true;
  el.random.disabled = true;
  try {
    for (let i = 0; i < RANDOM_TRIES; i++) {
      const id = randomSvsId();
      setNumber(id);
      const res = await lookup(id);
      if (res.stale) return;
      if (res.ok) { await start(); return; }
    }
    tilt("No record turned up this time. Press Random again.", "No luck");
  } finally {
    state.randomBusy = false;
    el.random.disabled = false;
  }
}

/* ===================== START → generate ===================== */
async function start() {
  if (state.starting) return;
  const v = state.num;
  if (!v || Number(v) === 0) { openNumberDialog(); return; }
  ensureAudioGraph(); // inside the click, so the browser lets us play later
  const id = Number(v);
  if (!state.selected || state.selected.id !== id) {
    const res = await lookup(id);
    if (!res.ok) { if (res.message) tilt(res.message, "No such record"); return; }
  }
  const sel = state.selected;
  state.starting = true;
  el.start.disabled = true;
  announce(`Coin accepted. Composing record ${sel.id}.`);
  try {
    const topic = state.topics[state.topicIdx];
    const resp = await fetch("/api/generate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ svs_id: sel.id, search_term: sel.fromTopic && topic ? topic.query : null }),
    });
    const body = await resp.json().catch(() => ({}));
    if (resp.status === 429) {
      const d = body.detail || {};
      tilt(d.message || "You've tried too many times. Please wait a while and try again.", "Too many plays");
      return;
    }
    if (!resp.ok) throw new Error(typeof body.detail === "string" ? body.detail : resp.statusText);
    addHistory(body.code, sel.id);
    history.pushState({ code: body.code }, "", `/share/${body.code}`);
    el.start.classList.remove("ready");
    openGeneration(body.code, { autoplay: true, preview: sel });
  } catch (err) {
    tilt(`Could not start: ${err.message}`);
  } finally {
    state.starting = false;
    el.start.disabled = false;
  }
}

/* ===================== player ===================== */
function resetPlayer() {
  clearTimeout(state.pollTimer);
  el.audio.pause();
  el.audio.removeAttribute("src"); el.audio.load();
  Object.assign(state, { data: null, track: "lyria", loadedSrc: null, autoplay: false, synced: false, boardTop: null, boardAnalysis: false, finished: false });
  el.pressPlay.hidden = true;
  el.play.disabled = true; el.play.classList.remove("is-playing"); el.play.setAttribute("aria-label", "Play");
  el.seek.hidden = true; el.seek.value = 0; el.dl.hidden = true;
  el.genBar.hidden = false; el.genBar.classList.remove("failed"); el.genFill.style.width = "0%";
  el.time.textContent = "0:00 / 0:00";
  el.parts.forEach((b) => { b.disabled = true; b.setAttribute("aria-pressed", "false"); b.querySelector(".lamp").className = "lamp"; b.querySelector(".part-pct").textContent = ""; });
  el.dome.classList.remove("playing", "spinning", "composing");
}
function openGeneration(code, { autoplay = false, preview = null } = {}) {
  resetPlayer();
  state.code = code;
  state.autoplay = autoplay;
  el.eject.disabled = false;
  el.shareUrl.value = `${location.origin}/share/${code}`;
  el.json.href = `/api/generations/${code}/record`;
  setLcd("Loading the record…", preview ? `${pad5(preview.id)} · ${preview.title}` : "…");
  if (preview) renderBoard({ code, svs: preview, analysis: null, status: "queued", tracks: {} });
  poll();
}
function eject(push = true) {
  resetPlayer();
  state.code = null;
  el.eject.disabled = true;
  setNumber(""); select(null);
  setLcd("Welcome · pick a topic, then a card", "EARTH SPINS");
  renderIdleBoard();
  if (push && location.pathname !== "/") history.pushState({}, "", "/");
}
const TERMINAL = new Set(["done", "failed", "disabled"]);
function allSettled(d) { return Object.values(d.tracks).every((t) => TERMINAL.has(t.status)); }
async function poll() {
  const code = state.code;
  if (!code) return;
  try {
    const resp = await fetch(`/api/generations/${code}`, { cache: "no-store" });
    if (resp.status === 404) { setLcd("Unknown share code", code.toUpperCase()); return; }
    const d = await resp.json();
    if (state.code !== code) return;
    render(d);
    if (!allSettled(d)) state.pollTimer = setTimeout(poll, 1500);
  } catch {
    if (state.code === code) state.pollTimer = setTimeout(poll, 3000);
  }
}
function render(d) {
  state.data = d;
  el.shareUrl.value = d.share_url || `${location.origin}/share/${d.code}`;
  if (!state.synced && d.svs && d.svs.title) {
    // A shared link (or a fresh start): show this generation's record in the window and the number dial.
    state.synced = true;
    setNumber(d.svs.id);
    state.lookupSeq++;
    state.selected = {
      id: d.svs.id, title: d.svs.title, release_date: d.svs.release_date, page_type: d.svs.page_type,
      image_url: d.svs.image_url, eic: (d.svs.keywords || []).includes("Earth Information Center"), fromTopic: false,
    };
    showcase(state.selected);
  }
  // If the current part failed, move to one that can still play.
  const cur = d.tracks[state.track];
  if (cur && (cur.status === "failed" || cur.status === "disabled")) {
    const alt = ORDER.find((n) => d.tracks[n] && !["failed", "disabled"].includes(d.tracks[n].status));
    if (alt && alt !== state.track) switchTrack(alt, state.autoplay);
  }
  renderParts(d);
  renderTransport();
  renderBoard(d);
}
function renderParts(d) {
  el.parts.forEach((b) => {
    const name = b.dataset.track;
    const t = d.tracks[name];
    const status = t ? t.status : "disabled";
    b.disabled = status === "disabled";
    b.setAttribute("aria-pressed", String(state.track === name));
    b.querySelector(".lamp").className = "lamp " + ({ done: "ok", failed: "bad", disabled: "" }[status] ?? "busy");
    b.querySelector(".part-pct").textContent = status === "done" ? "READY" : status === "failed" ? "FAILED" : status === "disabled" ? "OFF" : `${t.progress || 0}%`;
    b.setAttribute("aria-label", `Part ${PARTS[name].n}, by ${PARTS[name].source.replace("·", "")}: ${t ? t.label : "off"}`);
  });
}
function renderTransport() {
  const d = state.data;
  if (!d) return;
  const name = state.track;
  const t = d.tracks[name];
  const P = PARTS[name];
  const title = `${pad5(d.svs.id)} · ${d.svs.title || ""}`;
  setLcd(el.lcdTop.textContent, title);
  if (t && t.status === "done" && t.url) {
    el.genBar.hidden = true; el.seek.hidden = false; el.play.disabled = false;
    el.dl.href = t.url;
    el.dl.download = `earth-spins-${d.code}-part${P.n}.mp3`;
    el.dl.setAttribute("aria-label", `Download Part ${P.n} (${P.lcd}) as MP3`);
    el.dl.hidden = false;
    if (state.loadedSrc !== t.url) {
      el.audio.src = t.url; state.loadedSrc = t.url;
      if (state.autoplay) tryPlay();
    }
    if (el.audio.paused) {
      setLcd(state.finished ? "The end · press ▶ to play again from Part 1" : `Part ${P.n} · ${P.lcd} · ready — press ▶`, title);
    }
  } else {
    el.genBar.hidden = false; el.seek.hidden = true; el.dl.hidden = true;
    el.play.disabled = true;
    const failed = !t || t.status === "failed" || (name === "lyria" && d.status === "failed");
    const pct = failed ? 100 : t.progress || 0;
    el.genBar.classList.toggle("failed", failed);
    el.genFill.style.width = `${pct}%`;
    el.genBar.setAttribute("aria-valuenow", String(pct));
    el.time.textContent = failed ? "" : `${pct}%`;
    let top;
    if (t && t.status === "disabled") top = `Part ${P.n} · ${P.lcd} is off on this server`;
    else if (failed) top = `Part ${P.n} failed · ${(t && t.error) || d.error || "unknown error"}`;
    else if (state.autoplay && name === "musicgen" && d.tracks.lyria && d.tracks.lyria.status === "done" && el.audio.paused && state.loadedSrc === null)
      top = `Part 2 · ${P.lcd} still composing — plays when ready · ${t.label} ${pct}%`;
    else top = `Part ${P.n} · ${P.lcd} · ${t.label}… ${pct}%`;
    setLcd(top, title);
  }
  const busy = Object.values(d.tracks).some((x) => !TERMINAL.has(x.status));
  el.dome.classList.toggle("composing", busy && el.audio.paused);
}
function setLcd(top, title) {
  el.lcdTop.textContent = top;
  if (title != null && el.lcdTitle.dataset.text !== title) {
    el.lcdTitle.dataset.text = title;
    el.lcdTitle.textContent = title;
    el.lcdTitle.classList.remove("scroll");
    requestAnimationFrame(() => {
      const wrap = el.lcdTitle.parentElement;
      if (el.lcdTitle.scrollWidth > wrap.clientWidth + 2) {
        el.lcdTitle.style.setProperty("--dur", `${Math.max(10, title.length * 0.28)}s`);
        el.lcdTitle.classList.add("scroll");
      }
    });
  }
}
function switchTrack(name, autoplay) {
  el.audio.pause();
  state.track = name;
  state.loadedSrc = null;
  el.audio.removeAttribute("src"); el.audio.load();
  el.dl.hidden = true;
  state.autoplay = autoplay;
  el.parts.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.track === name)));
}
function onPart(name) {
  const d = state.data;
  if (!d) return;
  state.finished = false;
  if (name === state.track) {
    if (el.audio.paused && state.loadedSrc) tryPlay();
    return;
  }
  switchTrack(name, true);
  renderTransport();
}
function tryPlay() {
  ensureAudioGraph();
  el.audio.play().then(() => {
    state.autoplay = false;
    el.pressPlay.hidden = true;
  }).catch(() => {
    // Browser blocked autoplay (e.g. a shared link opened cold): ask for one tap.
    el.pressPlay.hidden = false;
    setLcd(`Part ${PARTS[state.track].n} ready · press ▶ to play`);
  });
}
function onEnded() {
  const d = state.data;
  if (state.track === "lyria" && d && d.tracks.musicgen && !["failed", "disabled"].includes(d.tracks.musicgen.status)) {
    switchTrack("musicgen", true);
    announce("Part 1 finished. Part 2, by MusicGen, is next.");
    renderTransport();
    return;
  }
  state.finished = true;
  const first = ORDER.find((n) => d && d.tracks[n] && d.tracks[n].status === "done") || "lyria";
  switchTrack(first, false);
  renderTransport();
  renderParts(d);
  announce("Finished.");
}

/* ===================== blackboard ===================== */
function hello() {
  return h("svg", { class: "doodle", viewBox: "0 0 150 150", "aria-hidden": "true", fill: "none", stroke: "#f1efe6", "stroke-width": "3", "stroke-linecap": "round" },
    ...[
      ["circle", { cx: 70, cy: 80, r: 46 }],
      ["path", { d: "M30 64c18 6 26-8 40-2s22 18 40 8M28 96c14-4 26 6 40 2s24-14 44-6" }],
      ["path", { d: "M70 34c-14 16-14 76 0 92M70 34c14 16 14 76 0 92", stroke: "#a6dcf2" }],
      ["path", { d: "M120 20v32M120 20l16 6v30", stroke: "#f7e48a" }],
      ["circle", { cx: 114, cy: 54, r: 6, stroke: "#f7e48a" }],
      ["circle", { cx: 130, cy: 58, r: 6, stroke: "#f7e48a" }],
    ].map(([tag, attrs]) => { const n = document.createElementNS("http://www.w3.org/2000/svg", tag); Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, v)); return n; }));
}
async function renderIdleBoard() {
  stopSpeaking();
  state.boardTop = "idle";
  state.boardAnalysis = false;
  const recent = h("ul", { class: "recent" }, h("li", { class: "dim" }, "…"));
  el.chalk.replaceChildren(
    h("div", { class: "w", style: { "--d": 0 } }, hello()),
    h("h2", { class: "c-hello w", style: { "--d": 0 } }, "Listen to the Earth"),
    h("p", { class: "w dim", style: { "--d": 1 } }, "Every record in this jukebox is a real NASA visualization. Gemini studies the picture, then two AI composers turn it into music."),
    h("ol", { class: "steps" },
      h("li", { class: "w", style: { "--d": 2 } }, "Spin the ", h("span", { class: "underline" }, "topic dial"), " with ◀ ▶ (or tap a topic). The one in the middle is searched."),
      h("li", { class: "w", style: { "--d": 3 } }, "Tap one of the six cards — or tap the number dial and type an SVS number. ", h("span", { class: "dim" }, "▲▼ change floors.")),
      h("li", { class: "w", style: { "--d": 4 } }, "Press ", h("span", { class: "underline" }, "START"), ". Part 1 is by Google Lyria, Part 2 by Meta MusicGen."),
      h("li", { class: "w", style: { "--d": 5 } }, "Share the link — anyone who opens it hears your track.")),
    h("h3", { class: "w", style: { "--d": 6 } }, "Recently on this jukebox"),
    recent,
  );
  try {
    // Newest first, one line per record (no times, so repeats of the same record would look identical).
    const seen = new Set();
    const rows = (await (await fetch("/api/recent?limit=40")).json())
      .filter((r) => r.status === "done" && !seen.has(r.svs_id) && seen.add(r.svs_id)).slice(0, 6);
    if (state.boardTop !== "idle") return;
    recent.replaceChildren(...(rows.length ? rows.map((r, i) => h("li", { class: "w", style: { "--d": 7 + i } },
      h("a", { href: `/share/${r.code}`, onclick: (e) => { e.preventDefault(); go(r.code); } },
        h("span", { class: "rid" }, `No. ${pad5(r.svs_id)}`), r.svs_title || "")))
      : [h("li", { class: "dim" }, "Nothing yet. Be the first!")]));
  } catch { recent.replaceChildren(); }
}
function renderBoard(d) {
  const s = d.svs || {};
  const topKey = `${d.code}:${s.title ? 1 : 0}`;
  if (state.boardTop !== topKey) {
    state.boardTop = topKey;
    state.boardAnalysis = false;
    stopSpeaking();
    const kicker = [`SVS No. ${pad5(s.id)}`, s.release_date, s.page_type].filter(Boolean).join(" · ");
    const nodes = [h("p", { class: "c-kicker w", style: { "--d": 0 } }, kicker), h("h2", { class: "c-title w", style: { "--d": 1 } }, s.title || "")];
    const img = s.image_url || (s.id ? `/api/cover/${s.id}` : null);
    if (img) {
      nodes.push(h("a", {
        class: "polaroid w", style: { "--d": 2 }, href: s.url || `https://svs.gsfc.nasa.gov/${s.id}/`,
        target: "_blank", rel: "noopener", title: "Open the NASA page",
      }, coverImg(s.id, img, s.title ? `NASA visualization: ${s.title}` : ""), h("span", {}, "NASA SVS ↗")));
    }
    nodes.push(
      h("h3", { id: "c-what", class: "w", style: { "--d": 2 } }, "What is this?"),
      h("p", { id: "c-summary", class: "w", style: { "--d": 3 } }, ""),
      h("p", { id: "c-status", class: "wait w", style: { "--d": 4 } }, "Reading the NASA page"),
      h("div", { id: "c-analysis" }),
    );
    el.chalk.replaceChildren(...nodes);
    el.chalk.scrollTop = 0;
  }
  const kick = el.chalk.querySelector(".c-kicker");
  if (kick) kick.textContent = [`SVS No. ${pad5(s.id)}`, s.release_date, s.page_type].filter(Boolean).join(" · ");
  const a = d.analysis;
  const summary = $("#c-summary");
  if (summary) {
    const text = a ? a.summary : clip(s.description || "", 420);
    if (summary.textContent !== text) summary.textContent = text;
  }
  const status = $("#c-status");
  if (status) {
    const failed = d.status === "failed" && !a;
    status.className = failed ? "err" : a ? "" : "wait";
    status.hidden = Boolean(a);
    status.textContent = failed ? `Something went wrong: ${d.error || "unknown error"}` : d.status === "analyzing" ? "Jukebox is studying the data stream"
      : d.status === "watching" ? "Jukebox is watching the video" : "Reading the NASA page";
  }
  if (a && !state.boardAnalysis) {
    state.boardAnalysis = true;
    $("#c-what")?.append(sayButton("What is this?", d.code, "summary"));
    $("#c-analysis").replaceChildren(...analysisNodes(d));
  }
}
function clip(text, n) { return text.length > n ? text.slice(0, n - 1).replace(/\s+\S*$/, "") + "…" : text; }
function analysisNodes(d) {
  const a = d.analysis;
  const s = d.svs;
  let k = 0;
  const w = (node) => { node.classList.add("w"); node.style.setProperty("--d", k++); return node; };
  const out = [];
  if (a.video_description) {
    out.push(
      w(h("h3", {}, "Video analysis", sayButton("Video analysis", d.code, "video"))),
      w(h("p", {}, a.video_description)),
    );
  }
  out.push(
    w(h("h3", {}, "How to listen", sayButton("How to listen", d.code, "listen"))),
    w(h("p", {}, a.listening_guide || "")),
    w(h("h3", {}, "The recipe", sayButton("The recipe", d.code, "recipe"))),
    w(h("div", { class: "recipe" },
      a.mood ? h("span", {}, h("b", {}, "Mood "), a.mood) : null,
      a.tempo_bpm ? h("span", {}, h("b", {}, "Tempo "), `${a.tempo_bpm} BPM`) : null,
      a.musical_key ? h("span", {}, h("b", {}, "Key "), a.musical_key) : null)),
  );
  if ((a.instruments || []).length) out.push(w(h("p", {}, h("span", { class: "dim" }, "Instruments: "), a.instruments.join(", "))));
  out.push(
    w(h("h3", {}, "Part 1 · About the music", h("span", { class: "src" }, "by Google Lyria 2"),
      sayButton("Part 1", d.code, "part1"))),
    w(h("div", { class: "prompt" }, a.lyria_prompt || "")),
  );
  out.push(
    w(h("h3", {}, "Part 2 · About the music", h("span", { class: "src" }, "by Meta MusicGen"),
      sayButton("Part 2", d.code, "part2"))),
    w(h("div", { class: "prompt p2" }, a.musicgen_prompt || "")),
  );
  out.push(w(h("div", { class: "links" },
    h("a", { href: s.url || `https://svs.gsfc.nasa.gov/${s.id}/`, target: "_blank", rel: "noopener" }, "Open on NASA SVS ↗"),
    h("a", { href: `/api/generations/${d.code}/record`, target: "_blank", rel: "noopener" }, "{ } saved JSON"))));
  return out;
}

/* ===================== read aloud (Google Cloud TTS) ===================== */
// One voice at a time: a new speaker button takes over; pressing the same one again stops it.
// The server reads the section from the saved analysis and caches the MP3 per record.
const voice = new Audio();
voice.preload = "none";
function setSaying(btn, on) {
  btn.classList.toggle("on", on);
  btn.setAttribute("aria-pressed", String(on));
}
function stopSpeaking() {
  const btn = state.sayBtn;
  state.sayBtn = null;
  if (btn) setSaying(btn, false);
  voice.pause();
}
function speak(btn, url) {
  const again = state.sayBtn === btn;
  stopSpeaking();
  if (again) return;
  state.sayBtn = btn;
  setSaying(btn, true);
  voice.src = url;
  voice.play().catch(() => { if (state.sayBtn === btn) stopSpeaking(); });
}
voice.addEventListener("ended", stopSpeaking);
voice.addEventListener("error", () => { if (voice.src) stopSpeaking(); });
function sayButton(label, code, section) {
  const btn = h("button", {
    class: "say", type: "button", title: "Read aloud", "aria-label": `Read "${label}" aloud`, "aria-pressed": "false",
    onclick: (e) => { e.stopPropagation(); speak(btn, `/api/tts/${code}/${section}`); },
  });
  btn.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path class="cone" d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5z"/>'
    + '<path class="wave" d="M15.5 9a4.2 4.2 0 0 1 0 6M18 6.5a7.8 7.8 0 0 1 0 11"/></svg>';
  return btn;
}

/* ===================== share popover + cookie history ===================== */
function readHistory() {
  const m = document.cookie.match(new RegExp(`(?:^|; )${HIST_COOKIE}=([^;]*)`));
  if (!m) return [];
  return decodeURIComponent(m[1]).split("~").map((part) => {
    const [code, id, ts] = part.split(".");
    return /^[a-z]{5}$/.test(code || "") ? { code, id: Number(id), ts: parseInt(ts, 36) * 1000 } : null;
  }).filter(Boolean);
}
function addHistory(code, id) {
  const list = [{ code, id, ts: Date.now() }, ...readHistory().filter((x) => x.code !== code)].slice(0, HIST_MAX);
  const value = list.map((x) => `${x.code}.${x.id}.${Math.floor(x.ts / 1000).toString(36)}`).join("~");
  document.cookie = `${HIST_COOKIE}=${encodeURIComponent(value)}; Max-Age=${60 * 60 * 24 * 365}; Path=/; SameSite=Lax`;
}
async function openPop() {
  el.pop.hidden = false;
  el.shareBtn.setAttribute("aria-expanded", "true");
  el.spCurrent.hidden = !state.code;
  el.spNone.hidden = Boolean(state.code);
  if (state.code) { el.shareUrl.focus(); el.shareUrl.select(); el.shareUrl.scrollLeft = 0; } else el.spClose.focus();
  const hist = readHistory();
  if (!hist.length) { el.spHistory.replaceChildren(h("li", { class: "sp-empty" }, "Nothing yet — tracks you START show up here.")); return; }
  el.spHistory.replaceChildren(h("li", { class: "sp-empty" }, "Loading…"));
  let rows = {};
  try {
    const list = await (await fetch(`/api/generations?codes=${hist.map((x) => x.code).join(",")}`)).json();
    rows = Object.fromEntries(list.map((r) => [r.code, r]));
  } catch { /* show what the cookie knows */ }
  el.spHistory.replaceChildren(...hist.map((x) => {
    const r = rows[x.code] || {};
    return h("li", {}, h("a", {
      href: `/share/${x.code}`, class: x.code === state.code ? "current" : null,
      onclick: (e) => { e.preventDefault(); closePop(); go(x.code); },
    },
    h("span", { class: "thumb" }, coverImg(x.id, r.image_url)),
    h("span", { style: { "min-width": "0" } },
      h("div", { class: "t" }, r.title || `Record ${pad5(x.id)}`),
      h("div", { class: "m" }, `No. ${pad5(x.id)} · /share/${x.code}${r.status && r.status !== "done" ? ` · ${r.status}` : ""}`)),
    x.code === state.code ? h("span", { class: "now" }, "Now playing") : null));
  }));
}
function closePop(refocus = false) {
  if (el.pop.hidden) return;
  el.pop.hidden = true; el.shareBtn.setAttribute("aria-expanded", "false");
  if (refocus) el.shareBtn.focus();
}
function go(code) {
  if (location.pathname !== `/share/${code}`) history.pushState({ code }, "", `/share/${code}`);
  ensureAudioGraph();
  openGeneration(code, { autoplay: true });
}

/* ===================== tilt (messages) ===================== */
function tilt(message, title = "Tilt!") {
  el.tiltH.textContent = title;
  el.tiltMsg.textContent = message;
  el.tilt.hidden = false;
  el.tiltOk.focus();
  announce(message);
}

/* ===================== audio graph + equalizer ===================== */
function ensureAudioGraph() {
  try {
    if (!state.audioCtx) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      state.audioCtx = new Ctx();
      const src = state.audioCtx.createMediaElementSource(el.audio);
      state.analyser = state.audioCtx.createAnalyser();
      state.analyser.fftSize = 256;
      state.analyser.smoothingTimeConstant = 0.78;
      src.connect(state.analyser);
      state.analyser.connect(state.audioCtx.destination);
    }
    if (state.audioCtx.state === "suspended") state.audioCtx.resume();
  } catch { /* the equalizer is optional */ }
}
function drawEq() {
  requestAnimationFrame(drawEq);
  const canvas = el.eq;
  const playing = !el.audio.paused;
  if (!playing && !state.eqFading) return;
  const w = 960, hh = 330;
  if (canvas.width !== w * 2) { canvas.width = w * 2; canvas.height = hh * 2; }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(2, 0, 0, 2, 0, 0);
  ctx.clearRect(0, 0, w, hh);
  const bars = 48, gap = 5, bw = (w - 40 - gap * (bars - 1)) / bars;
  let data = null;
  if (state.analyser && playing) {
    data = new Uint8Array(state.analyser.frequencyBinCount);
    state.analyser.getByteFrequencyData(data);
  }
  const grad = ctx.createLinearGradient(0, hh, 0, 30);
  grad.addColorStop(0, "#ff3f8e"); grad.addColorStop(0.55, "#ffb627"); grad.addColorStop(1, "#38f4ff");
  ctx.globalCompositeOperation = "lighter";
  for (let i = 0; i < bars; i++) {
    // log-ish spread so the bass doesn't hog the screen
    // music lives mostly below ~8 kHz (first ~45 of 128 bins); spread bars over that, lift the treble a bit
    const bin = data ? Math.min(data.length - 1, Math.floor(Math.pow(i / bars, 1.25) * 46) + 1) : 0;
    const v = data ? Math.min(1, (data[bin] / 255) * (0.9 + 0.9 * (i / bars))) : 0;
    const bh = Math.max(4, v * (hh - 40));
    const x = 20 + i * (bw + gap);
    ctx.fillStyle = grad;
    ctx.globalAlpha = 0.72;
    for (let y = hh - 4; y > hh - bh; y -= 9) ctx.fillRect(x, y - 6, bw, 6); // segmented, like an old LED meter
    const peak = Math.max(bh, (state.peaks[i] || 0) - 1.6);
    state.peaks[i] = peak;
    ctx.globalAlpha = 0.95;
    ctx.fillStyle = "#fff";
    ctx.fillRect(x, hh - peak - 10, bw, 3);
  }
  ctx.globalAlpha = 1;
  ctx.globalCompositeOperation = "source-over";
}

/* ===================== wiring ===================== */
function wire() {
  window.addEventListener("resize", fit);
  el.floorUp.addEventListener("click", () => { if (state.page > 0) loadFloor(state.page - 1); });
  el.floorDown.addEventListener("click", () => { if (state.page < state.pages - 1) loadFloor(state.page + 1); });
  el.dialPrev.addEventListener("click", () => stepDial(-1));
  el.dialNext.addEventListener("click", () => stepDial(1));
  el.keys.addEventListener("keydown", (e) => {
    if (e.key === "ArrowLeft") { e.preventDefault(); stepDial(-1); el.keys.querySelector(".key.center")?.focus(); }
    if (e.key === "ArrowRight") { e.preventDefault(); stepDial(1); el.keys.querySelector(".key.center")?.focus(); }
  });
  let wheelAt = 0;
  el.keys.addEventListener("wheel", (e) => {
    const d = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
    if (Math.abs(d) < 4) return;
    e.preventDefault();
    const now = performance.now();
    if (now - wheelAt < 140) return;
    wheelAt = now; stepDial(d > 0 ? 1 : -1);
  }, { passive: false });
  let swipeX = null;
  el.keys.addEventListener("pointerdown", (e) => { swipeX = e.clientX; });
  el.keys.addEventListener("pointerup", (e) => {
    if (swipeX == null) return;
    const scale = el.stage.getBoundingClientRect().width / el.stage.offsetWidth || 1;
    const dx = (e.clientX - swipeX) / scale; swipeX = null;
    if (Math.abs(dx) > 40) { stepDial(-Math.round(dx / 130) || (dx > 0 ? -1 : 1)); el.keys.dataset.swiped = "1"; setTimeout(() => { delete el.keys.dataset.swiped; }, 0); }
  });
  el.keys.addEventListener("click", (e) => { if (el.keys.dataset.swiped) { e.stopPropagation(); e.preventDefault(); } }, true);

  el.digitsBtn.addEventListener("click", () => openNumberDialog(state.num));
  el.clear.addEventListener("click", clearNumber);
  el.random.addEventListener("click", playRandom);
  el.ndForm.addEventListener("submit", (e) => { e.preventDefault(); submitNumber(); });
  el.ndCancel.addEventListener("click", closeNumberDialog);
  el.nd.addEventListener("click", (e) => { if (e.target === el.nd) closeNumberDialog(); });
  el.ndInput.addEventListener("input", () => {
    const v = el.ndInput.value.replace(/\D/g, "").slice(0, 5);
    if (v !== el.ndInput.value) el.ndInput.value = v;
    if (el.ndMsg.classList.contains("bad")) ndMessage("");
  });
  el.start.addEventListener("click", start);

  el.play.addEventListener("click", () => {
    state.finished = false;
    if (el.audio.paused) tryPlay(); else el.audio.pause();
  });
  el.pressPlay.addEventListener("click", () => { el.pressPlay.hidden = true; tryPlay(); });
  el.parts.forEach((b) => b.addEventListener("click", () => onPart(b.dataset.track)));
  el.audio.addEventListener("play", () => {
    el.play.classList.add("is-playing"); el.play.setAttribute("aria-label", "Pause");
    el.dome.classList.add("playing", "spinning"); el.dome.classList.remove("composing");
    state.finished = false;
    const P = PARTS[state.track];
    setLcd(`Now playing · Part ${P.n} · ${P.lcd}`);
  });
  el.audio.addEventListener("pause", () => {
    el.play.classList.remove("is-playing"); el.play.setAttribute("aria-label", "Play");
    el.dome.classList.remove("playing", "spinning");
    state.eqFading = true; setTimeout(() => { state.eqFading = false; }, 700);
    if (state.data && !el.audio.ended) renderTransport();
  });
  el.audio.addEventListener("ended", onEnded);
  el.audio.addEventListener("timeupdate", () => {
    const dur = el.audio.duration;
    if (Number.isFinite(dur) && dur > 0) el.seek.value = String(Math.round((el.audio.currentTime / dur) * 1000));
    el.time.textContent = `${fmtTime(el.audio.currentTime)} / ${fmtTime(dur)}`;
  });
  el.audio.addEventListener("loadedmetadata", () => { el.time.textContent = `0:00 / ${fmtTime(el.audio.duration)}`; });
  el.seek.addEventListener("input", () => {
    const dur = el.audio.duration;
    if (Number.isFinite(dur)) el.audio.currentTime = (Number(el.seek.value) / 1000) * dur;
  });

  el.eject.addEventListener("click", () => eject());
  el.shareBtn.addEventListener("click", (e) => { e.stopPropagation(); if (el.pop.hidden) openPop(); else closePop(); });
  el.pop.addEventListener("click", (e) => { if (e.target === el.pop) closePop(true); });
  el.spClose.addEventListener("click", () => closePop(true));
  el.copy.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(el.shareUrl.value); }
    catch { el.shareUrl.select(); document.execCommand("copy"); }
    el.copy.textContent = "Copied!"; announce("Share link copied.");
    setTimeout(() => { el.copy.textContent = "Copy link"; }, 1600);
  });
  el.shareUrl.addEventListener("focus", () => el.shareUrl.select());
  el.tiltOk.addEventListener("click", () => { el.tilt.hidden = true; el.start.focus(); });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (!el.tilt.hidden) { el.tilt.hidden = true; return; }
      if (!el.nd.hidden) { closeNumberDialog(); return; }
      if (!el.pop.hidden) { closePop(); el.shareBtn.focus(); return; }
    }
    // Typing a digit anywhere opens the number dialog with that digit in it.
    const typing = e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement;
    if (!typing && el.nd.hidden && el.tilt.hidden && /^[0-9]$/.test(e.key) && !e.metaKey && !e.ctrlKey && !e.altKey) {
      e.preventDefault();
      openNumberDialog(e.key);
    }
  });
  window.addEventListener("popstate", routeFromUrl);
}
function routeFromUrl() {
  const m = location.pathname.match(/^\/share\/([a-z]{5})\/?$/);
  if (m) { if (state.code !== m[1]) openGeneration(m[1], { autoplay: true }); }
  else if (state.code) eject(false);
}

async function init() {
  fit();
  wire();
  renderDigits("", false);
  renderCards("Loading…"); renderRack();
  try {
    state.topics = await (await fetch("/api/categories")).json();
  } catch {
    state.topics = [{ label: "EIC Latest", query: "" }];
  }
  renderKeys();
  if (!location.pathname.startsWith("/share/")) renderIdleBoard();
  routeFromUrl();
  selectTopic(0, { now: true });
  requestAnimationFrame(drawEq);
}
init();
