// 番号配置アプリ(Web版) 画面と操作
// Win版(zumen_annot.pyw)と同じ編集ファイル(.annot)を読み書きする。操作は、指・ペン・マウスのどれでもできるようにしてある。
import * as C from "./core.js";
import { drawAnnotation, drawSelection } from "./render.js";
import * as IO from "./io.js";

const $ = (s) => document.querySelector(s);
const APP = C.APP_NAME;
const LONG_PRESS_MS = 520;
const DOUBLE_TAP_MS = 420;

// ------------------------------------------------------------- 状態 ---
const S = {
  sources: [], order: [], curPage: 0, showTable: true,
  cache: new Map(), base: null, pageTok: 0,
  pages: {}, common: C.defaultCommon(), nextNum: 1,
  sel: null, selArrow: 0,
  view: { z: 1, tx: 0, ty: 0 },
  annotName: null, dirty: false, undo: [], redo: [],
  paper: { size: C.NO_PAPER, orient: "自動", margin: "10", dpi: "200" },
  sortMode: 0, placeMode: true,
  pend: { font_size: "28", length: "80", width: "4", c_text: "#000000", c_circle: "#000000", c_arrow: "#000000" },
  drag: null, pointers: new Map(), gesture: null, ghost: null, lastTap: null, lp: null, menuAt: 0,
  touch: false, editing: null, rowLP: null, savedBackup: false,
};
const pageCount = () => S.order.length;
const firstName = () => (S.sources[0] ? S.sources[0].name : null);
const SORT_MARK = { 0: "▲▼", 1: "▲", 2: "▼" };
const TIP_KEY = { tip: "tip", shaft: "shaft" };

const cv = $("#view");
const ctx = cv.getContext("2d");
let drawQueued = false;

function requestDraw() {
  if (drawQueued) return;
  drawQueued = true;
  requestAnimationFrame(() => { drawQueued = false; draw(); });
}

function status(msg) {
  $("#status").textContent = msg;
}

// -------------------------------------------------- ダイアログ・メニュー ---
function modal({ title = "", message = "", buttons, row = false }) {
  return new Promise((resolve) => {
    const back = $("#modalBack"), box = $("#modal");
    box.innerHTML = "";
    if (title) { const h = document.createElement("h3"); h.textContent = title; box.appendChild(h); }
    if (message) { const p = document.createElement("p"); p.textContent = message; box.appendChild(p); }
    const wrap = document.createElement("div");
    wrap.className = "btns" + (row ? " row" : "");
    for (const b of buttons) {
      const el = document.createElement("button");
      el.className = "btn" + (b.primary ? " primary" : "");
      el.textContent = b.label;
      el.onclick = async () => {
        if (b.keepOpen) { b.onClick && b.onClick(); return; }
        back.hidden = true;
        resolve(b.value);
      };
      wrap.appendChild(el);
    }
    box.appendChild(wrap);
    back.hidden = false;
  });
}
const alertBox = (message, title = APP) => modal({ title, message, buttons: [{ label: "OK", value: true, primary: true }], row: true });

function hideMenu() {
  const m = $("#menu");
  m.hidden = true;
  m.innerHTML = "";
}

function placeMenu(x, y) {
  const m = $("#menu");
  m.hidden = false;
  const w = m.offsetWidth, h = m.offsetHeight;
  const vw = window.innerWidth, vh = window.innerHeight;
  m.style.left = Math.max(4, Math.min(vw - w - 4, x)) + "px";
  m.style.top = Math.max(4, Math.min(vh - h - 4, y)) + "px";
}

function showMenu(items, x, y) {
  const m = $("#menu");
  m.innerHTML = "";
  for (const it of items) {
    if (it === "-") { m.appendChild(document.createElement("hr")); continue; }
    const b = document.createElement("button");
    b.textContent = it.label;
    b.disabled = !!it.disabled;
    b.onclick = () => { hideMenu(); it.action(); };
    m.appendChild(b);
  }
  placeMenu(x, y);
  S.menuAt = performance.now();
}

function showMenuBelow(btn, items) {
  const r = btn.getBoundingClientRect();
  showMenu(items, r.left, r.bottom + 4);
}

function showPalette(x, y, current, cb) {
  const m = $("#menu");
  m.innerHTML = "";
  const grid = document.createElement("div");
  grid.className = "grid";
  for (const [name, hx] of C.PALETTE) {
    const b = document.createElement("button");
    b.textContent = name;
    b.style.background = hx;
    b.style.color = C.contrastFg(hx);
    b.onclick = () => { hideMenu(); cb(hx); };
    grid.appendChild(b);
  }
  m.appendChild(grid);
  const other = document.createElement("button");
  other.textContent = "その他の色…";
  const inp = document.createElement("input");
  inp.type = "color";
  inp.value = /^#[0-9a-f]{6}$/i.test(current) ? current : "#000000";
  inp.style.cssText = "position:absolute;opacity:0;width:1px;height:1px;pointer-events:none";
  inp.onchange = () => { hideMenu(); cb(inp.value.toUpperCase()); };
  other.onclick = () => inp.click();
  m.appendChild(other);
  m.appendChild(inp);
  placeMenu(x, y);
  S.menuAt = performance.now();
}

document.addEventListener("pointerdown", (e) => {
  const m = $("#menu");
  if (!m.hidden && !m.contains(e.target)) hideMenu();
}, true);

// --------------------------------------------------------- データ操作 ---
const anns = () => (S.pages[S.curPage] ||= []);
function curAnn() {
  if (S.sel == null) return null;
  const l = anns();
  return S.sel >= 0 && S.sel < l.length ? l[S.sel] : null;
}
const snapshot = () => C.clone({ pages: S.pages, common: S.common, nextNum: S.nextNum, order: S.order });
function restore(snap) {
  const s = C.clone(snap);
  const orderChanged = JSON.stringify(s.order) !== JSON.stringify(S.order);
  S.pages = s.pages; S.common = s.common; S.nextNum = s.nextNum; S.order = s.order;
  $("#vNext").value = String(S.nextNum);
  S.sel = null; S.selArrow = 0;
  loadCommonToUI();
  refreshTable();
  requestDraw();
  if (orderChanged && S.base) goPage(Math.min(S.curPage, pageCount() - 1)); // 追加・入れ替えを取り消したときは、表示も合わせる
}
function pushUndo() {
  S.undo.push(snapshot());
  if (S.undo.length > 100) S.undo.shift();
  S.redo.length = 0;
  setDirty(true);
}
function undo() {
  if (!S.undo.length) return;
  S.redo.push(snapshot());
  restore(S.undo.pop());
  setDirty(true);
}
function redo() {
  if (!S.redo.length) return;
  S.undo.push(snapshot());
  restore(S.redo.pop());
  setDirty(true);
}
function setDirty(v) {
  S.dirty = v;
  updateTitle();
  if (v) scheduleAutosave();
}
function updateTitle() {
  const name = S.annotName || firstName() || "";
  document.title = `${S.dirty ? "*" : ""}${name || "(未選択)"} - ${APP}`;
  $("#btnUndo").disabled = !S.undo.length;
  $("#btnRedo").disabled = !S.redo.length;
}
window.addEventListener("beforeunload", (e) => {
  if (S.dirty) { e.preventDefault(); e.returnValue = ""; }
});

// ------------------------------------------------------ 共通ステータス ---
function pendingValues() {
  const fs = parseFloat($("#pFs").value), ln = parseFloat($("#pLen").value), wd = parseFloat($("#pWid").value);
  if (![fs, ln, wd].every(Number.isFinite)) return null;
  return { font_size: fs, length: ln, width: wd, c_text: S.pend.c_text, c_circle: S.pend.c_circle, c_arrow: S.pend.c_arrow };
}
function refreshPending() {
  const pv = pendingValues();
  let diff = pv === null;
  if (!diff) {
    diff = ["font_size", "length", "width"].some((k) => Math.abs(pv[k] - S.common[k]) > 1e-9)
      || C.COLOR_FIELDS.some(([k]) => String(pv[k]).toLowerCase() !== String(S.common[k]).toLowerCase());
  }
  $("#pending").textContent = diff ? "※未更新(「更新」で反映)" : "";
}
function updateColorButtons() {
  const map = { c_text: "#cText", c_circle: "#cCircle", c_arrow: "#cArrow" };
  for (const [k, sel] of Object.entries(map)) {
    const b = $(sel), hx = S.pend[k];
    b.style.background = hx;
    b.style.color = C.contrastFg(hx);
    b.textContent = C.colorName(hx);
  }
}
function loadCommonToUI() {
  $("#pFs").value = C.fmtG(S.common.font_size);
  $("#pLen").value = C.fmtG(S.common.length);
  $("#pWid").value = C.fmtG(S.common.width);
  for (const [k] of C.COLOR_FIELDS) S.pend[k] = S.common[k];
  updateColorButtons();
  refreshPending();
}
function applyCommon() {
  const pv = pendingValues();
  if (!pv) { alertBox("文字サイズ・矢印の長さ・太さには数値を入力してください。"); return; }
  const nw = C.clampCommon(pv);
  pushUndo();
  S.common = nw;
  const n = C.applyCommonToPages(S.pages, nw);
  loadCommonToUI();
  refreshTable();
  requestDraw();
  status(`共通ステータスを更新しました(共通ONの注記 ${n} 件に反映)`);
}
function nextNumberFromUI() {
  const v = parseInt($("#vNext").value, 10);
  return Number.isFinite(v) ? v : S.nextNum;
}
function setNext(n) {
  S.nextNum = n;
  $("#vNext").value = String(n);
}

// ------------------------------------------------------------- 表示 ---
function resize() {
  const st = $("#stage");
  const w = st.clientWidth, h = st.clientHeight, dpr = window.devicePixelRatio || 1;
  cv.width = Math.max(1, Math.round(w * dpr));
  cv.height = Math.max(1, Math.round(h * dpr));
  requestDraw();
}
const stageSize = () => ({ w: $("#stage").clientWidth, h: $("#stage").clientHeight });

function fitParams() {
  if (!S.base) return null;
  const { w, h } = stageSize();
  const [x0, y0, x1, y1] = C.contentRect(S.base.W, S.base.H, S.paper);
  const z = Math.max(0.01, Math.min(w / (x1 - x0), h / (y1 - y0)) * 0.95);
  return { z, tx: (w - (x1 - x0) * z) / 2 - x0 * z, ty: (h - (y1 - y0) * z) / 2 - y0 * z };
}
function fit() {
  const f = fitParams();
  if (!f) return;
  S.view.z = f.z; S.view.tx = f.tx; S.view.ty = f.ty;
  requestDraw();
}
function zoomAt(pt, z) {
  z = Math.max(0.03, Math.min(16, z));
  const ix = (pt.x - S.view.tx) / S.view.z, iy = (pt.y - S.view.ty) / S.view.z;
  S.view.z = z;
  S.view.tx = pt.x - ix * z;
  S.view.ty = pt.y - iy * z;
  requestDraw();
}
function zoomBy(f) {
  const { w, h } = stageSize();
  zoomAt({ x: w / 2, y: h / 2 }, S.view.z * f);
}

function draw() {
  const dpr = window.devicePixelRatio || 1;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.fillStyle = "#6b6b6b";
  ctx.fillRect(0, 0, cv.width, cv.height);
  if (!S.base) return;
  const { z, tx, ty } = S.view;
  const { W, H, raster } = S.base;
  ctx.setTransform(dpr * z, 0, 0, dpr * z, dpr * tx, dpr * ty);
  const lay = C.paperLayout(W, H, S.paper);
  let rects = null;
  if (lay) {
    rects = C.paperRects(lay);
    const [px0, py0, px1, py1] = rects.paper;
    ctx.fillStyle = "#FFFFFF";
    ctx.fillRect(px0, py0, px1 - px0, py1 - py0);
    ctx.lineWidth = 2 / z;
    ctx.strokeStyle = "#222222";
    ctx.strokeRect(px0, py0, px1 - px0, py1 - py0);
  }
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(raster, 0, 0, W, H);
  if (rects) {
    const [ax0, ay0, ax1, ay1] = rects.area;
    ctx.save();
    ctx.setLineDash([6 / z, 4 / z]);
    ctx.lineWidth = 1 / z;
    ctx.strokeStyle = "#2F7FD8";
    ctx.strokeRect(ax0, ay0, ax1 - ax0, ay1 - ay0);
    ctx.restore();
  }
  const list = anns();
  list.forEach((a) => { if (C.isPlaced(a)) drawAnnotation(ctx, a); });
  const sa = curAnn();
  if (sa && C.isPlaced(sa)) drawSelection(ctx, sa, S.selArrow, z, S.touch);
  if (S.ghost) drawAnnotation(ctx, S.ghost);
}

// ------------------------------------------------------ 図面の読み込み ---
async function confirmDiscard() {
  if (!S.dirty) return true;
  const ans = await modal({
    title: APP, message: "変更が保存されていません。どうしますか?",
    buttons: [{ label: "保存する", value: "save", primary: true }, { label: "保存しないで続ける", value: "discard" }, { label: "キャンセル", value: "cancel" }],
  });
  if (ans === "save") { await saveAnnot(); return true; }
  return ans === "discard";
}

function pickFile(input) {
  return new Promise((resolve) => {
    input.value = "";
    input.onchange = () => resolve(Array.from(input.files || []));
    input.click();
  });
}

const DRAW_EXT = ["pdf", "png", "jpg", "jpeg", "bmp", "gif", "webp", "tif", "tiff"];
const isAnnotFile = (f) => ["annot", "json"].includes(IO.extOf(f.name));
const isDrawingFile = (f) => DRAW_EXT.includes(IO.extOf(f.name)) || f.type === "application/pdf" || String(f.type || "").startsWith("image/");

/** 図面ファイル1つを読み込んで、1つの「図面」(sources の要素)にする */
async function readSource(file) {
  const ext = IO.extOf(file.name);
  if (ext === "pdf" || file.type === "application/pdf") {
    const doc = await IO.openPdf(file);
    return { name: file.name, type: "pdf", blob: file, pdf: doc, imageBase: null, pageCount: doc.numPages };
  }
  const base = await IO.openImage(file);
  return { name: file.name, type: "image", blob: file, pdf: null, imageBase: base, pageCount: 1 };
}

async function readSources(files) {
  const out = [];
  try {
    for (const f of files) {
      try { out.push(await readSource(f)); } catch (e) { throw new Error(`${f.name}: ${e && e.message ? e.message : e}`); }
    }
  } catch (e) { closeSources(out); throw e; }
  return out;
}

function closeSources(list) {
  for (const s of list) if (s.pdf && s.pdf.destroy) { try { s.pdf.destroy(); } catch (e) { /* 無視 */ } }
}

const naturalEntries = (idx, count) => Array.from({ length: count }, (_, p) => ({ s: idx, p }));

/** 図面を全部入れ替える(開き直し)。order を省くと、頭から全ページの並びになる */
function useSources(srcs, order = null) {
  closeSources(S.sources);
  S.sources = srcs;
  S.order = order || srcs.flatMap((s, i) => naturalEntries(i, s.pageCount));
  S.cache.clear();
}

async function getBase(g) {
  const e = S.order[g];
  const src = S.sources[e.s];
  if (src.type === "image") return src.imageBase;
  const key = `${e.s}:${e.p}`;
  if (S.cache.has(key)) return S.cache.get(key);
  const b = await IO.renderPdfPage(src.pdf, e.p);
  S.cache.set(key, b);
  while (S.cache.size > 2) S.cache.delete(S.cache.keys().next().value);
  return b;
}

async function goPage(n) {
  const tok = ++S.pageTok;
  n = Math.max(0, Math.min(pageCount() - 1, n));
  status("読み込み中…");
  let base;
  try { base = await getBase(n); } catch (e) { await alertBox("ページを表示できませんでした。\n" + e.message); return; }
  if (tok !== S.pageTok) return;
  S.curPage = n;
  S.base = base;
  S.sel = null; S.selArrow = 0;
  $("#pageLabel").textContent = `${n + 1} / ${pageCount()}`;
  $("#empty").hidden = true;
  resize();
  fit();
  refreshTable();
  updateTitle();
  const e = S.order[n], src = S.sources[e.s];
  let msg = src.name;
  if (src.pageCount > 1) msg += ` (${e.p + 1}/${src.pageCount}ページ)`;
  msg += `  (${base.W} × ${base.H} px)`;
  if (base.k < 1) msg += `  ※大きいため、表示用の画像は ${Math.round(base.k * 100)}% に縮小しています(座標は元の大きさのまま)`;
  status(msg);
}

function autoDefaults(base) {
  const fs = Math.trunc(Math.min(80, Math.max(16, base.W / 80)));
  S.common.font_size = fs;
  S.common.length = fs * 3;
  S.common.width = Math.max(2, C.pyround(fs / 7));
  loadCommonToUI();
}

/** 「開く」: 図面(PDF・画像)も編集ファイル(.annot)も、ここから開く */
async function openFiles() {
  const files = await pickFile($("#fileOpen"));
  if (!files.length) return;
  if (!(await confirmDiscard())) return;
  await openPicked(files);
}

async function openPicked(files) {
  if (files.some(isAnnotFile)) { await openAnnotFiles(files); return; }
  const drawings = files.filter(isDrawingFile);
  if (!drawings.length) { await alertBox("図面(PDF・画像)か、編集ファイル(.annot)を選んでください。"); return; }
  await newDrawing(drawings);
}

async function newDrawing(files) {
  files = [].concat(files);
  status("読み込み中…");
  let srcs;
  try { srcs = await readSources(files); } catch (e) { await alertBox("図面を開けませんでした。\n" + (e && e.message ? e.message : e)); return false; }
  useSources(srcs);
  S.pages = {}; S.annotName = null; S.undo = []; S.redo = [];
  setNext(1);
  S.common = C.defaultCommon();
  try { autoDefaults(await getBase(0)); } catch (e) { /* goPage で報告される */ }
  await goPage(0);
  setDirty(false);
  saveBlobBackup();
  return true;
}

/** 「追加」: 開いている作業の最後のページの後ろに、図面(PDF・画像)を足す。編集ファイルは足せない */
async function addFiles() {
  const files = await pickFile($("#fileOpen"));
  if (!files.length) return;
  if (!S.base) { await openPicked(files); return; }
  const drawings = files.filter((f) => !isAnnotFile(f) && isDrawingFile(f));
  const skipped = files.length - drawings.length;
  if (!drawings.length) { await alertBox("追加できるのは図面(PDF・画像)です。編集ファイル(.annot)は追加できません。"); return; }
  status("読み込み中…");
  let srcs;
  try { srcs = await readSources(drawings); } catch (e) { await alertBox("図面を追加できませんでした。\n" + (e && e.message ? e.message : e)); return; }
  pushUndo();
  const first = pageCount();
  for (const s of srcs) {
    const i = S.sources.length;
    S.sources.push(s);
    S.order.push(...naturalEntries(i, s.pageCount));
  }
  const added = pageCount() - first;
  await goPage(first);
  saveBlobBackup();
  status(`${added}ページを最後に追加しました(全 ${pageCount()} ページ)` + (skipped ? `  ※図面以外の ${skipped} 件は追加していません` : ""));
}

// ------------------------------------------------------ 編集ファイル ---
/** 保存する図面の一覧と並び。いま使っている図面だけを、使い始めた順に並べ直す */
function layoutForSave() {
  const map = new Map(), sources = [];
  const order = S.order.map((e) => {
    if (!map.has(e.s)) { map.set(e.s, sources.length); sources.push(S.sources[e.s]); }
    return [map.get(e.s), e.p];
  });
  return { sources, order };
}

function annotData() {
  const { sources, order } = layoutForSave();
  return C.buildAnnot({
    sources: sources.map((s) => ({ name: s.name, type: s.type, pageCount: s.pageCount })), order,
    curPage: S.curPage, common: S.common, nextNum: S.nextNum, paper: S.paper, pages: S.pages,
  });
}

async function saveAnnot() {
  if (!S.base) { await alertBox("先に図面を開いてください。"); return false; }
  const name = S.annotName || IO.stemOf(firstName()) + ".annot";
  const blob = new Blob([JSON.stringify(annotData(), null, 1)], { type: "application/octet-stream" });
  IO.download(blob, name);
  S.annotName = name;
  setDirty(false);
  status(`編集ファイルを書き出しました: ${name}(iPadでは「ファイル」アプリのダウンロード先に入ります)`);
  return true;
}

async function openAnnotFiles(files) {
  const annotFile = files.find((f) => IO.extOf(f.name) === "annot") || files.find((f) => IO.extOf(f.name) === "json") || files[0];
  let data;
  try {
    data = JSON.parse(await annotFile.text());
    if (!data || typeof data !== "object" || !data.pages) throw new Error("編集ファイルの形式ではありません");
  } catch (e) { await alertBox("読み込めませんでした。\n" + e.message); return; }
  const infos = annotSources(data);
  const pool = files.filter((f) => f !== annotFile && isDrawingFile(f));
  const picked = await resolveSources(infos, pool);
  if (!picked) return;
  status("読み込み中…");
  let srcs;
  try { srcs = await readSources(picked); } catch (e) { await alertBox("図面を開けませんでした。\n" + (e && e.message ? e.message : e)); return; }
  const order = C.validPageOrder(data.page_order, srcs.map((s) => s.pageCount));
  useSources(srcs, order ? order.map(([s, p]) => ({ s, p })) : null);
  applyAnnot(data, annotFile.name);
  await goPage(parseInt(data.current_page, 10) || 0);
  setDirty(false);
  saveBlobBackup();
  const renamed = picked.map((f, i) => [wantName(infos[i]), f.name]).filter(([w, n]) => w && w !== n);
  if (renamed.length) status(`※編集ファイルの元の図面は「${renamed[0][0]}」ですが、「${renamed[0][1]}」を使っています`);
}

/** 編集ファイルが使う図面の一覧(version 2 は1つだけ、version 3 は sources) */
const annotSources = (data) => (Array.isArray(data.sources) && data.sources.length ? data.sources : [data.source || {}]);
const wantName = (si) => IO.baseName((si && (si.rel || si.path)) || "");

/** 編集ファイルが使う図面を、選ばれたファイル(pool)から名前で探す。足りなければ選んでもらう。取り消されたら null */
async function resolveSources(infos, pool) {
  const wants = infos.map(wantName);
  const resolved = infos.map(() => null);
  const assign = () => {
    wants.forEach((w, i) => {
      if (resolved[i]) return;
      const j = pool.findIndex((f) => f.name === w);
      if (j >= 0) resolved[i] = pool.splice(j, 1)[0];
    });
    const un = resolved.map((r, i) => (r ? -1 : i)).filter((i) => i >= 0);
    if (un.length && pool.length === un.length) un.forEach((i) => { resolved[i] = pool.shift(); }); // 名前が違っても、数がぴったりなら順に使う
  };
  assign();
  while (resolved.some((r) => !r)) {
    const missing = wants.filter((w, i) => !resolved[i]);
    const got = await askDrawings(missing);
    if (!got) return null;
    pool.push(...got.filter(isDrawingFile));
    assign();
  }
  return resolved;
}

/** 編集ファイルと一緒に選ばれなかった図面を、選んでもらう(ボタンを押した操作の中でファイル選択を開く) */
function askDrawings(wants) {
  const list = wants.filter(Boolean).map((w) => "・" + w).join("\n");
  return new Promise((resolve) => {
    modal({
      title: "元の図面を選んでください",
      message: list ? `この編集ファイルの図面は次のとおりです。\n${list}\n同じ図面ファイルを選んでください(複数選べます)。` : "この編集ファイルに合う図面(PDF・画像)を選んでください。",
      buttons: [
        { label: "図面を選ぶ", primary: true, keepOpen: true, onClick: async () => {
          const f = await pickFile($("#fileOpen"));
          if (f.length) { $("#modalBack").hidden = true; resolve(f); }
        } },
        { label: "キャンセル", value: null },
      ],
    }).then((v) => { if (v === null) resolve(null); });
  });
}

function applyAnnot(data, annotName) {
  const { pages, common } = C.migrateLoaded(data);
  S.pages = pages; S.common = common;
  setNext(parseInt(data.next_num, 10) || 1);
  const pp = data.paper || {};
  if (pp.size === C.NO_PAPER || Object.hasOwn(C.PAPERS, pp.size)) S.paper.size = pp.size;
  if (C.ORIENTS.includes(pp.orient)) S.paper.orient = pp.orient;
  if (C.MARGINS.includes(String(pp.margin))) S.paper.margin = String(pp.margin);
  if (C.DPIS.includes(String(pp.dpi))) S.paper.dpi = String(pp.dpi);
  updatePaperButton();
  S.annotName = annotName;
  S.undo = []; S.redo = [];
  loadCommonToUI();
}

// ------------------------------------------------ 一時保存(自動バックアップ) ---
let autosaveTimer = null;
function scheduleAutosave() {
  clearTimeout(autosaveTimer);
  autosaveTimer = setTimeout(async () => {
    if (!S.base) return;
    try {
      await IO.idbSet("meta", { annot: annotData(), annotName: S.annotName, savedAt: Date.now() });
      S.savedBackup = true;
    } catch (e) { /* 保存できない環境(プライベートモードなど)では何もしない */ }
  }, 1500);
}
async function saveBlobBackup() {
  try { await IO.idbSet("blobs", S.sources.map((s) => ({ name: s.name, blob: s.blob }))); } catch (e) { /* 同上 */ }
  scheduleAutosave();
}
async function checkBackup() {
  try {
    const meta = await IO.idbGet("meta");
    let blobs = await IO.idbGet("blobs");
    if (!blobs) { // 図面が1つだった頃の保存形式
      const one = await IO.idbGet("blob");
      if (one && meta && meta.srcName) blobs = [{ name: meta.srcName, blob: one }];
    }
    if (meta && blobs && blobs.length) {
      $("#btnRestore2").hidden = false;
      return { meta, blobs };
    }
  } catch (e) { /* なし */ }
  return null;
}
async function restoreBackup() {
  const b = await checkBackup();
  if (!b) { await alertBox("復元できる作業が見つかりません。"); return; }
  if (!(await confirmDiscard())) return;
  const data = b.meta.annot;
  const pool = b.blobs.map((x) => new File([x.blob], x.name, { type: x.blob.type }));
  const picked = annotSources(data).map((si) => {
    const j = pool.findIndex((f) => f.name === wantName(si));
    return j >= 0 ? pool.splice(j, 1)[0] : null;
  });
  if (picked.some((f) => !f)) { await alertBox("一時保存した図面が見つかりません。"); return; }
  status("読み込み中…");
  let srcs;
  try { srcs = await readSources(picked); } catch (e) { await alertBox("図面を開けませんでした。\n" + e.message); return; }
  const order = C.validPageOrder(data.page_order, srcs.map((s) => s.pageCount));
  useSources(srcs, order ? order.map(([s, p]) => ({ s, p })) : null);
  applyAnnot(data, b.meta.annotName);
  await goPage(parseInt(data.current_page, 10) || 0);
  setDirty(true);
  status("前回の作業を復元しました(まだファイルには保存していません。「保存」で書き出してください)");
}

// ----------------------------------------------------------- 書き出し ---
async function renderExport(pageIdx) {
  const base = pageIdx === S.curPage ? S.base : await getBase(pageIdx);
  const plan = C.planExport(base.W, base.H, S.paper);
  const c = document.createElement("canvas");
  c.width = plan.outW; c.height = plan.outH;
  const g = c.getContext("2d");
  g.imageSmoothingQuality = "high";
  g.fillStyle = "#FFFFFF";
  g.fillRect(0, 0, c.width, c.height);
  if (plan.kind === "paper") {
    g.drawImage(base.raster, plan.ox, plan.oy, Math.max(1, Math.round(base.W * plan.scale)), Math.max(1, Math.round(base.H * plan.scale)));
  } else {
    g.drawImage(base.raster, 0, 0, plan.outW, plan.outH);
  }
  for (const a of S.pages[pageIdx] || []) {
    if (C.isPlaced(a)) drawAnnotation(g, C.scaleAnn(a, plan.scale, plan.ox, plan.oy));
  }
  const blob = await new Promise((res) => c.toBlob(res, "image/png"));
  if (!blob) throw new Error("画像を作れませんでした(大きすぎる可能性があります。dpiを下げてください)");
  const bytes = C.pngWithDpi(new Uint8Array(await blob.arrayBuffer()), plan.dpi);
  return { bytes, plan, w: plan.outW, h: plan.outH };
}

function exportNote(plan) {
  if (!plan.reduced) return "";
  if (plan.kind === "paper") return `  ※大きすぎるため、解像度を ${plan.requestedDpi}dpi から ${plan.dpi}dpi に下げました`;
  return "  ※大きすぎるため、書き出しの大きさを縮小しました";
}

async function exportPng(share) {
  if (!S.base) return;
  status("書き出し中…");
  try {
    const r = await renderExport(S.curPage);
    let stem = IO.stemOf(firstName());
    if (pageCount() > 1) stem += `_p${S.curPage + 1}`;
    const res = await IO.deliver(new Blob([r.bytes], { type: "image/png" }), `${stem}_注記.png`, { share });
    status(res === "cancel" ? "書き出しをやめました" : `書き出しました: ${stem}_注記.png (${r.w}×${r.h} px)${exportNote(r.plan)}`);
  } catch (e) { await alertBox("書き出せませんでした。\n" + e.message); }
}

async function exportAll() {
  if (!S.base) return;
  try {
    const files = [];
    let note = "";
    const stem = IO.stemOf(firstName());
    const total = pageCount();
    for (let p = 0; p < total; p++) {
      status(`書き出し中… ${p + 1} / ${total}`);
      const r = await renderExport(p);
      files.push({ name: `${stem}_p${p + 1}_注記.png`, bytes: r.bytes });
      note = exportNote(r.plan) || note;
    }
    const res = await IO.deliver(IO.buildZip(files), `${stem}_注記.zip`, { share: IO.isTouchDevice() });
    status(res === "cancel" ? "書き出しをやめました" : `${total}ページをZIPで書き出しました${note}`);
  } catch (e) { await alertBox("書き出せませんでした。\n" + e.message); }
}

// -------------------------------------------------------------- 用紙 ---
function paperLabel() {
  if (S.paper.size === C.NO_PAPER) return "用紙: 図面サイズ ▾";
  return `用紙: ${S.paper.size} ▾`;
}
function updatePaperButton() { $("#btnPaper").textContent = paperLabel(); }

function onPaperChange() {
  updatePaperButton();
  if (!S.base) return;
  setDirty(true);
  fit();
  const lay = C.paperLayout(S.base.W, S.base.H, S.paper);
  if (lay) status(`用紙 ${lay.name} ${lay.land ? "横" : "縦"}  ${lay.pw}×${lay.ph} px (${lay.dpi} dpi)  図面は ${Math.round(lay.s * 100)}% で配置`);
  else status("図面サイズのまま書き出します");
}

function showPaperPanel() {
  const m = $("#menu");
  m.innerHTML = "";
  const form = document.createElement("div");
  form.className = "form";
  const mk = (label, key, values) => {
    const l = document.createElement("span");
    l.textContent = label;
    const sel = document.createElement("select");
    for (const v of values) {
      const o = document.createElement("option");
      o.value = v; o.textContent = v;
      if (String(S.paper[key]) === String(v)) o.selected = true;
      sel.appendChild(o);
    }
    sel.onchange = () => { S.paper[key] = sel.value; onPaperChange(); };
    form.appendChild(l); form.appendChild(sel);
  };
  mk("用紙", "size", [C.NO_PAPER, ...Object.keys(C.PAPERS)]);
  mk("向き", "orient", C.ORIENTS);
  mk("余白(mm)", "margin", C.MARGINS);
  mk("dpi", "dpi", C.DPIS);
  const note = document.createElement("div");
  note.className = "note";
  note.textContent = "図面が大きすぎる場合は、書き出し時にdpiを自動で下げます。";
  form.appendChild(note);
  m.appendChild(form);
  const r = $("#btnPaper").getBoundingClientRect();
  placeMenu(Math.max(4, r.right - m.offsetWidth), r.bottom + 4);
  S.menuAt = performance.now();
}

// ------------------------------------------------------ 注記の操作 ---
function select(idx, arrow = 0) {
  S.sel = idx;
  S.selArrow = arrow;
  requestDraw();
  syncTableSel();
}

function placeAt(ix, iy, pt, { aim = true } = {}) {
  const cur = curAnn();
  if (cur && !C.isPlaced(cur)) { // 表で選んだ未配置の行を、この位置に配置する
    pushUndo();
    cur.x = ix; cur.y = iy;
    S.selArrow = 0;
    cur.arrow = true;
    cur.angle = C.DEFAULT_ANGLE;
    if (!String(cur.num).trim()) { // 番号が空のときだけ自動採番
      const n = nextNumberFromUI();
      cur.num = String(n);
      setNext(n + 1);
    }
    if (aim) S.drag = { mode: "new", idx: S.sel, sx: pt.x, sy: pt.y, x0: ix, y0: iy, pushed: true };
    requestDraw();
    refreshTable();
    return;
  }
  if (!S.placeMode && aim) { select(null); return; }
  pushUndo();
  const n = nextNumberFromUI();
  const a = C.makeAnn(S.common, ix, iy, String(n), C.DEFAULT_ANGLE, true);
  anns().push(a);
  S.sel = anns().length - 1;
  S.selArrow = 0;
  setNext(n + 1);
  if (aim) S.drag = { mode: "new", idx: S.sel, sx: pt.x, sy: pt.y, x0: ix, y0: iy, pushed: true };
  requestDraw();
  refreshTable();
}

function dragArrow(d) {
  const l = anns();
  if (d.idx >= l.length) return null;
  const a = l[d.idx];
  const k = d.arrow || 0;
  if (k === 0) return a;
  return k - 1 < a.extra.length ? a.extra[k - 1] : null;
}

function deleteSelected() {
  const a = curAnn();
  if (!a) return;
  if (S.selArrow >= 1 && S.selArrow <= a.extra.length) { // 2本目以降の矢印を選んでいるときは、その矢印だけ消す
    pushUndo();
    a.extra.splice(S.selArrow - 1, 1);
    S.selArrow = 0;
    refreshTable(); requestDraw();
    status("矢印を削除しました");
    return;
  }
  pushUndo();
  anns().splice(S.sel, 1);
  S.sel = null; S.selArrow = 0;
  refreshTable(); requestDraw();
}

function addArrow() {
  const a = curAnn();
  if (!a || !C.isPlaced(a)) { alertBox("配置済みの注記を選んでから実行してください。"); return; }
  const angles = (a.arrow ? [a.angle] : []).concat(a.extra.map((e) => e.angle));
  pushUndo();
  a.extra.push(C.makeArrow(S.common, C.freeAngle(angles)));
  S.selArrow = a.extra.length;
  refreshTable(); requestDraw();
  status(`矢印を追加しました(矢印${S.selArrow + 1})。先端の●をドラッグして向きを変えられます`);
}

function insertRowBelow() {
  if (!curAnn()) { alertBox("表で行を選んでから実行してください。"); return; }
  if (S.sortMode) { S.sortMode = 0; } // 並べ替え中は、表示順と挿入位置がずれるため、元の順に戻してから挿入する
  pushUndo();
  const i = S.sel + 1;
  anns().splice(i, 0, C.makeAnn(S.common));
  S.sel = i; S.selArrow = 0;
  refreshTable(); requestDraw();
  status("行を挿入しました。番号をタップして入力し、図面をダブルタップすると配置されます(空なら自動採番)");
}

async function renumber() {
  if (!S.base) { await alertBox("先に図面を開いてください。"); return; }
  const mode = await modal({
    title: "番号の振り直し",
    message: "番号の振り直し方を選んでください(「元に戻す」で戻せます)。\n\n・欠番を詰める: 番号の順序・重複・複数番号はそのままで、抜けた番号だけ詰めます\n・表の並び順に連番: いまの表の並び順に、配置済みの注記へ1から付け直します(重複はなくなります)",
    buttons: [{ label: "欠番を詰める", value: "compact", primary: true }, { label: "表の並び順に1から連番", value: "seq" }, { label: "キャンセル", value: null }],
  });
  if (!mode) return;
  const order = C.sortRows(allRows(), S.sortMode).map((r) => [r.pg, r.idx]);
  const changes = C.computeRenumber(S.pages, mode, order);
  if (!changes.length) { status("振り直しの必要がありませんでした(変更なし)"); return; }
  pushUndo();
  for (const [pg, i, text] of changes) S.pages[pg][i].num = text;
  let top = 0;
  for (const lst of Object.values(S.pages)) for (const a of lst) {
    for (const k of C.numKeys(a.num)) if (/^[0-9]+$/.test(k)) top = Math.max(top, parseInt(k, 10));
  }
  setNext(top + 1);
  refreshTable(); requestDraw();
  status(`番号を振り直しました(${changes.length}件)。次の番号は ${S.nextNum} です`);
}

// ----------------------------------------------- 図面上のポインター操作 ---
function localPt(e) {
  const r = cv.getBoundingClientRect();
  return { x: e.clientX - r.left, y: e.clientY - r.top };
}
const toImg = (pt) => [(pt.x - S.view.tx) / S.view.z, (pt.y - S.view.ty) / S.view.z];
const hit = (ix, iy, skip = false) => C.hitTest(anns(), ix, iy, S.view.z, S.sel, S.selArrow, skip, S.touch);

function clearLongPress() {
  if (S.lp) { clearTimeout(S.lp.timer); S.lp = null; }
}

function startHitDrag(h, ix, iy, pt) {
  let k = h.k;
  if (k === null) k = h.idx === S.sel ? S.selArrow : 0;
  S.sel = h.idx; S.selArrow = k;
  const a = anns()[h.idx];
  const mode = TIP_KEY[h.part] || "move";
  S.drag = { mode, idx: h.idx, arrow: k, sx: pt.x, sy: pt.y, x0: ix, y0: iy, dx: a.x - ix, dy: a.y - iy, pushed: false, active: false };
  S.ghost = null;
  requestDraw();
  syncTableSel();
}

function openCtxMenu(pt, clientX, clientY) {
  if (!S.base) return;
  const [ix, iy] = toImg(pt);
  const h = hit(ix, iy);
  if (h) {
    const k = h.k === null ? (h.idx === S.sel ? S.selArrow : 0) : h.k;
    select(h.idx, k);
    showMenu([
      { label: "矢印を追加", action: addArrow },
      { label: k >= 1 ? "この矢印を削除" : "この注記を削除", action: deleteSelected },
    ], clientX + 6, clientY + 6);
  } else {
    showMenu([{ label: "ここに新規配置", action: () => placeAt(ix, iy, pt, { aim: false }) }], clientX + 6, clientY + 6);
  }
}

function onDown(e) {
  hideMenu();
  cv.setPointerCapture(e.pointerId);
  const pt = localPt(e);
  S.pointers.set(e.pointerId, { x: pt.x, y: pt.y, type: e.pointerType });
  S.touch = e.pointerType !== "mouse";
  if (S.pointers.size === 2) { // 2本指: 拡大縮小と移動
    clearLongPress();
    S.drag = null;
    const [p1, p2] = [...S.pointers.values()];
    S.gesture = { d0: Math.hypot(p1.x - p2.x, p1.y - p2.y) || 1, cx0: (p1.x + p2.x) / 2, cy0: (p1.y + p2.y) / 2, z0: S.view.z, tx0: S.view.tx, ty0: S.view.ty };
    return;
  }
  if (S.pointers.size > 2) return;
  e.preventDefault();
  if (e.pointerType === "mouse" && (e.button === 1 || e.button === 2)) { // 右・中ボタン: 画面の移動(動かさずに離すと、右はメニュー)
    S.drag = { mode: "pan", sx: pt.x, sy: pt.y, tx0: S.view.tx, ty0: S.view.ty, moved: false, right: e.button === 2, cx: e.clientX, cy: e.clientY };
    return;
  }
  if (e.pointerType === "mouse" && e.button !== 0) return;
  if (!S.base) return;
  const [ix, iy] = toImg(pt);
  S.lpFired = false;
  if (S.touch) S.lp = { x: pt.x, y: pt.y, cx: e.clientX, cy: e.clientY, timer: setTimeout(() => onLongPress(pt, e.clientX, e.clientY), LONG_PRESS_MS) };
  const h = hit(ix, iy, !!e.altKey);
  if (h) { startHitDrag(h, ix, iy, pt); return; }
  // 何もない所: 2回続けてタップ(クリック)すると新規配置
  const now = performance.now();
  const lt = S.lastTap;
  if (lt && now - lt.t < DOUBLE_TAP_MS && Math.hypot(pt.x - lt.x, pt.y - lt.y) < 34) {
    S.lastTap = null;
    clearLongPress();
    placeAt(ix, iy, pt);
    return;
  }
  const cur = curAnn();
  if (!(cur && !C.isPlaced(cur)) && S.sel !== null) select(null); // 表で選んだ未配置の行は、ダブルタップで配置するので選択を残す
  S.drag = { mode: "pan", sx: pt.x, sy: pt.y, tx0: S.view.tx, ty0: S.view.ty, moved: false, tap: true };
}

function onLongPress(pt, cx, cy) {
  S.lp = null;
  if (S.gesture) return;
  const d = S.drag;
  if (d && (d.mode === "new" || d.active || d.pushed)) return;
  S.lpFired = true;
  S.drag = null;
  S.lastTap = null;
  openCtxMenu(pt, cx, cy);
}

function onMove(e) {
  const pt = localPt(e);
  if (S.pointers.has(e.pointerId)) S.pointers.set(e.pointerId, { x: pt.x, y: pt.y, type: e.pointerType });
  if (S.gesture && S.pointers.size >= 2) {
    const [p1, p2] = [...S.pointers.values()];
    const g = S.gesture;
    const d = Math.hypot(p1.x - p2.x, p1.y - p2.y) || 1;
    const z = Math.max(0.03, Math.min(16, g.z0 * (d / g.d0)));
    const cx = (p1.x + p2.x) / 2, cy = (p1.y + p2.y) / 2;
    const ix = (g.cx0 - g.tx0) / g.z0, iy = (g.cy0 - g.ty0) / g.z0;
    S.view.z = z;
    S.view.tx = cx - ix * z;
    S.view.ty = cy - iy * z;
    requestDraw();
    return;
  }
  const d = S.drag;
  if (!d) { if (e.pointerType === "mouse") updateGhost(pt); return; }
  if (S.lp && Math.hypot(pt.x - S.lp.x, pt.y - S.lp.y) > 8) clearLongPress();
  if (!S.base) return;
  if (d.mode === "pan") {
    if (Math.hypot(pt.x - d.sx, pt.y - d.sy) > 4) d.moved = true;
    if (d.moved) { S.view.tx = d.tx0 + (pt.x - d.sx); S.view.ty = d.ty0 + (pt.y - d.sy); requestDraw(); }
    return;
  }
  const lst = anns();
  if (d.idx >= lst.length) return;
  const a = lst[d.idx];
  const [ix, iy] = toImg(pt);
  const screenDist = Math.hypot(pt.x - d.sx, pt.y - d.sy);
  const slop = S.touch ? 4 : 0;
  if (d.mode === "move") {
    if (!d.active && screenDist <= slop) return;
    d.active = true;
    if (!d.pushed) { pushUndo(); d.pushed = true; }
    a.x = ix + d.dx; a.y = iy + d.dy;
  } else if (d.mode === "tip" || d.mode === "shaft") {
    const arrow = dragArrow(d);
    if (!arrow) return;
    if (d.mode === "shaft" && screenDist < 4 && !d.active) return; // 軸をつかんだときは、少し動かしてから向きを変える
    if (d.mode === "tip" && !d.active && screenDist <= slop) return;
    d.active = true;
    if (!d.pushed) { pushUndo(); d.pushed = true; }
    // 先端の●をつかんだときは、●が指(マウス)に付いてくる(長さも変わる)。軸をつかんだとき・Shiftを押しているときは、長さを保つ
    C.aim(a, arrow, ix, iy, d.mode === "shaft" || e.shiftKey);
  } else if (d.mode === "new") {
    if (screenDist >= 8) C.aim(a, a, ix, iy, a.common);
  }
  requestDraw();
}

function onUp(e) {
  const pt = localPt(e);
  S.pointers.delete(e.pointerId);
  clearLongPress();
  if (S.gesture) { // 2本指の操作が終わったとき、残った指が勝手に動かさないようにする
    if (S.pointers.size < 2) S.gesture = null;
    S.drag = null;
    return;
  }
  const d = S.drag;
  S.drag = null;
  if (!d) return;
  if (d.mode === "pan") {
    if (d.right && !d.moved) { openCtxMenu(pt, e.clientX, e.clientY); return; }
    if (d.tap && !d.moved && !S.lpFired) S.lastTap = { t: performance.now(), x: pt.x, y: pt.y };
    return;
  }
  if (d.mode === "tip" && d.pushed) {
    const arrow = dragArrow(d);
    if (arrow && arrow.common) { arrow.length = S.common.length; requestDraw(); } // 共通ONの矢印は、離すと長さが共通値に戻る(向きは残る)
  }
  refreshTable();
  if (d.pushed) scheduleAutosave();
}

function onCancel(e) {
  S.pointers.delete(e.pointerId);
  clearLongPress();
  if (S.pointers.size < 2) S.gesture = null;
  S.drag = null;
}

function updateGhost(pt) {
  if (!S.base || !S.placeMode) { if (S.ghost) { S.ghost = null; requestDraw(); } return; }
  const [ix, iy] = toImg(pt);
  if (hit(ix, iy)) { if (S.ghost) { S.ghost = null; requestDraw(); } return; }
  const cur = curAnn();
  let g;
  if (cur && !C.isPlaced(cur)) {
    g = C.makeAnn(S.common, ix, iy, String(cur.num).trim() || String(nextNumberFromUI()), C.DEFAULT_ANGLE, true);
    for (const k of C.COMMON_KEYS) g[k] = cur[k];
  } else {
    g = C.makeAnn(S.common, ix, iy, String(nextNumberFromUI()), C.DEFAULT_ANGLE, true);
  }
  S.ghost = g;
  status(`x=${Math.round(ix)}  y=${Math.round(iy)}   倍率 ${Math.round(S.view.z * 100)}%`);
  requestDraw();
}

cv.addEventListener("pointerdown", onDown);
cv.addEventListener("pointermove", onMove);
cv.addEventListener("pointerup", onUp);
cv.addEventListener("pointercancel", onCancel);
cv.addEventListener("pointerleave", () => { if (S.ghost) { S.ghost = null; requestDraw(); } });
cv.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  if (e.pointerType === "mouse" || performance.now() - S.menuAt < 800) return; // マウスの右クリックは、ボタンを離したときに出す
  openCtxMenu(localPt(e), e.clientX, e.clientY);
});
cv.addEventListener("wheel", (e) => {
  e.preventDefault();
  if (!S.base) return;
  const pt = localPt(e);
  if (e.ctrlKey || e.metaKey) {
    const step = Math.sign(e.deltaY) * Math.min(1, Math.abs(e.deltaY) / 50);
    zoomAt(pt, S.view.z * Math.pow(1.15, -step));
  } else if (e.shiftKey && !e.deltaX) {
    S.view.tx -= e.deltaY; requestDraw();
  } else {
    S.view.tx -= e.deltaX; S.view.ty -= e.deltaY; requestDraw();
  }
}, { passive: false });
document.addEventListener("gesturestart", (e) => e.preventDefault());

// -------------------------------------------------------- 注記一覧(表) ---
function allRows() {
  const rows = [];
  for (const pg of Object.keys(S.pages).map(Number).sort((a, b) => a - b)) S.pages[pg].forEach((a, i) => rows.push({ pg, idx: i, a }));
  return rows;
}

const FIX1 = C.TREE_COLS[0][3];

function refreshTable() {
  cancelEdit();
  const info = C.analyzeNumbers(S.pages);
  $("#summary").textContent = C.summaryText(info);
  const cols = C.TREE_COLS;
  const total = cols.reduce((s, c) => s + c[3], 0);
  let html = `<colgroup>${cols.map((c) => `<col style="width:${c[3]}px">`).join("")}</colgroup><thead><tr>`;
  cols.forEach((c, i) => {
    const cls = i === 0 ? "fix1" : i === 1 ? "fix2" : "";
    if (c[0] === "num") html += `<th class="${cls}"><button class="sortbtn" id="sortBtn">番号<br>${SORT_MARK[S.sortMode]}</button></th>`;
    else html += `<th class="${cls}">${c[1]}${c[2] ? "<br>" + c[2] : ""}</th>`;
  });
  html += "</tr></thead><tbody>";
  for (const r of C.sortRows(allRows(), S.sortMode)) {
    const a = r.a;
    let status_ = "", cls = "";
    if (!C.isPlaced(a)) { status_ = "未配置"; cls = "unplaced"; }
    else if (info.dup_rows.has(`${r.pg}:${r.idx}`)) { status_ = "重複"; cls = "dup"; }
    html += rowHtml(`${r.pg}:${r.idx}`, C.tableRowValues(r.pg, a, status_), cls);
    (a.extra || []).forEach((e, k) => { html += rowHtml(`${r.pg}:${r.idx}:${k + 1}`, C.tableChildValues(r.pg, e, k + 1), "child"); });
  }
  html += "</tbody>";
  const tbl = $("#tbl");
  tbl.style.width = total + "px";
  tbl.innerHTML = html;
  $("#sortBtn").onclick = () => { S.sortMode = (S.sortMode + 1) % 3; refreshTable(); };
  syncTableSel();
  updateTitle();
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function rowHtml(iid, vals, cls) {
  let h = `<tr data-iid="${iid}" class="${cls}">`;
  C.TREE_COLS.forEach((c, i) => {
    const fix = i === 0 ? "fix1" : i === 1 ? "fix2" : "";
    h += `<td data-c="${c[0]}" class="${fix}">${esc(vals[i])}</td>`;
  });
  return h + "</tr>";
}

function iidOfSel() {
  if (S.sel == null) return null;
  let iid = `${S.curPage}:${S.sel}`;
  if (S.selArrow) iid += `:${S.selArrow}`;
  if (!$(`#tbl tr[data-iid="${iid}"]`)) iid = `${S.curPage}:${S.sel}`;
  return iid;
}
function syncTableSel() {
  document.querySelectorAll("#tbl tr.selrow").forEach((r) => r.classList.remove("selrow"));
  const iid = iidOfSel();
  if (!iid) return;
  const tr = $(`#tbl tr[data-iid="${iid}"]`);
  if (!tr) return;
  tr.classList.add("selrow");
  const wrap = $("#tableWrap");
  const top = tr.offsetTop, bottom = top + tr.offsetHeight;
  if (top < wrap.scrollTop + 40) wrap.scrollTop = Math.max(0, top - 40);
  else if (bottom > wrap.scrollTop + wrap.clientHeight) wrap.scrollTop = bottom - wrap.clientHeight;
}

const parseIid = (iid) => { const p = iid.split(":").map(Number); return { pg: p[0], idx: p[1], k: p[2] || 0 }; };

async function selectRow(pg, idx, k = 0) {
  if (!S.base) return;
  if (pg !== S.curPage) await goPage(pg);
  S.sel = idx; S.selArrow = k;
  requestDraw();
  syncTableSel();
  const a = curAnn();
  if (a && !C.isPlaced(a)) status("未配置の行です。番号を入力して(空なら自動採番)、図面をダブルタップすると配置されます");
}

function cancelEdit() {
  S.editing = null;
}

function onTableClick(e) {
  if (S.rowLPFired) { S.rowLPFired = false; return; }
  const td = e.target.closest("td[data-c]");
  if (!td) return;
  const tr = td.parentElement;
  const iid = tr.dataset.iid, cid = td.dataset.c;
  const { pg, idx, k } = parseIid(iid);
  const wasSel = tr.classList.contains("selrow");
  (async () => {
    await selectRow(pg, idx, k);
    const a = curAnn();
    if (!a) return;
    if (k >= 1) {
      if (cid === "com" && k <= a.extra.length) toggleArrowCommon(a.extra[k - 1]);
      else if (wasSel) tableEdit(iid, cid, td);
      return;
    }
    if (cid === "com") toggleCommon(a);
    else if (cid === "arw" && C.isPlaced(a)) {
      pushUndo(); a.arrow = !a.arrow; refreshTable(); requestDraw();
    } else if (wasSel) tableEdit(iid, cid, td);
  })();
}

function onTableDbl(e) {
  const td = e.target.closest("td[data-c]");
  if (!td) return;
  const tr = td.parentElement;
  tableEdit(tr.dataset.iid, td.dataset.c, td);
}

function toggleCommon(a) {
  pushUndo();
  a.common = !a.common;
  if (a.common) for (const k of C.COMMON_KEYS) a[k] = S.common[k]; // 共通に戻すと、共通値に揃う
  refreshTable(); requestDraw();
  status("共通 " + (a.common ? "ON: 共通ステータスの値に揃えました" : "OFF: 一律更新の対象から外れ、個別に編集できます"));
}
function toggleArrowCommon(e) {
  pushUndo();
  e.common = !e.common;
  if (e.common) for (const k of C.ARROW_KEYS) e[k] = S.common[k];
  refreshTable(); requestDraw();
  status("矢印の共通 " + (e.common ? "ON: 共通ステータスの長さ・太さ・色に揃えました" : "OFF: 一律更新の対象から外れ、個別に編集できます"));
}

const NEED_OFF = "共通がONのため編集できません。「共通」列をタップしてOFFにしてください";

function tableEdit(iid, cid, td) {
  const { pg, idx, k } = parseIid(iid);
  const a = (S.pages[pg] || [])[idx];
  if (!a) return;
  const r = td.getBoundingClientRect();
  if (k >= 1) { // 子行(2本目以降の矢印): 長さ・太さ・色は共通OFFのときだけ、角度はいつでも編集できる
    const e = a.extra[k - 1];
    if (!e) return;
    if (["ln", "wd", "ca"].includes(cid) && e.common) status(NEED_OFF);
    else if (cid === "ca") showPalette(r.left, r.bottom + 2, e.c_arrow, (hx) => setArrowColor(e, hx));
    else if (["ln", "wd", "ang"].includes(cid)) startEdit(td, pg, idx, cid, k);
    return;
  }
  if (["com", "arw", "pg", "st"].includes(cid)) return;
  if (cid in C.COMMON_CELLS && a.common) { status(NEED_OFF); return; }
  if (cid === "ang" && !C.isPlaced(a)) return;
  if (["ct", "cc", "ca"].includes(cid)) {
    const key = C.COMMON_CELLS[cid];
    showPalette(r.left, r.bottom + 2, a[key], (hx) => setColorValue(a, key, hx));
    return;
  }
  startEdit(td, pg, idx, cid, 0);
}

function startEdit(td, pg, idx, cid, k) {
  const key = `${pg}:${idx}:${k}:${cid}`;
  if (S.editing === key) return;
  S.editing = key;
  const cur = td.textContent;
  td.textContent = "";
  const inp = document.createElement("input");
  inp.type = "text";
  inp.className = "cell-edit";
  inp.value = cur;
  inp.inputMode = cid === "num" ? "text" : "decimal";
  td.appendChild(inp);
  inp.focus();
  inp.select();
  let done = false;
  const commit = () => {
    if (done) return;
    done = true;
    S.editing = null;
    setCell(pg, idx, cid, inp.value, k);
    refreshTable();
  };
  inp.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); commit(); }
    else if (ev.key === "Escape") { done = true; S.editing = null; refreshTable(); }
  });
  inp.addEventListener("blur", commit);
}

function setCell(pg, idx, cid, text, k = 0) {
  const a = (S.pages[pg] || [])[idx];
  if (!a) return;
  text = text.trim();
  const num = (s) => { const v = Number(s); return s !== "" && Number.isFinite(v) ? v : NaN; };
  if (k >= 1) {
    const e = a.extra[k - 1];
    if (!e) return;
    let key, v;
    if (cid === "ang") { key = "angle"; v = ((num(text) % 360) + 360) % 360; }
    else if ((cid === "ln" || cid === "wd") && !e.common) {
      key = C.COMMON_CELLS[cid];
      const [lo, hi] = C.CLAMPS[key];
      v = Math.min(hi, Math.max(lo, num(text)));
    } else return;
    if (!Number.isFinite(v) || v === e[key]) return;
    pushUndo();
    e[key] = v;
    requestDraw();
    return;
  }
  if (cid === "num") {
    if (text === String(a.num)) return;
    pushUndo();
    a.num = text;
  } else if (cid === "ang") {
    const v = ((num(text) % 360) + 360) % 360;
    if (!Number.isFinite(v) || v === a.angle) return;
    pushUndo();
    a.angle = v;
  } else if (cid === "fs" || cid === "ln" || cid === "wd") {
    const key = C.COMMON_CELLS[cid];
    if (a.common) return;
    const [lo, hi] = C.CLAMPS[key];
    const v = Math.min(hi, Math.max(lo, num(text)));
    if (!Number.isFinite(v) || v === a[key]) return;
    pushUndo();
    a[key] = v;
  } else return;
  requestDraw();
}

function setArrowColor(e, hx) {
  if (e.common || e.c_arrow.toLowerCase() === hx.toLowerCase()) return;
  pushUndo(); e.c_arrow = hx; refreshTable(); requestDraw();
}
function setColorValue(a, key, hx) {
  if (a.common || a[key].toLowerCase() === hx.toLowerCase()) return;
  pushUndo(); a[key] = hx; refreshTable(); requestDraw();
}

function rowMenu(tr, x, y) {
  const { pg, idx, k } = parseIid(tr.dataset.iid);
  selectRow(pg, idx, k).then(() => {
    const a = curAnn();
    const items = [];
    if (a && C.isPlaced(a)) items.push({ label: "矢印を追加", action: addArrow });
    items.push({ label: "下に行を挿入", action: insertRowBelow });
    items.push({ label: k >= 1 ? "この矢印を削除" : "この行を削除", action: deleteSelected });
    showMenu(items, x + 6, y + 6);
  });
}

(() => {
  const tbl = $("#tbl");
  tbl.addEventListener("click", onTableClick);
  tbl.addEventListener("dblclick", onTableDbl);
  tbl.addEventListener("contextmenu", (e) => {
    const tr = e.target.closest("tr[data-iid]");
    e.preventDefault();
    if (!tr || performance.now() - S.menuAt < 800) return;
    rowMenu(tr, e.clientX, e.clientY);
  });
  tbl.addEventListener("pointerdown", (e) => {
    S.rowLPFired = false;
    const tr = e.target.closest("tr[data-iid]");
    if (!tr || e.pointerType === "mouse") return;
    if (S.rowLP) clearTimeout(S.rowLP.timer);
    S.rowLP = { x: e.clientX, y: e.clientY, timer: setTimeout(() => { S.rowLPFired = true; S.rowLP = null; rowMenu(tr, e.clientX, e.clientY); }, LONG_PRESS_MS) };
  });
  const stop = () => { if (S.rowLP) { clearTimeout(S.rowLP.timer); S.rowLP = null; } };
  tbl.addEventListener("pointermove", (e) => { if (S.rowLP && Math.hypot(e.clientX - S.rowLP.x, e.clientY - S.rowLP.y) > 8) stop(); });
  tbl.addEventListener("pointerup", stop);
  tbl.addEventListener("pointercancel", stop);
  $("#tableWrap").addEventListener("scroll", stop, { passive: true });
})();

// 表と図面の境界をドラッグして、表の幅(縦持ちのときは高さ)を変える
(() => {
  const sash = $("#sash"), panel = $("#panel");
  const mq = window.matchMedia("(orientation: portrait)");
  let st = null;
  sash.addEventListener("pointerdown", (e) => {
    sash.setPointerCapture(e.pointerId);
    st = { x: e.clientX, y: e.clientY, w: panel.offsetWidth, h: panel.offsetHeight, portrait: mq.matches };
  });
  sash.addEventListener("pointermove", (e) => {
    if (!st) return;
    if (st.portrait) {
      const h = Math.max(160, Math.min(window.innerHeight - 260, st.h - (e.clientY - st.y)));
      panel.style.flexBasis = h + "px"; panel.style.height = h + "px";
    } else {
      const w = Math.max(240, Math.min(window.innerWidth - 300, st.w - (e.clientX - st.x)));
      panel.style.flexBasis = w + "px"; panel.style.width = w + "px";
    }
    resize();
  });
  const end = () => { st = null; };
  sash.addEventListener("pointerup", end);
  sash.addEventListener("pointercancel", end);
  // 向きが変わったら、ドラッグで付けた寸法を捨てて向きごとの既定に戻す
  const reset = () => { panel.style.flexBasis = ""; panel.style.width = ""; panel.style.height = ""; };
  (mq.addEventListener ? mq.addEventListener("change", reset) : mq.addListener(reset));
})();

// ------------------------------------------------------ 表の表示・非表示 ---
function setTableVisible(v) {
  const before = stageSize();
  const f = fitParams();
  const wasFit = !!f && Math.abs(S.view.z - f.z) < 1e-6 && Math.abs(S.view.tx - f.tx) < 0.5 && Math.abs(S.view.ty - f.ty) < 0.5;
  S.showTable = v;
  $("#panel").hidden = !v;
  $("#sash").hidden = !v;
  const b = $("#btnTbl");
  b.classList.toggle("on", v);
  b.setAttribute("aria-checked", String(v));
  $("#btnTbl .swtxt").textContent = v ? "ON" : "OFF";
  cancelEdit();
  resize();
  if (S.base) {
    if (wasFit) fit(); // 全体表示のままなら、広がった分に合わせて全体を表示し直す
    else { // 拡大・移動中なら、いま見ている中心を保つ
      const now = stageSize();
      S.view.tx += (now.w - before.w) / 2;
      S.view.ty += (now.h - before.h) / 2;
      requestDraw();
    }
  }
  status(v ? "表を表示しました" : "表を隠しました(図面が広く表示されます。「表の表示」で戻せます)");
}

// ------------------------------------------------------ ページの入れ替え ---
/** work[i] = 入れ替え後の i 番目に来る、元のページ(0始まり)。図面・注記を新しい並びに直す */
async function applyPageOrder(work) {
  if (work.length !== pageCount() || work.every((o, i) => o === i)) return false;
  pushUndo();
  const pages = {};
  work.forEach((o, i) => { if (S.pages[o]) pages[i] = S.pages[o]; });
  const cur = S.curPage;
  S.order = work.map((o) => S.order[o]);
  S.pages = pages;
  await goPage(work.indexOf(cur));
  return true;
}

function reorderPages() {
  if (!S.base || pageCount() < 2) { alertBox("入れ替えるページが2ページ以上必要です。"); return Promise.resolve(); }
  const n = pageCount();
  const work = Array.from({ length: n }, (_, i) => i);
  const back = $("#modalBack"), box = $("#modal");
  box.innerHTML = "";
  box.classList.add("wide");
  const h = document.createElement("h3");
  h.textContent = "ページの入れ替え";
  const p = document.createElement("p");
  p.textContent = "▲▼ か、左の ≡ をドラッグして並べ替えます。注記は、ページといっしょに動きます。";
  const list = document.createElement("div");
  list.className = "rlist";
  const btns = document.createElement("div");
  btns.className = "btns row";
  box.append(h, p, list, btns);

  const rows = new Map(); // 元のページ → 行の要素
  let alive = true, dragOrig = null;
  const counts = (o) => (S.pages[o] || []).filter(C.isPlaced).length;
  const renderList = () => {
    work.forEach((o, i) => {
      const r = rows.get(o);
      r.querySelector(".rno").textContent = String(i + 1);
      r.querySelector(".rup").disabled = i === 0;
      r.querySelector(".rdn").disabled = i === n - 1;
      r.classList.toggle("moved", o !== i);
      list.appendChild(r);
    });
  };
  const move = (o, to) => {
    const from = work.indexOf(o);
    to = Math.max(0, Math.min(n - 1, to));
    if (from === to) return;
    work.splice(from, 1);
    work.splice(to, 0, o);
    renderList();
  };
  for (let o = 0; o < n; o++) {
    const e = S.order[o], src = S.sources[e.s];
    const r = document.createElement("div");
    r.className = "rrow";
    const nm = src.pageCount > 1 ? `${src.name} (${e.p + 1}/${src.pageCount})` : src.name;
    r.innerHTML = `<span class="rgrip" title="ドラッグで移動">≡</span><span class="rno"></span><span class="rth"></span>` +
      `<span class="rinfo"><span class="rname"></span><span class="rcnt">注記 ${counts(o)} 件</span></span>` +
      `<button class="btn sq rup" aria-label="上へ">▲</button><button class="btn sq rdn" aria-label="下へ">▼</button>`;
    r.querySelector(".rname").textContent = nm;
    r.querySelector(".rup").onclick = () => move(o, work.indexOf(o) - 1);
    r.querySelector(".rdn").onclick = () => move(o, work.indexOf(o) + 1);
    const grip = r.querySelector(".rgrip");
    grip.onpointerdown = (ev) => { ev.preventDefault(); grip.setPointerCapture(ev.pointerId); dragOrig = o; r.classList.add("drag"); };
    grip.onpointermove = (ev) => {
      if (dragOrig !== o) return;
      const els = [...list.children];
      let to = els.findIndex((el) => { const b = el.getBoundingClientRect(); return ev.clientY < b.top + b.height / 2; });
      if (to < 0) to = els.length;
      const from = work.indexOf(o);
      move(o, to > from ? to - 1 : to);
    };
    const end = () => { if (dragOrig === o) { dragOrig = null; r.classList.remove("drag"); } };
    grip.onpointerup = end; grip.onpointercancel = end;
    rows.set(o, r);
  }
  renderList();

  const close = () => { alive = false; back.hidden = true; box.classList.remove("wide"); };
  return new Promise((resolve) => {
    const mk = (label, primary, fn) => {
      const b = document.createElement("button");
      b.className = "btn" + (primary ? " primary" : "");
      b.textContent = label;
      b.onclick = fn;
      btns.appendChild(b);
    };
    mk("元の並びに戻す", false, () => { work.sort((a, b) => a - b); renderList(); });
    mk("キャンセル", false, () => { close(); resolve(false); });
    mk("この並びにする", true, async () => { close(); resolve(await applyPageOrder(work)); });
    back.hidden = false;
    // 小さな画像は、表示してから順に作る(PDFが多くても、画面はすぐ開く)
    (async () => {
      for (let o = 0; o < n && alive; o++) {
        try {
          const e = S.order[o], src = S.sources[e.s];
          const c = src.type === "image" ? IO.imageThumb(src.imageBase.raster) : await IO.renderPdfThumb(src.pdf, e.p);
          if (alive) rows.get(o).querySelector(".rth").replaceChildren(c);
        } catch (err) { /* 画像がなくても入れ替えはできる */ }
      }
    })();
  });
}

// ----------------------------------------------------------- ボタン類 ---
function editMenuItems() {
  return [
    { label: "ページの入れ替え…", action: reorderPages, disabled: !S.base || pageCount() < 2 },
  ];
}
function exportMenuItems() {
  const touch = IO.isTouchDevice();
  const items = [];
  if (touch) items.push({ label: "PNG書き出し(共有・写真に保存)", action: () => exportPng(true) });
  items.push({ label: touch ? "PNG書き出し(ファイルとして保存)" : "PNG書き出し(表示中のページ)", action: () => exportPng(false) });
  items.push({ label: "全ページをZIPで書き出し", action: exportAll });
  return items;
}

function setPlaceMode(v) {
  S.placeMode = v;
  const b = $("#btnPlace");
  b.textContent = "新規配置 " + (v ? "ON" : "OFF");
  b.classList.toggle("on", v);
  if (!v) { S.ghost = null; requestDraw(); }
}

function changePage(d) {
  if (S.base && S.curPage + d >= 0 && S.curPage + d < pageCount()) goPage(S.curPage + d);
}

function bind() {
  $("#btnOpen").onclick = () => openFiles();
  $("#btnAdd").onclick = () => addFiles();
  $("#btnEdit").onclick = (e) => showMenuBelow(e.currentTarget, editMenuItems());
  $("#btnTbl").onclick = () => setTableVisible(!S.showTable);
  $("#btnOpen2").onclick = () => openFiles();
  $("#btnRestore2").onclick = () => restoreBackup();
  $("#btnSave").onclick = () => saveAnnot();
  $("#btnExport").onclick = (e) => {
    if (!S.base) { alertBox("先に図面を開いてください。"); return; }
    showMenuBelow(e.currentTarget, exportMenuItems());
  };
  $("#btnUndo").onclick = undo;
  $("#btnRedo").onclick = redo;
  $("#btnDelete").onclick = deleteSelected;
  $("#btnPrev").onclick = () => changePage(-1);
  $("#btnNext").onclick = () => changePage(1);
  $("#btnZoomOut").onclick = () => zoomBy(0.8);
  $("#btnZoomIn").onclick = () => zoomBy(1.25);
  $("#btnFit").onclick = fit;
  $("#btnPlace").onclick = () => setPlaceMode(!S.placeMode);
  $("#btnPaper").onclick = showPaperPanel;
  $("#btnApply").onclick = applyCommon;
  $("#btnInsert").onclick = insertRowBelow;
  $("#btnDelRow").onclick = deleteSelected;
  $("#btnRenum").onclick = renumber;
  for (const id of ["#pFs", "#pLen", "#pWid"]) $(id).addEventListener("input", refreshPending);
  const pick = (btn, key) => { $(btn).onclick = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    showPalette(r.left, r.bottom + 4, S.pend[key], (hx) => { S.pend[key] = hx; updateColorButtons(); refreshPending(); });
  }; };
  pick("#cText", "c_text"); pick("#cCircle", "c_circle"); pick("#cArrow", "c_arrow");
  $("#vNext").addEventListener("change", () => { const v = parseInt($("#vNext").value, 10); if (Number.isFinite(v)) S.nextNum = v; });

  window.addEventListener("resize", () => { resize(); });
  window.addEventListener("orientationchange", () => setTimeout(() => { resize(); fit(); }, 200));
  new ResizeObserver(() => resize()).observe($("#stage"));

  document.addEventListener("keydown", (e) => {
    const t = e.target;
    const typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT");
    const mod = e.ctrlKey || e.metaKey;
    if (e.key === "Escape") { hideMenu(); if (!typing) select(null); return; }
    if (mod && !typing) {
      const k = e.key.toLowerCase();
      if (k === "z" && !e.shiftKey) { e.preventDefault(); undo(); }
      else if (k === "y" || (k === "z" && e.shiftKey)) { e.preventDefault(); redo(); }
      else if (k === "s") { e.preventDefault(); saveAnnot(); }
      else if (k === "e") { e.preventDefault(); exportPng(false); }
      else if (k === "o") { e.preventDefault(); e.shiftKey ? addFiles() : openFiles(); }
      return;
    }
    if (typing) return;
    if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); deleteSelected(); }
    else if (e.key === "PageUp") changePage(-1);
    else if (e.key === "PageDown") changePage(1);
  });
}

// ------------------------------------------------------------- 起動 ---
function init() {
  bind();
  setPlaceMode(true);
  loadCommonToUI();
  updatePaperButton();
  refreshTable();
  updateTitle();
  resize();
  checkBackup().then((b) => { if (b) status("前回の作業を復元できます(下の「前回の作業を復元」ボタン)"); });
  if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost" || location.hostname === "127.0.0.1")) {
    navigator.serviceWorker.register("./sw.js").catch(() => {});
  }
}
window.addEventListener("error", (e) => status("エラー: " + (e.message || e)));
window.addEventListener("unhandledrejection", (e) => status("エラー: " + ((e.reason && e.reason.message) || e.reason)));
init();

// テストやデバッグ用(通常の操作には使いません)
window.__npa = { S, C, IO, newDrawing, openAnnotFiles, openPicked, readSources, goPage, fit, draw, renderExport, annotData, placeAt, select, refreshTable, setTableVisible, applyPageOrder, pageCount };
