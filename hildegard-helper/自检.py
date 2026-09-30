# -*- coding: utf-8 -*-
"""自检脚本（可选，平时不用跑）。

改过 gui.py / templates.py / vision.py / hildegard_math.py 之后，
双击或命令行跑一下这个，确认没把别的地方改坏：

    python 自检.py

会检查 12 项：语法、切块算法、min_scale_factor 吸附、提示词模板语法陷阱、
JSON 截断检测、界面布局（4 档字号都不挤压不裁切）、设置说明生成、
最小重叠对输出尺寸的影响、参数指纹能感知重叠变化、文档与默认值一致、
texture 档不会把「材质措辞词典」当答案照抄，也不会漏掉植物 / 花这类大宗材质，
也不会给画出来的东西安上真实材质的微观结构（那会让出图偏离原图）、
档位判定边界对齐仓库的决策规则。
不联网、不调模型，几秒钟跑完。
"""
import os
import re
import sys
import tkinter as tk
import tkinter.font as _tkf
from tkinter import ttk

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import py_compile

fails = []

# 自检绝不能改动用户的 config.json。踩过一次：遍历四档字号验布局时
# 顺手把 font_scale 写成了最后一档「特大」。
_cfg_path = os.path.join(BASE, "config.json")
_cfg_before = None
if os.path.exists(_cfg_path):
    with open(_cfg_path, "rb") as _f:
        _cfg_before = _f.read()

# ---------- 1 语法 ----------
for f in ("gui.py", "hildegard_math.py", "templates.py", "settings_doc.py",
          "vision.py", "md_conform.py"):
    try:
        py_compile.compile(f, doraise=True)
    except Exception as e:
        fails.append(f"{f} 语法错误: {e}")
print("[1] 语法检查", "通过" if not fails else "失败")

import hildegard_math as hm
import templates as T
import settings_doc
import vision
import md_conform
import gui as G

# ---------- 2 算法：说明文档里的 tile 表 ----------
expect = {
    1024: (1792, 2688, 1.75, 2, 4, 8),
    1536: (1536, 2304, 1.50, 1, 2, 2),
    2048: (2048, 3072, 2.00, 1, 2, 2),
    2560: (2560, 3840, 2.50, 1, 2, 2),
    3048: (3040, 4560, 2.969, 1, 2, 2),
}
bad = 0
for t, (ew, eh, es, gx, gy, nt) in expect.items():
    a = hm.analyze(1024, 1536, tile_width=t, tile_height=t, overlap=0.25,
                   min_scale_factor=1.3)
    got = (a["upscaled_width"], a["upscaled_height"],
           round(a["effective_scale_x"], 3), a["grid_x"], a["grid_y"],
           a["num_tiles"])
    if got != (ew, eh, round(es, 3), gx, gy, nt):
        bad += 1
        print("   差异", t, got, (ew, eh, es, gx, gy, nt))
print(f"[2] 切块算法表", "通过" if bad == 0 else f"失败 {bad}")

# ---------- 3 min_scale_factor 刻度 ----------
ticks = {1.0: 2.0, 1.3: 2.0, 2.0: 2.0, 2.1: 3.5, 3.5: 3.5, 3.6: 5.0,
         5.0: 5.0, 5.1: 6.5, 8.0: 8.0}
bad = 0
for v, want in ticks.items():
    a = hm.analyze(1024, 1536, tile_width=2048, tile_height=2048,
                   overlap=0.25, min_scale_factor=v)
    if a["effective_scale_x"] != want:
        bad += 1
        print("   差异", v, a["effective_scale_x"], want)
print("[3] min_scale_factor 吸附", "通过" if bad == 0 else f"失败 {bad}")

# ---------- 4 模板：提示词尺寸 + 语法陷阱 ----------
issues = []
for tier in ("full_build", "texture", "subject_less"):
    n = len(vision.build_system_prompt(tier))
    if n > 4600:
        issues.append(f"{tier} 系统提示 {n} 字符，超预算（4096 上下文会截断）")

base = {"scene": "this wallpaper", "mode": ["object"], "object": "peacock",
        "painterly": True}
cases = [
    ("双 the", dict(base, environment={"background": "the painted ground",
                                    "depth": "flat", "soft_elements": "the flowers",
                                    "state": "natural"},
                    materials=[{"material": "the feathers", "sub_features": "barbs",
                                "guard": "each barb defined"}])),
    ("空 background", dict(base, environment={"background": None, "depth": "flat",
                                            "soft_elements": "flowers",
                                            "state": "natural"})),
    ("只有 Style 无 Mood", dict(base, style_medium="oil painting")),
    ("lighting 是介词短语", dict(base, lighting="over the flat colour fields",
                             lighting_style="oil painting",
                             style_medium="oil painting")),
]
for name, d in cases:
    p = T.assemble_full_build(d)
    if " the the " in p:
        issues.append(f"{name}: 出现双 the")
    if "Keep the naturally" in p or "within its existing existing" in p:
        issues.append(f"{name}: 环境句出现空位")
    if "Mood:." in p or p.rstrip().endswith("Mood:"):
        issues.append(f"{name}: 空 Mood 标签")
    ok, warn = T.validate(p, "full_build")
    if warn:
        issues.append(f"{name}: 自检警告 {warn}")

# Image 1/2/3 只存在于 full_build 那一档的模板里。另外两档的模板里
# **一个 "Image" 都没有**（实测 3 档 × 2 种参考形态 = 6 种组合）。
# 这条是回答「我只传一张图为什么提示词里有图1图2图3」的依据，
# 而且界面上那行实时提示要靠它决定说什么 —— 写错了就会让用户
# 去满篇找一个不存在的词。
for _tier in ("texture", "subject_less"):
    for _mr in (True, False):
        _out = T.assemble({"scene": "a landscape", "painterly": "",
                           "multi_reference": _mr}, _tier)
        if "Image 1" in _out or "image 3" in _out:
            issues.append(f"{_tier}（multi={_mr}）不该出现 Image 编号")
if "Image 1" not in T.assemble({"scene": "a landscape", "painterly": "",
                                "multi_reference": True}, "full_build"):
    issues.append("full_build + 三参考 应当出现 Image 1")
if "Image 1" in T.assemble({"scene": "a landscape", "painterly": "",
                            "multi_reference": False}, "full_build"):
    issues.append("full_build + 单参考 不该出现 Image 1")

print("[4] 提示词模板与语法", "通过" if not issues else "失败")
for i in issues:
    print("   ✗", i)

# ---------- 5 截断检测 ----------
trunc_ok = True
for bad_json in ('{"a": 1, "b"', '{"a": [1,2', '{"a": "half'):
    try:
        vision.parse_json(bad_json)
        trunc_ok = False
    except ValueError as e:
        if "截断" not in str(e):
            trunc_ok = False
print("[5] JSON 截断检测", "通过" if trunc_ok else "失败")
if not trunc_ok:
    fails.append("截断检测")

# ---------- 6 GUI 布局（4 档字号，无挤压 / 无裁切）----------
G.enable_dpi_awareness()
dpi = G.get_system_dpi()
root = tk.Tk()
try:
    st = ttk.Style()
    if "vista" in st.theme_names():
        st.theme_use("vista")
except Exception:
    pass
app = G.App(root, dpi=dpi)
root.update_idletasks()
root.update()

pw = next(c for c in root.winfo_children() if isinstance(c, ttk.PanedWindow))
right = root.nametowidget(pw.panes()[1])

# ⚠ 必须**带图**测。无图时「关键数字」全是「—」，面板很窄；有图时值会变成
# 「5687×3584  (20.4MP)」这种，stats 一下宽 500px 以上 —— 右栏横向溢出
# 只在有图时才出现。之前只测无图状态，漏掉过一次真实的溢出。
_img = os.path.join(BASE, "界面预览.png")
if os.path.exists(_img) and getattr(G, "HAS_PIL", False):
    app.paths = [_img]
    app.refresh_list()
    app.listbox.selection_set(0)
    app.on_select()
    root.update_idletasks()
    root.update()

squeeze = []
for label, scale in G.FONT_CHOICES:
    app.v_font.set(label)
    app.on_font_change(persist=False)   # 不能写 config.json，见 gui.on_font_change
    root.update_idletasks()
    root.update()
    for ch in right.winfo_children():
        if ch.winfo_height() < ch.winfo_reqheight() - 2:
            squeeze.append(f"{label}: {ch.winfo_class()} "
                           f"{ch.winfo_reqheight()}→{ch.winfo_height()}")
    if right.winfo_reqwidth() > right.winfo_width() + 2:
        squeeze.append(f"{label}: 右栏横向溢出 "
                       f"{right.winfo_reqwidth()}>{right.winfo_width()}")

# --- 精细度页 + 参数区那一行实时判断 ---
# 那一行**必须单行**：特大字号下右栏只剩 ~74px 纵向余量，折行会把
# Notebook 挤到需求高度以下（上面那个循环立刻会报）。所以既查有没有
# 换行，也查它是否塞得进自己的 wraplength。
app.v_font.set("特大")
app.on_font_change(persist=False)
root.update_idletasks()
root.update()
_f = _tkf.Font(family=G.UI_FAMILY, size=app._fs["small"])
_line = app.v_fine_line.get()
if "\n" in _line:
    squeeze.append("参数区那行「精细度：…」被折成了多行")
_wrap = int(app.fine_lbl.cget("wraplength"))
if _f.measure(_line) > _wrap:
    squeeze.append(f"参数区那行「精细度：…」宽 {_f.measure(_line)}px，"
                   f"超过 wraplength {_wrap}px（会被折行）")

# 精细度页的正文
_ftxt = app.fine_txt.get("1.0", "end")
for _must, _why in [("① tile 尺寸", "精细度页没说 tile 是唯一直接杠杆"),
                    ("② Flux2KleinRefLatentWeight", "精细度页没写参考权重"),
                    ("③ tile_high_freq_reduce", "精细度页没写 high_freq_reduce"),
                    ("不会改变精细度的", "精细度页没列「不影响精细度」的参数"),
                    ("多切几块", "精细度页没点出「多切几块 ≠ 更细」")]:
    if _must not in _ftxt:
        squeeze.append(f"精细度页缺：{_why}")

# 换 tile，判断和数字必须跟着重算
for _t, _must in [("2048", "4.19 MP"), ("1536", "2.36 MP"), ("1280", "1.64 MP")]:
    app.v_tile.set(_t)
    root.update_idletasks()
    if _must not in app.fine_txt.get("1.0", "end"):
        squeeze.append(f"精细度页没按 tile={_t} 重算（缺 {_must}）")
# 「对齐官方示例」只对 2048 成立 —— 官方那两个 PrimitiveInt 填的就是 2048。
# 套到 3048 上就是错的。
app.v_tile.set("2048")
root.update_idletasks()
if "不是画质推荐值" not in app.fine_txt.get("1.0", "end"):
    squeeze.append("精细度页在 tile=2048 时没点出「不是画质推荐值」")
app.v_tile.set("3048")
root.update_idletasks()
if "对齐官方示例" in app.fine_txt.get("1.0", "end"):
    squeeze.append("精细度页把 tile=3048 也说成「对齐官方示例」了")
# 1536 是推荐值，不能判成「偏细」
app.v_tile.set("1536")
root.update_idletasks()
if "推荐区间" not in app.fine_txt.get("1.0", "end"):
    squeeze.append("精细度页没把 tile=1536 认成推荐区间")
# reduce = 0 时 low_freq_radius 是死值；reduce > 0 才生效
app.v_tile.set("2048")
app.v_reduce.set("0.00")
root.update_idletasks()
if "死值" not in app.fine_txt.get("1.0", "end"):
    squeeze.append("精细度页没提示 low_freq_radius 在 reduce=0 时是死值")
app.v_reduce.set("0.50")
root.update_idletasks()
_ftxt2 = app.fine_txt.get("1.0", "end")
if "已生效" not in _ftxt2 or "死值" in _ftxt2:
    squeeze.append("精细度页没跟上 reduce>0（low_freq_radius 应变「已生效」）")
# cell_size / ctrl3 也要能实时反映
app.v_reduce.set("0.00")
app.v_cell.set("256")
root.update_idletasks()
if "256" not in app.fine_txt.get("1.0", "end"):
    squeeze.append("精细度页没跟上 cell_size 的变化")
app.v_cell.set("512")
app.v_tile.set(str(G.DEFAULT_TILE))
root.update_idletasks()

# --- 提示词页那行「Image 1/2/3 是什么」 ---
# 这行的存在理由是：用户只传一张图却在提示词里看到 Image 1/2/3，
# 会以为「清除没生效」。文案必须跟着参考形态实时变，而且必须塞得下 ——
# 它是加在按钮那一行的（**不能**另起一行：Notebook 在特大字号下只剩
# 几像素纵向余量，自检开头那个循环会立刻报）。
if len(G.REF_CHOICES) != 2:
    squeeze.append("REF_CHOICES 不再是 2 项，参考形态 → image 编号的映射断了")
# 下拉选项本身必须带着 image1/2/3 的对应关系（这是零高度成本的说明位）
for _tok in ("image1=tile", "image2=位置", "image3=全图"):
    if _tok not in G.REF_CHOICES[0]:
        squeeze.append(f"参考形态选项没写清映射（缺 {_tok}）")
if "Image" in G.REF_CHOICES[1]:
    squeeze.append("单参考的选项文案不该出现 Image 编号")

app.v_font.set("特大")
app.on_font_change(persist=False)
root.update_idletasks()
root.update()

# 这行是**两个**条件驱动的：参考形态 + 档位。
# ⚠ 必须两个分支都测。只测当前那张图会漏 —— 自检用的 界面预览.png
#   很小，tile 2048 下只有 1 块（single pass → full_build），
#   所以「三参考 → 有 Image 1/2/3」这条会**假通过**，
#   真正会出事的那条（texture 档该改口）根本没被走到。
_real_info = app.info
app.v_ref.set(G.REF_CHOICES[0])
app.info = {"tier": "full_build"}
app._update_ref_hint()
_h_multi = app.v_ref_hint.get()
if "Image 1/2/3" not in _h_multi or "块本身" not in _h_multi:
    squeeze.append(f"三参考 + full_build 没给出 Image 1/2/3 的映射：{_h_multi!r}")
for _tier in ("texture", "subject_less"):
    app.info = {"tier": _tier}
    app._update_ref_hint()
    _h = app.v_ref_hint.get()
    if "没有 Image 编号" not in _h:
        squeeze.append(f"三参考 + {_tier} 档应当改口说「本档没有 Image 编号」，"
                       f"现在说的是：{_h!r}")
# 单参考：任何档位都不该提编号
app.v_ref.set(G.REF_CHOICES[1])
for _tier in ("full_build", "texture"):
    app.info = {"tier": _tier}
    app._update_ref_hint()
    _h = app.v_ref_hint.get()
    if "Image 1" in _h or not _h:
        squeeze.append(f"单参考（{_tier}）那行提示不对：{_h!r}")
# 没图（info=None）时要退回通用文案，不能留着上一张图的档位判断
app.info = None
app.v_ref.set(G.REF_CHOICES[0])
app._update_ref_hint()
if "Image 1/2/3" not in app.v_ref_hint.get():
    squeeze.append("没选图时那行提示没退回通用文案")
app.info = _real_info
app._update_ref_hint()
root.update_idletasks()

# 横向必须塞得下：按钮 + 状态 + 这行提示。阈值现算，不写死 ——
# 字号/字体/DPI 一变，写死的像素数就会误报。
# ⚠ 量的是**最长的那条**：这行文案会随档位在三种之间切换。
_btns = app.btn_gen.master
_fh = _tkf.Font(font=(G.UI_FAMILY, app._fs["small"]))
_btn_w = sum(k.winfo_reqwidth() for k in _btns.winfo_children()
             if k is not app.ref_hint_lbl)
_avail = _btns.winfo_width() - _btn_w - 14
for _txt in (G.REF_HINT[0], G.REF_HINT[1], G.REF_HINT_NO_NUM):
    _w = _fh.measure(_txt)
    if _w > _avail:
        squeeze.append(f"提示词页那行说明宽 {_w}px，超过可用 {_avail}px"
                       f"（按钮行会挤爆 / 被截断）：{_txt!r}")

print("[6] GUI 布局", "通过" if not squeeze else "失败")
for s in squeeze:
    print("   ✗", s)
root.destroy()

# ---------- 7 设置说明生成 ----------
try:
    info = hm.analyze(1024, 1536)
    info["overlap_label"] = "1/4 Tile"
    info["min_scale_factor"] = 1.3
    txt = settings_doc.build(info, {"cell_size": 512, "ctrl3_max_size": 768,
                                    "tile_high_freq_reduce": 0.0,
                                    "low_freq_radius": 256, "tile_order": "spiral"})
    # 设置说明里可以「提醒」触发词，但不该把整段提示词正文塞进去
    assert "refine and add detail to this upscaled tile" not in txt, "混进了提示词正文"
    assert "Hildegard Plan" in txt and "Hildegard Combine" in txt, "节点清单不完整"
    # 三路参考旁边必须写明它们就是提示词里的 Image 1/2/3 ——
    # 用户只传一张图却看到「图1图2图3」时的第一个疑问，答案要在这里找得到。
    for _must in ("Image 1 = tile_latent", "Image 2 = position_latent",
                  "Image 3 = global_latent", "毫无关系"):
        assert _must in txt, f"设置说明没写清三路参考 → Image 1/2/3 的映射（缺 {_must}）"
    print("[7] ComfyUI 设置说明生成", "通过" if len(txt) > 200 else "失败")
except Exception as e:
    print("[7] ComfyUI 设置说明生成 失败:", e)
    fails.append("settings_doc")

# ---------- 8 参数联动 ----------
# (a) 最小重叠确实会改变输出尺寸。用户反馈过的点：min_overlap 不只是
#     「接缝软硬」，它和 tile 尺寸一样参与格点计算
#     （输出宽 = tile×n − 重叠×(n−1)），所以会改输出尺寸和块数。
#     锁一个已知会变的组合，防止以后有人把它当成纯装饰参数删掉。
ov_pairs = [
    # (源宽, 源高, tile, min_scale, 低重叠, 高重叠)
    (1024, 1536, 1024, 1.3, 0.0, 0.5),
    (2048, 2048, 2048, 1.3, 0.0, 0.25),
    (1024, 1536, 2048, 4.0, 0.0, 0.25),
]
ov_ok = True
for w, h, t, sf, lo, hi in ov_pairs:
    a = hm.plan(w, h, tile_width=t, tile_height=t, overlap=lo, min_scale_factor=sf)
    b = hm.plan(w, h, tile_width=t, tile_height=t, overlap=hi, min_scale_factor=sf)
    if a["upscaled_width"] == b["upscaled_width"] and a["num_tiles"] == b["num_tiles"]:
        ov_ok = False
        print(f"   ✗ {w}x{h} tile={t} sf={sf}：重叠 {lo}→{hi} 输出没变，"
              f"和「重叠参与格点计算」矛盾")
print("[8] 最小重叠影响输出尺寸", "通过" if ov_ok else "失败")
if not ov_ok:
    fails.append("最小重叠")

# (b) 重叠改到一定程度，块数会变 → 档位会换 → 旧提示词作废。
#     GUI 的 size_key() 必须能识别出「参数变了」，否则不会提醒重新生成。
G.enable_dpi_awareness()
_root2 = tk.Tk()
try:
    _st2 = ttk.Style()
    if "vista" in _st2.theme_names():
        _st2.theme_use("vista")
except Exception:
    pass
_app2 = G.App(_root2, dpi=G.get_system_dpi())
_app2.cur = os.path.join(BASE, "界面预览.png")   # 随便给个存在的路径
_app2.v_tile.set("2048")
_app2.v_scale.set("4.0")
_app2.v_ovl.set("None")
k1 = _app2.size_key()
t1 = hm.analyze(1024, 1536, tile_width=2048, tile_height=2048,
                overlap=0.0, min_scale_factor=4.0)["tier"]
_app2.v_ovl.set("1/4 Tile")
k2 = _app2.size_key()
t2 = hm.analyze(1024, 1536, tile_width=2048, tile_height=2048,
                overlap=0.25, min_scale_factor=4.0)["tier"]
link_ok = (k1 != k2)
print("[9] 参数指纹能感知重叠变化", "通过" if link_ok else "失败")
if not link_ok:
    fails.append("size_key 不感知重叠")
if t1 == t2:
    print(f"   ⚠ 该组合下档位没变（{t1}），换个组合试试")
_root2.destroy()

# (c) 文档与默认值必须和代码一致
_doc = open(os.path.join(BASE, "使用说明.txt"), encoding="utf-8").read()
doc_ok = True
for must, why in [("【参数不是各管各的——它们是联动的】", "使用说明缺联动一节"),
                  ("【最小重叠是什么】", "使用说明缺最小重叠一节"),
                  ("参数已改", "使用说明没提过期提醒"),
                  ("默认是「标准」", "使用说明的字号默认值没跟上"),
                  ("把「材质措辞词典」当答案整段抄回来",
                   "使用说明没提 texture 档照抄词典这个坑"),
                  ("坑二：漏材质（尤其是植物和花）",
                   "使用说明没提 texture 档漏材质这个坑"),
                  ("坑三：给「画出来的东西」加真实材质的微观结构",
                   "使用说明没提 texture 档「画出来的东西写成写实材质」这个坑"),
                  ("【三个档位是怎么判出来的】",
                   "使用说明缺档位判定一节"),
                  ("【为什么会有塑料感 / 为什么色调越来越暗】",
                   "使用说明缺「塑料感 / 变暗」成因一节"),
                  ("死值", "使用说明没提 Plan 节点的 tile 显示值是死值"),
                  ("要改 tile 尺寸，就改这两个节点的「数值」",
                   "使用说明没说清改 tile 到底改哪两个 PrimitiveInt 节点"),
                  ("Flux2Scheduler.width", "使用说明没说 PrimitiveInt 还驱动调度器分辨率"),
                  ("【提示词里的 Image 1 / Image 2 / Image 3 是什么】",
                   "使用说明缺「提示词里的 Image 1/2/3 是什么」一节"),
                  ("tile_latent", "使用说明没说 Image 1 对应哪个 latent"),
                  ("position_latent", "使用说明没说 Image 2 对应哪个 latent"),
                  ("global_latent", "使用说明没说 image 3 对应哪个 latent"),
                  ("不是你的图片列表", "使用说明没点明三个 image 跟图片列表无关"),
                  ("不是残留", "使用说明没给出「不是清除没生效」的结论"),
                  ("「清除」到底有没有用", "使用说明没正面回答清除是否有效"),
                  ("提示词框上方会有一行小灰字", "使用说明没提界面上新增的实时提示行")]:
    if must not in _doc:
        print(f"   ✗ {why}")
        doc_ok = False
# 文档里的档位区间必须和代码一致。texture 的上界从 20 改成 24 过一次，
# 文档如果没跟着走，用户会照着错的区间去调 tile。
for _must in ("7~24 块", ">24 块"):
    if _must not in _doc:
        print(f"   ✗ 使用说明的档位区间没跟上（缺 {_must!r}）")
        doc_ok = False
if "7~20 块" in _doc or ">20 块" in _doc:
    print("   ✗ 使用说明里还留着旧的档位区间 20（代码是 24）")
    doc_ok = False

# (c2) 「tile 越小细节越好吗」那一节的数字表必须由算法现算，不能手写。
# 这一节回答的是用户实测提的问题（title 尺寸是否影响精细度）。核心证据是
# **输出尺寸不随 tile 单调变化**：同一张 5970×3840 的图，tile 1536 的输出
# （7761×4992）比 tile 1280（7960×5120）还小；而 1280 和 2048 输出完全相同，
# 却要 40 块 vs 15 块。这些一旦和 hildegard_math 漂开，用户会照着错的
# 数字去调 tile。
import re as _re
for _t in (1024, 1280, 1536, 1792, 2048, 2304, 2560, 2816, 3048):
    _a = hm.analyze(5970, 3840, tile_width=_t, tile_height=_t, overlap=0.25,
                    min_scale_factor=1.3)
    _pat = _re.compile(
        r"^\s*" + str(_t) + r"\s+"
        + str(_a["upscaled_width"]) + r"×" + str(_a["upscaled_height"]) + r"\s+"
        + r"[\d.]+×\s+" + str(_a["grid_x"]) + r"×" + str(_a["grid_y"]) + r"\s+"
        + str(_a["num_tiles"]) + r"\s+[\d.]+ MP\s+"
        + _a["tier"] + r"\s*$", _re.M)
    if not _pat.search(_doc):
        print(f"   ✗ 使用说明的 tile 表里 tile={_t} 这一行和算法对不上"
              f"（应为 {_a['upscaled_width']}×{_a['upscaled_height']}"
              f" {_a['grid_x']}×{_a['grid_y']} {_a['num_tiles']}块"
              f" {_a['tier']}）")
        doc_ok = False

# (c3) 「最小重叠是什么」里的实测三（重叠扫描表）同样必须现算。
# 这张表是回答「min_overlap 要不要和 tile 配合」的核心证据：
# 1/4 和 1/2 输出完全相同（7960×5120），但 1/2 要 28 块、1/4 只要 15 块。
for _lab, _ov in [("None", 0.0), ("1/64", 1 / 64), ("1/32", 1 / 32),
                  ("1/16", 1 / 16), ("1/8", 0.125), ("1/4", 0.25),
                  ("1/2", 0.5)]:
    _a = hm.analyze(5970, 3840, tile_width=2048, tile_height=2048,
                    overlap=_ov, min_scale_factor=1.3)
    _pat = _re.compile(
        r"^\s*" + _re.escape(_lab) + r"\s+(?:Tile\s+)?\d+\s+\d+\s+"
        + str(_a["upscaled_width"]) + r"×" + str(_a["upscaled_height"]) + r"\s+"
        + str(_a["grid_x"]) + r"×" + str(_a["grid_y"]) + r"\s+"
        + str(_a["num_tiles"]) + r"\s+\d+\s+\w+\s*$", _re.M)
    if not _pat.search(_doc):
        print(f"   ✗ 使用说明的重叠表里 {_lab} Tile 这一行和算法对不上"
              f"（应为 {_a['upscaled_width']}×{_a['upscaled_height']}"
              f" {_a['grid_x']}×{_a['grid_y']} {_a['num_tiles']}块）")
        doc_ok = False

# (c5) 画质排序表：按「每遍画布」从小到大排。用户实测反馈过
# 「tile 2048 画得不精细，你之前为什么建议 2048」——所以这张表必须存在，
# 而且必须现算，因为它就是「默认 2048 只是对齐、不是画质推荐」的证据。
for _t in (1024, 1280, 1536, 1792, 2048, 2304, 2560, 2816, 3048):
    _p = hm.plan(5970, 3840, tile_width=_t, tile_height=_t, overlap=0.25,
                 min_scale_factor=1.3)
    _pat = _re.compile(
        r"^\s*" + str(_t) + r"\s+"
        + r"[\d.]+ MP\s+" + str(round(_t / _p["effective_scale_x"])) + r" px\s+"
        + str(_p["upscaled_width"]) + r"×" + str(_p["upscaled_height"]) + r"\s+"
        + r"[\d.]+×\s+" + str(_p["num_tiles"]) + r"\s+[\d.]+ MP"
        + r"(?:\s+←.*)?\s*$", _re.M)
    if not _pat.search(_doc):
        print(f"   ✗ 使用说明的画质排序表里 tile={_t} 这一行和算法对不上"
              f"（应为 {_t * _t / 1e6:.2f} MP / "
              f"{round(_t / _p['effective_scale_x'])} px / "
              f"{_p['upscaled_width']}×{_p['upscaled_height']} / "
              f"{_p['num_tiles']}块）")
        doc_ok = False
# 2048 及以上必须是「每遍画布最大」的那一段 —— 这是「默认值不是画质推荐」
# 这个论断的量化依据。
if hm.plan(5970, 3840, tile_width=2048, tile_height=2048,
           overlap=0.25, min_scale_factor=1.3)["num_tiles"] != 15:
    print("   ✗ tile 2048 的块数不再是 15，画质排序表要重写")
    doc_ok = False

# (c6) 「为什么不是越小越好到 1024」的代价表：小 tile 的反向代价（缝段、
# 羽化带、上下文占比）必须现算。这是「1024 不是画质最好，是局部最锐、
# 全局最差」这个结论的量化依据 —— 用户就是顺着「越小越好」问到 1024 的。
for _t in (1024, 1280, 1536, 1792, 2048):
    _p = hm.plan(5970, 3840, tile_width=_t, tile_height=_t, overlap=0.25,
                 min_scale_factor=1.3)
    _ox = _p["overlap_x"]
    _seams = (_p["grid_x"] - 1) * _p["grid_y"] + (_p["grid_y"] - 1) * _p["grid_x"]
    _widthpct = _t / _p["effective_scale_x"] / 5970 * 100
    _pat = _re.compile(
        r"^\s*" + str(_t) + r"\s+"
        + f"{_t * _t / 1e6:.2f}" + r" MP\s+"
        + str(_p["num_tiles"]) + r"\s+" + str(_seams) + r"\s+"
        + str(_ox // 4) + r" px\s+" + str(2 * int(_ox ** 0.5)) + r" px\s+"
        + r"画面宽的 " + f"{_widthpct:.1f}" + r"%\s*$", _re.M)
    if not _pat.search(_doc):
        print(f"   ✗ 使用说明的「小 tile 代价表」里 tile={_t} 这一行和算法对不上"
              f"（应为 {_t * _t / 1e6:.2f} MP / {_p['num_tiles']}块 / "
              f"{_seams}段缝 / 羽化带 {_ox // 4}px / 淡入 {2 * int(_ox ** 0.5)}px / "
              f"画面宽 {_widthpct:.1f}%）")
        doc_ok = False
# 「1024 的缝段是 2048 的 6.8 倍」这条结论必须成立
_s1024 = hm.plan(5970, 3840, tile_width=1024, tile_height=1024,
                 overlap=0.25, min_scale_factor=1.3)
_s2048 = hm.plan(5970, 3840, tile_width=2048, tile_height=2048,
                 overlap=0.25, min_scale_factor=1.3)
_n1 = (_s1024["grid_x"] - 1) * _s1024["grid_y"] + (_s1024["grid_y"] - 1) * _s1024["grid_x"]
_n2 = (_s2048["grid_x"] - 1) * _s2048["grid_y"] + (_s2048["grid_y"] - 1) * _s2048["grid_x"]
if round(_n1 / _n2, 1) != 6.8:
    print(f"   ✗ 「1024 的缝段是 2048 的 6.8 倍」不成立了（现在是 "
          f"{_n1 / _n2:.1f} 倍：{_n1} vs {_n2}）")
    doc_ok = False
# 「1024 想补回羽化带要提到 1/2 重叠、会变成 135 块」这条也要成立
if hm.plan(5970, 3840, tile_width=1024, tile_height=1024,
           overlap=0.5, min_scale_factor=1.3)["num_tiles"] != 135:
    print("   ✗ 「tile 1024 + 1/2 重叠 = 135 块」不成立了")
    doc_ok = False

# (c7) ctrl3_max_size 那张表必须由源码公式现算（_build_global_thumb：
# scale = min(cap/宽, cap/高)，scale ≥ 1 就原样返回）。它是回答
# 「ctrl3_max_size 会不会影响精细度」的核心证据：cap 只把整幅缩略图缩到
# 长边以内，**不改块画布 / 输出尺寸 / 块数**；而 cap ≥ 输出长边时它等于没接。
# 尺寸是 int 截断的（不是四舍五入），所以是 493 / 1975 这种尾数。
_UW, _UH = 7960, 5120


def _thumb(cap):
    _s = min(cap / _UW, cap / _UH)
    return (_UW, _UH) if _s >= 1.0 else (int(_UW * _s), int(_UH * _s))


for _cap in (256, 768, 2048, 3072):
    _tw, _th = _thumb(_cap)
    _pat = _re.compile(r"^\s*" + str(_cap) + r"\s+"
                       + str(_tw) + r"×" + str(_th) + r"\s", _re.M)
    if not _pat.search(_doc):
        print(f"   ✗ 使用说明的 ctrl3_max_size 表里 cap={_cap} 这一行和源码对不上"
              f"（应为 {_tw}×{_th}）")
        doc_ok = False
if "≥7960" not in _doc:
    print("   ✗ 使用说明没写出 ctrl3_max_size 的失效边界（cap ≥ 输出长边）")
    doc_ok = False
# cap ≥ 输出长边时源码走 scale ≥ 1 分支 —— 这个边界必须真的成立
if _thumb(7960) != (7960, 5120) or _thumb(8192) != (7960, 5120):
    print("   ✗ ctrl3_max_size 的失效边界不成立了（cap ≥ 输出长边应原样返回）")
    doc_ok = False
# 768 → 2048 的增益：线性 ×2.67、像素 ×7.1（文档里引用了这两个数）
_lin = 2048 / 768
if round(_lin, 2) != 2.67 or round(_lin * _lin, 1) != 7.1:
    print(f"   ✗ 「768 → 2048 线性 ×2.67、像素 ×7.1」不成立了（现为 "
          f"{_lin:.2f} / {_lin * _lin:.1f}）")
    doc_ok = False

# (c8) cell_size 那张表：位置图 = cell_size × 3 见方（源码 grid_size = cell_size*3）。
for _cs in (256, 384, 512, 640, 768):
    _pat = _re.compile(r"^\s*" + str(_cs) + r"\s+" + str(_cs * 3) + r"×"
                       + str(_cs * 3) + r"\s", _re.M)
    if not _pat.search(_doc):
        print(f"   ✗ 使用说明的 cell_size 表里 cell_size={_cs} 这一行对不上"
              f"（位置图应为 {_cs * 3}×{_cs * 3}）")
        doc_ok = False
# 「位置图吃掉了整轮 VAE 编码量的约 36%」：位置图和 tile_latent 都是每块编码
# 一次，所以这个比例与块数无关，只跟 cell_size 和 tile 有关。
_pos_mp = (512 * 3) ** 2 / 1e6
_share = _pos_mp / (_pos_mp + 2048 * 2048 / 1e6) * 100
if round(_share) != 36:
    print(f"   ✗ 「位置图占 VAE 编码量约 36%」不成立了（现为 {_share:.1f}%）")
    doc_ok = False

# (c9) 文档里那张「档位 × 参考形态 → 有没有 Image 编号」的表必须和代码一致。
# 这是回答「我只传一张图为什么提示词里有图1图2图3」的核心依据，
# 也是界面上那行实时提示换口的依据。逐组合现算，别信手抄。
for _tier in ("full_build", "texture", "subject_less"):
    for _mr, _mname in ((True, "三参考"), (False, "单参考")):
        _out = T.assemble({"scene": "a landscape", "painterly": "",
                           "multi_reference": _mr}, _tier)
        _has = "Image 1" in _out
        _pat = _re.compile(r"^\s*" + _tier + r"\s+" + _mname + r"\s+([✅❌])", _re.M)
        _m = _pat.search(_doc)
        if not _m:
            print(f"   ✗ 使用说明缺「{_tier} × {_mname}」这一行")
            doc_ok = False
            continue
        if (_m.group(1) == "✅") != _has:
            print(f"   ✗ 使用说明的 Image 编号表里 {_tier} × {_mname} 标错了"
                  f"（代码里{'有' if _has else '没有'}）")
            doc_ok = False

for _must, _why in [
        ("方向对，但「越小越好」是错的", "使用说明没回答「tile 越小细节越好吗」"),
        ("工具为什么默认 2048", "使用说明没解释工具默认 2048 的来由"),
        ("对齐用 2048 ≠ 画质用 2048",
         "使用说明没把「对齐用」和「画质用」这两个问题分开"),
        ("别选 1280", "使用说明没点出 1280 和 2048 输出相同、1280 却要 40 块"),
        ("各赢一半", "使用说明把 1536 和 1792 的关系说成了「全面压住」"),
        ("它和 tile 要怎么配合", "使用说明没正面回答「min_overlap 要不要和 tile 配合」"),
        ("实际重叠 = (tile × 块数 − 输出尺寸) ÷ (块数 − 1)",
         "使用说明没给「实际重叠 ≠ 名义重叠」的公式"),
        ("多切几块能不能换到更多精细度",
         "使用说明没回答「块数多了精细度会不会更高」"),
        ("tile1792 + 1/4", "使用说明没给「同样 28 块但更划算」的替代配置"),
        ("单独调大 min_scale_factor", "使用说明没点出 min_scale_factor 不是画质杠杆"),
        ("局部最锐、全局最差",
         "使用说明没明确回答「1024 是不是画质最好」"),
        ("先试 **1280**", "使用说明没给「想验证再小一点」的低成本实验步骤"),
        ("全局变暗", "使用说明没把「全局变暗」和「色带」区分开"),
        ("My suggestion is to do 2 or 3 repeated lower",
         "使用说明没引用官方「多跑几趟小的」这条便签"),
        ("cell_size 和 ctrl3_max_size 会影响精细度吗",
         "使用说明没回答「cell_size / ctrl3_max_size 是否影响精细度」"),
        ("两个都不是「精细度」旋钮",
         "使用说明没明确否定这两个是精细度旋钮"),
        ("位置图吃掉了整轮 VAE 编码量的约 36%",
         "使用说明没给出位置图的编码开销"),
        ("跟 tile 无关", "使用说明没说位置图开销与 tile 无关"),
        ("真正提高精细度的杠杆", "使用说明没给精细度杠杆排序"),
        ("index 0 = tile_latent",
         "使用说明没说清三个 RefLatentWeight 的 index 对应关系"),
        ("真正的死值", "使用说明没点出 low_freq_radius 是死值"),
        ("「精细度」页", "使用说明没提界面上那个「精细度」页"),
        ("会跟着 tile 实时变", "使用说明没说参数区那行精细度判断是实时的")]:
    if _must not in _doc:
        print(f"   ✗ {_why}")
        doc_ok = False

# GUI 的 tile 提示必须写明「默认 2048 不是画质推荐值」——用户就是看了
# 默认值才以为 2048 画质最好。
_gui_src = open(os.path.join(BASE, "gui.py"), encoding="utf-8").read()
for _must, _why in [("不是画质推荐值", "gui.py 的 tile 提示没写明默认值不是画质推荐"),
                    ("1536 ~ 1792", "gui.py 的 tile 提示没给出画质推荐区间")]:
    if _must not in _gui_src:
        print(f"   ✗ {_why}")
        doc_ok = False

# (c4) 「多切几块 ≠ 更精细」这段的论据是**每块画布 / 每块覆盖源图 / 每块倍数**
# 三项在两档下完全相同。锁住这个前提 —— 它一旦不成立，整段论证就垮了。
_p1 = hm.plan(5970, 3840, tile_width=2048, tile_height=2048,
              overlap=0.5, min_scale_factor=1.3)
_p2 = hm.plan(5970, 3840, tile_width=2048, tile_height=2048,
              overlap=0.25, min_scale_factor=1.3)
if not (_p1["upscaled_width"] == _p2["upscaled_width"]
        and _p1["upscaled_height"] == _p2["upscaled_height"]
        and _p1["effective_scale_x"] == _p2["effective_scale_x"]):
    print("   ✗ 「1/4 与 1/2 输出尺寸相同」这个前提不成立了，"
          "使用说明里那段论证要重写")
    doc_ok = False
if _p1["num_tiles"] <= _p2["num_tiles"]:
    print(f"   ✗ 1/2 Tile 的块数（{_p1['num_tiles']}）没有多于 1/4 "
          f"（{_p2['num_tiles']}），「多跑的块全是重复」这个论据失效")
    doc_ok = False
# 同样 28 块但更划算的那组替代配置：tile 1792 + 1/4 Tile
_p3 = hm.plan(5970, 3840, tile_width=1792, tile_height=1792,
              overlap=0.25, min_scale_factor=1.3)
if _p3["num_tiles"] != _p1["num_tiles"]:
    print(f"   ✗ tile1792 + 1/4 的块数（{_p3['num_tiles']}）和 tile2048 + 1/2"
          f"（{_p1['num_tiles']}）不再相同，「同样 28 块但更好」的结论失效")
    doc_ok = False
if not (_p3["upscaled_width"] > _p1["upscaled_width"]
        and _p3["upscaled_height"] > _p1["upscaled_height"]):
    print("   ✗ tile1792 + 1/4 的输出不再大于 tile2048 + 1/2")
    doc_ok = False
# 反面守卫：不能写「作者在 v1.3.0 把默认值从 1024 提到 1536」这种没有出处
# 的版本变更。仓库里既没有 CHANGELOG 也没有 release/tag，无法佐证。
# 能佐证的只有源码事实：tile_width / tile_height 的 default=1536。
if "v1.3.0" in _doc:
    print("   ✗ 使用说明里出现了无法佐证的版本号（仓库没有 CHANGELOG / release）")
    doc_ok = False
if G.DEFAULT_FONT_SCALE != 1.2:
    print(f"   ✗ DEFAULT_FONT_SCALE = {G.DEFAULT_FONT_SCALE}，应该是 1.2")
    doc_ok = False
# tile 默认值必须是 2048。官方示例工作流里 Plan 节点显示的 1536 是死值，
# 真正的 tile_width / tile_height 来自 PrimitiveInt（2048）。
if str(getattr(G, "DEFAULT_TILE", None)) != "2048":
    print(f"   ✗ DEFAULT_TILE = {getattr(G, 'DEFAULT_TILE', None)}，应该是 2048"
          "（官方示例工作流的实际取值；显示的 1536 是死值）")
    doc_ok = False
if 'value="1536"' in open(os.path.join(BASE, "gui.py"), encoding="utf-8").read():
    print("   ✗ gui.py 里还留着硬编码的 tile 默认值 \"1536\"")
    doc_ok = False
if _cfg_before is not None:
    with open(_cfg_path, "rb") as _f:
        if _f.read() != _cfg_before:
            print("   ✗ 自检改动了 config.json（不该写盘）")
            doc_ok = False
print("[10] 文档与默认值一致", "通过" if doc_ok else "失败")
if not doc_ok:
    fails.append("文档/默认值")

_sd = settings_doc.build(dict(hm.analyze(1024, 1536),
                              overlap_label="1/4 Tile", min_scale_factor=1.3))
if "决定输出尺寸" not in _sd:
    print("   ✗ 设置说明里没有提醒 min_overlap 会改变输出尺寸")
    fails.append("设置说明缺重叠提醒")
# 用户实测报的两个问题：色调每趟变暗 / 塑料感重。设置说明必须给出成因和解法。
for _must, _why in [
        ("死值", "设置说明没提 Plan 节点的 tile 是死值这件事"),
        ("PrimitiveInt", "设置说明没点名是 PrimitiveInt 覆盖了 tile 尺寸"),
        ("#25", "设置说明没给出改 tile 要去哪两个节点（#25 / #24）"),
        ("Flux2Scheduler.width", "设置说明没说改 PrimitiveInt 会一并改调度器分辨率"),
        ("Flux2KleinColorAnchor", "设置说明没提色彩锚定节点"),
        ("BYPASS", "设置说明没说色彩锚定节点默认是 BYPASS 的"),
        ("conditioning 接上", "设置说明没提色彩锚定节点的 conditioning 必须接"),
        ("ramp_curve", "设置说明没提 4 步采样要把 ramp_curve 调到 2~4"),
        ("RandomNoise", "设置说明没提 seed 随机化会让每次色调都不同"),
        ("ctrl3_max_size", "设置说明没提 ctrl3_max_size 768→2048"),
        ("photorealistic", "设置说明没指出 photorealistic 这句是塑料感的来源"),
        ("evenly-toned", "设置说明没指出「平涂区域变灰变暗」的成因"),
        ("painterly", "设置说明没提这一档没有 painterly 子句"),
        ("每一遍的画布大小", "设置说明没把 tile 尺寸解释成「每一遍的画布」"),
        ("重叠和 tile 怎么配合", "设置说明没解释 min_overlap 与 tile 的配合关系"),
        ("不是画质推荐值", "设置说明没点出默认 2048 只是对齐、不是画质推荐"),
        ("RefLatentWeight", "设置说明没把「调高权重 = 更贴源图」列为第一手段"),
        ("不是「精细度」旋钮",
         "设置说明没说清 cell_size / ctrl3_max_size 不是精细度旋钮"),
        ("每块编码一次", "设置说明没提位置图是每块编码一次"),
        ("完全不参与计算", "设置说明没提 low_freq_radius 在 reduce=0 时是死值"),
        ("不改块画布", "设置说明没说 ctrl3_max_size 不改块画布/输出尺寸/块数")]:
    if _must not in _sd:
        print(f"   ✗ {_why}")
        fails.append(f"设置说明缺：{_must}")

# 重叠对比是「现算」的，必须真的会触发。锁一个用户实测过的组合：
# 源图 5970×3840、tile 2048、min_overlap 1/2 Tile —— 1/2 和 1/4 输出
# 完全相同（7960×5120），但 1/2 要 28 块、1/4 只要 15 块。
_sd_ov = settings_doc.build(
    dict(hm.analyze(5970, 3840, tile_width=2048, tile_height=2048,
                    overlap=0.5, min_scale_factor=1.3),
         overlap_label="1/2 Tile", min_scale_factor=1.3))
for _must, _why in [
        ("7960×5120", "重叠对比没算出 1/2 Tile 的输出尺寸"),
        ("完全相同", "重叠对比没识别出「输出相同但块数更多」这种白花时间的情况"),
        ("建议把 Plan 的", "重叠对比没给出「改回 1/4 Tile」的建议"),
        ("28 vs 15", "重叠对比的块数没算对")]:
    if _must not in _sd_ov:
        print(f"   ✗ {_why}")
        fails.append(f"重叠对比缺：{_must}")
# 反面：1/4 Tile 时不该报「白花时间」（它本身就是被推荐的那一档）
_sd_q = settings_doc.build(
    dict(hm.analyze(5970, 3840, tile_width=2048, tile_height=2048,
                    overlap=0.25, min_scale_factor=1.3),
         overlap_label="1/4 Tile", min_scale_factor=1.3))
if "建议把 Plan 的" in _sd_q:
    print("   ✗ 已经是 1/4 Tile 了，重叠对比还在劝人改回 1/4 Tile")
    fails.append("重叠对比误报")

# ctrl3_max_size 的「活 / 死」两态必须都能真的触发。
# 活态：用户实测配置（cap 768、输出 7960×5120）→ 应算出缩略图 768×493。
# 死态：cap ≥ 输出长边 → 源码 _build_global_thumb 走 scale ≥ 1 分支，
#       整幅原样返回，这个参数等于没接。
_sd_info = dict(hm.analyze(5970, 3840, tile_width=2048, tile_height=2048,
                           overlap=0.5, min_scale_factor=1.3),
                overlap_label="1/2 Tile", min_scale_factor=1.3)
_sd_cap = settings_doc.build(_sd_info)
for _must, _why in [("768×493", "ctrl3_max_size 的缩略图尺寸没按源码算对"),
                    ("不改块画布", "设置说明没说 ctrl3_max_size 不改块画布")]:
    if _must not in _sd_cap:
        print(f"   ✗ {_why}")
        fails.append(f"ctrl3 说明缺：{_must}")
if "没接" not in settings_doc.build(_sd_info, {"ctrl3_max_size": 8192}):
    print("   ✗ cap ≥ 输出长边时，设置说明没提示这个参数等于没接")
    fails.append("ctrl3 失效边界未提示")

# ---------- 11 texture 档：不照抄词典、也不漏掉大宗材质 ----------
# 用户实测报过两个问题：
#   (a) 图是手绘壁纸，texture_survey 却输出「皮肤 / 头发 / 嘴唇」一整串
#       —— 把措辞词典当答案抄了回来。texture 档的槽位是**直接拼进提示词**的，
#          模型会照着把这些材质真的造出来，所以这是内容污染，不是风格问题。
#   (b) 图是「植物、树木、花、鸟」，输出里却只有 wood-grain texture ——
#       植物和花全漏了，而且活树被写成了「木料」（wood-grain 是家具/地板用的）。
#   (c) 为了补 (b)，给画出来的花鸟树石安上了**真实材质的微观结构**
#       （bark / 叶脉 veining / 羽枝 barbs）→ 模型在平涂画面上叠了一层写实
#       材质，出图明显偏离原图。用户实测反馈：「没改之前的提示词好，
#       生成的图片更符合原图」。
# 这里把三件事都锁住。
_copied = ("natural matte skin with pore-level texture; fine individual hair strands "
           "with flyaways; soft layered feathers with their barbs and downy texture; "
           "glossy patterned fabric with its print and folds; soft matte velvet with "
           "directional nap; coarse woven fabric texture; detailed eyes with iris "
           "fibres; fine individual eyelashes and eyebrow hairs; the soft sheen of "
           "the lips")
# ⚠ 这个「正确样本」**全部用词典措辞**——那本来就是这一档的设计意图。
#   它绝不能被误判成照抄，否则工具会拒绝输出完全正确的答案。
#   （整幅是手绘壁纸时的正确形态：底色 + 一条 painted …，不要微观特征词。）
_legit = ("flat painted pigment fields with their brushwork; "
          "painted foliage, flowers, and plumage")
_sys = vision.build_system_prompt("texture")
copy_ok = True

# (a) 照抄判定：抄的必须拦住，正确的不能误杀
if not T.survey_is_copied(_copied):
    print("   ✗ 照抄词典的输出没被判定为「照抄」")
    copy_ok = False
if T.survey_is_copied(_legit):
    print("   ✗ 正确回答（全用词典措辞）被误判为「照抄」")
    copy_ok = False
if T.survey_is_copied(""):
    print("   ✗ 空 survey 被误判为「照抄」")
    copy_ok = False

# (b) 词典必须覆盖自然材质，且活树 ≠ 木料
_rows = dict(T.TEXTURE_DESCRIPTORS)
_zh = " ".join(_rows)
for _need, _why in [("花瓣", "词典里没有花瓣 / 花朵这一行"),
                    ("草", "词典里没有草 / 地被这一行"),
                    ("枝叶", "词典里没有活树的枝叶 / 树皮这一行"),
                    ("木料", "词典里没把木料和活树分开")]:
    if _need not in _zh:
        print(f"   ✗ {_why}")
        copy_ok = False
if _rows.get("木料 / 木板（家具、地板、画框）") != "wood-grain texture":
    print("   ✗ 「木料」那行没指向 wood-grain texture")
    copy_ok = False

# (b2) 「整幅是画」的落脚点必须存在，而且画出来的东西**不许**带真实材质的
#      微观结构词 —— 这正是上一轮把保真度做坏的根因，必须锁住。
_painted_zh = [zh for zh in _rows if "画出来的" in zh]
if len(_painted_zh) != 1:
    print(f"   ✗ 词典里应有且只有一条「画出来的…」行（现有 {len(_painted_zh)} 条）")
    copy_ok = False
else:
    _painted_en = _rows[_painted_zh[0]]
    if "painted" not in _painted_en:
        print(f"   ✗ 「{_painted_zh[0]}」的措辞里没有 painted：{_painted_en!r}")
        copy_ok = False
    for _bad in ("bark", "barbs", "downy", "veining", "wood-grain", "pore-level"):
        if _bad in _painted_en:
            print(f"   ✗ 「画出来的…」那行混进了真实材质微观结构词：{_bad!r}")
            copy_ok = False
# 底色的措辞必须保住 brushwork —— 它是「保住平涂笔触」的信号，拿掉反而掉保真度。
if _rows.get("平涂颜料（手绘、壁纸、绘画的底色）") \
        != "flat painted pigment fields with their brushwork":
    print("   ✗ 「平涂颜料」那行的措辞被改了（应为 flat painted pigment fields "
          "with their brushwork）")
    copy_ok = False
# veining 只属于「半透明背光有机物」，不该出现在词典的任何一行里
for _zh2, _en2 in T.TEXTURE_DESCRIPTORS:
    if "veining" in _en2:
        print(f"   ✗ 词典里 {_zh2!r} 又混进了 veining —— 那是真实材质的微观结构")
        copy_ok = False

# (c) 提示词里给模型看的词库，必须和判定用的词库同源。
#     漂了以后 survey_is_copied 就认不出提示词里发出去的措辞，检测等于失效。
_table_rows = [l.split("→", 1)[1].strip() for l in vision.TEX_TABLE.splitlines()
               if l.startswith("- ")]
_bank_rows = [en for _, en in T.TEXTURE_DESCRIPTORS]
if _table_rows != _bank_rows:
    print(f"   ✗ TEX_TABLE 与 TEXTURE_DESCRIPTORS 不一致"
          f"（{len(_table_rows)} 条 vs {len(_bank_rows)} 条）")
    copy_ok = False

# (d) 系统提示里要真的写了这些约束
# ⚠ 「不是答案」这个说法 2026-09-27 改成了「照抄整张表 = 失败」：
#   为了给 md 的逐字描述符腾出上下文预算，把 TEX_TABLE 表头里那句
#   和上面小标题重复的说明压短了。约束本身没丢，只是换了更直接的写法。
for _must in ("surfaces_seen", "照抄整张表", "没有皮肤、没有头发、没有嘴唇",
              "也不许漏", "活着的植物", "painted foliage", "微观结构",
              "保真优先于描述详尽"):
    if _must not in _sys:
        print(f"   ✗ texture 系统提示里缺约束：{_must!r}")
        copy_ok = False
# 旧版写的是「材质描述词库（优先取这里的措辞）」——那句话本身就是叫模型照抄
if "优先取这里的措辞" in _sys:
    print("   ✗ 系统提示里还留着「优先取这里的措辞」，那是在叫模型照抄")
    copy_ok = False

# (e) 模型写列表时爱留尾分号，模板是 `— {槽位} —`，
#     漏进去就会拼出 "… texture; — recovering"
_tail_p = T.assemble_texture({"texture_survey": "pigment fields; feathers; leaves,"})
_tail_s = T.assemble_subject_less({"texture_families": "fabric, skin, and"})
for _p, _tag in ((_tail_p, "texture"), (_tail_s, "subject_less")):
    if re.search(r"[,;，；]\s+—", _p) or re.search(r"\band\s+wherever", _p):
        print(f"   ✗ {_tag} 档槽位结尾的杂标点漏进了提示词")
        copy_ok = False
# (f) 「只扫了一两条就交卷」也要能被识别 —— 用户报的第二个问题就是漏了植物和花。
#     实测里模型 surfaces_seen 只有 2 条，自己的 notes 却写着「有树叶、树枝和树干」。
_thin = {"texture_survey": "flat painted pigment fields with their brushwork; "
                           "soft layered feathers with their barbs and downy texture",
         "surfaces_seen": ["手绘壁纸", "羽毛"],
         "notes": "背景是植物和树枝"}
if vision._texture_problem(_thin)[0] != "thin":
    print("   ✗ 只扫了两条表面的输出没被识别为「扫得不全」")
    copy_ok = False
if vision._texture_problem({"texture_survey": _legit,
                            "surfaces_seen": ["壁纸底色", "羽毛", "枝叶", "花瓣"]})[0] \
        is not None:
    print("   ✗ 正常输出被误判为有问题")
    copy_ok = False
# (g) 重试机制本身要能跑通。用假后端，不联网、不调模型：
#     第一次「扫得不全」→ 应该重问一次并采用第二次结果；
#     一直「照抄」→ 必须报错，绝不把凭空造的材质交付出去。
class _FakeBackend:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0

    def analyze(self, *a, **kw):
        self.calls += 1
        return self.replies[min(self.calls - 1, len(self.replies) - 1)]


_ginfo = hm.analyze(1024, 1536)
_good = {"texture_survey": _legit,
         "surfaces_seen": ["壁纸底色", "羽毛", "枝叶", "花瓣"]}
_fb = _FakeBackend([_thin, _good])
_res = vision.analyze_image(_fb, "不用真的读图", "texture", _ginfo)
if _fb.calls != 2:
    print(f"   ✗ 「扫得不全」没有触发重试（只调用了 {_fb.calls} 次）")
    copy_ok = False
if _res.get("texture_survey") != _legit:
    print("   ✗ 重试后没有采用第二次的结果")
    copy_ok = False
if _res.get("_warnings"):
    print("   ✗ 重试后已经正常，却还挂着提醒")
    copy_ok = False

_fb2 = _FakeBackend([{"texture_survey": _copied,
                      "surfaces_seen": ["皮肤", "头发", "嘴唇"]}])
try:
    vision.analyze_image(_fb2, "不用真的读图", "texture", _ginfo)
    print("   ✗ 连续两次照抄竟然没有报错")
    copy_ok = False
except vision.BackendError:
    pass

print("[11] texture 档不照抄、不漏材质", "通过" if copy_ok else "失败")
if not copy_ok:
    fails.append("texture 档照抄/漏材质")

# ---------- 12 档位判定边界（对齐仓库的决策规则）----------
# 仓库 llm_prompt_templates/README.md 的 decision rule：
#   Result ≲4K   → grid 1×1~3×2-ish → full_build
#   Result 4K–8K → grid 4×3~6×4     → texture
#   Result 8K+   → subject_less
# 主 README 另有一条 tie-break：「If a pass straddles two tiers, pick the less
# specific one.」→ 所以 texture 的上界取**网格上界 24**，不取「8K 对应的 ~35 块」。
# 锁住这几个边界：曾经阈值是 20，导致结果落在 4K–8K、网格 6×4（24 块）的图
# 被误判成 subject_less，比仓库规定更激进。
_tier_cases = [
    # (块数, 结果长边, 期望档位)
    (1, 2048, "full_build"),
    (6, 4608, "full_build"),
    (12, 4800, "texture"),
    (24, 6656, "texture"),        # ← 就是这条：阈值 20 时会错判成 subject_less
    (25, 6656, "subject_less"),
    (40, 9216, "subject_less"),
    (4, 13000, "subject_less"),   # 结果极大，即使块少也要退档
]
tier_ok = True
for _n, _le, _want in _tier_cases:
    _got = hm.pick_tier(_n, _le)
    if _got != _want:
        print(f"   ✗ {_n}块 / 长边{_le} → {_got}，应为 {_want}")
        tier_ok = False
# 端到端：4000x3000 在官方 tile 1536 下落进 4K–8K 带，仓库规定是 texture
_a = hm.analyze(4000, 3000, tile_width=1536, tile_height=1536, overlap=0.25,
                min_scale_factor=1.3)
if _a["tier"] != "texture":
    print(f"   ✗ 4000x3000 / tile1536 → {_a['tier']}"
          f"（长边{_a['long_edge']}，{_a['num_tiles']}块），应为 texture")
    tier_ok = False
# texture 档必须带着「改档出口」的说明 —— 仓库那条内容判据（密集重复主体 /
# 大面积空白）纯像素算不出来，只能靠看图，走 tier_override 回传。
if "tier_override" not in vision.build_system_prompt("texture") \
        or "密集重复主体" not in vision.build_system_prompt("texture"):
    print("   ✗ texture 提示词里没有 tier_override 的使用说明")
    tier_ok = False
# 列表档不该带分块规则（光照 / 环境块那几条），那是 full_build 的
if "光照要写清光源" in vision.build_system_prompt("texture"):
    print("   ✗ texture 提示词里混进了 full_build 的分块规则")
    tier_ok = False
if "光照要写清光源" not in vision.build_system_prompt("full_build"):
    print("   ✗ full_build 提示词丢了分块规则")
    tier_ok = False
# 改档时必须把槽位一起带过去。texture 判出「密集重复主体 / 大面积空白」会改档到
# subject_less，但它手里只有 texture_survey —— 若退回通用兜底串，就等于把用户
# 这张图的信息全丢掉，拼出一段「fabric, skin, hair, metal…」的万能话。实测过。
_x = {"texture_survey": "leaves, bark, and twigs, each its own texture"}
_p = T.assemble_subject_less(_x)
if "leaves, bark, and twigs" not in _p or "fabric, skin, hair, metal" in _p:
    print("   ✗ texture→subject_less 改档时丢了槽位（退回了通用兜底串）")
    tier_ok = False
if "fur and wool" not in T.assemble_texture(
        {"texture_families": "fur and wool, fleece, and skin"}):
    print("   ✗ subject_less→texture 改档时丢了槽位")
    tier_ok = False
print("[12] 档位判定边界", "通过" if tier_ok else "失败")
if not tier_ok:
    fails.append("档位边界")

# ---------- 13 md 模板一致性（用户反馈过「提示词跑偏了」） ----------
# 固定块被改写 / 截短不会报错，只会让 LoRA 的训练信号被稀释。
# md 已随项目存档在 llm_prompt_templates/，所以这一步离线可跑。
try:
    _md_ok, _md_bad = md_conform.check()
    print("[13] md 模板一致性", "通过" if not _md_bad else "失败")
    for _line in _md_bad:
        print("   ✗", _line)
    if _md_bad:
        fails.append("md 一致性")
except Exception as _e:
    print("[13] md 模板一致性 失败:", _e)
    fails.append("md_conform")

# ---------- 14 md 的 Always 块必须真的落进提示词 ----------
# md 的 Skeleton 表把第 2/4/5/7 块标成 Always（第 4 块还标了 core slot）。
# 只看 prompt 查不出来 —— 块缺失时 prompt 里什么都没留下，所以必须带 data。
try:
    _blk = True
    # (a) 材质一条都没有 → 必须报「第 4 块缺失」
    _p = T.assemble_full_build({"scene": "this portrait", "mode": ["scene"],
                                "lighting": "warm soft directional interior light",
                                "fine_details": ["Hold the glowing halo as a smooth ring."]})
    _ok, _w = T.validate(_p, "full_build",
                         {"mode": ["scene"],
                          "lighting": "warm soft directional interior light",
                          "fine_details": ["x"]})
    if not any("第 4 块" in w for w in _w):
        print("   ✗ 一条材质都没填，却没报「第 4 块（core slot）缺失」")
        _blk = False
    # (b) 材质填了 → 不该报
    _p2 = T.assemble_full_build({"scene": "this portrait", "mode": ["scene"],
                                 "lighting": "warm soft directional interior light",
                                 "fine_details": ["Hold the halo."],
                                 "materials": [{"material": "polished steel armour",
                                                "sub_features": "rivets, plate seams",
                                                "guard": "reflections accurate and true to the source"}]})
    _ok2, _w2 = T.validate(_p2, "full_build",
                           {"lighting": "warm soft directional interior light",
                            "fine_details": ["x"]})
    if any("第 4 块" in w for w in _w2):
        print("   ✗ 材质明明填了，还报「第 4 块缺失」")
        _blk = False
    # (c) 光照退化成兜底串 → 必须报（md 写作规则 4 要求写具体）
    _ok3, _w3 = T.validate(_p2, "full_build", {"lighting": "", "fine_details": ["x"]})
    if not any("光照" in w for w in _w3):
        print("   ✗ 光照没写具体，却没报（md 写作规则 4）")
        _blk = False
    # (d) 绘画源图没给 Style 标签 → 必须报（md painterly 变体）
    _ok4, _w4 = T.validate(_p2, "full_build",
                           {"lighting": "warm light", "fine_details": ["x"],
                            "painterly": True, "style_medium": ""})
    if not any("Style 标签" in w for w in _w4):
        print("   ✗ 绘画源图没给 style_medium，却没报（md painterly 变体）")
        _blk = False
    # (e) 不给 data 时必须照旧工作（自检 [4] 就是这么调的）：
    #     只测文本的调用不能凭空报「第 N 块缺失」
    _ok5, _w5 = T.validate(_p2, "full_build")
    if any("第 " in w and "块" in w for w in _w5):
        print(f"   ✗ 不传 data 时不该报 Always 块的警告，现在报了：{_w5}")
        _blk = False
    print("[14] md 的 Always 块检查", "通过" if _blk else "失败")
    if not _blk:
        fails.append("Always 块")
except Exception as _e:
    print("[14] md 的 Always 块检查 失败:", _e)
    fails.append("always_blocks")

# ---------- 15 自检报告不能污染提示词 ----------
# 「复制提示词 / 导出 txt」读的都是 prompt_txt。旧版把【自检提醒】append 进
# prompt_txt，于是粘进 CLIPTextEncode 的提示词尾巴上多一段中文报告 ——
# 正好违反「提示词严格等于 md 模板」。现在报告必须落在独立的 warn_txt 里。
_FULL15 = {
    "scene": "this portrait", "mode": ["person"], "skin_variant": "young_adult",
    "eyes": True, "hair_detail": "the windblown wisps",
    "materials": [{"material": "polished steel armour",
                   "sub_features": "specular highlights, rivets, plate seams",
                   "guard": "reflections accurate and true to the source"}],
    "fine_details": ["Hold the glowing halo as a smooth, even ring of light."],
    "environment": {"background": "produce and shelving",
                    "depth": "shallow depth of field",
                    "soft_elements": "soft bokeh",
                    "state": "smooth, evenly filled discs"},
    "lighting": "warm, soft directional interior light",
    "grade": "muted desaturated", "luminance_noise": True, "notes": "自检",
}
_THIN15 = dict(_FULL15)
_THIN15["materials"] = []
_THIN15["fine_details"] = []
_THIN15["lighting"] = ""

try:
    _sep = True
    _r15 = tk.Tk()
    try:
        _s15 = ttk.Style()
        if "vista" in _s15.theme_names():
            _s15.theme_use("vista")
    except Exception:
        pass
    _a15 = G.App(_r15, dpi=G.get_system_dpi())
    _a15.cur = os.path.join(BASE, "界面预览.png")
    _a15.info = {"multi_reference": False, "tier": "full_build"}
    _a15._gen_key = None

    # (a) 填全 → 不该冒出提醒面板
    _a15.on_generated(dict(_FULL15), "full_build", None)
    if _a15.warn_frame.winfo_manager():
        print("   ✗ 填全了却弹出了自检提醒面板")
        _sep = False
    _body_ok = _a15.prompt_txt.get("1.0", "end")

    # (b) 漏填第 4/5/7 块 → 必须出提醒，且提醒**只在** warn_txt 里
    _a15.on_generated(dict(_THIN15), "full_build", None)
    _body = _a15.prompt_txt.get("1.0", "end")
    _wbody = _a15.warn_txt.get("1.0", "end")
    if not _a15.warn_frame.winfo_manager():
        print("   ✗ 漏了 Always 块，提醒面板却没弹出来")
        _sep = False
    if "第 4 块" not in _wbody:
        print("   ✗ 提醒面板里没有「第 4 块」那条")
        _sep = False
    # 表头不许写死成某一类提醒 —— 提醒有缺块 / 光照 / 禁用动词 / 槽位 / 重复
    # 好几类，写死会对着「禁用动词」那条说「缺了 Always 的块」。
    _head = _a15.warn_head.cget("text")
    if "自检提醒" not in _head or "Always" in _head:
        print(f"   ✗ 提醒面板表头写死了：{_head!r}")
        _sep = False
    for _mk in ("自检提醒", "通过项", "第 4 块", "第 5 块"):
        if _mk in _body:
            print(f"   ✗ 提示词正文里混进了自检报告（含「{_mk}」）—— "
                  f"复制/导出会把它一起带走")
            _sep = False
    # 正文必须仍然**逐字**是 assemble 的输出。
    # ⚠ 必须显式带上 multi_reference —— on_generated 会用 info 里的真实值覆盖它，
    #   而 assemble 自己的默认值是 True（三参考）。不写就等于换了开场白
    #   （Image 1 开头 vs Finish this image 开头），对不上。
    _want = T.assemble(dict(_THIN15, multi_reference=False), "full_build")
    if _body.strip() != _want.strip():
        print("   ✗ prompt_txt 的内容不等于 assemble 的输出（被追加过东西）")
        _sep = False
    # (c) 换图/清空要收起面板
    _a15.clear_view()
    if _a15.warn_frame.winfo_manager():
        print("   ✗ clear_view() 之后提醒面板还挂着")
        _sep = False
    _r15.destroy()
    print("[15] 自检报告不污染提示词", "通过" if _sep else "失败")
    if not _sep:
        fails.append("提醒面板")
except Exception as _e:
    print("[15] 自检报告不污染提示词 失败:", _e)
    fails.append("warn_panel")

# ---------- 16 参考形态必须同时决定「提示词」和「设置说明」 ----------
# 这两份是给同一个 ComfyUI 工作流用的，说岔了就会互相打架：
# 旧版设置说明永远写「Flux2KleinRefLatentWeight × 3」和「这三路就是提示词里
# 写的 Image 1/2/3」，而单参考的提示词里根本没有 Image 编号。
try:
    _ref = True
    _rb = dict(hm.analyze(1024, 1536, tile_width=2560, tile_height=2560,
                          overlap=0.25, min_scale_factor=1.3))
    _d3 = dict(_rb); _d3["multi_reference"] = True
    _d1 = dict(_rb); _d1["multi_reference"] = False
    _s3 = settings_doc.build(_d3)
    _s1 = settings_doc.build(_d1)
    # (a) 三参考：写三路 + 映射
    if "RefLatentWeight × 3" not in _s3:
        print("   ✗ 三参考的设置说明没写「× 3」")
        _ref = False
    if "Image 1 = tile_latent" not in _s3:
        print("   ✗ 三参考的设置说明丢了 Image 1/2/3 映射")
        _ref = False
    # (b) 单参考：不许再出现「× 3」和映射，且要说清只接一路
    if "RefLatentWeight × 1" not in _s1:
        print("   ✗ 单参考的设置说明没写「× 1」")
        _ref = False
    for _bad in ("RefLatentWeight × 3", "Image 1 = tile_latent",
                 "这三路就是提示词里写的"):
        if _bad in _s1:
            print(f"   ✗ 单参考的设置说明里还留着「{_bad}」—— 与提示词自相矛盾")
            _ref = False
    if "不接" not in _s1:
        print("   ✗ 单参考的设置说明没说位置图 / 全局图不接")
        _ref = False
    # (c) 两份设置说明确实不一样（防止哪天又被写回同一个常量）
    if _s1 == _s3:
        print("   ✗ 三参考与单参考的设置说明完全相同（没跟着参考形态变）")
        _ref = False
    print("[16] 参考形态同时决定提示词与设置说明", "通过" if _ref else "失败")
    if not _ref:
        fails.append("参考形态一致性")
except Exception as _e:
    print("[16] 参考形态同时决定提示词与设置说明 失败:", _e)
    fails.append("ref_mode")

# ---------- 17 槽位清洗 / 去重 / 禁用动词 ----------
# 2026-09-30 加的四项优化。都是「模型不守规矩时工具怎么兜」，
# 所以必须用**畸形输入**测，正常输入测不出来。
try:
    _cl = True
    _b = {"scene": "this portrait", "mode": ["person"], "skin_variant": "young_adult",
          "eyes": True, "lighting": "warm soft directional interior light",
          "grade": "muted desaturated"}

    # (a) 存在句开头要切掉
    if T._bare("there is a marble counter") != "marble counter":
        print(f"   ✗ 存在句没切干净：{T._bare('there is a marble counter')!r}")
        _cl = False
    # (b) 主语在前的系表句要翻回名词短语
    if T._bare("bokeh is soft") != "soft bokeh":
        print(f"   ✗ 系表句没翻回来：{T._bare('bokeh is soft')!r}")
        _cl = False
    # (c) 尾标点要去掉（否则会把句子劈成两半）
    if T._bare("discs are smooth.") != "smooth discs":
        print(f"   ✗ 尾标点没去掉 / 没翻：{T._bare('discs are smooth.')!r}")
        _cl = False
    # (d) scene 槽位模板里不带冠词，**必须保留**冠词
    if T._strip_copula("this is a portrait") != "a portrait":
        print(f"   ✗ scene 的冠词被误删：{T._strip_copula('this is a portrait')!r}")
        _cl = False
    # (e) 翻不动的不许硬翻（含介词 / 分词 → 翻了会出笑话）。
    #     ⚠ 期望值要带上「冠词会被剥掉」这一步：_bare 的职责就是给
    #       已经带冠词的模板喂裸名词短语，所以 "the light …" 变成
    #       "light …" 是**对的**；要断的是**语序有没有被翻**
    #       （翻了会变成 "coming from the left light"）。
    _hard = "the light is coming from the left"
    if T._bare(_hard) != "light is coming from the left":
        print(f"   ✗ 该保留的整句被硬翻了：{T._bare(_hard)!r}")
        _cl = False

    # 拼出来的句子必须通顺（这是这一项的意义所在）
    _d = dict(_b)
    _d["environment"] = {"background": "there is a marble counter",
                         "depth": "it is shallow", "soft_elements": "bokeh is soft",
                         "state": "discs are smooth."}
    _p = T.assemble_full_build(_d, False)
    if "Keep the marble counter naturally detailed within its existing shallow, " \
       "holding the soft bokeh as smooth discs." not in _p:
        print("   ✗ 环境句拼出来不通顺")
        _cl = False
    # (e2) 修不动的要报出来，而不是闷声拼进去
    _d2 = dict(_b)
    _d2["environment"] = {"background": "a counter", "depth": _hard,
                          "soft_elements": "bokeh", "state": "discs"}
    _ok, _w = T.validate(T.assemble_full_build(_d2, False), "full_build",
                         dict(_d2, multi_reference=False))
    if not any("整句" in w for w in _w):
        print("   ✗ 修不动的整句没报出来")
        _cl = False

    # (f)(g) 完全重复 → 自动去重 + 提醒
    _d3 = dict(_b)
    _mat = {"material": "polished steel armour", "sub_features": "rivets",
            "guard": "reflections accurate and true to the source"}
    _d3["materials"] = [dict(_mat), dict(_mat)]
    _d3["fine_details"] = ["Resolve the rivets as crisp, well-defined detail."] * 2
    _p3 = T.assemble_full_build(_d3, False)
    if _p3.count("On the polished steel armour") != 1:
        print("   ✗ 完全重复的材质没去重")
        _cl = False
    if _p3.count("Resolve the rivets as crisp") != 1:
        print("   ✗ 完全重复的精细元素没去重")
        _cl = False
    _ok3, _w3 = T.validate(_p3, "full_build", dict(_d3, multi_reference=False))
    if not any("重复" in w for w in _w3):
        print("   ✗ 去重了但没告诉用户（静默去重同样是看不出来）")
        _cl = False
    # (h) 同材质、子特征不同 → 都保留，但提醒
    _d4 = dict(_b)
    _d4["materials"] = [dict(_mat),
                        dict(_mat, sub_features="plate seams")]
    _p4 = T.assemble_full_build(_d4, False)
    if _p4.count("On the polished steel armour") != 2:
        print("   ✗ 子特征不同的两条材质被误删了")
        _cl = False
    _ok4, _w4 = T.validate(_p4, "full_build", dict(_d4, multi_reference=False))
    if not any("同一材质" in w for w in _w4):
        print("   ✗ 同一材质写多条没提醒")
        _cl = False

    # (i) 禁用动词：md 规则 2 的四个都要报，前缀词不许误报
    _tpl = ("RFNTILE. refine and add detail to this upscaled tile. Resolve the {} rivets. "
            "Clean up degradation from the source: suppress compression artifacts, colour "
            "banding, and aliasing, and render smooth, even tonal gradients and clean, "
            "clear shadow detail. Keep the content identical to the source image.")
    for _v in ("fix", "enhance", "improve", "make it better"):
        _o, _ww = T.validate(_tpl.format(_v), "full_build")
        if not any("变换类" in w for w in _ww):
            print(f"   ✗ 禁用动词 {_v!r} 没报（md 写作规则 2）")
            _cl = False
    for _v in ("prefix", "suffix", "fixture"):
        _o, _ww = T.validate(_tpl.format(_v), "full_build")
        if any("变换类" in w for w in _ww):
            print(f"   ✗ {_v!r} 被误判成禁用动词（子串匹配的坑）")
            _cl = False

    # (j) environment 缺槽位被通用词顶上 → 必须提醒
    _d5 = dict(_b)
    _d5["environment"] = {"background": "produce and shelving"}
    _ok5, _w5 = T.validate(T.assemble_full_build(_d5, False), "full_build",
                           dict(_d5, multi_reference=False))
    if not any("environment 缺了" in w for w in _w5):
        print("   ✗ environment 被通用词顶上却没提醒")
        _cl = False
    print("[17] 槽位清洗 / 去重 / 禁用动词", "通过" if _cl else "失败")
    if not _cl:
        fails.append("槽位清洗")
except Exception as _e:
    print("[17] 槽位清洗 / 去重 / 禁用动词 失败:", _e)
    fails.append("slot_clean")

print()
print("=" * 60)
print("总体：", "全部通过" if not fails and not issues and not squeeze else "有问题，见上")
