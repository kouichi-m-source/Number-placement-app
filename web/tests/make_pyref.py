"""Win版(zumen_annot.pyw)の計算を基準に、Web版core.jsと突き合わせるための期待値(pyref.json)を作る。
tkinter が無い環境でも動くよう、画面部品は見せかけの部品に差し替えて読み込む。必要: Pillow"""
import sys, types, json, random, math
from unittest import mock

def stub(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m

class _Any:
    def __init__(self, *a, **k): pass
tk = stub("tkinter", Canvas=_Any, Tk=_Any, Toplevel=_Any, Menu=_Any, Frame=_Any, Entry=_Any, Spinbox=_Any,
          StringVar=_Any, BooleanVar=_Any, TclError=Exception, Button=_Any)
stub("tkinter.font"); stub("tkinter.ttk", Entry=_Any, Spinbox=_Any)
stub("tkinter.filedialog"); stub("tkinter.messagebox"); stub("tkinter.colorchooser")
tk.font = sys.modules["tkinter.font"]; tk.ttk = sys.modules["tkinter.ttk"]
tk.filedialog = sys.modules["tkinter.filedialog"]; tk.messagebox = sys.modules["tkinter.messagebox"]
tk.colorchooser = sys.modules["tkinter.colorchooser"]
import PIL
stub("PIL.ImageTk")
PIL.ImageTk = sys.modules["PIL.ImageTk"]

from pathlib import Path
HERE = Path(__file__).resolve().parent
src = (HERE.parent.parent / "zumen_annot.pyw").read_text(encoding="utf-8")
mod = types.ModuleType("zumen")
mod.__dict__["__name__"] = "zumen"
exec(compile(src, "zumen_annot.pyw", "exec"), mod.__dict__)
Z = mod

rnd = random.Random(7)

def rand_color():
    return rnd.choice([h for _, h in Z.PALETTE])

def rand_ann(common, placed=True):
    nums = ["1", "12", "123", "3,4", "3-5", "A", "７", "", "10-12", "7,9-11", "x1"]
    a = Z.make_ann(common)
    a["num"] = rnd.choice(nums)
    if placed:
        a["x"] = rnd.uniform(50, 1500); a["y"] = rnd.uniform(50, 1000)
        a["arrow"] = rnd.random() > 0.2
        a["angle"] = rnd.uniform(0, 360)
        if rnd.random() < 0.5:
            a["common"] = False
            a["font_size"] = rnd.choice([10, 16.5, 28, 40, 64]); a["length"] = rnd.choice([0, 30, 80.5, 200]); a["width"] = rnd.choice([0.5, 2, 4, 9.5])
            a["c_text"] = rand_color(); a["c_circle"] = rand_color(); a["c_arrow"] = rand_color()
        for _ in range(rnd.randint(0, 3)):
            e = Z.make_arrow(common, rnd.uniform(0, 360))
            if rnd.random() < 0.5:
                e["common"] = False; e["length"] = rnd.choice([0, 25, 90]); e["width"] = rnd.choice([1, 3.5]); e["c_arrow"] = rand_color()
            a["extra"].append(e)
    return a

common = Z.default_common(); common.update(font_size=30.0, length=90.0, width=5.0, c_arrow="#FF0000")
pages = {0: [rand_ann(common, rnd.random() > 0.15) for _ in range(40)],
         1: [rand_ann(common, rnd.random() > 0.15) for _ in range(25)]}
# 配置済み以外は make_ann の初期状態(arrow/angleがNone)のまま

out = {"common": common, "pages": {str(k): v for k, v in pages.items()}}
out["geometry"] = {}
for pg, lst in pages.items():
    for i, a in enumerate(lst):
        if Z.is_placed(a):
            g = Z.geometry(a)
            out["geometry"]["%d:%d" % (pg, i)] = {"g": g, "radii": Z.circle_radii(a)}
info = Z.analyze_numbers(pages)
info["dup_rows"] = sorted("%d:%d" % r for r in info["dup_rows"])
out["analyze"] = info
out["summary"] = Z.summary_text(Z.analyze_numbers(pages))
rows = [(pg, i, a) for pg in sorted(pages) for i, a in enumerate(pages[pg])]
out["sort"] = {str(m): [[r[0], r[1]] for r in Z.sort_rows(rows, m)] for m in (0, 1, 2)}
order = [(r[0], r[1]) for r in Z.sort_rows(rows, 1)]
out["renum_compact"] = Z.compute_renumber(pages, "compact", order)
out["renum_seq"] = Z.compute_renumber(pages, "seq", order)
out["table"] = {"%d:%d" % (pg, i): Z.table_row_values(pg, a, "x") for pg, lst in pages.items() for i, a in enumerate(lst) if Z.is_placed(a)}
out["child"] = [Z.table_child_values(0, pages[0][i]["extra"][0], 1) for i in range(len(pages[0])) if Z.is_placed(pages[0][i]) and pages[0][i]["extra"]][:5]
out["parse"] = {s: Z.parse_nums(s) for s in ["3,4", "3-5", "１２、１３", "A", "7・9", "10~12", "5-3", "1-2000", "", "  8 "]}

class V:
    def __init__(s, v): s.v = v
    def get(s): return s.v
class Fake: pass
lay = []
for size in ["図面サイズのまま", "A4", "A3", "B4", "B5"]:
    for orient in ["自動", "縦", "横"]:
        for dpi in ["150", "200", "300"]:
            for (bw, bh) in [(1754, 1240), (1240, 1754), (4000, 3000), (999, 1500)]:
                f = Fake(); f.v_paper = V(size); f.v_orient = V(orient); f.v_margin = V("10"); f.v_dpi = V(dpi)
                r = Z.App.paper_layout(f, bw, bh)
                lay.append({"in": [size, orient, "10", dpi, bw, bh], "out": r})
out["paper"] = lay

# hit_test
hits = []
class FA:
    pass
for t in range(300):
    f = FA(); f.cur_page = 0; f.pages = {0: pages[0]}
    f.zoom = rnd.choice([0.3, 1.0, 2.5])
    f.sel = rnd.choice([None] + list(range(len(pages[0]))))
    f.sel_arrow = rnd.randint(0, 2)
    f.anns = lambda f=f: f.pages[0]
    f.cur_ann = lambda f=f: None if f.sel is None else f.pages[0][f.sel]
    # 配置済み注記の近くを狙う
    a = rnd.choice([x for x in pages[0] if Z.is_placed(x)])
    ix = a["x"] + rnd.uniform(-150, 150); iy = a["y"] + rnd.uniform(-150, 150)
    skip = rnd.random() < 0.2
    r = Z.App.hit_test(f, ix, iy, skip)
    hits.append({"zoom": f.zoom, "sel": f.sel, "sel_arrow": f.sel_arrow, "ix": ix, "iy": iy, "skip": skip, "out": r})
out["hits"] = hits

# migrate (新形式・旧形式)
old = {"colors": {"text": "#FF0000", "circle": "#0000FF", "arrow": "#00A000"}, "defaults": {"font_size": 20, "length": 70, "width": 3},
       "pages": {"0": [{"x": 10, "y": 20, "num": "1"}, {"num": "2"}]}}
out["migrate_old_in"] = old
out["migrate_old"] = {"pages": {str(k): v for k, v in Z.migrate_loaded(json.loads(json.dumps(old)))[0].items()}, "common": Z.migrate_loaded(json.loads(json.dumps(old)))[1]}
out["free_angle"] = [[[], Z.free_angle([])], [[45], Z.free_angle([45.0])], [[45, 200], Z.free_angle([45.0, 200.0])], [[0, 90, 180], Z.free_angle([0.0, 90.0, 180.0])]]
json.dump(out, open(HERE / "pyref.json", "w", encoding="utf-8"), ensure_ascii=False)
print("ok", len(out["geometry"]), "geoms", len(hits), "hits")
