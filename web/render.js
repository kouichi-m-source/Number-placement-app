// 番号配置アプリ(Web版) canvas への描画(画面表示と PNG 書き出しで共通)
import { geometry, arrowShapes } from "./core.js";

export const FONT = 'Arial, "Helvetica Neue", Helvetica, "Hiragino Sans", "Noto Sans JP", sans-serif';

function poly(ctx, pts, fill) {
  ctx.beginPath();
  ctx.moveTo(pts[0][0], pts[0][1]);
  for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
  ctx.closePath();
  ctx.fillStyle = fill;
  ctx.fill();
}

/** 配置済みの注記1件を描く(矢印→〇(白塗り+枠)→番号の順)。座標は ctx の座標系(画像ピクセル)。 */
export function drawAnnotation(ctx, a) {
  const g = geometry(a);
  for (const { color, sh } of arrowShapes(a, g)) {
    poly(ctx, sh.shaft, color);
    poly(ctx, sh.head, color);
  }
  ctx.beginPath();
  ctx.ellipse(g.cx, g.cy, g.rx, g.ry, 0, 0, Math.PI * 2);
  ctx.fillStyle = "#FFFFFF";
  ctx.fill();
  ctx.lineWidth = g.ring;
  ctx.strokeStyle = a.c_circle;
  ctx.stroke();
  ctx.font = `bold ${Math.max(1, a.font_size)}px ${FONT}`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillStyle = a.c_text;
  ctx.fillText(String(a.num), g.cx, g.cy);
}

/** 選択中の注記の枠・選んでいる矢印・つかみ位置(●)を描く。z=画面の倍率(線の太さを画面上で一定にする) */
export function drawSelection(ctx, a, selArrow, z, touch) {
  const g = geometry(a);
  const m = g.ring / 2 + 4 / z;
  ctx.save();
  ctx.setLineDash([4 / z, 3 / z]);
  ctx.lineWidth = 2 / z;
  ctx.strokeStyle = "#FF8C00";
  ctx.beginPath();
  ctx.ellipse(g.cx, g.cy, g.rx + m, g.ry + m, 0, 0, Math.PI * 2);
  ctx.stroke();
  ctx.setLineDash([]);
  const big = (touch ? 10 : 6) / z, small = (touch ? 7 : 4) / z;
  for (const { k, sh } of arrowShapes(a, g)) {
    const [tx, ty] = sh.tip;
    if (k === selArrow) {
      ctx.lineWidth = 2 / z;
      ctx.strokeStyle = "#FF8C00";
      for (const key of ["shaft", "head"]) {
        ctx.beginPath();
        sh[key].forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
        ctx.closePath();
        ctx.stroke();
      }
      ctx.beginPath();
      ctx.arc(tx, ty, big, 0, Math.PI * 2);
      ctx.fillStyle = "#FF8C00";
      ctx.fill();
      ctx.lineWidth = 2 / z;
      ctx.strokeStyle = "#FFFFFF";
      ctx.stroke();
    } else {
      ctx.beginPath();
      ctx.arc(tx, ty, small, 0, Math.PI * 2);
      ctx.fillStyle = "#FFFFFF";
      ctx.fill();
      ctx.lineWidth = 2 / z;
      ctx.strokeStyle = "#FF8C00";
      ctx.stroke();
    }
  }
  ctx.restore();
}
