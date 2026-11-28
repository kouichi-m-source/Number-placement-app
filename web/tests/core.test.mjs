// Win版(Python)の計算結果(pyref.json)と、Web版 core.js の結果が一致するかを確かめるテスト
// 実行: node --test web/tests/   (pyref.json は web/tests/make_pyref.py で作り直せる)
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import * as C from "../core.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const ref = JSON.parse(fs.readFileSync(path.join(here, "pyref.json"), "utf8"));
const EPS = 1e-9;

function close(a, b, msg) {
  if (a === null || b === null) return assert.equal(a, b, msg);
  if (Array.isArray(a)) {
    assert.equal(a.length, b.length, msg + " length");
    a.forEach((v, i) => close(v, b[i], msg + "[" + i + "]"));
    return;
  }
  if (typeof a === "number") return assert.ok(Math.abs(a - b) <= EPS * Math.max(1, Math.abs(b)), `${msg}: ${a} vs ${b}`);
  assert.deepEqual(a, b, msg);
}

const pages = () => C.clone(Object.fromEntries(Object.entries(ref.pages).map(([k, v]) => [Number(k), v])));

test("geometry / circle radii", () => {
  const P = pages();
  let n = 0;
  for (const [id, exp] of Object.entries(ref.geometry)) {
    const [pg, i] = id.split(":").map(Number);
    const a = P[pg][i];
    const g = C.geometry(a);
    close(C.circleRadii(a), exp.radii, id + " radii");
    for (const key of ["cx", "cy", "rx", "ry", "ring", "shaft", "head", "tip", "seg", "width"]) close(g[key], exp.g[key], `${id}.${key}`);
    assert.equal(g.extras.length, exp.g.extras.length);
    g.extras.forEach((sh, k) => {
      if (exp.g.extras[k] === null) assert.equal(sh, null);
      else for (const key of ["shaft", "head", "tip", "seg", "width"]) close(sh[key], exp.g.extras[k][key], `${id}.extra${k}.${key}`);
    });
    n++;
  }
  assert.ok(n > 30);
});

test("analyze_numbers / summary", () => {
  const P = pages();
  const info = C.analyzeNumbers(P);
  assert.equal(info.placed, ref.analyze.placed);
  assert.equal(info.unplaced, ref.analyze.unplaced);
  assert.deepEqual(info.dups, ref.analyze.dups);
  assert.deepEqual(info.missing, ref.analyze.missing);
  assert.deepEqual([...info.dup_rows].sort(), ref.analyze.dup_rows.slice().sort());
  assert.equal(C.summaryText(info), ref.summary);
});

test("sort_rows", () => {
  const P = pages();
  const rows = [];
  for (const pg of Object.keys(P).map(Number).sort((a, b) => a - b)) P[pg].forEach((a, i) => rows.push({ pg, idx: i, a }));
  for (const m of [0, 1, 2]) {
    assert.deepEqual(C.sortRows(rows, m).map((r) => [r.pg, r.idx]), ref.sort[String(m)], "mode " + m);
  }
});

test("renumber", () => {
  const P = pages();
  const rows = [];
  for (const pg of Object.keys(P).map(Number).sort((a, b) => a - b)) P[pg].forEach((a, i) => rows.push({ pg, idx: i, a }));
  const order = C.sortRows(rows, 1).map((r) => [r.pg, r.idx]);
  assert.deepEqual(C.computeRenumber(P, "compact", order), ref.renum_compact);
  assert.deepEqual(C.computeRenumber(P, "seq", order), ref.renum_seq);
});

test("table values", () => {
  const P = pages();
  for (const [id, exp] of Object.entries(ref.table)) {
    const [pg, i] = id.split(":").map(Number);
    assert.deepEqual(C.tableRowValues(pg, P[pg][i], "x"), exp.map(String), id); // 頁は表示用の文字
  }
  const kids = [];
  for (let i = 0; i < P[0].length && kids.length < 5; i++) if (C.isPlaced(P[0][i]) && P[0][i].extra.length) kids.push(C.tableChildValues(0, P[0][i].extra[0], 1));
  assert.deepEqual(kids, ref.child.map((r) => r.map(String)));
});

test("parse_nums", () => {
  for (const [s, exp] of Object.entries(ref.parse)) assert.deepEqual(C.parseNums(s), exp, JSON.stringify(s));
});

test("paper layout", () => {
  for (const { in: [size, orient, margin, dpi, bw, bh], out } of ref.paper) {
    const r = C.paperLayout(bw, bh, { size, orient, margin, dpi });
    if (out === null) { assert.equal(r, null); continue; }
    for (const k of ["pw", "ph", "m", "ox", "oy", "dpi", "land"]) assert.equal(r[k], out[k], `${size}/${orient}/${dpi}/${bw}x${bh} ${k}`);
    close(r.s, out.s, "s");
  }
});

test("hit_test", () => {
  const P = pages();
  let hitCount = 0;
  for (const h of ref.hits) {
    const r = C.hitTest(P[0], h.ix, h.iy, h.zoom, h.sel, h.sel_arrow, h.skip, false);
    if (h.out === null) assert.equal(r, null, JSON.stringify(h));
    else {
      assert.ok(r, JSON.stringify(h));
      assert.equal(r.idx, h.out[0]); assert.equal(r.part, h.out[1]); assert.equal(r.k, h.out[2]);
      hitCount++;
    }
  }
  assert.ok(hitCount > 50);
});

test("migrate (old format)", () => {
  const r = C.migrateLoaded(C.clone(ref.migrate_old_in));
  assert.deepEqual(r.common, ref.migrate_old.common);
  assert.deepEqual(Object.fromEntries(Object.entries(r.pages).map(([k, v]) => [String(k), v])), ref.migrate_old.pages);
});

test("free_angle", () => {
  for (const [inp, exp] of ref.free_angle) assert.equal(C.freeAngle(inp), exp);
});

test("planExport reduces big output", () => {
  const p = C.planExport(5000, 7000, { size: "A3", orient: "縦", margin: "10", dpi: "300" });
  assert.ok(p.outW * p.outH <= C.MAX_PIXELS && p.reduced && p.dpi < 300);
  const q = C.planExport(4000, 3000, { size: "A4", orient: "自動", margin: "10", dpi: "200" });
  assert.ok(!q.reduced && q.dpi === 200);
  const r = C.planExport(9000, 9000, { size: "図面サイズのまま", orient: "自動", margin: "10", dpi: "200" });
  assert.ok(r.reduced && r.outW * r.outH <= C.MAX_PIXELS);
});

test("pngWithDpi inserts valid pHYs", () => {
  // 1x1 の最小PNG
  const png = Uint8Array.from(Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==", "base64"));
  const out = C.pngWithDpi(png, 300);
  assert.equal(out.length, png.length + 21);
  assert.equal(Buffer.from(out.subarray(37, 41)).toString(), "pHYs");
  const dv = new DataView(out.buffer);
  assert.equal(dv.getUint32(41), Math.round(300 / 0.0254));
});

// ---- 複数の図面・ページの入れ替え(.annot version 3) ---
const baseArgs = { curPage: 0, common: C.defaultCommon(), nextNum: 1, paper: { size: "A4", orient: "自動", margin: "10", dpi: "200" }, pages: {} };

test("図面が1つで並びもそのままなら version 2 で保存する(旧版と互換)", () => {
  const d = C.buildAnnot({ ...baseArgs, sources: [{ name: "a.pdf", type: "pdf", pageCount: 2 }], order: [[0, 0], [0, 1]] });
  assert.equal(d.version, 2);
  assert.equal(d.sources, undefined);
  assert.equal(d.page_order, undefined);
  assert.deepEqual(d.source, { path: "a.pdf", rel: "a.pdf", type: "pdf" });
});

test("図面が複数、または並びが変わったら version 3 で保存する", () => {
  const two = C.buildAnnot({ ...baseArgs, sources: [{ name: "a.png", type: "image", pageCount: 1 }, { name: "b.pdf", type: "pdf", pageCount: 2 }], order: [[0, 0], [1, 0], [1, 1]] });
  assert.equal(two.version, 3);
  assert.deepEqual(two.sources.map((s) => s.path), ["a.png", "b.pdf"]);
  assert.deepEqual(two.page_order, [[0, 0], [1, 0], [1, 1]]);
  assert.equal(two.source.path, "a.png");
  const swapped = C.buildAnnot({ ...baseArgs, sources: [{ name: "b.pdf", type: "pdf", pageCount: 2 }], order: [[0, 1], [0, 0]] });
  assert.equal(swapped.version, 3);
});

test("page_order の検査: 範囲外や形の違うものは捨て、使えなければ null", () => {
  assert.equal(C.validPageOrder(undefined, [1]), null);
  assert.equal(C.validPageOrder([], [1]), null);
  assert.deepEqual(C.validPageOrder([[0, 0], [1, 1], [1, 5], [2, 0], "x", [0]], [1, 2]), [[0, 0], [1, 1]]);
  assert.deepEqual(C.validPageOrder([["0", "0"]], [1]), [[0, 0]]);
});
