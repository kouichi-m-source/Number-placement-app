// 番号配置アプリ(Web版) 注記データ・幾何・番号解析のコア
// Win版(zumen_annot.pyw)と同じ計算式・同じ編集ファイル形式(.annot / version 2)で動かすための移植。
// DOM に依存しないので、Node でもテストできる。
export const APP_NAME = "図面注記";
export const PDF_DPI = 150;
export const DEFAULT_ANGLE = 45.0;

export const PALETTE = [
  ["黒", "#000000"], ["白", "#FFFFFF"], ["赤", "#FF0000"], ["青", "#0000FF"],
  ["緑", "#00A000"], ["黄", "#FFD400"], ["橙", "#FF8000"], ["紫", "#8000C0"],
  ["水色", "#00B4F0"], ["桃", "#FF50A0"], ["灰", "#808080"], ["茶", "#804000"],
];
export const COLOR_FIELDS = [["c_text", "数字"], ["c_circle", "〇の枠"], ["c_arrow", "矢印"]];
export const COMMON_KEYS = ["font_size", "length", "width", "c_text", "c_circle", "c_arrow"];
export const ARROW_KEYS = ["length", "width", "c_arrow"];
export const COMMON_CELLS = { fs: "font_size", ln: "length", wd: "width", ct: "c_text", cc: "c_circle", ca: "c_arrow" };
export const CLAMPS = { font_size: [4, 500], length: [0, 10000], width: [0.5, 200] };

export const NO_PAPER = "図面サイズのまま";
export const PAPERS = { A4: [210, 297], A3: [297, 420], B4: [257, 364], B5: [182, 257] };
export const ORIENTS = ["自動", "縦", "横"];
export const MARGINS = ["0", "5", "10", "15", "20"];
export const DPIS = ["150", "200", "300"];

// 画像が大きすぎるときの上限(iPad の Safari は、1枚の canvas の大きさに上限があるため)
export const MAX_PIXELS = 16000000;
export const MAX_SIDE = 16384;

// 表の列: [id, 見出し(1行目), 見出し(2行目), 幅]
export const TREE_COLS = [
  ["com", "共通", "", 46], ["num", "番号", "", 58], ["st", "状態", "", 50], ["pg", "頁", "", 38],
  ["fs", "文字", "(px)", 52], ["ln", "長さ", "(px)", 52], ["wd", "太さ", "(px)", 52], ["arw", "矢印", "", 46],
  ["ang", "角度", "", 54], ["ct", "数字色", "", 60], ["cc", "〇色", "", 60], ["ca", "矢印色", "", 64],
];

// ---------------------------------------------------------------- 小道具 ---
/** Python の round()(偶数丸め)と同じ結果にする。ファイルの互換のため */
export function pyround(x) {
  const r = Math.round(x);
  if (Math.abs(x % 1) === 0.5) return 2 * Math.round(x / 2);
  return r;
}

/** Python の "%g" 相当(6桁、末尾の0なし) */
export function fmtG(x) {
  if (!Number.isFinite(x)) return String(x);
  return String(parseFloat(x.toPrecision(6)));
}

const round1 = (x) => Math.round(x * 10) / 10;
const round2 = (x) => Math.round(x * 100) / 100;

function setdefault(o, k, v) {
  if (!(k in o)) o[k] = v;
  return o[k];
}

export function clone(o) {
  return JSON.parse(JSON.stringify(o));
}

// -------------------------------------------------------- 注記データ操作 ---
export function defaultCommon() {
  return { font_size: 28.0, length: 80.0, width: 4.0, c_text: "#000000", c_circle: "#000000", c_arrow: "#000000" };
}

export function isPlaced(a) {
  return a.x != null && a.y != null;
}

/** 注記を1件作る。共通部分は共通値で埋め、共通=ONで作る。未配置(x,y=null)なら番号・角度・矢印は空。 */
export function makeAnn(common, x = null, y = null, num = "", angle = null, arrow = null) {
  const a = { x, y, num, arrow, angle, common: true, extra: [] };
  for (const k of COMMON_KEYS) a[k] = common[k];
  return a;
}

/** 2本目以降の矢印を1本作る(長さ・太さ・色は共通値、共通=ON) */
export function makeArrow(common, angle) {
  return { angle: Number(angle), length: common.length, width: common.width, c_arrow: common.c_arrow, common: true };
}

/** 既存の矢印の向きから最も離れた向き(15度刻み)を返す */
export function freeAngle(angles) {
  if (!angles.length) return DEFAULT_ANGLE;
  let best = DEFAULT_ANGLE, score = -1.0;
  for (let cand = 0; cand < 360; cand += 15) {
    let m = Infinity;
    for (const a of angles) {
      const d = Math.abs(cand - a) % 360;
      m = Math.min(m, Math.min(d, 360 - d));
    }
    if (m > score) { best = cand; score = m; }
  }
  return best;
}

/** 共通=ONの注記すべて(全ページ)に共通値を反映する。反映した件数を返す。 */
export function applyCommonToPages(pages, common) {
  let n = 0;
  for (const lst of Object.values(pages)) {
    for (const a of lst) {
      if (a.common) {
        for (const k of COMMON_KEYS) a[k] = common[k];
        n++;
      }
      for (const e of a.extra || []) {
        if (e.common) for (const k of ARROW_KEYS) e[k] = common[k];
      }
    }
  }
  return n;
}

export function clampCommon(vals) {
  const out = { ...vals };
  for (const [k, [lo, hi]] of Object.entries(CLAMPS)) out[k] = Math.min(hi, Math.max(lo, Number(out[k])));
  return out;
}

/** .annot(JSON)から {pages, common} を作る。旧形式(全体色)は見た目を変えないよう全注記を共通OFFで開く。 */
export function migrateLoaded(data) {
  const pages = {};
  for (const [k, v] of Object.entries(data.pages || {})) pages[parseInt(k, 10)] = v;
  let common;
  if (data.common && typeof data.common === "object" && !Array.isArray(data.common)) {
    common = defaultCommon();
    for (const k of COMMON_KEYS) if (k in data.common) common[k] = data.common[k];
    for (const lst of Object.values(pages)) {
      for (const a of lst) {
        setdefault(a, "common", true);
        for (const k of COMMON_KEYS) setdefault(a, k, common[k]);
      }
    }
  } else {
    common = defaultCommon();
    const cols = data.colors || {};
    const df = data.defaults || {};
    for (const key of ["font_size", "length", "width"]) {
      const v = parseFloat(df[key]);
      if (Number.isFinite(v)) common[key] = v;
    }
    const map = [["c_text", "text"], ["c_circle", "circle"], ["c_arrow", "arrow"]];
    for (const [key, ck] of map) if (ck in cols) common[key] = cols[ck];
    for (const lst of Object.values(pages)) {
      for (const a of lst) {
        a.common = false;
        for (const [key, ck] of map) a[key] = ck in cols ? cols[ck] : "#000000";
      }
    }
  }
  for (const lst of Object.values(pages)) {
    for (const a of lst) {
      setdefault(a, "x", null);
      setdefault(a, "y", null);
      setdefault(a, "num", "");
      if (isPlaced(a)) {
        if (a.arrow == null) a.arrow = true;
        if (a.angle == null) a.angle = DEFAULT_ANGLE;
      } else {
        a.arrow = null;
        a.angle = null;
      }
      if (!Array.isArray(a.extra) || !isPlaced(a)) a.extra = [];
      for (const e of a.extra) {
        setdefault(e, "angle", DEFAULT_ANGLE);
        setdefault(e, "common", true);
        for (const k of ARROW_KEYS) setdefault(e, k, common[k]);
      }
    }
  }
  return { pages, common };
}

export function colorName(hx) {
  for (const [n, h] of PALETTE) if (h.toLowerCase() === String(hx).toLowerCase()) return n;
  return String(hx);
}

export function tableRowValues(pg, a, status = "") {
  const placed = isPlaced(a);
  return [a.common ? "☑" : "☐", String(a.num), status, String(pg + 1),
    fmtG(round1(a.font_size)), fmtG(round1(a.length)), fmtG(round2(a.width)),
    placed ? (a.arrow ? "☑" : "☐") : "", placed ? fmtG(round1(a.angle)) : "",
    colorName(a.c_text), colorName(a.c_circle), colorName(a.c_arrow)];
}

export function tableChildValues(pg, e, k) {
  return [e.common ? "☑" : "☐", "└矢印" + (k + 1), "", "", "", fmtG(round1(e.length)),
    fmtG(round2(e.width)), "", fmtG(round1(e.angle)), "", "", colorName(e.c_arrow)];
}

/** 表示順を決める。rows=[{pg,idx,a}]。mode 0=元の順 / 1=番号の昇順 / 2=降順。 */
export function sortRows(rows, mode) {
  if (mode !== 1 && mode !== 2) return rows.slice();
  const nums = [], txts = [], blanks = [];
  for (const r of rows) {
    const n = String(r.a.num).trim();
    if (n === "") { blanks.push(r); continue; }
    const f = Number(n);
    if (n !== "" && Number.isFinite(f) && /^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/.test(n)) {
      nums.push([f, r]);
    } else {
      const ints = parseNums(n).filter((it) => it[0] !== "t").map((it) => it[1]);
      if (ints.length) nums.push([Math.min(...ints), r]);
      else txts.push([n, r]);
    }
  }
  const rev = mode === 2;
  // 同じ番号は元の順を保つ(安定ソート)
  nums.sort((a, b) => (rev ? b[0] - a[0] : a[0] - b[0]));
  txts.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0) * (rev ? -1 : 1));
  return [...nums.map((t) => t[1]), ...txts.map((t) => t[1]), ...blanks];
}

export function contrastFg(hx) {
  const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hx);
  if (!m) return "black";
  const [r, g, b] = [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)];
  return 0.299 * r + 0.587 * g + 0.114 * b > 140 ? "black" : "white";
}

// ---------------------------------------------------------------- 幾何 ---
export function ringWidth(fs) {
  return Math.max(1.5, fs * 0.09);
}

export function textWidthEm(s) {
  let w = 0.0;
  for (const ch of String(s)) {
    if (",.-:;' ".includes(ch)) w += 0.30;
    else if (ch.codePointAt(0) < 128) w += 0.56;
    else w += 1.0;
  }
  return w;
}

/** 〇(楕円)の横・縦の半径 [rx, ry]。1〜2桁は円。それより長い番号は、高さは2桁のままで横にだけ伸びる楕円になる。 */
export function circleRadii(a) {
  const fs = a.font_size;
  const w = Math.max(0.56, textWidthEm(a.num));
  let rx = fs * Math.max(0.7, 0.5 * w + 0.25);
  if (rx > fs * 0.81) rx = fs * (0.58 * w + 0.20);
  return [rx, Math.min(rx, fs * 0.81)];
}

export function ellipseDist(rx, ry, dx, dy) {
  return 1.0 / Math.sqrt((dx / rx) ** 2 + (dy / ry) ** 2);
}

// ------------------------------------------------------- 番号の解析・整理 ---
const NUM_SEP = /[,、，・\s]+/;
const NUM_RANGE = /^(\d+)[-~〜ー](\d+)$/;

/** 番号欄の文字を分解する。戻り値: ["n", 数, 数] 単独 / ["r", 始, 終] 範囲 / ["t", 文字] 数字以外 */
export function parseNums(s) {
  s = String(s).normalize("NFKC").trim();
  const out = [];
  for (const tok of s.split(NUM_SEP)) {
    if (!tok) continue;
    const m = NUM_RANGE.exec(tok);
    if (m && parseInt(m[1], 10) <= parseInt(m[2], 10) && parseInt(m[2], 10) <= parseInt(m[1], 10) + 1000) {
      out.push(["r", parseInt(m[1], 10), parseInt(m[2], 10)]);
    } else if (/^[0-9]+$/.test(tok)) {
      out.push(["n", parseInt(tok, 10), parseInt(tok, 10)]);
    } else {
      out.push(["t", tok]);
    }
  }
  return out;
}

/** その注記が持つ番号の集合(範囲は展開)。重複・欠番の判定に使う。 */
export function numKeys(s) {
  const keys = new Set();
  for (const it of parseNums(s)) {
    if (it[0] === "t") keys.add(it[1]);
    else for (let i = it[1]; i <= it[2]; i++) keys.add(String(i));
  }
  return keys;
}

const isDigits = (k) => /^[0-9]+$/.test(k);

export function analyzeNumbers(pages) {
  const count = new Map();
  const rows = new Map();
  let placed = 0, unplaced = 0;
  for (const pgKey of Object.keys(pages)) {
    const pg = Number(pgKey);
    pages[pgKey].forEach((a, i) => {
      if (!isPlaced(a)) { unplaced++; return; }
      placed++;
      const ks = numKeys(a.num);
      rows.set(pg + ":" + i, ks);
      for (const k of ks) count.set(k, (count.get(k) || 0) + 1);
    });
  }
  const dupKeys = new Set([...count].filter(([, c]) => c >= 2).map(([k]) => k));
  const dupRows = new Set();
  for (const [rid, ks] of rows) for (const k of ks) if (dupKeys.has(k)) { dupRows.add(rid); break; }
  const ints = [...count.keys()].filter(isDigits).map((k) => parseInt(k, 10));
  const top = ints.length ? Math.max(...ints) : 0;
  const missing = [];
  if (top <= 100000) for (let n = 1; n <= top; n++) if (!count.has(String(n))) missing.push(n);
  const dups = [...dupKeys].filter(isDigits).sort((a, b) => a - b)
    .concat([...dupKeys].filter((k) => !isDigits(k)).sort());
  return { placed, unplaced, dups, dup_rows: dupRows, missing };
}

function fmtList(items, limit = 8) {
  if (!items.length) return "なし";
  const txt = items.slice(0, limit).join("・");
  return txt + (items.length > limit ? " 他" + (items.length - limit) : "");
}

export function summaryText(info) {
  return `配置 ${info.placed} / 未配置 ${info.unplaced} / 重複 ${fmtList(info.dups)} / 欠番 ${fmtList(info.missing)}`;
}

function remapText(s, mp) {
  const parts = [];
  for (const it of parseNums(s)) {
    if (it[0] === "t") parts.push(it[1]);
    else if (it[0] === "n" || it[1] === it[2]) parts.push(String(mp.get(it[1])));
    else parts.push(`${mp.get(it[1])}-${mp.get(it[2])}`);
  }
  return parts.join(",");
}

/** 番号の振り直しの変更内容 [[ページ, 番号, 新しい文字]] を返す。mode "compact"=欠番を詰める / "seq"=表の並び順に1から */
export function computeRenumber(pages, mode, order) {
  const changes = [];
  if (mode === "seq") {
    let k = 0;
    for (const [pg, i] of order) {
      const a = pages[pg][i];
      if (isPlaced(a)) {
        k++;
        if (String(a.num) !== String(k)) changes.push([pg, i, String(k)]);
      }
    }
    return changes;
  }
  const keys = new Set();
  for (const lst of Object.values(pages)) {
    for (const a of lst) for (const k of numKeys(a.num)) if (isDigits(k)) keys.add(parseInt(k, 10));
  }
  const mp = new Map([...keys].sort((a, b) => a - b).map((n, j) => [n, j + 1]));
  for (const pg of Object.keys(pages).map(Number).sort((a, b) => a - b)) {
    pages[pg].forEach((a, i) => {
      const items = parseNums(a.num);
      if (!items.some((it) => it[0] !== "t")) return;
      const nw = remapText(a.num, mp);
      if (nw !== String(a.num)) changes.push([pg, i, nw]);
    });
  }
  return changes;
}

/** 矢印1本の図形。〇(楕円)の縁から出る。長さ0以下はnull。 */
export function arrowShape(cx, cy, rx, ry, ring, angle, length, width) {
  if (length <= 0) return null;
  const th = (angle * Math.PI) / 180;
  const dx = Math.cos(th), dy = -Math.sin(th); // 角度0=右, 90=上(反時計回り)
  const px = -dy, py = dx;
  const w = Math.max(0.5, width);
  const startR = ellipseDist(rx + ring / 2, ry + ring / 2, dx, dy);
  const rIn = ellipseDist(rx, ry, dx, dy);
  const sx = cx + dx * rIn, sy = cy + dy * rIn;
  const tx = cx + dx * (startR + length), ty = cy + dy * (startR + length);
  const hl = Math.max(1.0, Math.min(w * 3.5 + 2, length));
  const hw = w * 1.7 + 1;
  const bx = tx - dx * hl, by = ty - dy * hl;
  return {
    shaft: [[sx + (px * w) / 2, sy + (py * w) / 2], [bx + (px * w) / 2, by + (py * w) / 2],
      [bx - (px * w) / 2, by - (py * w) / 2], [sx - (px * w) / 2, sy - (py * w) / 2]],
    head: [[tx, ty], [bx + px * hw, by + py * hw], [bx - px * hw, by - py * hw]],
    tip: [tx, ty], seg: [[cx + dx * startR, cy + dy * startR], [tx, ty]], width: w,
  };
}

/** 配置済みの注記1件の図形(画像座標)。1本目の矢印は g.shaft/head/tip/seg、2本目以降は g.extras。 */
export function geometry(a) {
  const cx = a.x, cy = a.y;
  const [rx, ry] = circleRadii(a);
  const ring = ringWidth(a.font_size);
  const g = { cx, cy, rx, ry, ring, shaft: null, head: null, tip: null, seg: null, width: 1.0, extras: [] };
  if (a.arrow) {
    const sh = arrowShape(cx, cy, rx, ry, ring, a.angle, a.length, a.width);
    if (sh) Object.assign(g, sh);
  }
  for (const e of a.extra || []) g.extras.push(arrowShape(cx, cy, rx, ry, ring, e.angle, e.length, e.width));
  return g;
}

/** 描く矢印の一覧 [{k, color, sh}]。k: 0=1本目, 1以上=2本目以降 */
export function arrowShapes(a, g) {
  const out = [];
  if (g.shaft) out.push({ k: 0, color: a.c_arrow, sh: g });
  g.extras.forEach((sh, i) => {
    if (sh) out.push({ k: i + 1, color: a.extra[i].c_arrow, sh });
  });
  return out;
}

/** 用紙書き出し用: 注記を倍率s・オフセット(ox,oy)で拡縮した複製 */
export function scaleAnn(a, s, ox = 0.0, oy = 0.0) {
  const b = { ...a };
  b.x = a.x * s + ox;
  b.y = a.y * s + oy;
  b.font_size = a.font_size * s;
  b.length = a.length * s;
  b.width = a.width * s;
  b.extra = (a.extra || []).map((e) => ({ ...e, length: e.length * s, width: e.width * s }));
  return b;
}

export function distPointSeg(px, py, seg) {
  const [[x1, y1], [x2, y2]] = seg;
  const vx = x2 - x1, vy = y2 - y1;
  const L2 = vx * vx + vy * vy;
  if (L2 === 0) return Math.hypot(px - x1, py - y1);
  const t = Math.max(0.0, Math.min(1.0, ((px - x1) * vx + (py - y1) * vy) / L2));
  return Math.hypot(px - (x1 + t * vx), py - (y1 + t * vy));
}

/**
 * クリック位置の注記を探す。戻り値 {idx, part:"tip"|"shaft"|"circle", k} または null。
 * k: 0=1本目, 1以上=2本目以降, null=〇の部分(矢印の選択は変えない)。
 * 優先順位: ①選んでいる矢印の先端● ②選んでいる注記のほかの矢印の先端● ③選んでいる矢印の軸
 *           ④選んでいる注記の〇 ⑤ほかの注記の〇(手前のもの) ⑥ほかの矢印(クリック位置にいちばん近いもの)
 * skipSel=true(Alt+クリック)は、選んでいる矢印・〇を除いて探す。touch=true は指・ペン用に当たり判定を広げる。
 */
export function hitTest(lst, ix, iy, zoom, sel, selArrow, skipSel = false, touch = false) {
  const tol = (touch ? 14 : 6) / zoom;
  const tipR = (touch ? 24 : 10) / zoom;
  const sa = sel != null && sel >= 0 && sel < lst.length ? lst[sel] : null;
  const selOk = sa != null && isPlaced(sa) && !skipSel;
  const inCircle = (g) => {
    const ex = g.rx + g.ring / 2 + tol * 0.5;
    const ey = g.ry + g.ring / 2 + tol * 0.5;
    return ((ix - g.cx) / ex) ** 2 + ((iy - g.cy) / ey) ** 2 <= 1;
  };
  const shaftGap = (sh) => distPointSeg(ix, iy, sh.seg) - Math.max(sh.width, 1) / 2;

  if (selOk) {
    const g = geometry(sa);
    const shapes = arrowShapes(sa, g);
    const sorted = shapes.slice().sort((p, q) => (p.k === selArrow ? 0 : 1) - (q.k === selArrow ? 0 : 1));
    for (const { k, sh } of sorted) {
      if (Math.hypot(ix - sh.tip[0], iy - sh.tip[1]) <= tipR) return { idx: sel, part: "tip", k };
    }
    for (const { k, sh } of shapes) {
      if (k === selArrow && shaftGap(sh) <= tol) return { idx: sel, part: "shaft", k };
    }
    if (inCircle(g)) return { idx: sel, part: "circle", k: null };
  }
  for (let i = lst.length - 1; i >= 0; i--) {
    const a = lst[i];
    if (!isPlaced(a) || i === sel) continue;
    if (inCircle(geometry(a))) return { idx: i, part: "circle", k: null };
  }
  let best = null;
  for (let i = lst.length - 1; i >= 0; i--) {
    const a = lst[i];
    if (!isPlaced(a)) continue;
    for (const { k, sh } of arrowShapes(a, geometry(a))) {
      if (i === sel && k === selArrow && skipSel) continue;
      const d = shaftGap(sh);
      if (d <= tol && (best === null || d < best[0])) best = [d, i, k];
    }
  }
  if (best) return { idx: best[1], part: "shaft", k: best[2] };
  return null;
}

/** 矢印(1本目なら注記自身、2本目以降なら extra の要素)を (ix,iy) の向きにする */
export function aim(ann, arrow, ix, iy, keepLen = false) {
  const g = geometry({ ...ann, arrow: false, extra: [] });
  const dx = ix - ann.x, dy = iy - ann.y;
  if (arrow === ann) ann.arrow = true;
  arrow.angle = (((Math.atan2(-dy, dx) * 180) / Math.PI) % 360 + 360) % 360;
  if (!keepLen) {
    const dist = Math.hypot(dx, dy) || 1.0;
    const edge = ellipseDist(g.rx + g.ring / 2, g.ry + g.ring / 2, dx / dist, dy / dist);
    arrow.length = Math.max(5.0, dist - edge);
  }
}

// ---------------------------------------------------------------- 用紙 ---
/**
 * 用紙設定が有効なら、図面(bw×bh px)を用紙へ収める配置を返す。無効なら null。
 * s=図面の拡縮倍率, ox/oy=用紙上での図面左上位置(用紙px)。dpiOverride は画像が大きすぎるときの解像度の引き下げ用。
 */
export function paperLayout(bw, bh, paper, dpiOverride = null) {
  const name = paper.size;
  if (!Object.hasOwn(PAPERS, name)) return null;
  let [wmm, hmm] = PAPERS[name];
  const o = paper.orient;
  const land = o === "横" || (o === "自動" && bw >= bh);
  if (land) [wmm, hmm] = [hmm, wmm];
  let dpi = parseInt(paper.dpi, 10);
  let mm = parseFloat(paper.margin);
  if (!Number.isFinite(dpi) || !Number.isFinite(mm)) { dpi = 200; mm = 10.0; }
  if (dpiOverride != null) dpi = dpiOverride;
  const pw = pyround((wmm / 25.4) * dpi), ph = pyround((hmm / 25.4) * dpi);
  const m = pyround((mm / 25.4) * dpi);
  const s = Math.min(Math.max(1, pw - 2 * m) / bw, Math.max(1, ph - 2 * m) / bh);
  const ox = pyround((pw - bw * s) / 2), oy = pyround((ph - bh * s) / 2);
  return { pw, ph, m, s, ox, oy, dpi, name, land };
}

/** 用紙全体と印刷可能範囲(余白の内側)を画像座標で返す */
export function paperRects(lay) {
  const { s, ox, oy, m } = lay;
  return {
    paper: [-ox / s, -oy / s, (lay.pw - ox) / s, (lay.ph - oy) / s],
    area: [(m - ox) / s, (m - oy) / s, (lay.pw - m - ox) / s, (lay.ph - m - oy) / s],
  };
}

export function contentRect(w, h, paper) {
  const lay = paperLayout(w, h, paper);
  if (!lay) return [0, 0, w, h];
  const p = paperRects(lay).paper;
  return [Math.min(0, p[0]), Math.min(0, p[1]), Math.max(w, p[2]), Math.max(h, p[3])];
}

/** 書き出しの計画。大きすぎる場合は解像度(dpi または倍率)を下げて、上限に収める。 */
export function planExport(w, h, paper, maxPixels = MAX_PIXELS, maxSide = MAX_SIDE) {
  let lay = paperLayout(w, h, paper);
  if (!lay) {
    const k = Math.min(1, Math.sqrt(maxPixels / (w * h)), maxSide / Math.max(w, h));
    const outW = Math.max(1, Math.floor(w * k)), outH = Math.max(1, Math.floor(h * k));
    return { kind: "plain", outW, outH, scale: k, ox: 0, oy: 0, dpi: null, reduced: k < 1 };
  }
  const requested = lay.dpi;
  let reduced = false;
  const tooBig = (l) => l.pw * l.ph > maxPixels || Math.max(l.pw, l.ph) > maxSide;
  if (tooBig(lay)) {
    reduced = true;
    const f = Math.min(Math.sqrt(maxPixels / (lay.pw * lay.ph)), maxSide / Math.max(lay.pw, lay.ph));
    let dpi = Math.max(20, Math.floor(requested * f));
    lay = paperLayout(w, h, paper, dpi);
    while (tooBig(lay) && dpi > 20) { dpi -= 2; lay = paperLayout(w, h, paper, dpi); }
  }
  return { kind: "paper", outW: lay.pw, outH: lay.ph, scale: lay.s, ox: lay.ox, oy: lay.oy,
    dpi: Math.round(lay.dpi), requestedDpi: requested, reduced, lay };
}

/** 画像の元の大きさ(w×h)に対する、保持する画像の縮小率(上限を超えるときだけ <1) */
export function rasterScale(w, h, maxPixels = MAX_PIXELS, maxSide = MAX_SIDE) {
  return Math.min(1, Math.sqrt(maxPixels / (w * h)), maxSide / Math.max(w, h));
}

// ------------------------------------------------------- 編集ファイル(.annot) ---
/** 保存用のデータ(Win版と同じ version 2)。Web版は図面のパスを知らないので、ファイル名だけを入れる。 */
export function buildAnnot({ srcName, srcType, curPage, common, nextNum, paper, pages }) {
  const outPages = {};
  for (const k of Object.keys(pages).sort((a, b) => a - b)) outPages[String(k)] = pages[k];
  return {
    version: 2,
    source: { path: srcName, rel: srcName, type: srcType },
    pdf_dpi: PDF_DPI,
    current_page: curPage,
    common,
    next_num: nextNum,
    paper: { size: paper.size, orient: paper.orient, margin: String(paper.margin), dpi: String(paper.dpi) },
    pages: outPages,
  };
}

// ---------------------------------------------------------------- PNG ---
let crcTable = null;
export function crc32(bytes) {
  if (!crcTable) {
    crcTable = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      crcTable[n] = c >>> 0;
    }
  }
  let c = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) c = crcTable[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

/** PNG に解像度(dpi)の情報(pHYsチャンク)を入れる。canvas から出したPNGには入っていないため。 */
export function pngWithDpi(bytes, dpi) {
  if (!dpi || bytes.length < 33) return bytes;
  const ppm = Math.round(dpi / 0.0254);
  const chunk = new Uint8Array(21);
  const dv = new DataView(chunk.buffer);
  dv.setUint32(0, 9);
  chunk.set([0x70, 0x48, 0x59, 0x73], 4); // "pHYs"
  dv.setUint32(8, ppm);
  dv.setUint32(12, ppm);
  chunk[16] = 1; // メートルあたり
  dv.setUint32(17, crc32(chunk.subarray(4, 17)));
  const out = new Uint8Array(bytes.length + 21);
  out.set(bytes.subarray(0, 33), 0); // 署名8 + IHDR(4+4+13+4)
  out.set(chunk, 33);
  out.set(bytes.subarray(33), 54);
  return out;
}
