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
import sys
import time
import traceback
import tkinter as tk
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

# 名前付きの基本色パレット
PALETTE = [
    ("黒", "#000000"), ("白", "#FFFFFF"), ("赤", "#FF0000"), ("青", "#0000FF"),
    ("緑", "#00A000"), ("黄", "#FFD400"), ("橙", "#FF8000"), ("紫", "#8000C0"),
    ("水色", "#00B4F0"), ("桃", "#FF50A0"), ("灰", "#808080"), ("茶", "#804000"),
]
COLOR_FIELDS = [("c_text", "数字"), ("c_circle", "〇の枠"), ("c_arrow", "矢印")]
COMMON_KEYS = ("font_size", "length", "width", "c_text", "c_circle", "c_arrow")
IMG_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".gif", ".webp")

# 用紙(縦向きの mm 寸法: 幅, 高さ。B判はJIS)
NO_PAPER = "図面サイズのまま"
PAPERS = {"A4": (210, 297), "A3": (297, 420), "B4": (257, 364), "B5": (182, 257)}
ORIENTS = ["自動", "縦", "横"]
MARGINS = ["0", "5", "10", "15", "20"]
DPIS = ["150", "200", "300"]

# 注記一覧(表)の列: (id, 見出し, 幅)
TREE_COLS = [("pg", "頁", 34), ("num", "番号", 52), ("fs", "文字", 46), ("ln", "長さ", 46), ("wd", "太さ", 46),
             ("ang", "角度", 48), ("arw", "矢印", 42), ("ct", "数字色", 54), ("cc", "〇色", 54),
             ("ca", "矢印色", 58), ("com", "共通", 42)]
TREE_IDS = [c[0] for c in TREE_COLS]
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
    a = {"x": x, "y": y, "num": num, "arrow": arrow, "angle": angle, "common": True}
    for k in COMMON_KEYS:
        a[k] = common[k]
    return a


def apply_common_to_pages(pages, common):
    """共通=ONの注記すべて(全ページ)に共通値を反映する。反映した件数を返す。"""
    n = 0
    for lst in pages.values():
        for a in lst:
            if a.get("common"):
                for k in COMMON_KEYS:
                    a[k] = common[k]
                n += 1
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
    return pages, common


def color_name(hx):
    for n, h in PALETTE:
        if h.lower() == str(hx).lower():
            return n
    return str(hx)


def table_row_values(pg, a):
    placed = is_placed(a)
    return (pg + 1, a["num"], "%g" % round(a["font_size"], 1), "%g" % round(a["length"], 1), "%g" % round(a["width"], 2),
            ("%g" % round(a["angle"], 1)) if placed else "",
            ("☑" if a["arrow"] else "☐") if placed else "",
            color_name(a["c_text"]), color_name(a["c_circle"]), color_name(a["c_arrow"]),
            "☑" if a["common"] else "☐")


def contrast_fg(hx):
    try:
        r, g, b = int(hx[1:3], 16), int(hx[3:5], 16), int(hx[5:7], 16)
    except Exception:
        return "black"
    return "black" if (0.299 * r + 0.587 * g + 0.114 * b) > 140 else "white"


# ---------------------------------------------------------------- 幾何 ---
def ring_width(fs):
    return max(1.5, fs * 0.09)


def circle_radius(a):
    fs = a["font_size"]
    n = max(1, len(str(a["num"])))
    return max(fs * 0.7, fs * 0.32 * n + fs * 0.35)


def geometry(a):
    """配置済みの注記1件の図形を画像座標で返す。"""
    cx, cy = a["x"], a["y"]
    r = circle_radius(a)
    ring = ring_width(a["font_size"])
    g = {"cx": cx, "cy": cy, "r": r, "ring": ring,
         "shaft": None, "head": None, "tip": None, "seg": None}
    if a.get("arrow") and a["length"] > 0:
        th = math.radians(a["angle"])
        dx, dy = math.cos(th), -math.sin(th)  # 角度0=右, 90=上(反時計回り)
        px, py = -dy, dx
        w = max(0.5, a["width"])
        start_r = r + ring / 2
        sx, sy = cx + dx * r, cy + dy * r
        tx, ty = cx + dx * (start_r + a["length"]), cy + dy * (start_r + a["length"])
        hl = max(1.0, min(w * 3.5 + 2, a["length"]))
        hw = w * 1.7 + 1
        bx, by = tx - dx * hl, ty - dy * hl
        g["shaft"] = [(sx + px * w / 2, sy + py * w / 2), (bx + px * w / 2, by + py * w / 2),
                      (bx - px * w / 2, by - py * w / 2), (sx - px * w / 2, sy - py * w / 2)]
        g["head"] = [(tx, ty), (bx + px * hw, by + py * hw), (bx - px * hw, by - py * hw)]
        g["tip"] = (tx, ty)
        g["seg"] = ((cx + dx * start_r, cy + dy * start_r), (tx, ty))
    return g


def scale_ann(a, s, ox=0.0, oy=0.0):
    """用紙書き出し用: 注記を倍率s・オフセット(ox,oy)で拡縮した複製を返す。"""
    b = dict(a)
    b["x"] = a["x"] * s + ox
    b["y"] = a["y"] * s + oy
    b["font_size"] = a["font_size"] * s
    b["length"] = a["length"] * s
    b["width"] = a["width"] * s
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
    xs = [g["cx"] - g["r"] - g["ring"], g["cx"] + g["r"] + g["ring"]]
    ys = [g["cy"] - g["r"] - g["ring"], g["cy"] + g["r"] + g["ring"]]
    for key in ("shaft", "head"):
        if g[key]:
            xs += [p[0] for p in g[key]]
            ys += [p[1] for p in g[key]]
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

    if g["shaft"]:
        d.polygon([T(p) for p in g["shaft"]], fill=a["c_arrow"])
        d.polygon([T(p) for p in g["head"]], fill=a["c_arrow"])
    R = g["r"] + g["ring"] / 2
    c = T((g["cx"], g["cy"]))
    d.ellipse([c[0] - R * SS, c[1] - R * SS, c[0] + R * SS, c[1] + R * SS],
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
class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry("1540x860")
        self.root.report_callback_exception = self.on_exception

        self.src_path = None
        self.src_type = None
        self.pdf = None
        self.page_count = 1
        self.cur_page = 0
        self.base_img = None
        self.pages = {}
        self.common = default_common()  # 「更新」で確定した共通値
        self.next_num = 1
        self.sel = None  # 表示中ページ内の選択注記のindex
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
        self.v_page = tk.StringVar(value="- / -")
        self.v_status = tk.StringVar(value="図面を開いてください(ファイル > 図面を開く)")
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
        fm.add_command(label="図面を開く(PDF・画像)...", accelerator="Ctrl+O", command=self.open_drawing)
        fm.add_command(label="編集ファイル(.annot)を開く...", accelerator="Ctrl+Shift+O", command=self.open_annot)
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
        ttk.Button(tb, text="図面を開く", command=self.open_drawing).pack(side="left", padx=2)
        ttk.Button(tb, text="編集ファイルを開く", command=self.open_annot).pack(side="left", padx=2)
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
        ttk.Checkbutton(tb, text="クリックで新規配置", variable=self.place_mode).pack(side="left")

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
        ttk.Spinbox(row2, textvariable=self.v_next, from_=0, to=99999, increment=1, width=7).pack(side="right")
        ttk.Label(row2, text="次の番号").pack(side="right", padx=(10, 2))

        ttk.Label(r, textvariable=self.v_status, relief="sunken", anchor="w", padding=(6, 2)).pack(side="bottom", fill="x")

        main = ttk.Frame(r)
        main.pack(fill="both", expand=True)
        panel = ttk.Frame(main, padding=4, width=560)
        panel.pack(side="right", fill="y")
        panel.pack_propagate(False)
        cf = ttk.Frame(main)
        cf.pack(side="left", fill="both", expand=True)
        self.canvas = tk.Canvas(cf, bg="#6b6b6b", highlightthickness=0)
        vs = ttk.Scrollbar(cf, orient="vertical", command=self.canvas.yview)
        hs = ttk.Scrollbar(cf, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(xscrollcommand=hs.set, yscrollcommand=vs.set)
        vs.pack(side="right", fill="y")
        hs.pack(side="bottom", fill="x")
        self.canvas.pack(side="left", fill="both", expand=True)

        c = self.canvas
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_motion)
        c.bind("<ButtonRelease-1>", self.on_release)
        for b in (2, 3):
            c.bind("<ButtonPress-%d>" % b, lambda e: c.scan_mark(e.x, e.y))
            c.bind("<B%d-Motion>" % b, lambda e: c.scan_dragto(e.x, e.y, gain=1))
        c.bind("<MouseWheel>", self.on_wheel)
        c.bind("<Button-4>", lambda e: self.on_wheel(e, 1))
        c.bind("<Button-5>", lambda e: self.on_wheel(e, -1))
        c.bind("<Motion>", self.on_hover)

        r.bind("<Delete>", self.on_delete_key)
        r.bind("<Escape>", lambda e: self.select(None))
        r.bind("<Control-z>", lambda e: self.undo())
        r.bind("<Control-y>", lambda e: self.redo())
        r.bind("<Control-s>", lambda e: self.save())
        r.bind("<Control-o>", lambda e: self.open_drawing())
        r.bind("<Control-O>", lambda e: self.open_annot())
        r.bind("<Control-e>", lambda e: self.export_png())
        r.bind("<Prior>", lambda e: self.change_page(-1))
        r.bind("<Next>", lambda e: self.change_page(1))

        # --- 右: 注記一覧(表)
        ttk.Label(panel, text="注記一覧", font=("", 10, "bold")).pack(anchor="w")
        tf = ttk.Frame(panel)
        tf.pack(fill="both", expand=True, pady=(2, 4))
        style = ttk.Style()
        style.configure("Treeview", rowheight=24)
        style.map("Treeview", background=[("selected", "#FF8C00")], foreground=[("selected", "#FFFFFF")])
        self.tree = ttk.Treeview(tf, columns=TREE_IDS, show="headings", selectmode="browse")
        for cid, head, w in TREE_COLS:
            self.tree.heading(cid, text=head)
            self.tree.column(cid, width=w, minwidth=w, stretch=False, anchor="center")
        tv = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tv.set)
        tv.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.tag_configure("unplaced", foreground="#888888")
        self.tree.bind("<Button-1>", self.on_tree_click)
        self.tree.bind("<Double-1>", self.on_tree_dbl)
        self.tree.bind("<Button-3>", self.on_tree_menu)

        bf = ttk.Frame(panel)
        bf.pack(fill="x")
        ttk.Button(bf, text="下に行を挿入", command=self.insert_row_below).pack(side="left")
        ttk.Button(bf, text="行を削除", command=self.delete_selected).pack(side="left", padx=6)
        ttk.Label(panel, foreground="#555", justify="left", wraplength=540, text=(
            "・行クリックで図面上の注記を選択 / 「共通」列をクリックでON・OFF\n"
            "・共通OFFの行だけ、文字・長さ・太さ・色をダブルクリックで編集\n"
            "・番号・角度はいつでもダブルクリックで編集 / 「矢印」列クリックで有無を切替\n"
            "・右クリック: 下に行を挿入(未配置の行。図面をクリックすると配置)")).pack(anchor="w", pady=(6, 0))

    def show_hints(self):
        messagebox.showinfo(APP_NAME, (
            "・空いている所をクリック: 新しい番号を配置(クリックのままドラッグで矢印の向き)\n"
            "・注記をドラッグ: 移動 / 矢印の先の ● をドラッグ: 向き(共通OFFなら長さも)\n"
            "・文字サイズ・矢印の長さ/太さ・色は、上の共通ステータスを入力して「更新」で、\n"
            "  共通ONの全注記(配置済みを含む)に反映されます\n"
            "・注記一覧(右の表)の「共通」列をOFFにした注記は、更新の対象から外れ、個別に編集できます\n"
            "・表で行を選んで右クリック > 下に行を挿入: 未配置の行ができます。番号を入力して\n"
            "  行を選んだまま図面をクリックすると、その位置に配置されます(番号が空なら自動採番)\n"
            "・Ctrl+ホイール: 拡大縮小 / 右・中ボタンドラッグ: 画面移動 / Delete: 削除 / Esc: 選択解除\n"
            "・用紙を選ぶと、画面に用紙の枠(実線)と余白の内側(青い破線)を表示し、PNGも用紙サイズで書き出します"))

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
        return copy.deepcopy((self.pages, self.common, self.next_num))

    def restore(self, snap):
        self.pages, self.common, self.next_num = copy.deepcopy(snap)
        self.v_next.set(str(self.next_num))
        self.sel = None
        self.load_common_to_ui()
        self.refresh_table()
        self.redraw_annotations()

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

    def open_drawing(self):
        if not self.confirm_discard():
            return
        path = filedialog.askopenfilename(
            title="図面を選択",
            filetypes=[("図面(PDF・画像)", "*.pdf *.png *.jpg *.jpeg *.bmp *.tif *.tiff *.gif *.webp"), ("すべて", "*.*")])
        if not path:
            return
        if self.load_source(path):
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

    def load_source(self, path):
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext == ".pdf":
                if fitz is None:
                    messagebox.showerror(APP_NAME, "PDFを開くには PyMuPDF が必要です。\n\npip install pymupdf")
                    return False
                doc = fitz.open(path)
                self.pdf = doc
                self.page_count = len(doc)
                self.src_type = "pdf"
            else:
                img = Image.open(path)
                img.load()
                self.pdf = None
                self.full_img = img.convert("RGB")
                self.page_count = 1
                self.src_type = "image"
        except Exception as e:
            messagebox.showerror(APP_NAME, "図面を開けませんでした。\n%s" % e)
            return False
        self.src_path = path
        return True

    def get_base(self, page):
        if self.src_type == "pdf":
            pg = self.pdf[page]
            pix = pg.get_pixmap(dpi=PDF_DPI, alpha=False)
            return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        return self.full_img

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
        self.v_page.set("%d / %d" % (self.cur_page + 1, self.page_count))
        self.fit()
        self.refresh_table()
        self.update_title()
        self.v_status.set("%s  (%d × %d px)" % (os.path.basename(self.src_path), self.base_img.width, self.base_img.height))

    def change_page(self, d):
        if self.base_img is not None and 0 <= self.cur_page + d < self.page_count:
            self.go_page(self.cur_page + d)

    # ---- 表示 -----------------------------------------------------------
    def max_zoom(self):
        if self.base_img is None:
            return 8.0
        return min(8.0, math.sqrt(MAX_DISPLAY_PIXELS / (self.base_img.width * self.base_img.height)))

    def fit(self):
        if self.base_img is None:
            return
        self.root.update_idletasks()
        cw, ch = max(100, self.canvas.winfo_width()), max(100, self.canvas.winfo_height())
        x0, y0, x1, y1 = self.content_rect()
        self.set_zoom(min(cw / (x1 - x0), ch / (y1 - y0)) * 0.95)

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
            if g["shaft"]:
                c.create_polygon([v * z for p in g["shaft"] for v in p], fill=a["c_arrow"], outline="", tags="ann")
                c.create_polygon([v * z for p in g["head"] for v in p], fill=a["c_arrow"], outline="", tags="ann")
            cx, cy, r = g["cx"] * z, g["cy"] * z, g["r"] * z
            c.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#FFFFFF", outline=a["c_circle"],
                          width=max(1, g["ring"] * z), tags="ann")
            c.create_text(cx, cy, text=str(a["num"]), fill=a["c_text"],
                          font=("Arial", -max(1, int(round(a["font_size"] * z))), "bold"), tags="ann")
            if i == self.sel:
                m = r + g["ring"] * z / 2 + 4
                c.create_oval(cx - m, cy - m, cx + m, cy + m, outline="#FF8C00", dash=(4, 3), width=2, tags="ann")
                if g["tip"]:
                    tx, ty = g["tip"][0] * z, g["tip"][1] * z
                    c.create_oval(tx - 6, ty - 6, tx + 6, ty + 6, fill="#FF8C00", outline="white", width=2, tags="ann")

    # ---- マウス操作(図面) -----------------------------------------------
    def to_img(self, e):
        return self.canvas.canvasx(e.x) / self.zoom, self.canvas.canvasy(e.y) / self.zoom

    def hit_test(self, ix, iy):
        lst = self.anns()
        tol = 6 / self.zoom
        a = self.cur_ann()
        if a is not None and is_placed(a):
            g = geometry(a)
            if g["tip"] and math.hypot(ix - g["tip"][0], iy - g["tip"][1]) <= 10 / self.zoom:
                return self.sel, "tip"
        for i in range(len(lst) - 1, -1, -1):
            if not is_placed(lst[i]):
                continue
            g = geometry(lst[i])
            if math.hypot(ix - g["cx"], iy - g["cy"]) <= g["r"] + g["ring"] / 2 + tol * 0.5:
                return i, "body"
            if g["seg"] and dist_point_seg(ix, iy, g["seg"]) <= max(lst[i]["width"], 1) / 2 + tol:
                return i, "body"
        return None

    def next_number_from_ui(self):
        try:
            return int(self.v_next.get())
        except ValueError:
            return self.next_num

    def on_press(self, e):
        self.canvas.focus_set()
        if self.base_img is None:
            return
        ix, iy = self.to_img(e)
        hit = self.hit_test(ix, iy)
        if hit:
            idx, part = hit
            self.sel = idx
            a = self.anns()[idx]
            self.drag = {"mode": "tip" if part == "tip" else "move", "idx": idx,
                         "dx": a["x"] - ix, "dy": a["y"] - iy, "pushed": False}
            self.redraw_annotations()
            self.sync_table_sel()
            return
        cur = self.cur_ann()
        if cur is not None and not is_placed(cur):
            # 表で選んでいる未配置の行を、クリックした位置に配置する
            self.push_undo()
            cur["x"], cur["y"] = ix, iy
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
        self.next_num = n + 1
        self.v_next.set(str(self.next_num))
        self.drag = {"mode": "new", "idx": self.sel, "x0": ix, "y0": iy, "pushed": True}
        self.redraw_annotations()
        self.refresh_table()

    def aim(self, a, ix, iy, keep_len=False):
        g = geometry({**a, "arrow": False})
        dx, dy = ix - a["x"], iy - a["y"]
        a["arrow"] = True
        a["angle"] = math.degrees(math.atan2(-dy, dx)) % 360
        if not keep_len:
            a["length"] = max(5.0, math.hypot(dx, dy) - g["r"] - g["ring"] / 2)

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
        elif d["mode"] == "tip":
            if not d["pushed"]:
                self.push_undo()
                d["pushed"] = True
            # 共通ONの注記は長さを共通値のまま保つ(向きだけ変える)
            self.aim(a, ix, iy, keep_len=bool(e.state & 0x1) or a["common"])
        elif d["mode"] == "new":
            if math.hypot(ix - d["x0"], iy - d["y0"]) * self.zoom >= 8:
                self.aim(a, ix, iy, keep_len=a["common"])
        self.redraw_annotations()

    def on_release(self, e):
        if self.drag:
            self.refresh_table()
        self.drag = None

    def on_hover(self, e):
        if self.base_img is not None and not self.drag:
            ix, iy = self.to_img(e)
            self.v_status.set("x=%d  y=%d   倍率 %d%%" % (ix, iy, round(self.zoom * 100)))

    def select(self, idx):
        self.sel = idx
        self.redraw_annotations()
        self.sync_table_sel()

    def on_delete_key(self, e):
        if isinstance(self.root.focus_get(), (tk.Entry, ttk.Entry, tk.Spinbox, ttk.Spinbox)):
            return
        self.delete_selected()

    def delete_selected(self):
        if self.cur_ann() is None:
            return
        self.push_undo()
        del self.anns()[self.sel]
        self.sel = None
        self.refresh_table()
        self.redraw_annotations()

    # ---- 注記一覧(表) ---------------------------------------------------
    def cancel_edit(self):
        if self._edit is not None:
            ent, self._edit = self._edit, None
            try:
                ent.destroy()
            except tk.TclError:
                pass

    def refresh_table(self):
        self.cancel_edit()
        t = self.tree
        t.delete(*t.get_children())
        for pg in sorted(self.pages):
            for i, a in enumerate(self.pages[pg]):
                t.insert("", "end", iid="%d:%d" % (pg, i), values=table_row_values(pg, a),
                         tags=() if is_placed(a) else ("unplaced",))
        self.sync_table_sel()

    def sync_table_sel(self):
        t = self.tree
        iid = "%d:%d" % (self.cur_page, self.sel) if self.sel is not None else None
        if iid and t.exists(iid):
            t.selection_set(iid)
            t.see(iid)
        else:
            t.selection_remove(*t.selection())

    def parse_iid(self, iid):
        pg, idx = iid.split(":")
        return int(pg), int(idx)

    def select_row(self, pg, idx):
        if self.base_img is None:
            return
        if pg != self.cur_page:
            self.go_page(pg)
        self.sel = idx
        self.redraw_annotations()
        self.sync_table_sel()
        a = self.cur_ann()
        if a is not None and not is_placed(a):
            self.v_status.set("未配置の行です。番号を入力して(空なら自動採番)、図面をクリックすると配置されます")

    def row_at(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell":
            return None, None
        iid = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not iid or not col:
            return None, None
        return iid, TREE_IDS[int(col[1:]) - 1]

    def on_tree_click(self, event):
        self.cancel_edit()
        iid, cid = self.row_at(event)
        if not iid:
            return
        pg, idx = self.parse_iid(iid)
        self.select_row(pg, idx)
        a = self.cur_ann()
        if a is None:
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

    def on_tree_dbl(self, event):
        iid, cid = self.row_at(event)
        if not iid:
            return "break"
        pg, idx = self.parse_iid(iid)
        self.select_row(pg, idx)
        a = self.cur_ann()
        if a is None or cid in ("com", "arw", "pg"):
            return "break"
        if cid in COMMON_CELLS and a["common"]:
            self.v_status.set("共通がONのため編集できません。「共通」列をクリックしてOFFにしてください")
            return "break"
        if cid == "ang" and not is_placed(a):
            return "break"
        if cid in ("ct", "cc", "ca"):
            key = COMMON_CELLS[cid]
            self.popup_palette(self.tree, a[key], lambda hx, k=key, aa=a: self.set_color_value(aa, k, hx),
                               pos=(event.x_root, event.y_root))
            return "break"
        self.edit_cell(iid, cid)
        return "break"

    def edit_cell(self, iid, cid):
        bbox = self.tree.bbox(iid, cid)
        if not bbox:
            return
        pg, idx = self.parse_iid(iid)
        cur = self.tree.set(iid, cid)
        self.cancel_edit()
        x, y, w, h = bbox
        ent = ttk.Entry(self.tree, justify="center")
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
            self.set_cell(pg, idx, cid, text)

        ent.bind("<Return>", commit)
        ent.bind("<KP_Enter>", commit)
        ent.bind("<FocusOut>", commit)
        ent.bind("<Escape>", lambda ev: self.cancel_edit())

    def set_cell(self, pg, idx, cid, text):
        if pg not in self.pages or idx >= len(self.pages[pg]):
            return
        a = self.pages[pg][idx]
        text = text.strip()
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
        pg, idx = self.parse_iid(iid)
        self.select_row(pg, idx)
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="下に行を挿入", command=self.insert_row_below)
        m.add_command(label="この行を削除", command=self.delete_selected)
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def insert_row_below(self):
        """選択行の下に未配置の行を挿入する。共通部分以外(番号を含む)は空。"""
        if self.cur_ann() is None:
            messagebox.showinfo(APP_NAME, "表で行を選んでから実行してください。")
            return
        self.push_undo()
        i = self.sel + 1
        self.anns().insert(i, make_ann(self.common))
        self.sel = i
        self.refresh_table()
        self.redraw_annotations()
        self.v_status.set("行を挿入しました。番号をダブルクリックで入力し、図面をクリックすると配置されます(空なら自動採番)")

    # ---- 保存・読込 -----------------------------------------------------
    def save(self):
        if self.src_path is None:
            messagebox.showinfo(APP_NAME, "先に図面を開いてください。")
            return False
        if not self.annot_path:
            return self.save_as()
        return self.write_annot(self.annot_path)

    def save_as(self):
        if self.src_path is None:
            return False
        init = os.path.splitext(os.path.basename(self.src_path))[0] + ".annot"
        path = filedialog.asksaveasfilename(title="編集ファイルを保存", defaultextension=".annot", initialfile=init,
                                            initialdir=os.path.dirname(self.src_path),
                                            filetypes=[("図面注記ファイル", "*.annot")])
        if not path:
            return False
        return self.write_annot(path)

    def write_annot(self, path):
        try:
            rel = os.path.relpath(self.src_path, os.path.dirname(os.path.abspath(path)))
        except ValueError:
            rel = None
        data = {"version": 2,
                "source": {"path": os.path.abspath(self.src_path), "rel": rel, "type": self.src_type},
                "pdf_dpi": PDF_DPI,
                "current_page": self.cur_page,
                "common": self.common,
                "next_num": self.next_num,
                "paper": {"size": self.v_paper.get(), "orient": self.v_orient.get(),
                          "margin": self.v_margin.get(), "dpi": self.v_dpi.get()},
                "pages": {str(k): v for k, v in self.pages.items()}}
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

    def open_annot(self):
        if not self.confirm_discard():
            return
        path = filedialog.askopenfilename(title="編集ファイルを選択", filetypes=[("図面注記ファイル", "*.annot"), ("すべて", "*.*")])
        if path:
            self.load_annot(path)

    def load_annot(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            messagebox.showerror(APP_NAME, "読み込めませんでした。\n%s" % e)
            return
        src = data.get("source", {})
        cands = []
        if src.get("rel"):
            cands.append(os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(path)), src["rel"])))
        if src.get("path"):
            cands.append(src["path"])
        found = next((c for c in cands if os.path.exists(c)), None)
        if not found:
            messagebox.showwarning(APP_NAME, "元の図面が見つかりません。図面ファイルを選んでください。")
            found = filedialog.askopenfilename(title="元の図面を選択",
                                               filetypes=[("図面(PDF・画像)", "*.pdf *.png *.jpg *.jpeg *.bmp *.tif *.tiff *.gif *.webp")])
            if not found:
                return
        if not self.load_source(found):
            return
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
        if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
            p = sys.argv[1]
            self.root.after(200, lambda: self.load_annot(p) if p.lower().endswith(".annot") else (
                self.load_source(p) and (self.auto_defaults(), self.go_page(0))))
        self.root.mainloop()


if __name__ == "__main__":
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
