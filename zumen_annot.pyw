# -*- coding: utf-8 -*-
"""
図面注記アプリ(丸数字+矢印)
 - 図面(PDF・画像)の上に「〇付き数字+矢印」を配置し、後から何度でも編集できる
 - 共通ステータス(文字サイズ・矢印の長さ/太さ・3色)を「更新」で一括反映。注記ごとに共通を外して個別編集もできる
 - 右側の注記一覧(表)で全注記を確認・編集。右クリックで下に行を挿入
 - 編集用ファイル(.annot / JSON)で保存、必要なときにPNGへ書き出し(用紙サイズ指定可)
 - 必要: Python 3.9+, Pillow, PyMuPDF(PDFを使う場合のみ)  pip install pillow pymupdf
"""
import copy
import json
import math
import os
import re
import sys
import time
import traceback
import unicodedata
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox, colorchooser

try:
    from PIL import Image, ImageDraw, ImageFont, ImageTk
except ImportError:
    _r = tk.Tk()
    _r.withdraw()
    messagebox.showerror("図面注記", "Pillow が見つかりません。\nコマンドプロンプトで次を実行してください。\n\npip install pillow pymupdf")
    sys.exit(1)

Image.MAX_IMAGE_PIXELS = None
try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None

APP_NAME = "図面注記"
PDF_DPI = 150
SS = 4  # 書き出し時のスーパーサンプリング倍率(アンチエイリアス)
MAX_DISPLAY_PIXELS = 60_000_000
DEFAULT_ANGLE = 45.0
# Alt キーの状態ビット。Windows の Tk では 0x8 は NumLock なので、Alt は 0x20000 だけで判定する(Linux/Mac の Alt は 0x8)
ALT_MASK = 0x20000 if sys.platform == "win32" else 0x8


def is_alt(state):
    return bool(state & ALT_MASK)


# 名前付きの基本色パレット
PALETTE = [
    ("黒", "#000000"), ("白", "#FFFFFF"), ("赤", "#FF0000"), ("青", "#0000FF"),
    ("緑", "#00A000"), ("黄", "#FFD400"), ("橙", "#FF8000"), ("紫", "#8000C0"),
    ("水色", "#00B4F0"), ("桃", "#FF50A0"), ("灰", "#808080"), ("茶", "#804000"),
]
COLOR_FIELDS = [("c_text", "数字"), ("c_circle", "〇の枠"), ("c_arrow", "矢印")]
COMMON_KEYS = ("font_size", "length", "width", "c_text", "c_circle", "c_arrow")
ARROW_KEYS = ("length", "width", "c_arrow")  # 2本目以降の矢印が、共通ONのとき共通値に従う項目
IMG_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".gif", ".webp")
DRAW_EXTS = (".pdf",) + IMG_EXT
_DRAW_PAT = " ".join("*" + e for e in DRAW_EXTS)
FILETYPES_DRAW = [("図面(PDF・画像)", _DRAW_PAT), ("すべて", "*.*")]
FILETYPES_OPEN = [("図面・編集ファイル", _DRAW_PAT + " *.annot"), ("図面(PDF・画像)", _DRAW_PAT),
                  ("編集ファイル", "*.annot"), ("すべて", "*.*")]

# 用紙(縦向きの mm 寸法: 幅, 高さ。B判はJIS)
NO_PAPER = "図面サイズのまま"
PAPERS = {"A4": (210, 297), "A3": (297, 420), "B4": (257, 364), "B5": (182, 257)}
ORIENTS = ["自動", "縦", "横"]
MARGINS = ["0", "5", "10", "15", "20"]
DPIS = ["150", "200", "300"]

# 注記一覧(表)の列: (id, 見出し, 幅)
TREE_COLS = [("com", "共通\n ", 42), ("num", "番号\n ", 52), ("st", "状態\n ", 44), ("pg", "頁\n ", 34),
             ("fs", "文字\n(px)", 46), ("ln", "長さ\n(px)", 46), ("wd", "太さ\n(px)", 46), ("arw", "矢印\n ", 42),
             ("ang", "角度\n ", 48), ("ct", "数字色\n ", 54), ("cc", "〇色\n ", 54), ("ca", "矢印色\n ", 58)]
TREE_IDS = [c[0] for c in TREE_COLS]
TREE_FIXED = 2  # 先頭の2列(共通・番号)は横スクロールしても固定で表示する
SORT_MARK = {0: "▲▼", 1: "▲", 2: "▼"}
COMMON_CELLS = {"fs": "font_size", "ln": "length", "wd": "width", "ct": "c_text", "cc": "c_circle", "ca": "c_arrow"}
CLAMPS = {"font_size": (4, 500), "length": (0, 10000), "width": (0.5, 200)}


# -------------------------------------------------------- 注記データ操作 ---
def default_common():
    return {"font_size": 28.0, "length": 80.0, "width": 4.0,
            "c_text": "#000000", "c_circle": "#000000", "c_arrow": "#000000"}


def is_placed(a):
    return a.get("x") is not None and a.get("y") is not None


def make_ann(common, x=None, y=None, num="", angle=None, arrow=None):
    """注記を1件作る。共通部分は共通値で埋め、共通=ONで作る。未配置(x,y=None)なら番号・角度・矢印は空。"""
    a = {"x": x, "y": y, "num": num, "arrow": arrow, "angle": angle, "common": True, "extra": []}
    for k in COMMON_KEYS:
        a[k] = common[k]
    return a


def make_arrow(common, angle):
    """2本目以降の矢印を1本作る(長さ・太さ・色は共通値、共通=ON)"""
    return {"angle": float(angle), "length": common["length"], "width": common["width"],
            "c_arrow": common["c_arrow"], "common": True}


def free_angle(angles):
    """既存の矢印の向きから最も離れた向き(15度刻み)を返す。矢印が重ならないように、追加する矢印の向きを決める。"""
    if not angles:
        return DEFAULT_ANGLE
    best, score = DEFAULT_ANGLE, -1.0
    for cand in range(0, 360, 15):
        m = min(min(abs(cand - a) % 360, 360 - abs(cand - a) % 360) for a in angles)
        if m > score:
            best, score = float(cand), m
    return best


def apply_common_to_pages(pages, common):
    """共通=ONの注記すべて(全ページ)に共通値を反映する。反映した件数を返す。"""
    n = 0
    for lst in pages.values():
        for a in lst:
            if a.get("common"):
                for k in COMMON_KEYS:
                    a[k] = common[k]
                n += 1
            for e in a.get("extra", []):
                if e.get("common"):
                    for k in ARROW_KEYS:
                        e[k] = common[k]
    return n


def clamp_common(vals):
    out = dict(vals)
    for k, (lo, hi) in CLAMPS.items():
        out[k] = min(hi, max(lo, float(out[k])))
    return out


def migrate_loaded(data):
    """.annot(JSON)から (pages, common) を作る。旧形式(全体色)は見た目を変えないよう全注記を共通OFFで開く。"""
    pages = {int(k): v for k, v in data.get("pages", {}).items()}
    if isinstance(data.get("common"), dict):
        common = default_common()
        common.update({k: v for k, v in data["common"].items() if k in COMMON_KEYS})
        for lst in pages.values():
            for a in lst:
                a.setdefault("common", True)
                for k in COMMON_KEYS:
                    a.setdefault(k, common[k])
    else:
        common = default_common()
        cols = data.get("colors", {})
        df = data.get("defaults", {})
        for key, dk in (("font_size", "font_size"), ("length", "length"), ("width", "width")):
            try:
                common[key] = float(df.get(dk, common[key]))
            except (TypeError, ValueError):
                pass
        for key, ck in (("c_text", "text"), ("c_circle", "circle"), ("c_arrow", "arrow")):
            common[key] = cols.get(ck, common[key])
        for lst in pages.values():
            for a in lst:
                a["common"] = False
                for key, ck in (("c_text", "text"), ("c_circle", "circle"), ("c_arrow", "arrow")):
                    a[key] = cols.get(ck, "#000000")
    for lst in pages.values():
        for a in lst:
            a.setdefault("x", None)
            a.setdefault("y", None)
            a.setdefault("num", "")
            if is_placed(a):
                if a.get("arrow") is None:
                    a["arrow"] = True
                if a.get("angle") is None:
                    a["angle"] = DEFAULT_ANGLE
            else:
                a["arrow"] = None
                a["angle"] = None
            if not isinstance(a.get("extra"), list) or not is_placed(a):
                a["extra"] = []
            for e in a["extra"]:
                e.setdefault("angle", DEFAULT_ANGLE)
                e.setdefault("common", True)
                for k in ARROW_KEYS:
                    e.setdefault(k, common[k])
    return pages, common


def is_drawing_path(p):
    return str(p).lower().endswith(DRAW_EXTS)


def valid_page_order(raw, page_counts):
    """.annot の page_order を検査する。範囲外や形の違うものは捨て、使えなければ None(呼び出し側で「頭から全ページ」にする)。
    page_counts は図面ごとのページ数。"""
    if not isinstance(raw, list):
        return None
    out = []
    for e in raw:
        if not isinstance(e, (list, tuple)) or len(e) < 2:
            continue
        try:
            si, pi = int(e[0]), int(e[1])
        except (TypeError, ValueError):
            continue
        if 0 <= si < len(page_counts) and 0 <= pi < page_counts[si]:
            out.append([si, pi])
    return out or None


def annot_layout(sources, order):
    """保存する図面の一覧と並びを返す。いま使っている図面だけを、使い始めた順に並べ直す。
    戻り値: (使う図面の番号の並び, 新しい並び [[図面の番号, ページ], ...], 「1つの図面を頭から全ページそのまま」か)。
    そのままなら version 2(旧版と同じ形)、それ以外は version 3 で保存する。"""
    remap, used, out = {}, [], []
    for si, pi in order:
        if si not in remap:
            remap[si] = len(used)
            used.append(si)
        out.append([remap[si], pi])
    natural = (len(used) == 1 and len(out) == sources[used[0]]["page_count"]
               and all(a == 0 and b == i for i, (a, b) in enumerate(out)))
    return used, out, natural


def color_name(hx):
    for n, h in PALETTE:
        if h.lower() == str(hx).lower():
            return n
    return str(hx)


def table_row_values(pg, a, status=""):
    placed = is_placed(a)
    return ("☑" if a["common"] else "☐", a["num"], status, pg + 1,
            "%g" % round(a["font_size"], 1), "%g" % round(a["length"], 1), "%g" % round(a["width"], 2),
            ("☑" if a["arrow"] else "☐") if placed else "",
            ("%g" % round(a["angle"], 1)) if placed else "",
            color_name(a["c_text"]), color_name(a["c_circle"]), color_name(a["c_arrow"]))


def sort_rows(rows, mode):
    """表示順を決める。rows=[(pg, idx, ann)]。mode 0=元の順 / 1=番号の昇順 / 2=降順。
    数字は数値として比べ、数字以外の文字は数字の後、番号が空の行は常に最後。同じ番号は元の順を保つ。"""
    if mode not in (1, 2):
        return list(rows)
    nums, txts, blanks = [], [], []
    for r in rows:
        n = str(r[2]["num"]).strip()
        if n == "":
            blanks.append(r)
            continue
        try:
            nums.append((float(n), r))
        except ValueError:
            ints = [it[1] for it in parse_nums(n) if it[0] != "t"]  # 「3,4」「3-5」は最初の(最小の)番号で並べる
            if ints:
                nums.append((float(min(ints)), r))
            else:
                txts.append((n, r))
    rev = (mode == 2)
    nums.sort(key=lambda t: t[0], reverse=rev)
    txts.sort(key=lambda t: t[0], reverse=rev)
    return [r for _, r in nums] + [r for _, r in txts] + blanks


def table_child_values(pg, e, k):
    """2本目以降の矢印の行(子行)。表の列の順(TREE_COLS)に合わせて返す。"""
    return ("☑" if e["common"] else "☐", "└矢印%d" % (k + 1), "", "", "", "%g" % round(e["length"], 1),
            "%g" % round(e["width"], 2), "", "%g" % round(e["angle"], 1), "", "", color_name(e["c_arrow"]))


def contrast_fg(hx):
    try:
        r, g, b = int(hx[1:3], 16), int(hx[3:5], 16), int(hx[5:7], 16)
    except Exception:
        return "black"
    return "black" if (0.299 * r + 0.587 * g + 0.114 * b) > 140 else "white"


# ---------------------------------------------------------------- 幾何 ---
def ring_width(fs):
    return max(1.5, fs * 0.09)


def text_width_em(s):
    """番号の文字の横幅の目安(文字サイズに対する倍率)。半角の記号は狭く、全角は広く数える。"""
    w = 0.0
    for ch in str(s):
        if ch in ",.-:;' ":
            w += 0.30
        elif ch.isascii():
            w += 0.56
        else:
            w += 1.0
    return w


def circle_radii(a):
    """〇(楕円)の横・縦の半径 (rx, ry)。1〜2桁は円。それより長い番号(3桁以上や「3,4」のような複数番号)は、
    高さは2桁のままで、横にだけ伸びる楕円になる。"""
    fs = a["font_size"]
    w = max(0.56, text_width_em(a["num"]))
    rx = fs * max(0.7, 0.5 * w + 0.25)
    if rx > fs * 0.81:  # 横に長い番号は楕円。楕円の縁に文字の角が当たらないよう、余裕を持たせる
        rx = fs * (0.58 * w + 0.20)
    return rx, min(rx, fs * 0.81)


def ellipse_dist(rx, ry, dx, dy):
    """中心から単位ベクトル(dx,dy)の向きに進んだとき、楕円(rx,ry)の縁までの距離"""
    return 1.0 / math.sqrt((dx / rx) ** 2 + (dy / ry) ** 2)


# ------------------------------------------------------- 番号の解析・整理 ---
_NUM_SEP = re.compile(r"[,、，・\s]+")
_NUM_RANGE = re.compile(r"^(\d+)[-~〜ー](\d+)$")


def parse_nums(s):
    """番号欄の文字を分解する。「3,4」「3-5」のように書くと、1つの〇に複数の番号を入れられる。
    戻り値: [("n", 数, 数) 単独 / ("r", 始, 終) 範囲 / ("t", 文字) 数字以外]"""
    s = unicodedata.normalize("NFKC", str(s)).strip()
    out = []
    for tok in _NUM_SEP.split(s):
        if not tok:
            continue
        m = _NUM_RANGE.match(tok)
        if m and int(m.group(1)) <= int(m.group(2)) <= int(m.group(1)) + 1000:
            out.append(("r", int(m.group(1)), int(m.group(2))))
        elif tok.isascii() and tok.isdigit():
            out.append(("n", int(tok), int(tok)))
        else:
            out.append(("t", tok))
    return out


def num_keys(s):
    """その注記が持つ番号の集合(範囲は展開)。重複・欠番の判定に使う。"""
    keys = set()
    for it in parse_nums(s):
        if it[0] == "t":
            keys.add(it[1])
        else:
            keys.update(str(i) for i in range(it[1], it[2] + 1))
    return keys


def analyze_numbers(pages):
    """全ページの配置済み注記から、配置数・未配置数・重複する番号・欠番を調べる。
    欠番 = 1 から「配置済みの最大の番号」までの間で、どの注記にもない番号。"""
    count, rows = {}, {}
    placed = unplaced = 0
    for pg in pages:
        for i, a in enumerate(pages[pg]):
            if not is_placed(a):
                unplaced += 1
                continue
            placed += 1
            ks = num_keys(a["num"])
            rows[(pg, i)] = ks
            for k in ks:
                count[k] = count.get(k, 0) + 1
    dup_keys = {k for k, c in count.items() if c >= 2}
    dup_rows = {rid for rid, ks in rows.items() if ks & dup_keys}
    ints = [int(k) for k in count if k.isdigit()]
    top = max(ints) if ints else 0
    missing = [n for n in range(1, top + 1) if str(n) not in count] if top <= 100000 else []
    dups = sorted((k for k in dup_keys if k.isdigit()), key=int) + sorted(k for k in dup_keys if not k.isdigit())
    return {"placed": placed, "unplaced": unplaced, "dups": dups, "dup_rows": dup_rows, "missing": missing}


def _fmt_list(items, limit=8):
    if not items:
        return "なし"
    txt = "・".join(str(x) for x in items[:limit])
    return txt + (" 他%d" % (len(items) - limit) if len(items) > limit else "")


def summary_text(info):
    return "配置 %d / 未配置 %d / 重複 %s / 欠番 %s" % (
        info["placed"], info["unplaced"], _fmt_list(info["dups"]), _fmt_list(info["missing"]))


def remap_text(s, mp):
    parts = []
    for it in parse_nums(s):
        if it[0] == "t":
            parts.append(it[1])
        elif it[0] == "n" or it[1] == it[2]:
            parts.append(str(mp[it[1]]))
        else:
            parts.append("%d-%d" % (mp[it[1]], mp[it[2]]))
    return ",".join(parts)


def compute_renumber(pages, mode, order):
    """番号の振り直しの変更内容 [(ページ, 番号, 新しい文字)] を返す。
    mode "compact": 欠番を詰める(重複・複数番号はそのまま、番号の順序も保つ)
    mode "seq": order(表の並び)の配置済み注記に、1から順に付け直す(重複もなくなる)"""
    changes = []
    if mode == "seq":
        k = 0
        for pg, i in order:
            a = pages[pg][i]
            if is_placed(a):
                k += 1
                if str(a["num"]) != str(k):
                    changes.append((pg, i, str(k)))
        return changes
    keys = set()
    for pg in pages:
        for a in pages[pg]:
            keys |= {int(k) for k in num_keys(a["num"]) if k.isdigit()}
    mp = {n: j + 1 for j, n in enumerate(sorted(keys))}
    for pg in sorted(pages):
        for i, a in enumerate(pages[pg]):
            items = parse_nums(a["num"])
            if not any(it[0] != "t" for it in items):
                continue
            new = remap_text(a["num"], mp)
            if new != str(a["num"]):
                changes.append((pg, i, new))
    return changes


def arrow_shape(cx, cy, rx, ry, ring, angle, length, width):
    """矢印1本の図形(軸・矢じり・先端・当たり判定用の線分)。〇(楕円)の縁から出る。長さ0以下はNone。"""
    if length <= 0:
        return None
    th = math.radians(angle)
    dx, dy = math.cos(th), -math.sin(th)  # 角度0=右, 90=上(反時計回り)
    px, py = -dy, dx
    w = max(0.5, width)
    start_r = ellipse_dist(rx + ring / 2, ry + ring / 2, dx, dy)
    r_in = ellipse_dist(rx, ry, dx, dy)
    sx, sy = cx + dx * r_in, cy + dy * r_in
    tx, ty = cx + dx * (start_r + length), cy + dy * (start_r + length)
    hl = max(1.0, min(w * 3.5 + 2, length))
    hw = w * 1.7 + 1
    bx, by = tx - dx * hl, ty - dy * hl
    return {"shaft": [(sx + px * w / 2, sy + py * w / 2), (bx + px * w / 2, by + py * w / 2),
                      (bx - px * w / 2, by - py * w / 2), (sx - px * w / 2, sy - py * w / 2)],
            "head": [(tx, ty), (bx + px * hw, by + py * hw), (bx - px * hw, by - py * hw)],
            "tip": (tx, ty), "seg": ((cx + dx * start_r, cy + dy * start_r), (tx, ty)), "width": w}


def geometry(a):
    """配置済みの注記1件の図形を画像座標で返す。1本目の矢印は g の shaft/head/tip/seg、2本目以降は g["extras"]。"""
    cx, cy = a["x"], a["y"]
    rx, ry = circle_radii(a)
    ring = ring_width(a["font_size"])
    g = {"cx": cx, "cy": cy, "rx": rx, "ry": ry, "ring": ring,
         "shaft": None, "head": None, "tip": None, "seg": None, "width": 1.0, "extras": []}
    if a.get("arrow"):
        sh = arrow_shape(cx, cy, rx, ry, ring, a["angle"], a["length"], a["width"])
        if sh:
            g.update(sh)
    for e in a.get("extra", []):
        g["extras"].append(arrow_shape(cx, cy, rx, ry, ring, e["angle"], e["length"], e["width"]))
    return g


def arrow_shapes(a, g):
    """描く矢印の一覧 [(矢印番号, 色, 図形)]。矢印番号: 0=1本目, 1以上=2本目以降"""
    out = []
    if g["shaft"]:
        out.append((0, a["c_arrow"], g))
    for i, sh in enumerate(g["extras"]):
        if sh:
            out.append((i + 1, a["extra"][i]["c_arrow"], sh))
    return out


def scale_ann(a, s, ox=0.0, oy=0.0):
    """用紙書き出し用: 注記を倍率s・オフセット(ox,oy)で拡縮した複製を返す。"""
    b = dict(a)
    b["x"] = a["x"] * s + ox
    b["y"] = a["y"] * s + oy
    b["font_size"] = a["font_size"] * s
    b["length"] = a["length"] * s
    b["width"] = a["width"] * s
    b["extra"] = [{**e, "length": e["length"] * s, "width": e["width"] * s} for e in a.get("extra", [])]
    return b


def dist_point_seg(px, py, seg):
    (x1, y1), (x2, y2) = seg
    vx, vy = x2 - x1, y2 - y1
    L2 = vx * vx + vy * vy
    if L2 == 0:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * vx + (py - y1) * vy) / L2))
    return math.hypot(px - (x1 + t * vx), py - (y1 + t * vy))


# ------------------------------------------------------------ 書き出し ---
_font_path = None
_font_checked = False


def get_font(size):
    global _font_path, _font_checked
    if not _font_checked:
        _font_checked = True
        for c in ("arialbd.ttf", "arial.ttf", "meiryob.ttc", "meiryo.ttc", "msgothic.ttc",
                  "DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "Helvetica.ttc"):
            try:
                ImageFont.truetype(c, 20)
                _font_path = c
                break
            except Exception:
                continue
    size = max(1, int(round(size)))
    if _font_path:
        return ImageFont.truetype(_font_path, size)
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def paste_annotation(base, a):
    """base(RGBA)に配置済みの注記1件を合成する。注記ごとの小タイルを高解像度で描いて縮小する。色は注記自身の値を使う。"""
    g = geometry(a)
    xs = [g["cx"] - g["rx"] - g["ring"], g["cx"] + g["rx"] + g["ring"]]
    ys = [g["cy"] - g["ry"] - g["ring"], g["cy"] + g["ry"] + g["ring"]]
    shapes = arrow_shapes(a, g)
    for k, col, sh in shapes:
        for key in ("shaft", "head"):
            xs += [p[0] for p in sh[key]]
            ys += [p[1] for p in sh[key]]
    m = 4
    x0 = max(0, int(math.floor(min(xs))) - m)
    y0 = max(0, int(math.floor(min(ys))) - m)
    x1 = min(base.width, int(math.ceil(max(xs))) + m)
    y1 = min(base.height, int(math.ceil(max(ys))) + m)
    if x1 <= x0 or y1 <= y0:
        return
    tile = Image.new("RGBA", ((x1 - x0) * SS, (y1 - y0) * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)

    def T(p):
        return ((p[0] - x0) * SS, (p[1] - y0) * SS)

    for k, col, sh in shapes:
        d.polygon([T(p) for p in sh["shaft"]], fill=col)
        d.polygon([T(p) for p in sh["head"]], fill=col)
    Rx, Ry = g["rx"] + g["ring"] / 2, g["ry"] + g["ring"] / 2
    c = T((g["cx"], g["cy"]))
    d.ellipse([c[0] - Rx * SS, c[1] - Ry * SS, c[0] + Rx * SS, c[1] + Ry * SS],
              fill="#FFFFFF", outline=a["c_circle"], width=max(1, int(round(g["ring"] * SS))))
    d.text(c, str(a["num"]), font=get_font(a["font_size"] * SS), fill=a["c_text"], anchor="mm")
    tile = tile.resize((x1 - x0, y1 - y0), Image.LANCZOS)
    base.alpha_composite(tile, (x0, y0))


def render_annotations(base_img, anns):
    base = base_img.convert("RGBA")
    for a in anns:
        if is_placed(a):
            paste_annotation(base, a)
    return base.convert("RGB")


# ------------------------------------------------------------- アプリ ---
class FatScrollbar(tk.Canvas):
    """太さを自由に決められるスクロールバー(Windows標準のものは太さを変えられないため自作)。
    ttk.Scrollbar と同じく set(first,last) と command("moveto"/"scroll", ...) で連動する。"""

    def __init__(self, master, orient, command, thick=20):
        kw = {"width": thick} if orient == "vertical" else {"height": thick}
        super().__init__(master, highlightthickness=0, bg="#e4e4e4", **kw)
        self.orient = orient
        self.command = command
        self.first, self.last = 0.0, 1.0
        self._grab = None
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<ButtonPress-1>", self.on_press)
        self.bind("<B1-Motion>", self.on_drag)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_grab", None))

    def length(self):
        return max(1, self.winfo_height() if self.orient == "vertical" else self.winfo_width())

    def thumb(self):
        L = self.length()
        a = self.first * L
        b = max(self.last * L, a + 28)
        if b > L:
            a, b = max(0, L - (b - a)), L
        return a, b

    def draw(self):
        self.delete("all")
        a, b = self.thumb()
        w = self.winfo_width() if self.orient == "vertical" else self.winfo_height()
        if self.orient == "vertical":
            self.create_rectangle(2, a + 1, w - 2, b - 1, fill="#9a9a9a", outline="")
        else:
            self.create_rectangle(a + 1, 2, b - 1, w - 2, fill="#9a9a9a", outline="")

    def set(self, first, last):
        self.first, self.last = float(first), float(last)
        self.draw()

    def pos(self, e):
        return e.y if self.orient == "vertical" else e.x

    def on_press(self, e):
        p = self.pos(e)
        a, b = self.thumb()
        if a <= p <= b:
            self._grab = p - a
        elif p < a:
            self.command("scroll", -1, "pages")
        else:
            self.command("scroll", 1, "pages")

    def on_drag(self, e):
        if self._grab is None:
            return
        span = self.last - self.first
        f = (self.pos(e) - self._grab) / self.length()
        self.command("moveto", max(0.0, min(1.0 - span, f)))


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry("1540x860")
        self.root.report_callback_exception = self.on_exception

        self.sources = []  # 開いている図面: {"path", "type", "pdf", "img", "page_count"}
        self.order = []  # ページの並び: [図面の番号, その図面のページ(0始まり)]
        self.src_path = None  # 先頭の図面(タイトル・保存名の既定に使う)
        self.page_count = 1
        self.show_table = True
        self.cur_page = 0
        self.base_img = None
        self.pages = {}
        self.common = default_common()  # 「更新」で確定した共通値
        self.next_num = 1
        self.sel = None  # 表示中ページ内の選択注記のindex
        self.sel_arrow = 0  # 選択中の矢印(0=1本目, 1以上=2本目以降)
        self.zoom = 1.0
        self.annot_path = None
        self.dirty = False
        self.undo_stack = []
        self.redo_stack = []
        self.drag = None
        self.tk_img = None
        self._edit = None

        # 共通ステータスの入力欄(「更新」を押すまで確定しない)
        self.p_fs = tk.StringVar(value="28")
        self.p_len = tk.StringVar(value="80")
        self.p_wid = tk.StringVar(value="4")
        self.p_col = {"c_text": "#000000", "c_circle": "#000000", "c_arrow": "#000000"}
        self.v_next = tk.StringVar(value="1")
        self.place_mode = tk.BooleanVar(value=True)
        self.place_mode.trace_add("write", lambda *a: self.remove_ghost() if not self.place_mode.get() else None)
        self.v_page = tk.StringVar(value="- / -")
        self.v_status = tk.StringVar(value="図面か編集ファイルを開いてください(ファイル > 開く)")
        self.v_paper = tk.StringVar(value=NO_PAPER)
        self.v_orient = tk.StringVar(value="自動")
        self.v_margin = tk.StringVar(value="10")
        self.v_dpi = tk.StringVar(value="200")

        self.build_ui()
        for v in (self.p_fs, self.p_len, self.p_wid):
            v.trace_add("write", lambda *a: self.refresh_pending())
        self.load_common_to_ui()
        self.update_title()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---- UI -------------------------------------------------------------
    def build_ui(self):
        r = self.root
        menu = tk.Menu(r)
        fm = tk.Menu(menu, tearoff=0)
        fm.add_command(label="開く(図面・編集ファイル)...", accelerator="Ctrl+O", command=self.open_files)
        fm.add_command(label="図面を追加(最後に追加)...", accelerator="Ctrl+Shift+O", command=self.add_files)
        fm.add_separator()
        fm.add_command(label="保存", accelerator="Ctrl+S", command=self.save)
        fm.add_command(label="名前を付けて保存...", command=self.save_as)
        fm.add_separator()
        fm.add_command(label="PNG書き出し(表示中のページ)...", accelerator="Ctrl+E", command=self.export_png)
        fm.add_command(label="PNG書き出し(全ページ)...", command=self.export_all)
        fm.add_separator()
        fm.add_command(label="終了", command=self.on_close)
        menu.add_cascade(label="ファイル", menu=fm)
        em = tk.Menu(menu, tearoff=0)
        em.add_command(label="元に戻す", accelerator="Ctrl+Z", command=self.undo)
        em.add_command(label="やり直し", accelerator="Ctrl+Y", command=self.redo)
        em.add_separator()
        em.add_command(label="ページの入れ替え...", command=self.reorder_pages)
        em.add_separator()
        em.add_command(label="選択行の下に行を挿入", command=self.insert_row_below)
        em.add_command(label="選択した注記を削除", accelerator="Delete", command=self.delete_selected)
        menu.add_cascade(label="編集", menu=em)
        vm = tk.Menu(menu, tearoff=0)
        vm.add_command(label="拡大", command=lambda: self.zoom_by(1.25))
        vm.add_command(label="縮小", command=lambda: self.zoom_by(0.8))
        vm.add_command(label="全体表示", command=self.fit)
        vm.add_command(label="等倍(100%)", command=lambda: self.set_zoom(1.0))
        menu.add_cascade(label="表示", menu=vm)
        hm = tk.Menu(menu, tearoff=0)
        hm.add_command(label="操作のヒント", command=self.show_hints)
        menu.add_cascade(label="ヘルプ", menu=hm)
        r.config(menu=menu)

        # --- 上部1段目: ファイル・ページ・表示・用紙
        tb = ttk.Frame(r, padding=(4, 4, 4, 0))
        tb.pack(side="top", fill="x")
        ttk.Button(tb, text="開く", command=self.open_files).pack(side="left", padx=2)
        ttk.Button(tb, text="追加", command=self.add_files).pack(side="left", padx=2)
        ttk.Button(tb, text="保存", command=self.save).pack(side="left", padx=2)
        ttk.Button(tb, text="PNG書き出し", command=self.export_png).pack(side="left", padx=2)
        ttk.Separator(tb, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(tb, text="◀", width=3, command=lambda: self.change_page(-1)).pack(side="left")
        ttk.Label(tb, textvariable=self.v_page, width=9, anchor="center").pack(side="left")
        ttk.Button(tb, text="▶", width=3, command=lambda: self.change_page(1)).pack(side="left")
        ttk.Separator(tb, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(tb, text="－", width=3, command=lambda: self.zoom_by(0.8)).pack(side="left")
        ttk.Button(tb, text="＋", width=3, command=lambda: self.zoom_by(1.25)).pack(side="left")
        ttk.Button(tb, text="全体", width=5, command=self.fit).pack(side="left", padx=2)
        ttk.Separator(tb, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Checkbutton(tb, text="ダブルクリックで新規配置", variable=self.place_mode).pack(side="left")

        pp = ttk.Frame(tb)
        pp.pack(side="right")

        def combo(label, var, values, w):
            ttk.Label(pp, text=label).pack(side="left", padx=(8, 2))
            cb = ttk.Combobox(pp, textvariable=var, values=values, width=w, state="readonly")
            cb.pack(side="left")
            cb.bind("<<ComboboxSelected>>", self.on_paper_change)

        combo("用紙", self.v_paper, [NO_PAPER] + list(PAPERS), 14)
        combo("向き", self.v_orient, ORIENTS, 5)
        combo("余白mm", self.v_margin, MARGINS, 4)
        combo("dpi", self.v_dpi, DPIS, 5)

        # --- 上部2段目: 共通ステータス
        row2 = ttk.Frame(r, padding=(4, 2, 4, 4))
        row2.pack(side="top", fill="x")
        ttk.Label(row2, text="共通ステータス", font=("", 9, "bold")).pack(side="left")

        def spin(label, var, lo, hi, step, w):
            ttk.Label(row2, text=label).pack(side="left", padx=(10, 2))
            ttk.Spinbox(row2, textvariable=var, from_=lo, to=hi, increment=step, width=w).pack(side="left")

        spin("文字サイズ", self.p_fs, 4, 500, 2, 6)
        spin("矢印の長さ", self.p_len, 0, 10000, 5, 7)
        spin("矢印の太さ", self.p_wid, 0.5, 200, 0.5, 5)
        self.col_btns = {}
        for key, label in COLOR_FIELDS:
            ttk.Label(row2, text=label + "色").pack(side="left", padx=(10, 2))
            b = tk.Button(row2, width=7, relief="raised", command=lambda k=key: self.pick_pending_color(k))
            b.pack(side="left")
            self.col_btns[key] = b
        ttk.Button(row2, text="更新", command=self.apply_common).pack(side="left", padx=(12, 4))
        self.lbl_pending = ttk.Label(row2, text="", foreground="#D35400")
        self.lbl_pending.pack(side="left")
        self.build_table_switch(row2)
        ttk.Spinbox(row2, textvariable=self.v_next, from_=0, to=99999, increment=1, width=7).pack(side="right")
        ttk.Label(row2, text="次の番号").pack(side="right", padx=(10, 2))

        ttk.Label(r, textvariable=self.v_status, relief="sunken", anchor="w", padding=(6, 2)).pack(side="bottom", fill="x")

        main = ttk.Frame(r)
        main.pack(fill="both", expand=True)
        panel = ttk.Frame(main, padding=4, width=360)
        panel.pack(side="right", fill="y")
        panel.pack_propagate(False)
        self.panel = panel
        sash = tk.Frame(main, width=6, cursor="sb_h_double_arrow", bg="#c8c8c8")
        sash.pack(side="right", fill="y")
        self.sash = sash
        sash.bind("<ButtonPress-1>", lambda e: setattr(self, "_sash0", (e.x_root, self.panel.winfo_width())))
        sash.bind("<B1-Motion>", self.on_sash_drag)
        cf = ttk.Frame(main)
        cf.pack(side="left", fill="both", expand=True)
        self.canvas_frame = cf
        self.canvas = tk.Canvas(cf, bg="#6b6b6b", highlightthickness=0)
        vs = ttk.Scrollbar(cf, orient="vertical", command=self.canvas.yview)
        hs = ttk.Scrollbar(cf, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(xscrollcommand=hs.set, yscrollcommand=vs.set)
        vs.pack(side="right", fill="y")
        hs.pack(side="bottom", fill="x")
        self.canvas.pack(side="left", fill="both", expand=True)

        c = self.canvas
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<Double-Button-1>", self.on_double)
        c.bind("<Triple-Button-1>", self.on_press)
        c.bind("<Leave>", lambda e: self.remove_ghost())
        c.bind("<B1-Motion>", self.on_motion)
        c.bind("<ButtonRelease-1>", self.on_release)
        for b in (2, 3):
            c.bind("<ButtonPress-%d>" % b, lambda e: c.scan_mark(e.x, e.y))
            c.bind("<B%d-Motion>" % b, lambda e: c.scan_dragto(e.x, e.y, gain=1))
        c.bind("<ButtonPress-3>", self.on_rpress, add="+")
        c.bind("<ButtonRelease-3>", self.on_rrelease)
        c.bind("<MouseWheel>", self.on_wheel)
        c.bind("<Button-4>", lambda e: self.on_wheel(e, 1))
        c.bind("<Button-5>", lambda e: self.on_wheel(e, -1))
        c.bind("<Motion>", self.on_hover)

        r.bind("<Delete>", self.on_delete_key)
        r.bind("<Escape>", lambda e: self.select(None))
        r.bind("<Control-z>", lambda e: self.undo())
        r.bind("<Control-y>", lambda e: self.redo())
        r.bind("<Control-s>", lambda e: self.save())
        r.bind("<Control-o>", lambda e: self.open_files())
        r.bind("<Control-O>", lambda e: self.add_files())
        r.bind("<Control-e>", lambda e: self.export_png())
        r.bind("<Prior>", lambda e: self.change_page(-1))
        r.bind("<Next>", lambda e: self.change_page(1))

        # --- 右: 注記一覧(表)。共通・番号の2列だけ別の表にして、横スクロールしても固定で見えるようにする
        self.sort_mode = 0
        self._ysync = False
        self._row_tags = {}
        self._sel_iid = None
        self.v_summary = tk.StringVar(value="")
        ttk.Label(panel, textvariable=self.v_summary, foreground="#333", wraplength=340, justify="left").pack(anchor="w", pady=(0, 3))
        tf = ttk.Frame(panel)
        tf.pack(fill="both", expand=True, pady=(0, 4))
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(1, weight=1)
        style = ttk.Style()
        self.tree_font = tkfont.nametofont("TkDefaultFont").copy()
        self.tree_font.configure(size=11)
        style.configure("Treeview", rowheight=24, font=self.tree_font)
        try:  # 見出しは2段なので、文字が切れないよう、見出し行の縦幅を文字2行ぶんにする
            ls = tkfont.nametofont("TkHeadingFont").metrics("linespace")
        except tk.TclError:
            ls = tkfont.nametofont("TkDefaultFont").metrics("linespace")
        style.configure("Treeview.Heading", padding=(2, ls // 2 + 4))
        style.map("Treeview", background=[("selected", "#FF8C00")], foreground=[("selected", "#FFFFFF")])
        fixed_ids = TREE_IDS[:TREE_FIXED]
        scroll_ids = TREE_IDS[TREE_FIXED:]
        self.tree_l = ttk.Treeview(tf, columns=fixed_ids, show="headings", selectmode="browse")
        self.tree_r = ttk.Treeview(tf, columns=scroll_ids, show="headings", selectmode="browse")
        self.col_ids = {self.tree_l: fixed_ids, self.tree_r: scroll_ids}
        for cid, head, w in TREE_COLS:
            t = self.tree_l if cid in fixed_ids else self.tree_r
            t.heading(cid, text=head)
            t.column(cid, width=w, minwidth=w, stretch=False, anchor="center")
        self.btn_sort = tk.Button(self.tree_l, text="番号\n▲▼", relief="raised", bd=2, takefocus=0,
                                  command=self.toggle_sort)  # 押せることが分かるよう、見出しに重ねた立体のボタン
        self.tree_l.bind("<Configure>", lambda e: self.root.after_idle(self.place_sort_button))
        self.vsb = FatScrollbar(tf, "vertical", self.yview_both)
        self.hsb = FatScrollbar(tf, "horizontal", self.tree_r.xview)
        self.tree_l.configure(yscrollcommand=lambda f, l: self.on_yscroll(self.tree_l, f, l))
        self.tree_r.configure(yscrollcommand=lambda f, l: self.on_yscroll(self.tree_r, f, l),
                              xscrollcommand=self.hsb.set)
        self.tree_l.grid(row=0, column=0, sticky="ns")
        self.tree_r.grid(row=0, column=1, sticky="nsew")
        self.vsb.grid(row=0, column=2, sticky="ns")
        self.hsb.grid(row=1, column=1, sticky="ew")
        for t in (self.tree_l, self.tree_r):
            t.tag_configure("unplaced", foreground="#888888")
            t.tag_configure("dup", background="#FFF0B3")
            t.tag_configure("child", foreground="#555555")
            t.tag_configure("selrow", background="#FF8C00", foreground="#FFFFFF")
            t.bind("<Button-1>", self.on_tree_click)
            t.bind("<Double-1>", self.on_tree_dbl)
            t.bind("<Button-3>", self.on_tree_menu)

        bf = ttk.Frame(panel)
        bf.pack(fill="x")
        ttk.Button(bf, text="下に行を挿入", command=self.insert_row_below).pack(side="left")
        ttk.Button(bf, text="行を削除", command=self.delete_selected).pack(side="left", padx=6)
        ttk.Button(bf, text="番号を振り直す", command=self.renumber).pack(side="left")
        ttk.Label(panel, foreground="#555", justify="left", wraplength=340, text=(
            "・行クリックで図面上の注記を選択 / 「共通」列をクリックでON・OFF\n"
            "・共通OFFの行だけ、文字・長さ・太さ・色をダブルクリックで編集\n"
            "・番号・角度はいつでもダブルクリックで編集 / 「矢印」列クリックで有無を切替\n"
            "・右クリック: 下に行を挿入(未配置の行。図面をダブルクリックすると配置)")).pack(anchor="w", pady=(6, 0))

    # ---- 表の表示・非表示 -----------------------------------------------
    def build_table_switch(self, parent):
        """共通ステータスの行の右端に置く「表の表示」スイッチ(ON=青でつまみが右 / OFF=灰でつまみが左)"""
        fr = ttk.Frame(parent)
        fr.pack(side="right", padx=(14, 0))
        self.sw_label = ttk.Label(fr, text="表の表示", font=("", 9, "bold"))
        self.sw_label.pack(side="left", padx=(0, 4))
        bg = ttk.Style().lookup("TFrame", "background") or "#f0f0f0"
        self.sw_canvas = tk.Canvas(fr, width=38, height=22, highlightthickness=0, bg=bg, cursor="hand2")
        self.sw_canvas.pack(side="left")
        self.sw_text = ttk.Label(fr, text="ON", width=4)
        self.sw_text.pack(side="left", padx=(4, 0))
        for w in (fr, self.sw_label, self.sw_canvas, self.sw_text):
            w.bind("<Button-1>", lambda e: self.toggle_table())
        self.draw_table_switch()

    def draw_table_switch(self):
        on = self.show_table
        track, edge, knob = ("#2F7FD8", "#1F5FA8", "#FFFFFF") if on else ("#FFFFFF", "#9A9AA2", "#8A8A92")
        c = self.sw_canvas
        c.delete("all")
        c.create_oval(2, 2, 20, 20, fill=track, outline=edge)
        c.create_oval(18, 2, 36, 20, fill=track, outline=edge)
        c.create_rectangle(11, 2, 27, 21, fill=track, outline=track)
        c.create_line(11, 2, 27, 2, fill=edge)
        c.create_line(11, 20, 27, 20, fill=edge)
        kx = 27 if on else 11
        c.create_oval(kx - 7, 4, kx + 7, 18, fill=knob, outline=knob)
        self.sw_text.config(text="ON" if on else "OFF", foreground="#1F5FA8" if on else "#777777")
        self.sw_label.config(foreground="#000000" if on else "#666666")

    def toggle_table(self, v=None):
        """右の注記一覧(表)を隠す/出す。隠すと図面が横いっぱいに広がる。"""
        v = (not self.show_table) if v is None else bool(v)
        if v == self.show_table:
            return
        was_fit = False
        if self.base_img is not None:  # 全体表示のままなら、広がった(狭まった)分に合わせて全体を表示し直す
            fz = max(0.03, min(self.max_zoom(), self.fit_zoom()))
            was_fit = abs(self.zoom - fz) < 1e-6
        self.show_table = v
        if v:
            self.panel.pack(side="right", fill="y", before=self.canvas_frame)
            self.sash.pack(side="right", fill="y", before=self.canvas_frame)
        else:
            self.cancel_edit()
            self.sash.pack_forget()
            self.panel.pack_forget()
        self.draw_table_switch()
        self.root.update_idletasks()
        if was_fit:
            self.fit()
        self.v_status.set("表を表示しました" if v else "表を隠しました(図面が広く表示されます。「表の表示」で戻せます)")

    # ---- ページの入れ替え -----------------------------------------------
    def apply_page_order(self, work):
        """work[i] = 入れ替え後の i 番目に来る、元のページ(0始まり)。図面の並びと注記を新しい並びに直す。"""
        if len(work) != self.page_count or work == list(range(self.page_count)):
            return False
        self.push_undo()
        self.pages = {i: self.pages[o] for i, o in enumerate(work) if o in self.pages}
        cur = self.cur_page
        self.order = [self.order[o] for o in work]
        self.go_page(work.index(cur))
        return True

    def page_thumb(self, o, box):
        """入れ替え画面用の、元の o ページの小さな画像"""
        si, pi = self.order[o]
        src = self.sources[si]
        if src["type"] == "pdf":
            pg = src["pdf"][pi]
            z = box / max(pg.rect.width, pg.rect.height)
            pix = pg.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False)
            im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        else:
            im = src["img"].copy()
        im.thumbnail((box, box))
        return ImageTk.PhotoImage(im)

    def reorder_pages(self):
        if self.base_img is None or self.page_count < 2:
            messagebox.showinfo(APP_NAME, "入れ替えるページが2ページ以上必要です。")
            return
        n = self.page_count
        box = 64
        top = tk.Toplevel(self.root)
        top.title("ページの入れ替え")
        top.transient(self.root)
        top.geometry("600x560")
        ttk.Label(top, text="行をドラッグするか、「上へ」「下へ」で並べ替えます。注記は、ページといっしょに動きます。").pack(
            anchor="w", padx=8, pady=(8, 4))
        ttk.Style().configure("Reorder.Treeview", rowheight=box + 8, font=self.tree_font)
        tf = ttk.Frame(top)
        tf.pack(fill="both", expand=True, padx=8)
        tree = ttk.Treeview(tf, columns=("name", "cnt"), show="tree headings", selectmode="browse", style="Reorder.Treeview")
        tree.heading("#0", text="ページ")
        tree.heading("name", text="図面")
        tree.heading("cnt", text="注記")
        tree.column("#0", width=150, stretch=False)
        tree.column("name", width=300)
        tree.column("cnt", width=70, anchor="center", stretch=False)
        vs = ttk.Scrollbar(tf, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vs.set)
        vs.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)
        thumbs = {}
        for o in range(n):
            si, pi = self.order[o]
            src = self.sources[si]
            nm = os.path.basename(src["path"])
            if src["page_count"] > 1:
                nm += " (%d/%d)" % (pi + 1, src["page_count"])
            cnt = sum(1 for a in self.pages.get(o, []) if is_placed(a))
            tree.insert("", "end", iid=str(o), text="  %d" % (o + 1), values=(nm, "%d件" % cnt))

        def relabel():
            for i, iid in enumerate(tree.get_children()):
                tree.item(iid, text="  %d%s" % (i + 1, "" if int(iid) == i else "  (移動)"))

        def fill_thumbs(o=0):  # 小さな画像は、画面を開いてから順に作る(PDFが多くても、すぐ開く)
            if o >= n or not top.winfo_exists():
                return
            try:
                thumbs[o] = self.page_thumb(o, box)
                tree.item(str(o), image=thumbs[o])
            except Exception:
                pass  # 画像がなくても入れ替えはできる
            top.after(5, lambda: fill_thumbs(o + 1))

        drag = {"iid": None}
        tree.bind("<ButtonPress-1>", lambda e: drag.update(iid=tree.identify_row(e.y) or None))

        def on_drag(e):
            tgt = tree.identify_row(e.y)
            if drag["iid"] and tgt and tgt != drag["iid"]:
                tree.move(drag["iid"], "", tree.index(tgt))
                relabel()

        tree.bind("<B1-Motion>", on_drag)
        tree.bind("<ButtonRelease-1>", lambda e: drag.update(iid=None))

        def shift(d):
            sel = tree.selection()
            if not sel:
                return
            i = tree.index(sel[0]) + d
            if 0 <= i < n:
                tree.move(sel[0], "", i)
                tree.see(sel[0])
                relabel()

        def reset():
            for i in range(n):
                tree.move(str(i), "", i)
            relabel()

        def ok():
            work = [int(i) for i in tree.get_children()]
            top.destroy()
            self.apply_page_order(work)

        bf = ttk.Frame(top)
        bf.pack(fill="x", padx=8, pady=8)
        ttk.Button(bf, text="上へ", command=lambda: shift(-1)).pack(side="left")
        ttk.Button(bf, text="下へ", command=lambda: shift(1)).pack(side="left", padx=4)
        ttk.Button(bf, text="元の並びに戻す", command=reset).pack(side="left", padx=4)
        ttk.Button(bf, text="この並びにする", command=ok).pack(side="right")
        ttk.Button(bf, text="キャンセル", command=top.destroy).pack(side="right", padx=4)
        tree.selection_set("0")
        top.bind("<Escape>", lambda e: top.destroy())
        try:
            top.grab_set()
        except tk.TclError:
            pass
        top.after(30, fill_thumbs)

    def show_hints(self):
        messagebox.showinfo(APP_NAME, (
            "・空いている所をダブルクリック: 新しい番号を配置(ダブルクリックのまま押し続けてドラッグで矢印の向き)\n"
            "・〇をドラッグ: 注記の移動 / 矢印の軸をドラッグ: 向きの調整(長さはそのまま)\n"
            "・矢印の先の ● をドラッグ: ●がマウスに付いてきて向きと長さが変わる(共通ONの矢印は、離すと長さが共通値に戻る。Shiftで長さを固定)\n"
            "・矢印を増やす: 注記を右クリック(図面でも表でも) > 矢印を追加。2本目以降は表の子行(└矢印2…)に出ます\n"
            "・矢印が重なって選びにくいとき: 表で行を選ぶか、Alt+クリックで重なっている次の候補を選べます\n"
            "・文字サイズ・矢印の長さ/太さ・色は、上の共通ステータスを入力して「更新」で、\n"
            "  共通ONの全注記(配置済みを含む)に反映されます\n"
            "・注記一覧(右の表)の「共通」列をOFFにした注記は、更新の対象から外れ、個別に編集できます\n"
            "・番号欄に「3,4」「3-5」と書くと、1つの〇に複数の番号を入れられます(重複・欠番の判定は番号ごと)\n"
            "・表の上の「配置/未配置/重複/欠番」は集計です。欠番は、1から最大の番号までの抜けです\n"
            "・表で行を選んで右クリック > 下に行を挿入: 未配置の行ができます。番号を入力して\n"
            "  行を選んだまま図面をダブルクリックすると、その位置に配置されます(番号が空なら自動採番)\n"
            "・Ctrl+ホイール: 拡大縮小 / 右・中ボタンドラッグ: 画面移動 / Delete: 削除 / Esc: 選択解除\n"
            "・用紙を選ぶと、画面に用紙の枠(実線)と余白の内側(青い破線)を表示し、PNGも用紙サイズで書き出します\n"
            "・「開く」は図面(PDF・画像)も編集ファイルも開けます。「追加」で、開いている作業の最後に図面を足せます\n"
            "・編集 > ページの入れ替え で、ページの順番を変えられます(注記はページといっしょに動きます)\n"
            "・共通ステータスの行の右端「表の表示」で、注記一覧の表を隠して図面を広く使えます"))

    def on_exception(self, exc, val, tb):
        msg = "".join(traceback.format_exception(exc, val, tb))
        try:
            base = os.path.dirname(os.path.abspath(sys.argv[0]))
            with open(os.path.join(base, "zumen_annot_error.log"), "a", encoding="utf-8") as f:
                f.write(time.strftime("[%Y-%m-%d %H:%M:%S]\n") + msg + "\n")
        except Exception:
            pass
        messagebox.showerror(APP_NAME, "エラーが発生しました。\n\n%s: %s" % (exc.__name__, val))

    # ---- 共通ステータス -------------------------------------------------
    def pending_values(self):
        try:
            vals = {"font_size": float(self.p_fs.get()), "length": float(self.p_len.get()),
                    "width": float(self.p_wid.get())}
        except ValueError:
            return None
        vals.update(self.p_col)
        return vals

    def refresh_pending(self):
        if not hasattr(self, "lbl_pending"):
            return
        pv = self.pending_values()
        diff = pv is None
        if not diff:
            diff = any(abs(pv[k] - self.common[k]) > 1e-9 for k in ("font_size", "length", "width")) or \
                any(str(pv[k]).lower() != str(self.common[k]).lower() for k, _ in COLOR_FIELDS)
        self.lbl_pending.config(text="※未更新(「更新」で反映)" if diff else "")

    def update_color_buttons(self):
        for key, _ in COLOR_FIELDS:
            hx = self.p_col[key]
            self.col_btns[key].config(bg=hx, activebackground=hx, fg=contrast_fg(hx), activeforeground=contrast_fg(hx),
                                      text=color_name(hx))

    def load_common_to_ui(self):
        self.p_fs.set("%g" % self.common["font_size"])
        self.p_len.set("%g" % self.common["length"])
        self.p_wid.set("%g" % self.common["width"])
        for key, _ in COLOR_FIELDS:
            self.p_col[key] = self.common[key]
        self.update_color_buttons()
        self.refresh_pending()

    def pick_pending_color(self, key):
        def done(hx):
            self.p_col[key] = hx
            self.update_color_buttons()
            self.refresh_pending()
        self.popup_palette(self.col_btns[key], self.p_col[key], done)

    def popup_palette(self, anchor, current, callback, pos=None):
        top = tk.Toplevel(self.root)
        top.title("色を選択")
        top.transient(self.root)
        top.resizable(False, False)
        x, y = pos if pos else (anchor.winfo_rootx(), anchor.winfo_rooty() + anchor.winfo_height())
        top.geometry("+%d+%d" % (x, y))

        def pick(hx):
            top.destroy()
            callback(hx)

        def other():
            res = colorchooser.askcolor(color=current, title="色を選択", parent=top)
            if res and res[1]:
                pick(res[1].upper())

        for j, (name, hx) in enumerate(PALETTE):
            tk.Button(top, text=name, bg=hx, activebackground=hx, fg=contrast_fg(hx), activeforeground=contrast_fg(hx),
                      width=6, command=lambda h=hx: pick(h)).grid(row=j // 4, column=j % 4, padx=2, pady=2)
        tk.Button(top, text="その他の色...", command=other).grid(row=3, column=0, columnspan=4, sticky="we", padx=2, pady=(2, 4))
        top.bind("<Escape>", lambda e: top.destroy())
        try:
            top.grab_set()
        except tk.TclError:
            pass

    def apply_common(self):
        pv = self.pending_values()
        if pv is None:
            messagebox.showwarning(APP_NAME, "文字サイズ・矢印の長さ・太さには数値を入力してください。")
            return
        new = clamp_common(pv)
        self.push_undo()
        self.common = new
        n = apply_common_to_pages(self.pages, new)
        self.load_common_to_ui()
        self.refresh_table()
        self.redraw_annotations()
        self.v_status.set("共通ステータスを更新しました(共通ONの注記 %d 件に反映)" % n)

    # ---- データ操作 -----------------------------------------------------
    def anns(self):
        return self.pages.setdefault(self.cur_page, [])

    def cur_ann(self):
        if self.sel is None:
            return None
        lst = self.anns()
        return lst[self.sel] if 0 <= self.sel < len(lst) else None

    def snapshot(self):
        return copy.deepcopy((self.pages, self.common, self.next_num, self.order))

    def restore(self, snap):
        self.pages, self.common, self.next_num, order = copy.deepcopy(snap)
        order_changed = order != self.order
        self.order = order
        self.page_count = len(order)
        self.v_next.set(str(self.next_num))
        self.sel = None
        self.sel_arrow = 0
        self.load_common_to_ui()
        self.refresh_table()
        self.redraw_annotations()
        if order_changed and self.base_img is not None:  # 追加・入れ替えを取り消したときは、表示も合わせる
            self.go_page(min(self.cur_page, self.page_count - 1))

    def push_undo(self):
        self.undo_stack.append(self.snapshot())
        if len(self.undo_stack) > 100:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self.set_dirty(True)

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(self.snapshot())
            self.restore(self.undo_stack.pop())
            self.set_dirty(True)

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(self.snapshot())
            self.restore(self.redo_stack.pop())
            self.set_dirty(True)

    def set_dirty(self, v):
        self.dirty = v
        self.update_title()

    def update_title(self):
        name = os.path.basename(self.annot_path) if self.annot_path else (
            os.path.basename(self.src_path) if self.src_path else "")
        self.root.title("%s%s - %s" % ("*" if self.dirty else "", name or "(未選択)", APP_NAME))

    # ---- 図面の読み込み -------------------------------------------------
    def confirm_discard(self):
        if not self.dirty:
            return True
        ans = messagebox.askyesnocancel(APP_NAME, "変更が保存されていません。保存しますか?")
        if ans is None:
            return False
        if ans:
            return self.save()
        return True

    def open_files(self):
        """「開く」: 図面(PDF・画像)も編集ファイル(.annot)も、ここから開く。複数の図面もまとめて選べる。"""
        if not self.confirm_discard():
            return
        paths = filedialog.askopenfilenames(title="開く(図面・編集ファイル)", filetypes=FILETYPES_OPEN)
        if paths:
            self.open_paths(list(paths))

    def open_paths(self, paths):
        annots = [p for p in paths if p.lower().endswith(".annot")]
        if annots:  # 一緒に選ばれた図面は、編集ファイルの図面を探すときの手がかりにする
            self.load_annot(annots[0], hints=[p for p in paths if p != annots[0]])
            return
        drawings = [p for p in paths if is_drawing_path(p)]
        if not drawings:
            messagebox.showwarning(APP_NAME, "図面(PDF・画像)か、編集ファイル(.annot)を選んでください。")
            return
        self.open_new(drawings)

    def open_new(self, paths):
        srcs = self.read_sources(paths)
        if srcs is None:
            return
        self.use_sources(srcs)
        self.pages = {}
        self.annot_path = None
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.next_num = 1
        self.v_next.set("1")
        self.common = default_common()
        self.auto_defaults()
        self.go_page(0)
        self.set_dirty(False)

    def add_files(self):
        """「追加」: 開いている作業の最後のページの後ろに、図面(PDF・画像)を足す。編集ファイルは足せない。"""
        if self.base_img is None:
            self.open_files()
            return
        paths = filedialog.askopenfilenames(title="追加する図面を選択", filetypes=FILETYPES_DRAW)
        if not paths:
            return
        drawings = [p for p in paths if is_drawing_path(p)]
        skipped = len(paths) - len(drawings)
        if not drawings:
            messagebox.showwarning(APP_NAME, "追加できるのは図面(PDF・画像)です。編集ファイル(.annot)は追加できません。")
            return
        srcs = self.read_sources(drawings)
        if srcs is None:
            return
        self.push_undo()
        first = len(self.order)
        for src in srcs:
            idx = len(self.sources)
            self.sources.append(src)
            self.order.extend([idx, p] for p in range(src["page_count"]))
        self.page_count = len(self.order)
        self.go_page(first)
        msg = "%dページを最後に追加しました(全 %dページ)" % (self.page_count - first, self.page_count)
        if skipped:
            msg += "  ※図面以外の %d 件は追加していません" % skipped
        self.v_status.set(msg)

    def read_source(self, path):
        """図面ファイル1つを読み込んで、1つの「図面」にする。失敗したら理由を表示して None。"""
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext == ".pdf":
                if fitz is None:
                    messagebox.showerror(APP_NAME, "PDFを開くには PyMuPDF が必要です。\n\npip install pymupdf")
                    return None
                doc = fitz.open(path)
                return {"path": path, "type": "pdf", "pdf": doc, "img": None, "page_count": len(doc)}
            img = Image.open(path)
            img.load()
            return {"path": path, "type": "image", "pdf": None, "img": img.convert("RGB"), "page_count": 1}
        except Exception as e:
            messagebox.showerror(APP_NAME, "図面を開けませんでした。\n%s\n%s" % (os.path.basename(path), e))
            return None

    def read_sources(self, paths):
        out = []
        for p in paths:
            src = self.read_source(p)
            if src is None:
                self.close_sources(out)
                return None
            out.append(src)
        return out

    def close_sources(self, srcs):
        for src in srcs:
            if src.get("pdf") is not None:
                try:
                    src["pdf"].close()
                except Exception:
                    pass

    def use_sources(self, srcs, order=None):
        """図面を全部入れ替える(開き直し)。order を省くと、頭から全ページの並びになる。"""
        self.close_sources(self.sources)
        self.sources = srcs
        self.order = order or [[i, p] for i, src in enumerate(srcs) for p in range(src["page_count"])]
        self.page_count = len(self.order)
        self.src_path = srcs[0]["path"] if srcs else None

    def get_base(self, page):
        si, pi = self.order[page]
        src = self.sources[si]
        if src["type"] == "pdf":
            pix = src["pdf"][pi].get_pixmap(dpi=PDF_DPI, alpha=False)
            return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        return src["img"]

    def auto_defaults(self):
        base = self.get_base(0)
        fs = int(min(80, max(16, base.width / 80)))
        self.common["font_size"] = float(fs)
        self.common["length"] = float(fs * 3)
        self.common["width"] = float(max(2, round(fs / 7)))
        self.load_common_to_ui()

    def go_page(self, page):
        self.cur_page = max(0, min(self.page_count - 1, page))
        self.base_img = self.get_base(self.cur_page)
        self.sel = None
        self.sel_arrow = 0
        self.v_page.set("%d / %d" % (self.cur_page + 1, self.page_count))
        self.fit()
        self.refresh_table()
        self.update_title()
        si, pi = self.order[self.cur_page]
        src = self.sources[si]
        name = os.path.basename(src["path"])
        if src["page_count"] > 1:
            name += " (%d/%dページ)" % (pi + 1, src["page_count"])
        self.v_status.set("%s  (%d × %d px)" % (name, self.base_img.width, self.base_img.height))

    def change_page(self, d):
        if self.base_img is not None and 0 <= self.cur_page + d < self.page_count:
            self.go_page(self.cur_page + d)

    # ---- 表示 -----------------------------------------------------------
    def max_zoom(self):
        if self.base_img is None:
            return 8.0
        return min(8.0, math.sqrt(MAX_DISPLAY_PIXELS / (self.base_img.width * self.base_img.height)))

    def fit_zoom(self):
        """いまの画面の大きさで、全体表示にしたときの倍率"""
        self.root.update_idletasks()
        cw, ch = max(100, self.canvas.winfo_width()), max(100, self.canvas.winfo_height())
        x0, y0, x1, y1 = self.content_rect()
        return min(cw / (x1 - x0), ch / (y1 - y0)) * 0.95

    def fit(self):
        if self.base_img is None:
            return
        self.set_zoom(self.fit_zoom())

    # ---- 用紙 -----------------------------------------------------------
    def paper_layout(self, bw, bh):
        """用紙設定が有効なら、図面(bw×bh px)を用紙へ収める配置を返す。無効ならNone。
        s=図面の拡縮倍率, ox/oy=用紙上での図面左上位置(用紙px)。"""
        name = self.v_paper.get()
        if name not in PAPERS:
            return None
        wmm, hmm = PAPERS[name]
        o = self.v_orient.get()
        land = (o == "横") or (o == "自動" and bw >= bh)
        if land:
            wmm, hmm = hmm, wmm
        try:
            dpi = int(self.v_dpi.get())
            mm = float(self.v_margin.get())
        except ValueError:
            dpi, mm = 200, 10.0
        pw, ph = round(wmm / 25.4 * dpi), round(hmm / 25.4 * dpi)
        m = round(mm / 25.4 * dpi)
        s = min(max(1, pw - 2 * m) / bw, max(1, ph - 2 * m) / bh)
        ox, oy = round((pw - bw * s) / 2), round((ph - bh * s) / 2)
        return {"pw": pw, "ph": ph, "m": m, "s": s, "ox": ox, "oy": oy, "dpi": dpi,
                "name": name, "land": land}

    def paper_rects(self, lay):
        """用紙全体と印刷可能範囲(余白の内側)を画像座標で返す。"""
        s, ox, oy, m = lay["s"], lay["ox"], lay["oy"], lay["m"]
        paper = (-ox / s, -oy / s, (lay["pw"] - ox) / s, (lay["ph"] - oy) / s)
        area = ((m - ox) / s, (m - oy) / s, (lay["pw"] - m - ox) / s, (lay["ph"] - m - oy) / s)
        return paper, area

    def content_rect(self):
        w, h = self.base_img.width, self.base_img.height
        lay = self.paper_layout(w, h)
        if not lay:
            return 0, 0, w, h
        p = self.paper_rects(lay)[0]
        return min(0, p[0]), min(0, p[1]), max(w, p[2]), max(h, p[3])

    def on_paper_change(self, event=None):
        if self.base_img is None:
            return
        self.set_dirty(True)
        self.fit()
        lay = self.paper_layout(self.base_img.width, self.base_img.height)
        if lay:
            self.v_status.set("用紙 %s %s  %d×%d px (%d dpi)  図面は %d%% で配置" % (
                lay["name"], "横" if lay["land"] else "縦", lay["pw"], lay["ph"], lay["dpi"], round(lay["s"] * 100)))
        else:
            self.v_status.set("図面サイズのまま書き出します")

    def render_for_export(self, base_img, anns):
        lay = self.paper_layout(base_img.width, base_img.height)
        if not lay:
            return render_annotations(base_img, anns), None
        s = lay["s"]
        scaled = base_img.convert("RGB").resize((max(1, round(base_img.width * s)), max(1, round(base_img.height * s))), Image.LANCZOS)
        paper = Image.new("RGBA", (lay["pw"], lay["ph"]), (255, 255, 255, 255))
        paper.paste(scaled.convert("RGBA"), (lay["ox"], lay["oy"]))
        for a in anns:
            if is_placed(a):
                paste_annotation(paper, scale_ann(a, s, lay["ox"], lay["oy"]))
        return paper.convert("RGB"), lay["dpi"]

    def zoom_by(self, f):
        self.set_zoom(self.zoom * f)

    def set_zoom(self, z, anchor=None):
        if self.base_img is None:
            return
        z = max(0.03, min(self.max_zoom(), z))
        w, h = self.base_img.width, self.base_img.height
        ix = iy = None
        if anchor:
            ix, iy = self.canvas.canvasx(anchor[0]) / self.zoom, self.canvas.canvasy(anchor[1]) / self.zoom
        self.zoom = z
        size = (max(1, int(w * z)), max(1, int(h * z)))
        resample = Image.LANCZOS if z < 0.5 else Image.BILINEAR
        self.tk_img = ImageTk.PhotoImage(self.base_img.resize(size, resample))
        c = self.canvas
        c.delete("all")
        lay = self.paper_layout(w, h)
        if lay:
            (px0, py0, px1, py1), (ax0, ay0, ax1, ay1) = self.paper_rects(lay)
            c.create_rectangle(px0 * z, py0 * z, px1 * z, py1 * z, fill="white", outline="#222222", width=2, tags="paper")
        c.create_image(0, 0, image=self.tk_img, anchor="nw", tags="bg")
        if lay:
            c.create_rectangle(ax0 * z, ay0 * z, ax1 * z, ay1 * z, outline="#2F7FD8", dash=(6, 4), width=1, tags="paper")
        cx0, cy0, cx1, cy1 = self.content_rect()
        pad = 30
        sx0, sy0, sx1, sy1 = cx0 * z - pad, cy0 * z - pad, cx1 * z + pad, cy1 * z + pad
        c.configure(scrollregion=(sx0, sy0, sx1, sy1))
        if anchor:
            c.xview_moveto(max(0, (ix * z - anchor[0] - sx0) / (sx1 - sx0)))
            c.yview_moveto(max(0, (iy * z - anchor[1] - sy0) / (sy1 - sy0)))
        else:
            self.canvas.xview_moveto(0)
            self.canvas.yview_moveto(0)
        self.redraw_annotations()

    def on_wheel(self, e, direction=None):
        d = direction if direction is not None else (1 if e.delta > 0 else -1)
        if e.state & 0x4:  # Ctrl
            self.set_zoom(self.zoom * (1.15 if d > 0 else 1 / 1.15), anchor=(e.x, e.y))
        elif e.state & 0x1:  # Shift
            self.canvas.xview_scroll(-d * 3, "units")
        else:
            self.canvas.yview_scroll(-d * 3, "units")

    def redraw_annotations(self):
        c = self.canvas
        c.delete("ann")
        if self.base_img is None:
            return
        z = self.zoom
        for i, a in enumerate(self.anns()):
            if not is_placed(a):
                continue
            g = geometry(a)
            shapes = arrow_shapes(a, g)
            for k, col, sh in shapes:
                c.create_polygon([v * z for p in sh["shaft"] for v in p], fill=col, outline="", tags="ann")
                c.create_polygon([v * z for p in sh["head"] for v in p], fill=col, outline="", tags="ann")
            cx, cy, rx, ry = g["cx"] * z, g["cy"] * z, g["rx"] * z, g["ry"] * z
            c.create_oval(cx - rx, cy - ry, cx + rx, cy + ry, fill="#FFFFFF", outline=a["c_circle"],
                          width=max(1, g["ring"] * z), tags="ann")
            c.create_text(cx, cy, text=str(a["num"]), fill=a["c_text"],
                          font=("Arial", -max(1, int(round(a["font_size"] * z))), "bold"), tags="ann")
            if i == self.sel:
                m = g["ring"] * z / 2 + 4
                c.create_oval(cx - rx - m, cy - ry - m, cx + rx + m, cy + ry + m, outline="#FF8C00", dash=(4, 3), width=2, tags="ann")
                for k, col, sh in shapes:
                    tx, ty = sh["tip"][0] * z, sh["tip"][1] * z
                    if k == self.sel_arrow:  # 選んでいる矢印: オレンジの枠と大きな●
                        for key in ("shaft", "head"):
                            c.create_polygon([v * z for p in sh[key] for v in p], fill="", outline="#FF8C00", width=2, tags="ann")
                        c.create_oval(tx - 6, ty - 6, tx + 6, ty + 6, fill="#FF8C00", outline="white", width=2, tags="ann")
                    else:  # ほかの矢印: 小さな●(ここをクリックしてそのまま向きを変えられる)
                        c.create_oval(tx - 4, ty - 4, tx + 4, ty + 4, fill="white", outline="#FF8C00", width=2, tags="ann")

    # ---- マウス操作(図面) -----------------------------------------------
    def to_img(self, e):
        return self.canvas.canvasx(e.x) / self.zoom, self.canvas.canvasy(e.y) / self.zoom

    def hit_test(self, ix, iy, skip_sel=False):
        """クリック位置の注記を探す。戻り値 (注記の番号, "tip"|"shaft"|"circle", 矢印番号) または None。
        矢印番号: 0=1本目, 1以上=2本目以降, None=〇の部分(矢印の選択は変えない)。
        優先順位: ①選んでいる矢印の先端● ②選んでいる注記のほかの矢印の先端● ③選んでいる矢印の軸
                  ④選んでいる注記の〇 ⑤ほかの注記の〇(手前のもの) ⑥ほかの矢印(クリック位置にいちばん近いもの)
        skip_sel=True(Alt+クリック)は、選んでいる矢印・〇を除いて探すので、重なっている次の候補を選べる。"""
        lst = self.anns()
        tol = 6 / self.zoom
        sa = self.cur_ann()
        sel_ok = sa is not None and is_placed(sa) and not skip_sel

        def in_circle(g):
            ex = g["rx"] + g["ring"] / 2 + tol * 0.5
            ey = g["ry"] + g["ring"] / 2 + tol * 0.5
            return ((ix - g["cx"]) / ex) ** 2 + ((iy - g["cy"]) / ey) ** 2 <= 1

        def shaft_gap(sh):
            return dist_point_seg(ix, iy, sh["seg"]) - max(sh["width"], 1) / 2

        if sel_ok:
            g = geometry(sa)
            shapes = arrow_shapes(sa, g)
            for k, col, sh in sorted(shapes, key=lambda t: 0 if t[0] == self.sel_arrow else 1):
                if math.hypot(ix - sh["tip"][0], iy - sh["tip"][1]) <= 10 / self.zoom:
                    return self.sel, "tip", k
            for k, col, sh in shapes:
                if k == self.sel_arrow and shaft_gap(sh) <= tol:
                    return self.sel, "shaft", k
            if in_circle(g):
                return self.sel, "circle", None
        for i in range(len(lst) - 1, -1, -1):
            a = lst[i]
            if not is_placed(a) or i == self.sel:
                continue
            if in_circle(geometry(a)):
                return i, "circle", None
        best = None
        for i in range(len(lst) - 1, -1, -1):
            a = lst[i]
            if not is_placed(a):
                continue
            for k, col, sh in arrow_shapes(a, geometry(a)):
                if i == self.sel and k == self.sel_arrow and skip_sel:
                    continue
                d = shaft_gap(sh)
                if d <= tol and (best is None or d < best[0]):
                    best = (d, i, k)
        if best:
            return best[1], "shaft", best[2]
        return None

    def next_number_from_ui(self):
        try:
            return int(self.v_next.get())
        except ValueError:
            return self.next_num

    def start_hit_drag(self, ix, iy, alt=False):
        """既存の注記の上なら、選択して移動/矢印先端ドラッグを始める(Trueを返す)"""
        hit = self.hit_test(ix, iy, skip_sel=alt)
        if not hit:
            return False
        idx, part, k = hit
        self.remove_ghost()
        if k is None:  # 〇をクリック: 同じ注記なら選んでいる矢印はそのまま
            k = self.sel_arrow if idx == self.sel else 0
        self.sel, self.sel_arrow = idx, k
        a = self.anns()[idx]
        # 〇をドラッグ=注記の移動 / 矢印の軸・先端の●をドラッグ=矢印の向きの調整(注記は動かない)
        mode = {"tip": "tip", "shaft": "shaft"}.get(part, "move")
        self.drag = {"mode": mode, "idx": idx, "arrow": k, "x0": ix, "y0": iy,
                     "dx": a["x"] - ix, "dy": a["y"] - iy, "pushed": False}
        self.redraw_annotations()
        self.sync_table_sel()
        return True

    def on_press(self, e):
        """シングルクリック: 注記の選択・移動。何もない所では配置しない(配置はダブルクリック)"""
        self.canvas.focus_set()
        if self.base_img is None:
            return
        ix, iy = self.to_img(e)
        if self.start_hit_drag(ix, iy, is_alt(e.state)):  # Alt+クリック: 重なっている次の候補を選ぶ
            return
        cur = self.cur_ann()
        if cur is not None and not is_placed(cur):
            return  # 表で選んだ未配置の行は、ダブルクリックで配置するので選択を残す
        if self.sel is not None:
            self.select(None)

    def on_double(self, e):
        """ダブルクリック: 新規配置(ダブルクリックのまま押し続けてドラッグすると矢印の向き)"""
        self.canvas.focus_set()
        if self.base_img is None:
            return
        ix, iy = self.to_img(e)
        if self.start_hit_drag(ix, iy, is_alt(e.state)):
            return
        self.remove_ghost()
        cur = self.cur_ann()
        if cur is not None and not is_placed(cur):
            # 表で選んでいる未配置の行を、ダブルクリックした位置に配置する
            self.push_undo()
            cur["x"], cur["y"] = ix, iy
            self.sel_arrow = 0
            cur["arrow"] = True
            cur["angle"] = DEFAULT_ANGLE
            if not str(cur["num"]).strip():
                # 番号が空のときだけ自動採番(自分で入力した番号は「次の番号」に影響させない)
                n = self.next_number_from_ui()
                cur["num"] = str(n)
                self.next_num = n + 1
                self.v_next.set(str(self.next_num))
            self.drag = {"mode": "new", "idx": self.sel, "x0": ix, "y0": iy, "pushed": True}
            self.redraw_annotations()
            self.refresh_table()
            return
        if not self.place_mode.get():
            self.select(None)
            return
        self.push_undo()
        n = self.next_number_from_ui()
        a = make_ann(self.common, ix, iy, str(n), DEFAULT_ANGLE, True)
        self.anns().append(a)
        self.sel = len(self.anns()) - 1
        self.sel_arrow = 0
        self.next_num = n + 1
        self.v_next.set(str(self.next_num))
        self.drag = {"mode": "new", "idx": self.sel, "x0": ix, "y0": iy, "pushed": True}
        self.redraw_annotations()
        self.refresh_table()

    # ---- 次に配置される〇+番号を、マウスカーソルの代わりに表示する ----
    def remove_ghost(self):
        try:
            self.canvas.delete("ghost")
            self.canvas.configure(cursor="")
        except tk.TclError:
            pass

    def ghost_ann(self, ix, iy):
        cur = self.cur_ann()
        if cur is not None and not is_placed(cur):
            num = str(cur["num"]).strip() or str(self.next_number_from_ui())
            g = make_ann(self.common, ix, iy, num, DEFAULT_ANGLE, True)
            for k in COMMON_KEYS:
                g[k] = cur[k]
            return g
        return make_ann(self.common, ix, iy, str(self.next_number_from_ui()), DEFAULT_ANGLE, True)

    def update_ghost(self, e):
        if self.base_img is None or self.drag or not self.place_mode.get():
            self.remove_ghost()
            return
        ix, iy = self.to_img(e)
        if self.hit_test(ix, iy):  # 既存の注記の上では、通常のカーソルに戻す(選択・移動ができる)
            self.remove_ghost()
            return
        a = self.ghost_ann(ix, iy)
        c, z = self.canvas, self.zoom
        c.delete("ghost")
        g = geometry(a)
        if g["shaft"]:
            c.create_polygon([v * z for p in g["shaft"] for v in p], fill=a["c_arrow"], outline="", tags="ghost")
            c.create_polygon([v * z for p in g["head"] for v in p], fill=a["c_arrow"], outline="", tags="ghost")
        cx, cy, rx, ry = g["cx"] * z, g["cy"] * z, g["rx"] * z, g["ry"] * z
        c.create_oval(cx - rx, cy - ry, cx + rx, cy + ry, fill="#FFFFFF", outline=a["c_circle"],
                      width=max(1, g["ring"] * z), tags="ghost")
        c.create_text(cx, cy, text=str(a["num"]), fill=a["c_text"],
                      font=("Arial", -max(1, int(round(a["font_size"] * z))), "bold"), tags="ghost")
        c.configure(cursor="none")

    def aim(self, ann, arrow, ix, iy, keep_len=False):
        """矢印arrow(1本目ならann自身、2本目以降ならann["extra"]の要素)を、(ix,iy)の向きにする"""
        g = geometry({**ann, "arrow": False, "extra": []})
        dx, dy = ix - ann["x"], iy - ann["y"]
        if arrow is ann:
            ann["arrow"] = True
        arrow["angle"] = math.degrees(math.atan2(-dy, dx)) % 360
        if not keep_len:
            dist = math.hypot(dx, dy) or 1.0
            edge = ellipse_dist(g["rx"] + g["ring"] / 2, g["ry"] + g["ring"] / 2, dx / dist, dy / dist)
            arrow["length"] = max(5.0, dist - edge)

    def on_motion(self, e):
        d = self.drag
        if not d or self.base_img is None:
            return
        lst = self.anns()
        if d["idx"] >= len(lst):
            return
        a = lst[d["idx"]]
        ix, iy = self.to_img(e)
        if d["mode"] == "move":
            if not d["pushed"]:
                self.push_undo()
                d["pushed"] = True
            a["x"], a["y"] = ix + d["dx"], iy + d["dy"]
        elif d["mode"] in ("tip", "shaft"):
            arrow = self.drag_arrow(d)
            if arrow is None:
                return
            if d["mode"] == "shaft":  # 軸をつかんだときは、少し動かしてから向きを変える(クリックだけなら選択のみ)
                if math.hypot(ix - d["x0"], iy - d["y0"]) * self.zoom < 4 and not d.get("active"):
                    return
                d["active"] = True
            if not d["pushed"]:
                self.push_undo()
                d["pushed"] = True
            # 先端の●をつかんだときは、●がマウスに付いてくる(長さも変わる)。軸をつかんだとき・Shiftを押しているときは、長さを保つ
            self.aim(a, arrow, ix, iy, keep_len=(d["mode"] == "shaft") or bool(e.state & 0x1))
        elif d["mode"] == "new":
            if math.hypot(ix - d["x0"], iy - d["y0"]) * self.zoom >= 8:
                self.aim(a, a, ix, iy, keep_len=a["common"])
        self.redraw_annotations()

    def drag_arrow(self, d):
        """ドラッグ中の矢印(1本目なら注記自身、2本目以降なら extra の要素)。なければNone"""
        lst = self.anns()
        if d["idx"] >= len(lst):
            return None
        a = lst[d["idx"]]
        k = d.get("arrow", 0)
        if k == 0:
            return a
        return a["extra"][k - 1] if k - 1 < len(a["extra"]) else None

    def on_release(self, e):
        d = self.drag
        if d:
            if d["mode"] == "tip" and d.get("pushed"):
                arrow = self.drag_arrow(d)
                if arrow is not None and arrow["common"]:  # 共通ONの矢印は、離すと長さが共通値に戻る(向きは残る)
                    arrow["length"] = self.common["length"]
                    self.redraw_annotations()
            self.refresh_table()
        self.drag = None

    def on_hover(self, e):
        if self.base_img is not None and not self.drag:
            ix, iy = self.to_img(e)
            self.v_status.set("x=%d  y=%d   倍率 %d%%" % (ix, iy, round(self.zoom * 100)))
        self.update_ghost(e)

    def select(self, idx):
        self.sel = idx
        self.sel_arrow = 0
        self.redraw_annotations()
        self.sync_table_sel()

    def on_delete_key(self, e):
        if isinstance(self.root.focus_get(), (tk.Entry, ttk.Entry, tk.Spinbox, ttk.Spinbox)):
            return
        self.delete_selected()

    def delete_selected(self):
        a = self.cur_ann()
        if a is None:
            return
        if 1 <= self.sel_arrow <= len(a["extra"]):  # 2本目以降の矢印を選んでいるときは、その矢印だけ消す
            self.push_undo()
            del a["extra"][self.sel_arrow - 1]
            self.sel_arrow = 0
            self.refresh_table()
            self.redraw_annotations()
            self.v_status.set("矢印を削除しました")
            return
        self.push_undo()
        del self.anns()[self.sel]
        self.sel = None
        self.sel_arrow = 0
        self.refresh_table()
        self.redraw_annotations()

    def add_arrow(self):
        """選んでいる注記に、矢印を1本追加する(向きは既存の矢印と重ならない向き。長さ・太さ・色は共通値)"""
        a = self.cur_ann()
        if a is None or not is_placed(a):
            messagebox.showinfo(APP_NAME, "配置済みの注記を選んでから実行してください。")
            return
        angles = ([a["angle"]] if a.get("arrow") else []) + [e["angle"] for e in a["extra"]]
        self.push_undo()
        a["extra"].append(make_arrow(self.common, free_angle(angles)))
        self.sel_arrow = len(a["extra"])
        self.refresh_table()
        self.redraw_annotations()
        self.v_status.set("矢印を追加しました(矢印%d)。先端の●をドラッグして向きを変えられます" % (self.sel_arrow + 1))

    def on_rpress(self, e):
        self._rp = (e.x, e.y)

    def on_rrelease(self, e):
        """右クリック(動かさずに離したとき)でメニューを出す。右ボタンのドラッグは、これまでどおり画面の移動"""
        rp, self._rp = getattr(self, "_rp", None), None
        if rp is None or self.base_img is None or math.hypot(e.x - rp[0], e.y - rp[1]) > 4:
            return
        ix, iy = self.to_img(e)
        hit = self.hit_test(ix, iy)
        if not hit:
            return
        idx, part, k = hit
        if k is None:
            k = self.sel_arrow if idx == self.sel else 0
        self.sel, self.sel_arrow = idx, k
        self.redraw_annotations()
        self.sync_table_sel()
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="矢印を追加", command=self.add_arrow)
        m.add_command(label="この矢印を削除" if k >= 1 else "この注記を削除", command=self.delete_selected)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    # ---- 注記一覧(表) ---------------------------------------------------
    def cancel_edit(self):
        if self._edit is not None:
            ent, self._edit = self._edit, None
            try:
                ent.destroy()
            except tk.TclError:
                pass

    def tree_of(self, cid):
        return self.tree_l if cid in TREE_IDS[:TREE_FIXED] else self.tree_r

    def yview_both(self, *args):
        self.tree_l.yview(*args)
        self.tree_r.yview(*args)

    def on_yscroll(self, src, first, last):
        """どちらの表が縦にスクロールしても、もう一方と縦スクロールバーを同じ位置にそろえる"""
        if self._ysync:
            return
        self._ysync = True
        try:
            self.vsb.set(first, last)
            other = self.tree_r if src is self.tree_l else self.tree_l
            other.yview_moveto(first)
        finally:
            self._ysync = False

    def on_sash_drag(self, e):
        x0, w0 = self._sash0
        w = max(240, min(self.root.winfo_width() - 300, w0 - (e.x_root - x0)))
        self.panel.configure(width=w)

    def toggle_sort(self):
        self.sort_mode = (self.sort_mode + 1) % 3
        self.btn_sort.configure(text="番号\n" + SORT_MARK[self.sort_mode])
        self.refresh_table()

    def place_sort_button(self):
        """「番号」の見出しの上に、並べ替えボタンを重ねる(見出しと同じ高さ)"""
        t = self.tree_l
        t.update_idletasks()
        h = 0
        for y in range(0, 100):
            if t.identify_region(5, y) == "heading":
                h = y + 1
            elif h:
                break
        if h < 20:
            h = 40
        self.btn_sort.place(x=int(t.column("com", "width")), y=0, width=int(t.column("num", "width")), height=h)

    def refresh_table(self):
        self.cancel_edit()
        info = analyze_numbers(self.pages)
        self.v_summary.set(summary_text(info))
        for t in (self.tree_l, self.tree_r):
            t.delete(*t.get_children())
        self._row_tags = {}
        self._sel_iid = None
        rows = [(pg, i, a) for pg in sorted(self.pages) for i, a in enumerate(self.pages[pg])]
        for pg, i, a in sort_rows(rows, self.sort_mode):
            if not is_placed(a):
                status, tags = "未配置", ("unplaced",)
            elif (pg, i) in info["dup_rows"]:
                status, tags = "重複", ("dup",)
            else:
                status, tags = "", ()
            self.insert_table_row("%d:%d" % (pg, i), table_row_values(pg, a, status), tags)
            for k, e in enumerate(a.get("extra", []), 1):  # 2本目以降の矢印は、注記の下の子行
                self.insert_table_row("%d:%d:%d" % (pg, i, k), table_child_values(pg, e, k), ("child",))
        self.sync_table_sel()

    def insert_table_row(self, iid, vals, tags):
        self._row_tags[iid] = tags
        self.tree_l.insert("", "end", iid=iid, values=vals[:TREE_FIXED], tags=tags)
        self.tree_r.insert("", "end", iid=iid, values=vals[TREE_FIXED:], tags=tags)

    def sync_table_sel(self):
        """図面で選んだ注記(矢印)に対応する表の行を、オレンジで目立たせる"""
        iid = None
        if self.sel is not None:
            iid = "%d:%d" % (self.cur_page, self.sel)
            if self.sel_arrow:
                iid += ":%d" % self.sel_arrow
            if iid not in self._row_tags:
                iid = "%d:%d" % (self.cur_page, self.sel)
        old = self._sel_iid
        for t in (self.tree_l, self.tree_r):
            if old and old != iid and t.exists(old):
                t.item(old, tags=self._row_tags.get(old, ()))
            if iid and t.exists(iid):
                t.item(iid, tags=("selrow",))
                t.selection_set(iid)
                t.see(iid)
            else:
                t.selection_remove(*t.selection())
        self._sel_iid = iid if (iid and self.tree_l.exists(iid)) else None

    def parse_iid(self, iid):
        """行のid → (ページ, 注記の番号, 矢印番号)。矢印番号は 0=注記の行(1本目), 1以上=子行(2本目以降)"""
        parts = iid.split(":")
        return int(parts[0]), int(parts[1]), (int(parts[2]) if len(parts) > 2 else 0)

    def select_row(self, pg, idx, k=0):
        if self.base_img is None:
            return
        if pg != self.cur_page:
            self.go_page(pg)
        self.sel = idx
        self.sel_arrow = k
        self.redraw_annotations()
        self.sync_table_sel()
        a = self.cur_ann()
        if a is not None and not is_placed(a):
            self.v_status.set("未配置の行です。番号を入力して(空なら自動採番)、図面をダブルクリックすると配置されます")

    def row_at(self, event):
        t = event.widget
        if t not in self.col_ids or t.identify_region(event.x, event.y) != "cell":
            return None, None
        iid = t.identify_row(event.y)
        col = t.identify_column(event.x)
        if not iid or not col:
            return None, None
        return iid, self.col_ids[t][int(col[1:]) - 1]

    def on_tree_click(self, event):
        self.cancel_edit()
        iid, cid = self.row_at(event)
        if not iid:
            return
        pg, idx, k = self.parse_iid(iid)
        self.select_row(pg, idx, k)
        a = self.cur_ann()
        if a is None:
            return "break"
        if k >= 1:
            if cid == "com" and k <= len(a["extra"]):
                self.toggle_arrow_common(a["extra"][k - 1])
            return "break"
        if cid == "com":
            self.toggle_common(a)
        elif cid == "arw" and is_placed(a):
            self.push_undo()
            a["arrow"] = not a["arrow"]
            self.refresh_table()
            self.redraw_annotations()
        return "break"

    def toggle_common(self, a):
        self.push_undo()
        a["common"] = not a["common"]
        if a["common"]:  # 共通に戻すと、共通値に揃う
            for k in COMMON_KEYS:
                a[k] = self.common[k]
        self.refresh_table()
        self.redraw_annotations()
        self.v_status.set("共通 " + ("ON: 共通ステータスの値に揃えました" if a["common"] else "OFF: 一律更新の対象から外れ、個別に編集できます"))

    def toggle_arrow_common(self, e):
        self.push_undo()
        e["common"] = not e["common"]
        if e["common"]:
            for k in ARROW_KEYS:
                e[k] = self.common[k]
        self.refresh_table()
        self.redraw_annotations()
        self.v_status.set("矢印の共通 " + ("ON: 共通ステータスの長さ・太さ・色に揃えました" if e["common"] else "OFF: 一律更新の対象から外れ、個別に編集できます"))

    def on_tree_dbl(self, event):
        iid, cid = self.row_at(event)
        if not iid:
            return "break"
        pg, idx, k = self.parse_iid(iid)
        self.select_row(pg, idx, k)
        a = self.cur_ann()
        if a is None:
            return "break"
        if k >= 1:  # 子行(2本目以降の矢印): 長さ・太さ・色は共通OFFのときだけ、角度はいつでも編集できる
            if k > len(a["extra"]):
                return "break"
            e = a["extra"][k - 1]
            if cid in ("ln", "wd", "ca") and e["common"]:
                self.v_status.set("共通がONのため編集できません。「共通」列をクリックしてOFFにしてください")
            elif cid == "ca":
                self.popup_palette(self.tree_r, e["c_arrow"], lambda hx, ee=e: self.set_arrow_color(ee, hx),
                                   pos=(event.x_root, event.y_root))
            elif cid in ("ln", "wd", "ang"):
                self.edit_cell(iid, cid)
            return "break"
        if cid in ("com", "arw", "pg", "st"):
            return "break"
        if cid in COMMON_CELLS and a["common"]:
            self.v_status.set("共通がONのため編集できません。「共通」列をクリックしてOFFにしてください")
            return "break"
        if cid == "ang" and not is_placed(a):
            return "break"
        if cid in ("ct", "cc", "ca"):
            key = COMMON_CELLS[cid]
            self.popup_palette(self.tree_r, a[key], lambda hx, k=key, aa=a: self.set_color_value(aa, k, hx),
                               pos=(event.x_root, event.y_root))
            return "break"
        self.edit_cell(iid, cid)
        return "break"

    def edit_cell(self, iid, cid):
        tree = self.tree_of(cid)
        bbox = tree.bbox(iid, cid)
        if not bbox:
            return
        pg, idx, k = self.parse_iid(iid)
        cur = tree.set(iid, cid)
        self.cancel_edit()
        x, y, w, h = bbox
        ent = ttk.Entry(tree, justify="center")
        ent.insert(0, cur)
        ent.select_range(0, "end")
        ent.place(x=x, y=y, width=w, height=h)
        ent.focus_set()
        self._edit = ent

        def commit(ev=None):
            if self._edit is not ent:
                return
            text = ent.get()
            self._edit = None
            ent.destroy()
            self.set_cell(pg, idx, cid, text, k)

        ent.bind("<Return>", commit)
        ent.bind("<KP_Enter>", commit)
        ent.bind("<FocusOut>", commit)
        ent.bind("<Escape>", lambda ev: self.cancel_edit())

    def set_cell(self, pg, idx, cid, text, k=0):
        if pg not in self.pages or idx >= len(self.pages[pg]):
            return
        a = self.pages[pg][idx]
        text = text.strip()
        if k >= 1:  # 子行(2本目以降の矢印)
            if k > len(a["extra"]):
                return
            e = a["extra"][k - 1]
            try:
                if cid == "ang":
                    key, v = "angle", float(text) % 360
                elif cid in ("ln", "wd") and not e["common"]:
                    key = COMMON_CELLS[cid]
                    lo, hi = CLAMPS[key]
                    v = min(hi, max(lo, float(text)))
                else:
                    return
            except ValueError:
                return
            if v == e[key]:
                return
            self.push_undo()
            e[key] = v
            self.refresh_table()
            self.redraw_annotations()
            return
        if cid == "num":
            if text == str(a["num"]):
                return
            self.push_undo()
            a["num"] = text
        elif cid == "ang":
            try:
                v = float(text) % 360
            except ValueError:
                return
            if v == a["angle"]:
                return
            self.push_undo()
            a["angle"] = v
        elif cid in ("fs", "ln", "wd"):
            key = COMMON_CELLS[cid]
            if a["common"]:
                return
            try:
                lo, hi = CLAMPS[key]
                v = min(hi, max(lo, float(text)))
            except ValueError:
                return
            if v == a[key]:
                return
            self.push_undo()
            a[key] = v
        else:
            return
        self.refresh_table()
        self.redraw_annotations()

    def set_arrow_color(self, e, hx):
        if e["common"] or e["c_arrow"].lower() == hx.lower():
            return
        self.push_undo()
        e["c_arrow"] = hx
        self.refresh_table()
        self.redraw_annotations()

    def set_color_value(self, a, key, hx):
        if a["common"] or a[key].lower() == hx.lower():
            return
        self.push_undo()
        a[key] = hx
        self.refresh_table()
        self.redraw_annotations()

    def on_tree_menu(self, event):
        iid, cid = self.row_at(event)
        if not iid:
            return
        pg, idx, k = self.parse_iid(iid)
        self.select_row(pg, idx, k)
        m = tk.Menu(self.root, tearoff=0)
        a = self.cur_ann()
        if a is not None and is_placed(a):
            m.add_command(label="矢印を追加", command=self.add_arrow)
        m.add_command(label="下に行を挿入", command=self.insert_row_below)
        m.add_command(label="この矢印を削除" if k >= 1 else "この行を削除", command=self.delete_selected)
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def insert_row_below(self):
        """選択行の下に未配置の行を挿入する。共通部分以外(番号を含む)は空。"""
        if self.cur_ann() is None:
            messagebox.showinfo(APP_NAME, "表で行を選んでから実行してください。")
            return
        if self.sort_mode:  # 並べ替え中は、表示順と挿入位置がずれるため、元の順に戻してから挿入する
            self.sort_mode = 0
            self.btn_sort.configure(text="番号\n" + SORT_MARK[0])
        self.push_undo()
        i = self.sel + 1
        self.anns().insert(i, make_ann(self.common))
        self.sel = i
        self.sel_arrow = 0
        self.refresh_table()
        self.redraw_annotations()
        self.v_status.set("行を挿入しました。番号をダブルクリックで入力し、図面をダブルクリックすると配置されます(空なら自動採番)")

    def renumber(self):
        if self.base_img is None:
            messagebox.showinfo(APP_NAME, "先に図面を開いてください。")
            return
        top = tk.Toplevel(self.root)
        top.title("番号の振り直し")
        top.transient(self.root)
        top.resizable(False, False)
        top.geometry("+%d+%d" % (self.root.winfo_rootx() + 260, self.root.winfo_rooty() + 160))
        ttk.Label(top, justify="left", padding=10, text=(
            "番号の振り直し方を選んでください(「元に戻す」で戻せます)。\n\n"
            "・欠番を詰める: 番号の順序・重複・複数番号はそのままで、抜けた番号だけ詰めます\n"
            "・表の並び順に連番: いまの表の並び順に、配置済みの注記へ1から付け直します(重複はなくなります)")).pack()

        def run(mode):
            top.destroy()
            self.do_renumber(mode)

        ttk.Button(top, text="欠番を詰める", command=lambda: run("compact")).pack(fill="x", padx=10, pady=3)
        ttk.Button(top, text="表の並び順に1から連番", command=lambda: run("seq")).pack(fill="x", padx=10, pady=3)
        ttk.Button(top, text="キャンセル", command=top.destroy).pack(fill="x", padx=10, pady=(3, 10))

    def do_renumber(self, mode):
        rows = [(pg, i, a) for pg in sorted(self.pages) for i, a in enumerate(self.pages[pg])]
        order = [(pg, i) for pg, i, a in sort_rows(rows, self.sort_mode)]
        changes = compute_renumber(self.pages, mode, order)
        if not changes:
            self.v_status.set("振り直しの必要がありませんでした(変更なし)")
            return
        self.push_undo()
        for pg, i, text in changes:
            self.pages[pg][i]["num"] = text
        top = 0
        for lst in self.pages.values():
            for a in lst:
                ints = [int(k) for k in num_keys(a["num"]) if k.isdigit()]
                top = max([top] + ints)
        self.next_num = top + 1
        self.v_next.set(str(self.next_num))
        self.refresh_table()
        self.redraw_annotations()
        self.v_status.set("番号を振り直しました(%d件)。次の番号は %d です" % (len(changes), self.next_num))

    # ---- 保存・読込 -----------------------------------------------------
    def save(self):
        if not self.sources:
            messagebox.showinfo(APP_NAME, "先に図面を開いてください。")
            return False
        if not self.annot_path:
            return self.save_as()
        return self.write_annot(self.annot_path)

    def save_as(self):
        if not self.sources:
            return False
        init = os.path.splitext(os.path.basename(self.src_path))[0] + ".annot"
        path = filedialog.asksaveasfilename(title="編集ファイルを保存", defaultextension=".annot", initialfile=init,
                                            initialdir=os.path.dirname(self.src_path),
                                            filetypes=[("図面注記ファイル", "*.annot")])
        if not path:
            return False
        return self.write_annot(path)

    def build_annot_data(self, path):
        used, order_out, natural = annot_layout(self.sources, self.order)
        base = os.path.dirname(os.path.abspath(path))
        srcs = []
        for i in used:
            sp = self.sources[i]["path"]
            try:
                rel = os.path.relpath(sp, base)
            except ValueError:
                rel = None
            srcs.append({"path": os.path.abspath(sp), "rel": rel, "type": self.sources[i]["type"]})
        data = {"version": 2 if natural else 3,
                "source": srcs[0],
                "pdf_dpi": PDF_DPI,
                "current_page": self.cur_page,
                "common": self.common,
                "next_num": self.next_num,
                "paper": {"size": self.v_paper.get(), "orient": self.v_orient.get(),
                          "margin": self.v_margin.get(), "dpi": self.v_dpi.get()},
                "pages": {str(k): v for k, v in self.pages.items()}}
        if not natural:  # 図面が複数、または並びが変わっているとき(旧版は読めない)
            data["sources"] = srcs
            data["page_order"] = order_out
        return data

    def write_annot(self, path):
        data = self.build_annot_data(path)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
        except Exception as e:
            messagebox.showerror(APP_NAME, "保存できませんでした。\n%s" % e)
            return False
        self.annot_path = path
        self.set_dirty(False)
        self.v_status.set("保存しました: %s" % path)
        return True

    def resolve_sources(self, annot_path, infos, hints):
        """編集ファイルが使う図面の場所を決める。編集ファイルからの相対 → 絶対パス → 一緒に選ばれたファイル(名前が同じもの)
        の順に探し、なければ選んでもらう。取り消されたら None。"""
        base = os.path.dirname(os.path.abspath(annot_path))
        pool = [h for h in hints if is_drawing_path(h)]
        found, wants = [], []
        for si in infos:
            si = si if isinstance(si, dict) else {}
            cands = []
            if si.get("rel"):
                cands.append(os.path.normpath(os.path.join(base, si["rel"])))
            if si.get("path"):
                cands.append(si["path"])
            wants.append(os.path.basename(si.get("rel") or si.get("path") or ""))
            found.append(next((c for c in cands if os.path.isfile(c)), None))

        def assign():
            for i, w in enumerate(wants):
                if found[i] is None:
                    j = next((k for k, p in enumerate(pool) if os.path.basename(p) == w), None)
                    if j is not None:
                        found[i] = pool.pop(j)
            un = [i for i, f in enumerate(found) if f is None]
            if un and len(pool) == len(un):  # 名前が違っても、数がぴったりなら順に使う
                for i in un:
                    found[i] = pool.pop(0)

        assign()
        while any(f is None for f in found):
            missing = [w for w, f in zip(wants, found) if f is None and w]
            messagebox.showwarning(APP_NAME, "元の図面が見つかりません。\n\n%s\n\n図面ファイルを選んでください(複数選べます)。" % (
                "\n".join("・" + w for w in missing) if missing else "(名前の記録なし)"))
            picked = filedialog.askopenfilenames(title="元の図面を選択", filetypes=FILETYPES_DRAW)
            if not picked:
                return None
            pool.extend(p for p in picked if is_drawing_path(p))
            assign()
        return found

    def load_annot(self, path, hints=()):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            messagebox.showerror(APP_NAME, "読み込めませんでした。\n%s" % e)
            return
        # 図面は、version 2 なら source の1つだけ、version 3 なら sources の全部
        infos = data["sources"] if isinstance(data.get("sources"), list) and data["sources"] else [data.get("source", {})]
        found = self.resolve_sources(path, infos, list(hints))
        if found is None:
            return
        srcs = self.read_sources(found)
        if srcs is None:
            return
        self.use_sources(srcs, valid_page_order(data.get("page_order"), [x["page_count"] for x in srcs]))
        self.pages, self.common = migrate_loaded(data)
        self.next_num = int(data.get("next_num", 1))
        self.v_next.set(str(self.next_num))
        pp = data.get("paper", {})
        if pp.get("size") in PAPERS or pp.get("size") == NO_PAPER:
            self.v_paper.set(pp["size"])
        if pp.get("orient") in ORIENTS:
            self.v_orient.set(pp["orient"])
        if str(pp.get("margin")) in MARGINS:
            self.v_margin.set(str(pp["margin"]))
        if str(pp.get("dpi")) in DPIS:
            self.v_dpi.set(str(pp["dpi"]))
        self.annot_path = path
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.load_common_to_ui()
        self.go_page(int(data.get("current_page", 0)))
        self.set_dirty(False)
        for info, f in zip(infos, found):
            want = os.path.basename((info.get("rel") or info.get("path") or "") if isinstance(info, dict) else "")
            if want and want != os.path.basename(f):
                self.v_status.set("※編集ファイルの元の図面は「%s」ですが、「%s」を使っています" % (want, os.path.basename(f)))
                break

    # ---- PNG書き出し ----------------------------------------------------
    def export_png(self):
        if self.base_img is None:
            return
        stem = os.path.splitext(os.path.basename(self.src_path))[0]
        if self.page_count > 1:
            stem += "_p%d" % (self.cur_page + 1)
        path = filedialog.asksaveasfilename(title="PNGで書き出し", defaultextension=".png", initialfile=stem + "_注記.png",
                                            filetypes=[("PNG画像", "*.png")])
        if not path:
            return
        img, dpi = self.render_for_export(self.base_img, self.anns())
        self.save_png(img, path, dpi)
        self.v_status.set("書き出しました: %s (%d×%d px)" % (path, img.width, img.height))

    def save_png(self, img, path, dpi):
        if dpi:
            img.save(path, dpi=(dpi, dpi))
        else:
            img.save(path)

    def export_all(self):
        if self.base_img is None:
            return
        folder = filedialog.askdirectory(title="書き出し先フォルダを選択")
        if not folder:
            return
        stem = os.path.splitext(os.path.basename(self.src_path))[0]
        for p in range(self.page_count):
            img, dpi = self.render_for_export(self.get_base(p), self.pages.get(p, []))
            self.save_png(img, os.path.join(folder, "%s_p%d_注記.png" % (stem, p + 1)), dpi)
        self.v_status.set("%dページを書き出しました: %s" % (self.page_count, folder))

    def on_close(self):
        if self.confirm_discard():
            self.root.destroy()

    def run(self):
        paths = [a for a in sys.argv[1:] if os.path.exists(a)]
        if paths:  # ファイルを重ねて起動(ドラッグ&ドロップ)したとき
            self.root.after(200, lambda: self.open_paths(paths))
        self.root.mainloop()


def run_selftest(out_path):
    """exe化したあとの動作確認用(--selftest 出力ファイル)。
    Tk・Pillow(描画)・PyMuPDF(PDFの描画と保存)が使えるかを調べ、結果をファイルに書く。通常の起動では使われない。"""
    lines = []
    state = {"ok": True}

    def check(name, fn):
        try:
            lines.append("OK  %s: %s" % (name, fn()))
        except Exception:
            state["ok"] = False
            lines.append("NG  %s\n%s" % (name, traceback.format_exc()))

    def t_tk():
        r = tk.Tk()
        r.withdraw()
        ver = r.tk.call("info", "patchlevel")
        r.destroy()
        return "Tcl/Tk %s" % ver

    def t_pillow():
        img = Image.new("RGB", (240, 140), "white")
        a = make_ann(default_common(), 120, 70, "12", 45, True)
        a["extra"].append(make_arrow(default_common(), 200))
        out = render_annotations(img, [a])
        if out.getextrema() == ((255, 255), (255, 255), (255, 255)):
            raise RuntimeError("注記が描かれていません")
        return "注記の描画 %dx%d, フォント=%s" % (out.width, out.height, _font_path)

    def t_pdf():
        if fitz is None:
            raise RuntimeError("PyMuPDF(fitz)を読み込めません")
        doc = fitz.open()
        page = doc.new_page(width=200, height=100)
        page.draw_rect(fitz.Rect(20, 20, 180, 80))
        pix = page.get_pixmap(dpi=72, alpha=False)
        Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        tmp = out_path + ".tmp.pdf"
        doc.save(tmp)
        doc.close()
        d2 = fitz.open(tmp)
        n = len(d2)
        d2.close()
        os.remove(tmp)
        return "PDFの描画 %dx%d, 保存→再読込 %dページ, PyMuPDF %s" % (
            pix.width, pix.height, n, getattr(fitz, "VersionBind", "?"))

    lines.append("frozen=%s  python=%s" % (getattr(sys, "frozen", False), sys.version.split()[0]))
    check("Tk", t_tk)
    check("Pillow", t_pillow)
    check("PDF", t_pdf)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return 0 if state["ok"] else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _i = sys.argv.index("--selftest")
        _p = sys.argv[_i + 1] if _i + 1 < len(sys.argv) else "selftest.txt"
        try:
            _code = run_selftest(_p)
        except Exception:
            _code = 1
        sys.exit(_code)
    try:
        App().run()
    except Exception as _e:
        _msg = traceback.format_exc()
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "zumen_annot_error.log"),
                      "a", encoding="utf-8") as _f:
                _f.write(time.strftime("[%Y-%m-%d %H:%M:%S]\n") + _msg + "\n")
        except Exception:
            pass
        _r = tk.Tk()
        _r.withdraw()
        messagebox.showerror(APP_NAME, "起動時にエラーが発生しました。\n\n%s" % _e)
