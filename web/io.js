// 番号配置アプリ(Web版) ファイルの入出力・保存(IndexedDB)・PDF/画像の読み込み
import { rasterScale, PDF_DPI, crc32 } from "./core.js";

export function baseName(p) {
  return String(p || "").split(/[\\/]/).pop();
}
export function extOf(name) {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i + 1).toLowerCase() : "";
}
export function stemOf(name) {
  const i = name.lastIndexOf(".");
  return i > 0 ? name.slice(0, i) : name;
}

// ---------------------------------------------------- 一時保存(IndexedDB) ---
const DB_NAME = "number-placement-app";
const STORE = "kv";
function openDb() {
  return new Promise((resolve, reject) => {
    const r = indexedDB.open(DB_NAME, 1);
    r.onupgradeneeded = () => r.result.createObjectStore(STORE);
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
}
export async function idbSet(key, value) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    tx.objectStore(STORE).put(value, key);
    tx.oncomplete = () => { db.close(); resolve(); };
    tx.onerror = () => { db.close(); reject(tx.error); };
    tx.onabort = () => { db.close(); reject(tx.error); };
  });
}
export async function idbGet(key) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readonly");
    const rq = tx.objectStore(STORE).get(key);
    rq.onsuccess = () => { db.close(); resolve(rq.result); };
    rq.onerror = () => { db.close(); reject(rq.error); };
  });
}

// ----------------------------------------------------------- 保存・共有 ---
export function isTouchDevice() {
  return navigator.maxTouchPoints > 0 && matchMedia("(pointer: coarse)").matches;
}

export function download(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

/** share=true なら、共有シート(iPad の「ファイルに保存」「写真に追加」など)を試し、使えなければダウンロードする */
export async function deliver(blob, filename, { share = false } = {}) {
  if (share && typeof navigator.canShare === "function") {
    const f = new File([blob], filename, { type: blob.type });
    if (navigator.canShare({ files: [f] })) {
      try {
        await navigator.share({ files: [f] });
        return "shared";
      } catch (err) {
        if (err && err.name === "AbortError") return "cancel";
      }
    }
  }
  download(blob, filename);
  return "download";
}

/** 圧縮なし(STORE)のZIPを作る。PNGはすでに圧縮されているので、これで十分 */
export function buildZip(files) {
  const enc = new TextEncoder();
  const chunks = [], central = [];
  let offset = 0;
  for (const f of files) {
    const name = enc.encode(f.name);
    const crc = crc32(f.bytes);
    const size = f.bytes.length;
    const lh = new Uint8Array(30 + name.length);
    const dv = new DataView(lh.buffer);
    dv.setUint32(0, 0x04034b50, true); dv.setUint16(4, 20, true); dv.setUint16(6, 0x0800, true);
    dv.setUint16(8, 0, true); dv.setUint16(10, 0, true); dv.setUint16(12, 0x21, true);
    dv.setUint32(14, crc, true); dv.setUint32(18, size, true); dv.setUint32(22, size, true);
    dv.setUint16(26, name.length, true); dv.setUint16(28, 0, true);
    lh.set(name, 30);
    chunks.push(lh, f.bytes);
    const ch = new Uint8Array(46 + name.length);
    const cv = new DataView(ch.buffer);
    cv.setUint32(0, 0x02014b50, true); cv.setUint16(4, 20, true); cv.setUint16(6, 20, true); cv.setUint16(8, 0x0800, true);
    cv.setUint16(10, 0, true); cv.setUint16(12, 0, true); cv.setUint16(14, 0x21, true);
    cv.setUint32(16, crc, true); cv.setUint32(20, size, true); cv.setUint32(24, size, true);
    cv.setUint16(28, name.length, true); cv.setUint32(42, offset, true);
    ch.set(name, 46);
    central.push(ch);
    offset += lh.length + size;
  }
  const cdSize = central.reduce((s, c) => s + c.length, 0);
  const end = new Uint8Array(22);
  const ev = new DataView(end.buffer);
  ev.setUint32(0, 0x06054b50, true); ev.setUint16(8, files.length, true); ev.setUint16(10, files.length, true);
  ev.setUint32(12, cdSize, true); ev.setUint32(16, offset, true);
  return new Blob([...chunks, ...central, end], { type: "application/zip" });
}

// --------------------------------------------------------------- PDF ---
let pdfjsPromise = null;
const LIB = new URL("./lib/pdfjs/", import.meta.url).href;

function loadPdfjs() {
  if (!pdfjsPromise) {
    pdfjsPromise = import(LIB + "pdf.min.mjs").then((m) => {
      m.GlobalWorkerOptions.workerSrc = LIB + "pdf.worker.min.mjs";
      return m;
    });
  }
  return pdfjsPromise;
}

export async function openPdf(file) {
  const pdfjs = await loadPdfjs();
  const bytes = new Uint8Array(await file.arrayBuffer());
  const task = pdfjs.getDocument({
    data: bytes,
    cMapUrl: LIB + "cmaps/", cMapPacked: true,
    standardFontDataUrl: LIB + "standard_fonts/",
    wasmUrl: LIB + "wasm/",
  });
  return task.promise;
}

function newCanvas(w, h) {
  const c = document.createElement("canvas");
  c.width = Math.max(1, Math.floor(w));
  c.height = Math.max(1, Math.floor(h));
  return c;
}

/** PDFの1ページを、Win版と同じ座標(150dpi相当のピクセル)の大きさ W×H として扱う。大きすぎるときは、画像だけ縮小して持つ(座標は変わらない) */
export async function renderPdfPage(doc, pageIdx) {
  const page = await doc.getPage(pageIdx + 1);
  const scale = PDF_DPI / 72;
  const vp1 = page.getViewport({ scale });
  const W = Math.max(1, Math.ceil(vp1.width - 0.001));
  const H = Math.max(1, Math.ceil(vp1.height - 0.001));
  const k = rasterScale(W, H);
  const vp = page.getViewport({ scale: scale * k });
  const raster = newCanvas(k < 1 ? vp.width : Math.ceil(vp.width), k < 1 ? vp.height : Math.ceil(vp.height));
  const ctx = raster.getContext("2d");
  ctx.fillStyle = "#FFFFFF";
  ctx.fillRect(0, 0, raster.width, raster.height);
  await page.render({ canvasContext: ctx, canvas: raster, viewport: vp }).promise;
  page.cleanup();
  return { W, H, raster, k };
}

// ------------------------------------------------------------- 画像 ---
async function decodeImage(file) {
  try { return await createImageBitmap(file, { imageOrientation: "none" }); } catch (e) { /* 次へ */ }
  try { return await createImageBitmap(file); } catch (e) { /* 次へ */ }
  const url = URL.createObjectURL(file);
  try {
    const img = new Image();
    img.src = url;
    await img.decode();
    return img;
  } finally {
    URL.revokeObjectURL(url);
  }
}

export async function openImage(file) {
  const src = await decodeImage(file);
  const W = src.naturalWidth || src.width, H = src.naturalHeight || src.height;
  if (!W || !H) throw new Error("画像の大きさを取得できませんでした");
  const k = rasterScale(W, H);
  const raster = newCanvas(W * k, H * k);
  const ctx = raster.getContext("2d");
  ctx.fillStyle = "#FFFFFF"; // 透過のある画像は白地に重ねる
  ctx.fillRect(0, 0, raster.width, raster.height);
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(src, 0, 0, raster.width, raster.height);
  if (src.close) src.close();
  return { W, H, raster, k };
}
