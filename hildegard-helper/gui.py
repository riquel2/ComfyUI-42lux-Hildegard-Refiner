# -*- coding: utf-8 -*-
"""Hildegard 放大助手 —— 本地 tkinter 界面。

单张精修工作流：选一张图 → 看参数 → 生成提示词 → 复制到 ComfyUI。
参数区改动会立刻重算输出尺寸 / 网格 / 块数 / 档位。

字号：工具栏右侧可实时切换（小/标准/大/特大/超大），选择会记进 config.json。
"""

import json
import os
import queue
import sys
import threading
import traceback
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import hildegard_math as hm
import settings_doc
import templates
import vision

try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except Exception:
    HAS_PIL = False

CONFIG_PATH = os.path.join(BASE, "config.json")
LOG_PATH = os.path.join(BASE, "gui_start.log")

IMG_EXT = ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.bmp", "*.tif", "*.tiff")

# 字号档位：标签 -> 缩放倍数。基准 10pt，乘完取整。
# 上限只到 1.6：本机 200% DPI 下窗口高度已经顶到屏幕，再大就没地方放内容了
# （文字本身还会被 Tk 按 DPI 放大一次，19pt 相当于常规界面的两倍）。
FONT_CHOICES = [("小", 1.0), ("标准", 1.2), ("大", 1.4), ("特大", 1.6)]
DEFAULT_FONT_SCALE = 1.2
BASE_PT = 10

UI_FAMILY = "Microsoft YaHei UI"
MONO_FAMILY = "Consolas"

PREVIEW_BASE_PX = 230     # 预览区基准边长（96 DPI），会按 DPI 放大、按字号缩小

BASE_W, BASE_H = 1460, 1000      # 窗口基准尺寸（96 DPI 下的像素）

DEFAULT_CONFIG = {
    "backend": "local",
    "font_scale": DEFAULT_FONT_SCALE,
    "backends": {
        "local": {
            "protocol": "openai",
            "base_url": "http://127.0.0.1:1234/v1",
            "api_key": "lm-studio",
            "model": "",
            "timeout": 300,
        },
        "cloud": {
            "protocol": "anthropic",
            "base_url": "https://api.anthropic.com",
            "api_key": "",
            "model": "claude-sonnet-4-5",
            "timeout": 300,
        },
    },
}

TILE_CHOICES = ["1024", "1280", "1536", "1792", "2048", "2304", "2560", "2816", "3048"]
# tile 默认值。⚠ 是 2048，不是 1536：官方示例工作流里 Hildegard Plan 节点
# 上显示的 1536 是**死值**——tile_width / tile_height 被 PrimitiveInt 节点
# 连走了（值 2048），ComfyUI 里输入一旦被连线，节点上的 widget 就被忽略。
# 自证：同一条链上 EmptyLatentImage 的宽高也来自同两个 PrimitiveInt，
# 若 tile 真是 1536 而采样画布是 2048，Hildegard Combine 的尺寸就对不上。
DEFAULT_TILE = 2048
OVERLAP_CHOICES = ["None", "1/64 Tile", "1/32 Tile", "1/16 Tile",
                   "1/8 Tile", "1/4 Tile", "1/2 Tile"]
# 选项文案直接带上 image1/2/3 的对应关系。原因：提示词模板（templates.OPEN_MULTI）
# 里逐字写着 "Image 1 is the tile to refine … image 3 is the full source photo"，
# 这句话里的三个 image 指的是 **ComfyUI 里这一个块的三路参考**，跟左边图片列表
# 里传了几张图毫无关系。用户只放一张图却看到「图1/图2/图3」，第一反应就是
# 「是不是没清除干净」。把映射写在选项上，选的时候就看见了，零高度成本。
# （宽度 37 是量出来的：四档字号下都不会把右栏顶宽，见自检 [6]。）
REF_CHOICES = ["三参考 image1=tile image2=位置 image3=全图",
               "单参考（只传 tile）"]

# 参考形态 → 提示词页那行提示。三参考才带 Image 编号，单参考没有，
# 所以这行必须跟着下拉实时变，不能写死。
REF_HINT = {
    0: "Image 1/2/3 = 块本身 / 位置图 / 全图缩略（不是图片列表）",
    1: "单参考：提示词里不出现 Image 编号",
}
# ⚠ 还有个隐藏条件：**只有 full_build 档的模板带那三个 image**。
# texture / subject_less 两档的模板里一个 "Image" 都没有（实测，不是读代码猜的）。
# 所以三参考 + 非 full_build 时，提示词里其实找不到 Image 编号 ——
# 这行必须说实话，否则用户会满篇去找一个不存在的词。
REF_HINT_NO_NUM = "本档提示词没有 Image 编号（只在 full_build 档出现）"

# 「提示词」页签的两种标题。参数一改，已生成的提示词就可能作废
# （档位按块数判，块数换档 = 该换模板了），用页签标题提醒。
# 不往正文里插字——正文是要被「复制提示词」整段复制走的。
TAB_FRESH = "  提示词  "
TAB_STALE = "  提示词 ⚠ 参数已改  "


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user = json.load(f)
            cfg["backend"] = user.get("backend", cfg["backend"])
            try:
                cfg["font_scale"] = float(user.get("font_scale", cfg["font_scale"]))
            except Exception:
                pass
            for k, v in (user.get("backends") or {}).items():
                cfg["backends"].setdefault(k, {}).update(v)
        except Exception:
            pass
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def enable_dpi_awareness():
    """必须在第一个 tk.Tk() 之前调用。

    Tk 会在初始化时读系统 DPI 来决定 point→pixel 的换算（tk scaling）。
    如果进程是 DPI-unaware，系统会直接把窗口位图拉伸 2 倍（发虚）；
    如果是 aware，Tk 自己按真实 DPI 放大字号（清晰）。
    但窗口尺寸和像素量不受 Tk scaling 影响，得自己乘。
    """
    try:
        from ctypes import windll
        try:
            windll.shcore.SetProcessDpiAwareness(1)   # 1 = system DPI aware
        except Exception:
            windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def get_system_dpi():
    """必须在 enable_dpi_awareness() 之后、tk.Tk() 之前调用。"""
    try:
        from ctypes import windll
        try:
            v = int(windll.user32.GetDpiForSystem())   # Win10 1607+
            if v > 0:
                return v
        except Exception:
            pass
        hdc = windll.user32.GetDC(0)
        try:
            v = int(windll.gdi32.GetDeviceCaps(hdc, 88))   # LOGPIXELSX
        finally:
            windll.user32.ReleaseDC(0, hdc)
        return v if v > 0 else 96
    except Exception:
        return 96


def scale_label(scale):
    for label, s in FONT_CHOICES:
        if abs(s - scale) < 0.01:
            return label
    return "大"


class App:
    def __init__(self, root, dpi=96):
        self.root = root
        self.dpi = int(dpi) if dpi else 96
        # Tk 自己按 DPI 放大字号（point→pixel），所以字号不用乘这个；
        # 但窗口尺寸、预览像素、wraplength 这些「像素量」必须乘。
        self.dpi_scale = max(1.0, self.dpi / 96.0)
        self.cfg = load_config()
        self.font_scale = self.cfg.get("font_scale", DEFAULT_FONT_SCALE)
        # 老 config 里可能存着已下架的档位（比如 1.9），回落到默认值
        if not any(abs(s - self.font_scale) < 0.01 for _, s in FONT_CHOICES):
            self.font_scale = DEFAULT_FONT_SCALE
        self.paths = []
        self.cur = None          # 当前图片路径
        self.info = None         # analyze() 结果
        self.prompt = ""
        self.prompt_key = None   # 生成提示词时用的参数指纹，用来判断是否过期
        self._gen_key = None
        self.thumb_ref = None
        self.busy = False
        self.calc_rows = []
        self._q = None           # 工作线程 → 主线程的结果队列
        self._fs = {}

        root.title("Hildegard 放大助手  ·  Flux2 Klein 分块精修")
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w = min(int(BASE_W * self.dpi_scale), int(sw * 0.94))
        h = min(int(BASE_H * self.dpi_scale), int(sh * 0.92))
        root.geometry(f"{w}x{h}")
        root.minsize(self.px(1000), self.px(700))

        self._build()
        self._apply_fonts()
        self._reload_backend_label()
        self.refresh_list()

    def px(self, n):
        """把 96 DPI 下的像素数换算到当前 DPI。"""
        return max(1, int(round(n * self.dpi_scale)))

    # ------------------------------------------------------------ 字体

    def _apply_fonts(self):
        s = self.font_scale
        ui = max(9, int(round(BASE_PT * s)))
        mono = max(9, int(round(BASE_PT * s)))
        small = max(8, ui - 1)
        self._fs = {"ui": ui, "mono": mono, "small": small}

        ui_font = (UI_FAMILY, ui)
        mono_font = (MONO_FAMILY, mono)
        small_font = (UI_FAMILY, small)

        # 命名字体：影响所有没显式设字体的控件（含 ttk 内部默认）
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont",
                     "TkHeadingFont", "TkIconFont", "TkTooltipFont"):
            try:
                tkfont.nametofont(name).configure(family=UI_FAMILY, size=ui)
            except Exception:
                pass
        try:
            tkfont.nametofont("TkFixedFont").configure(family=MONO_FAMILY, size=mono)
        except Exception:
            pass

        st = ttk.Style()
        for style_name in ("TButton", "TLabel", "TCombobox", "TSpinbox",
                           "TCheckbutton", "TRadiobutton", "TEntry",
                           "TLabelframe.Label", "TNotebook.Tab"):
            try:
                st.configure(style_name, font=ui_font)
            except Exception:
                pass
        st.configure("Muted.TLabel", font=ui_font, foreground="#5F5E5A")
        st.configure("Hint.TLabel", font=small_font, foreground="#888780")
        # 参数区那行「精细度：…」是实时的，用暖色和普通提示区分开。
        # ⚠ 它必须保持单行 —— 特大字号下右栏只剩 ~74px 纵向余量，
        #   折行会把 Notebook 挤到需求高度以下（自检 [6] 会报）。
        st.configure("FineHead.TLabel", font=small_font, foreground="#993C1D")
        st.configure("Stat.TLabel", font=ui_font)
        st.configure("StatTier.TLabel", font=ui_font, foreground="#185FA5")
        st.configure("StatNote.TLabel", font=small_font, foreground="#5F5E5A",
                     wraplength=self.px(int(320 * s)))

        # 下拉列表的字体要单独用 option database 设
        self.root.option_add("*TCombobox*Listbox.font", ui_font)

        # tk 原生控件不吃 style，得单独设
        if hasattr(self, "listbox"):
            self.listbox.configure(font=ui_font)
            self.preview.configure(font=ui_font)
            self.prompt_txt.configure(font=mono_font)
            self.warn_txt.configure(font=mono_font)
            self.settings_txt.configure(font=mono_font)
            self.fine_txt.configure(font=mono_font)
            self.calcbox.configure(font=mono_font)
            self._size_preview()
            if self.cur:
                self.load_thumb()

    def _preview_base(self):
        """预览边长（96 DPI 基准）。字号调大时主动缩小，把高度让给文字区。"""
        k = DEFAULT_FONT_SCALE / max(1.0, self.font_scale)
        return int(max(150, min(320, round(PREVIEW_BASE_PX * k))))

    def _size_preview(self):
        """把预览容器钉成正方形。

        关键：tk.Label 一旦设了 image，width/height 的单位就从「字符」变成
        「像素」——所以绝不能让 Label 自己决定尺寸，否则一载图就塌成几十像素。
        这里用一个固定像素的 Frame 兜住它，Label 只在里面居中。
        """
        side = self.px(self._preview_base())
        self.prev_holder.configure(width=side, height=side)

    def on_font_change(self, event=None, persist=True):
        self.font_scale = dict(FONT_CHOICES).get(self.v_font.get(), DEFAULT_FONT_SCALE)
        # 只有用户真的在下拉里换档才写盘。
        # 自检脚本会遍历四档字号来验布局，如果它也写盘，跑完自检
        # config.json 就会被改成最后一档（特大）——那是污染用户配置。
        if persist:
            self.cfg["font_scale"] = self.font_scale
            try:
                save_config(self.cfg)
            except Exception:
                pass
        self._apply_fonts()
        self.root.update_idletasks()

    # ------------------------------------------------------------ 界面

    def _build(self):
        # ---- 顶部工具条 ----
        bar = ttk.Frame(self.root, padding=(10, 8))
        bar.pack(fill="x")
        ttk.Button(bar, text="添加图片…", command=self.add_images).pack(side="left")
        ttk.Button(bar, text="移除选中", command=self.remove_selected).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="清空列表", command=self.clear_list).pack(side="left", padx=(8, 0))
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=14)
        self.backend_lbl = ttk.Label(bar, text="", style="StatTier.TLabel")
        self.backend_lbl.pack(side="left")

        ttk.Button(bar, text="后端设置…", command=self.open_settings).pack(side="right")
        self.v_font = tk.StringVar(value=scale_label(self.font_scale))
        fcb = ttk.Combobox(bar, textvariable=self.v_font,
                           values=[l for l, _ in FONT_CHOICES], width=6,
                           state="readonly")
        fcb.pack(side="right", padx=(0, 10))
        fcb.bind("<<ComboboxSelected>>", self.on_font_change)
        ttk.Label(bar, text="字号", style="Muted.TLabel").pack(side="right", padx=(0, 6))

        main = ttk.PanedWindow(self.root, orient="horizontal")
        main.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # ---- 左：图片列表 ----
        left = ttk.LabelFrame(main, text=" 图片列表 ", padding=8)
        main.add(left, weight=1)
        self.listbox = tk.Listbox(left, activestyle="none", exportselection=False,
                                  font=(UI_FAMILY, 10))
        sb = ttk.Scrollbar(left, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.listbox.bind("<<ListboxSelect>>", self.on_select)

        # ---- 右 ----
        right = ttk.Frame(main)
        main.add(right, weight=4)

        top = ttk.Frame(right)
        top.pack(fill="x")

        prev = ttk.LabelFrame(top, text=" 预览 ", padding=8)
        prev.pack(side="left", fill="y")
        # 固定像素的容器，pack_propagate(False) 保证它不被里面的 Label 撑大或压小
        self.prev_holder = tk.Frame(prev, background="#F1EFE8",
                                    highlightthickness=1,
                                    highlightbackground="#D3D1C7")
        self.prev_holder.pack(expand=True)
        self.prev_holder.pack_propagate(False)
        self.preview = tk.Label(self.prev_holder, background="#F1EFE8",
                                text="未选择图片", foreground="#5F5E5A",
                                font=(UI_FAMILY, 10), justify="center")
        self.preview.pack(expand=True)

        stats = ttk.LabelFrame(top, text=" 关键数字（参数改动会实时重算） ", padding=(12, 8))
        stats.pack(side="left", fill="both", expand=True, padx=(12, 0))
        self.stat_vars = {}

        def stat_cell(r, c, key, label, style="Stat.TLabel"):
            ttk.Label(stats, text=label, style="Muted.TLabel").grid(
                row=r, column=c, sticky="w", pady=2)
            var = tk.StringVar(value="—")
            self.stat_vars[key] = var
            ttk.Label(stats, textvariable=var, style=style).grid(
                row=r, column=c + 1, sticky="w", padx=(14, 16), pady=2)

        # 两列并排：8 行压成 4 行。字号调大时纵向很吃紧，少一行就多一分余地。
        stat_cell(0, 0, "src", "源图尺寸")
        stat_cell(0, 2, "out", "放大后尺寸")
        stat_cell(1, 0, "scale", "实际放大倍数")
        stat_cell(1, 2, "grid", "网格 / 块数")
        stat_cell(2, 0, "tile", "每块尺寸")
        stat_cell(2, 2, "ovl", "重叠像素")
        stat_cell(3, 0, "tier", "提示词档位", "StatTier.TLabel")
        self.stat_vars["note"] = tk.StringVar(value="—")
        ttk.Label(stats, text="档位依据", style="Muted.TLabel").grid(
            row=4, column=0, sticky="nw", pady=(6, 2))
        ttk.Label(stats, textvariable=self.stat_vars["note"],
                  style="StatNote.TLabel").grid(
            row=4, column=1, columnspan=3, sticky="w", padx=(14, 0), pady=(6, 2))

        # ---- 参数区 ----
        ctl = ttk.LabelFrame(right, text=" 参数（可调，改完立刻重算） ", padding=(12, 8))
        ctl.pack(fill="x", pady=(12, 0))

        self.v_tile = tk.StringVar(value=str(DEFAULT_TILE))
        self.v_ovl = tk.StringVar(value="1/4 Tile")
        self.v_scale = tk.StringVar(value="1.3")
        self.v_ref = tk.StringVar(value=REF_CHOICES[0])
        self.v_cell = tk.StringVar(value="512")
        self.v_ctrl3 = tk.StringVar(value="768")
        self.v_reduce = tk.StringVar(value="0.00")
        self.v_radius = tk.StringVar(value="256")

        def combo(parent, label, var, values, width, r, c):
            box = ttk.Frame(parent)
            box.grid(row=r, column=c, sticky="w", padx=(0, 20), pady=4)
            ttk.Label(box, text=label, style="Muted.TLabel").pack(anchor="w")
            cb = ttk.Combobox(box, textvariable=var, values=values, width=width,
                              state="readonly")
            cb.pack(anchor="w")
            cb.bind("<<ComboboxSelected>>", lambda e: self.recompute())
            return cb

        combo(ctl, "tile 尺寸", self.v_tile, TILE_CHOICES, 8, 0, 0)
        combo(ctl, "最小重叠", self.v_ovl, OVERLAP_CHOICES, 11, 0, 1)
        combo(ctl, "参考形态", self.v_ref, REF_CHOICES, 37, 0, 2)
        combo(ctl, "cell_size", self.v_cell, ["256", "384", "512", "640", "768"], 8, 1, 0)
        combo(ctl, "ctrl3_max_size", self.v_ctrl3,
              ["512", "768", "1024", "1536", "2048"], 8, 1, 1)

        box = ttk.Frame(ctl)
        box.grid(row=1, column=2, sticky="w", padx=(0, 20), pady=4)
        ttk.Label(box, text="min_scale_factor", style="Muted.TLabel").pack(anchor="w")
        ttk.Spinbox(box, textvariable=self.v_scale, from_=1.0, to=8.0,
                    increment=0.1, width=8).pack(anchor="w")
        self.v_scale.trace_add("write", lambda *a: self.recompute())

        # 说明文字单独几行：塞进每列会把列宽顶爆（大字号下右栏装不下）。
        # 画质/精细度那部分已经搬到右上角的「精细度」块，这里只留
        # 「这个参数是什么」，避免两处说同一件事。
        ttk.Label(ctl,
                  text="tile 尺寸为正方形边长；cell_size = 位置图每格边长；"
                       "ctrl3_max_size = 全局缩略图长边。",
                  style="Hint.TLabel", justify="left",
                  wraplength=self.px(1000)).grid(row=2, column=0, columnspan=3,
                                                sticky="w", pady=(6, 0))
        # 这行必须保持单行——「特大」字号下右栏只剩几十像素余量，
        # 多折一行就会把 Notebook 压到需求高度以下（自检 [6] 会报）。
        ttk.Label(ctl,
                  text="min_scale_factor 只是下限，实际倍数会被网格吸附；"
                       "最小重叠同样影响输出尺寸。",
                  style="Hint.TLabel", justify="left",
                  wraplength=self.px(1000)).grid(row=3, column=0, columnspan=3,
                                                sticky="w")
        # 这一行是**实时**的：tile 一改，判断和数字立刻跟着变。
        # 它**顶掉**了原来那行静态的「不改精细度：…」—— 那一条现在放在
        # 下方「精细度」页里，因为多加一行会多占 ~52px（特大字号），而
        # 右栏总共只剩 74px 余量，会连带把 Notebook 挤到需求高度以下。
        # 同样必须保持单行（理由同上）。
        self.v_fine_line = tk.StringVar(value="")
        self.fine_lbl = ttk.Label(ctl, textvariable=self.v_fine_line,
                                  style="FineHead.TLabel", justify="left",
                                  wraplength=self.px(1000))
        self.fine_lbl.grid(row=4, column=0, columnspan=3, sticky="w")

        # ---- 试算表 ----
        calc = ttk.LabelFrame(right, text=" 试算（双击某行即套用该 tile 尺寸） ", padding=8)
        calc.pack(fill="x", pady=(12, 0))
        self.calcbox = tk.Listbox(calc, height=4, activestyle="none",
                                  font=(MONO_FAMILY, 10), exportselection=False)
        self.calcbox.pack(fill="x")
        self.calcbox.bind("<Double-Button-1>", self.apply_calc_row)

        # ---- 底部 Notebook ----
        nb = ttk.Notebook(right)
        nb.pack(fill="both", expand=True, pady=(12, 0))
        self.nb = nb

        tab1 = ttk.Frame(nb, padding=8)
        nb.add(tab1, text=TAB_FRESH)
        self.tab1 = tab1
        btns = ttk.Frame(tab1)
        btns.pack(fill="x")
        self.btn_gen = ttk.Button(btns, text="生成提示词", command=self.generate)
        self.btn_gen.pack(side="left")
        ttk.Button(btns, text="复制提示词", command=self.copy_prompt).pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="导出 txt", command=self.export).pack(side="left", padx=(8, 0))
        self.status = ttk.Label(btns, text="", style="Muted.TLabel")
        self.status.pack(side="left", padx=(14, 0))

        # 提示词正上方那行「Image 1/2/3 是什么」。放在这里是因为用户的疑问
        # 就发生在读提示词的时候；放在参数区得跨半屏去找。
        # 打包顺序：放在 status 之后、side="left"。宽度不够时被挤掉的是这行
        # 静态提示（状态消息比如「参数已改，提示词可能过期」必须完整可见），
        # 这是刻意选的失败方向。
        self.v_ref_hint = tk.StringVar(value="")
        self.ref_hint_lbl = ttk.Label(btns, textvariable=self.v_ref_hint,
                                      style="Hint.TLabel")
        self.ref_hint_lbl.pack(side="left", padx=(14, 0))

        # height 只是「请求高度」，实际会 expand 撑满；调小它才能给别的区块让出空间
        self.prompt_txt = tk.Text(tab1, wrap="word", font=(MONO_FAMILY, 10),
                                  undo=True, height=5, width=40)
        psb = ttk.Scrollbar(tab1, orient="vertical", command=self.prompt_txt.yview)
        self.prompt_txt.configure(yscrollcommand=psb.set)
        psb.pack(side="right", fill="y", pady=(8, 0))
        self.prompt_txt.pack(fill="both", expand=True, pady=(8, 0))

        # 【自检提醒】面板 —— 默认不显示，有提醒时才 pack 出来。
        # ⚠ 它**必须**是独立控件，不能像旧版那样 append 进 prompt_txt：
        #   「复制提示词」和「导出 txt」读的都是 prompt_txt，append 进去等于
        #   让粘进 CLIPTextEncode 的提示词里多出一段中文自检报告 ——
        #   正好违反「提示词严格等于 md 模板」这条约束。
        #   另外旧版把它塞在正文末尾，1477 字符的提示词早就滚出可视区，
        #   状态栏写着「见下」却什么都看不见。
        self.warn_frame = ttk.Frame(tab1)
        self.warn_head = ttk.Label(self.warn_frame, text="", style="FineHead.TLabel")
        self.warn_head.pack(anchor="w", pady=(0, 2))
        self.warn_txt = tk.Text(self.warn_frame, wrap="word", font=(MONO_FAMILY, 9),
                                height=7, width=40, state="disabled",
                                background="#FDF6EC", relief="flat",
                                highlightthickness=0)
        wsb = ttk.Scrollbar(self.warn_frame, orient="vertical",
                            command=self.warn_txt.yview)
        self.warn_txt.configure(yscrollcommand=wsb.set)
        wsb.pack(side="right", fill="y")
        self.warn_txt.pack(fill="both", expand=True)

        tab2 = ttk.Frame(nb, padding=8)
        nb.add(tab2, text="  ComfyUI 设置说明  ")
        b2 = ttk.Frame(tab2)
        b2.pack(fill="x")
        ttk.Button(b2, text="复制设置说明", command=self.copy_settings).pack(side="left")
        ttk.Label(b2, text="  照这份清单在云上设节点即可",
                  style="Hint.TLabel").pack(side="left")
        self.settings_txt = tk.Text(tab2, wrap="none", font=(MONO_FAMILY, 10),
                                    height=5, width=40)
        s2 = ttk.Scrollbar(tab2, orient="vertical", command=self.settings_txt.yview)
        h2 = ttk.Scrollbar(tab2, orient="horizontal", command=self.settings_txt.xview)
        self.settings_txt.configure(yscrollcommand=s2.set, xscrollcommand=h2.set)
        s2.pack(side="right", fill="y", pady=(8, 0))
        h2.pack(side="bottom", fill="x")
        self.settings_txt.pack(fill="both", expand=True, pady=(8, 0))

        # ---- 「精细度」页 ----
        # 为什么不放在右上角预览旁边：那一行横向上已经被「关键数字」占满了
        # （有图时特大字号 stats 要 1484px，右栏总共 2463px），纵向也只剩
        # 74px 余量。放页签里则是零宽高成本，而且能写得更完整。
        tab3 = ttk.Frame(nb, padding=8)
        nb.add(tab3, text=" 精细度 ")
        b3 = ttk.Frame(tab3)
        b3.pack(fill="x")
        ttk.Label(b3, text="  这一页跟着参数实时重算，改 tile / 重叠时照着看",
                  style="Hint.TLabel").pack(side="left")
        self.fine_txt = tk.Text(tab3, wrap="word", font=(MONO_FAMILY, 10),
                                height=5, width=40)
        s3 = ttk.Scrollbar(tab3, orient="vertical", command=self.fine_txt.yview)
        self.fine_txt.configure(yscrollcommand=s3.set)
        s3.pack(side="right", fill="y", pady=(8, 0))
        self.fine_txt.pack(fill="both", expand=True, pady=(8, 0))

        # 精细度只依赖 tile / reduce / radius，跟有没有选图无关，
        # 所以单独挂 trace —— 不能挂在 recompute 里，没选图时它提前 return。
        for _v in (self.v_tile, self.v_ovl, self.v_scale, self.v_cell,
                   self.v_ctrl3, self.v_reduce, self.v_radius):
            _v.trace_add("write", lambda *a: self._update_fineness())
        self._update_fineness()

        # 同理：提示词页那行 Image 1/2/3 说明只依赖参考形态，
        # 没选图也得显示，所以也走独立 trace。
        self.v_ref.trace_add("write", lambda *a: self._update_ref_hint())
        self._update_ref_hint()

    # ------------------------------------------------------------ 列表

    def refresh_list(self):
        self.listbox.delete(0, "end")
        for i, p in enumerate(self.paths):
            self.listbox.insert("end", f"{i+1:>3}.  {os.path.basename(p)}")
        if self.paths and self.cur is None:
            self.listbox.selection_set(0)
            self.on_select()

    def add_images(self):
        files = filedialog.askopenfilenames(
            title="选择要放大的图片",
            filetypes=[("图片", " ".join(IMG_EXT)), ("所有文件", "*.*")])
        if not files:
            return
        added = 0
        for f in files:
            if f not in self.paths:
                self.paths.append(f)
                added += 1
        self.refresh_list()
        if added:
            self.status_set(f"已添加 {added} 张")

    def remove_selected(self):
        sel = list(self.listbox.curselection())
        if not sel:
            return
        for i in reversed(sel):
            self.paths.pop(i)
        if self.cur in self.paths:
            self.cur = None
        self.refresh_list()
        if not self.paths:
            self.clear_view()

    def clear_list(self):
        self.paths = []
        self.cur = None
        self.refresh_list()
        self.clear_view()

    def clear_view(self):
        self.info = None
        self.prompt = ""
        self.prompt_key = None
        self._mark_prompt_tab(True)
        self.prompt_txt.delete("1.0", "end")
        self._hide_warnings()
        self.settings_txt.delete("1.0", "end")
        self.preview.configure(image="", text="未选择图片")
        self.thumb_ref = None
        for v in self.stat_vars.values():
            v.set("—")
        # 没图 = 没档位，提示退回通用文案（不然会留着上一张图的档位判断）
        self._update_ref_hint()

    def on_select(self, event=None):
        sel = self.listbox.curselection()
        if not sel:
            return
        self.cur = self.paths[sel[0]]
        self.prompt = ""
        self.prompt_key = None
        self._mark_prompt_tab(True)
        self.prompt_txt.delete("1.0", "end")
        self._hide_warnings()
        self.load_thumb()
        self.recompute()
        self.status_set("")

    def load_thumb(self):
        if not self.cur:
            return
        if not HAS_PIL:
            self.preview.configure(text="（未安装 Pillow，无法预览）", image="")
            return
        try:
            im = Image.open(self.cur).convert("RGB")
            side = self.px(self._preview_base())
            im.thumbnail((side, side), Image.LANCZOS)
            self.thumb_ref = ImageTk.PhotoImage(im)
            self.preview.configure(image=self.thumb_ref, text="")
        except Exception as e:
            self.preview.configure(text=f"预览失败：{e}", image="")
            self.thumb_ref = None

    # ------------------------------------------------------------ 重算

    def cur_params(self):
        try:
            tile = int(float(self.v_tile.get()))
        except Exception:
            tile = DEFAULT_TILE
        try:
            scale = float(self.v_scale.get())
        except Exception:
            scale = 1.3
        scale = min(8.0, max(1.0, scale))
        ovl = hm.OVERLAP_DICT.get(self.v_ovl.get(), 0.25)
        return tile, ovl, scale

    def size_key(self):
        """影响「输出尺寸 / 块数 / 档位」的参数指纹。

        这四个参数不是各管各的——它们一起决定块数，而块数决定提示词档位。
        所以改最小重叠也可能把档位从 full_build 顶到 texture，提示词就该换了。
        （cell_size / ctrl3_max_size / tile_high_freq_reduce 只影响 ComfyUI
          设置说明，不影响提示词，所以不进指纹。）
        """
        tile, _ovl, scale = self.cur_params()
        return (self.cur, tile, self.v_ovl.get(), round(scale, 3),
                self.v_ref.get())

    def _mark_prompt_tab(self, fresh):
        try:
            self.nb.tab(self.tab1, text=TAB_FRESH if fresh else TAB_STALE)
        except Exception:
            pass

    def _check_prompt_fresh(self):
        if not self.prompt or self.prompt_key is None:
            return
        if self.prompt_key == self.size_key():
            return
        self._mark_prompt_tab(False)
        self.status_set("参数已改，提示词可能过期——请重新生成", "#854F0B")

    def _update_ref_hint(self):
        """刷新提示词页那行「Image 1/2/3 是什么」。

        背景：用户只上传一张图，却在提示词里看到「Image 1 / Image 2 /
        image 3」，第一反应是「清除没生效 / 工具串了图」。其实那是
        templates.OPEN_MULTI 里逐字写死的模板句，指的是 ComfyUI 里
        **这一个块**的三路参考（tile / 位置图 / 全图缩略），跟图片列表无关。
        工具永远只把你选中的那一张图当**全图原图**用。

        两个条件都满足才会有那三个 image：
          ① 参考形态 = 三参考  ② 档位 = full_build（块数 ≤ 6）
        texture / subject_less 的模板里没有 "Image" 字样，所以第 ② 条
        不满足时要改口说「本档没有编号」，不能让用户去找。
        （实测：逐档 × 逐形态跑过 6 种组合。）

        只依赖参考形态 + 档位，跟有没有选图无关（档位没有图时就是未知，
        按通用文案显示）—— 用 trace 驱动，recompute 里再补调一次。
        """
        try:
            idx = REF_CHOICES.index(self.v_ref.get())
        except ValueError:
            idx = 0
        tier = (self.info or {}).get("tier")
        if idx == 0 and tier and tier != "full_build":
            self.v_ref_hint.set(REF_HINT_NO_NUM)
            return
        self.v_ref_hint.set(REF_HINT.get(idx, REF_HINT[0]))

    def _update_fineness(self):
        """刷新「精细度」页 + 参数区那一行实时判断。

        只依赖 tile / 重叠 / 倍数 / cell_size / ctrl3 / reduce / radius
        （加固定的采样步数），跟有没有选图无关，所以用 trace 驱动 ——
        不能挂在 recompute 的图片分支里，没选图时它会提前 return。

        ⚠ 参数区那一行**必须保持单行**：特大字号下右栏只剩 ~74px 纵向
          余量，折行会把 Notebook 挤到需求高度以下（自检 [6] 会报）。
          页签里的正文没有这个限制（Notebook 本来就 expand）。
        """
        try:
            tile = int(float(self.v_tile.get()))
        except Exception:
            tile = DEFAULT_TILE
        mp = tile * tile / 1e6
        steps = settings_doc.DEFAULTS.get("steps", 4)

        if mp >= 4.0:
            verdict = "偏大，出图偏平滑"
            short = "偏大偏平滑，改 1536~1792"
            # ⚠ 「对齐官方示例」只对 2048 成立 —— 官方那两个 PrimitiveInt
            #   填的就是 2048。tile 2304 / 3048 不能套这句话。
            advice = ("2048 只是对齐官方示例工作流用的，不是画质推荐值。"
                      if tile == DEFAULT_TILE else
                      "每遍画布比 1536 大一倍以上，出图自然更平滑。")
        elif mp >= 2.0:
            # 1536（2.36 MP）/ 1792（3.21 MP）就是推荐的那一段，
            # 不能判成「偏细」。
            verdict = "推荐区间"
            short = "推荐区间"
            advice = "1536 ~ 1792 是平衡点：再大偏平滑，再小缝变多。"
        else:
            verdict = "偏细，注意块数与缝"
            short = "偏细，块数与缝会上升"
            advice = "再小缝段会快速上升（1024 的缝段是 2048 的 6.8 倍）。"

        d = settings_doc.DEFAULTS
        try:
            reduce_ = float(self.v_reduce.get())
        except Exception:
            reduce_ = 0.0
        dead = abs(reduce_) < 1e-9
        radius = self.v_radius.get()
        wt, wp, wg = d["w_tile"], d["w_position"], d["w_global"]
        cell = self.v_cell.get()
        ctrl3 = self.v_ctrl3.get()
        try:
            scale = float(self.v_scale.get())
        except Exception:
            scale = 1.3
        ovl = self.v_ovl.get()

        self.v_fine_line.set(
            f"精细度：tile {tile} → 每遍 {mp:.2f} MP → {short}"
            "（详见下方「精细度」页）")

        if dead:
            r3 = (f"③ tile_high_freq_reduce  {reduce_:.2f}  已是最保真的一档，别动。\n"
                  f"   ⚠ 因为它是 0，low_freq_radius（你填 {radius}）"
                  f"**完全不参与计算**，是个死值。")
        else:
            r3 = (f"③ tile_high_freq_reduce  {reduce_:.2f}  越大越让模型重造细节。\n"
                  f"   low_freq_radius {radius} 已生效：越大，越多源图细节被算作\n"
                  f"   「低频」而在参考里保留下来。")

        body = "\n".join([
            "═══ 会改变「每像素多少细节」的（按性价比排序）═══",
            "",
            "① tile 尺寸  ← 唯一直接杠杆",
            f"   现在 {tile} → 每遍画布 {mp:.2f} MP / {steps} 步采样 → {verdict}",
            f"   {advice}",
            "   想更精细 → 1536（2.36 MP）或 1792（3.21 MP）。",
            "",
            "② Flux2KleinRefLatentWeight  ← 零提示词风险，最容易被忽略",
            f"   现在 {wt:g} / {wp:g} / {wg:g}"
            "（index 0 = tile / 1 = 位置 / 2 = 全局）",
            "   节点默认就是 1.0，你三个都低于中性 → 提到 1.0~1.2 更贴源图。",
            "",
            r3,
            "",
            "═══ 不会改变精细度的（只改尺寸 / 块数 / 一致性）═══",
            "",
            f"   cell_size          {cell:<5s} 只改位置图分辨率（近邻环境多清楚）",
            f"   ctrl3_max_size     {ctrl3:<5s} 只改全局缩略图长边（跨块色彩一致性）",
            f"   min_scale_factor   {scale:<5g} 只改输出尺寸和块数，每遍画布没变",
            f"   最小重叠           {ovl:<8s} 只改输出尺寸、块数和提示词档位",
            "",
            "⚠ 改上面这几个不会让画面更细，只会让输出更大 / 更慢 / 换档。",
            "⚠ 换 tile 或换重叠后块数会变 → 档位可能换 → 提示词必须重新生成。",
            "",
            "⚠ 「多切几块」也不算提高精细度：每块画布、每块覆盖的源图范围、",
            "   每块倍数都没变，只是同一片区域被重复画了更多遍（更慢、缝更多）。",
        ])
        try:
            self.fine_txt.configure(state="normal")
            self.fine_txt.delete("1.0", "end")
            self.fine_txt.insert("1.0", body)
            self.fine_txt.configure(state="disabled")
        except Exception:
            pass

    def recompute(self):
        if not self.cur:
            return
        tile, ovl, scale = self.cur_params()
        try:
            with Image.open(self.cur) as im:
                w, h = im.size
        except Exception as e:
            self.status_set(f"读图失败：{e}")
            return

        info = hm.analyze(w, h, tile_width=tile, tile_height=tile,
                          overlap=ovl, min_scale_factor=scale)
        info["overlap_label"] = self.v_ovl.get()
        info["min_scale_factor"] = scale
        info["multi_reference"] = self.v_ref.get() == REF_CHOICES[0]
        self.info = info

        s = self.stat_vars
        s["src"].set(f"{w}×{h}  ({w * h / 1e6:.1f}MP)")
        s["out"].set(f"{info['upscaled_width']}×{info['upscaled_height']}"
                     f"  ({info['megapixels']}MP)")
        got = info["effective_scale_x"]
        mark = "  ←吸附" if abs(got - scale) > 0.05 else ""
        s["scale"].set(f"{got}×  (填 {scale}){mark}")
        s["grid"].set(f"{info['grid_x']}×{info['grid_y']} = {info['num_tiles']} 块"
                      + ("  ←单张精修" if info["single_pass"] else ""))
        s["tile"].set(f"{info['tile_width']}×{info['tile_height']}")
        s["ovl"].set(f"{info['overlap_x']}×{info['overlap_y']}px")
        # 档位只显示短名（括号里的解释太长，会把这一列顶宽、把整块顶高）
        tier_label = info["tier_label"]
        tier_short = tier_label.split("（")[0] or tier_label
        s["tier"].set(f"{info['tier']}  ·  {tier_short}")
        s["note"].set(info["tier_reason"])

        self.rebuild_calc(w, h, ovl, scale)
        self.rebuild_settings()
        self._check_prompt_fresh()
        # 档位出来了，Image 那行提示要跟着换口（texture 档没有编号）
        self._update_ref_hint()

    def rebuild_calc(self, w, h, ovl, scale):
        self.calcbox.delete(0, "end")
        self.calc_rows = []
        cur_tile = int(float(self.v_tile.get()))
        for t in TILE_CHOICES:
            tv = int(t)
            a = hm.analyze(w, h, tile_width=tv, tile_height=tv,
                           overlap=ovl, min_scale_factor=scale)
            tag = "  ◀ 当前" if tv == cur_tile else ""
            self.calcbox.insert(
                "end",
                f"tile {tv:>4}  →  {a['upscaled_width']:>5}×{a['upscaled_height']:<5} "
                f"{a['effective_scale_x']:>5}×  {a['grid_x']}×{a['grid_y']}="
                f"{a['num_tiles']:>3}块  {a['megapixels']:>5}MP{tag}")
            self.calc_rows.append(tv)
        for i, tv in enumerate(self.calc_rows):
            if tv == cur_tile:
                self.calcbox.see(i)
                break

    def apply_calc_row(self, event=None):
        sel = self.calcbox.curselection()
        if not sel:
            return
        self.v_tile.set(str(self.calc_rows[sel[0]]))
        self.recompute()

    def rebuild_settings(self):
        if not self.info:
            return
        opts = {
            "cell_size": int(self.v_cell.get()),
            "ctrl3_max_size": int(self.v_ctrl3.get()),
            "tile_high_freq_reduce": float(self.v_reduce.get()),
            "low_freq_radius": int(self.v_radius.get()),
            "tile_order": "spiral",
        }
        self.settings_txt.delete("1.0", "end")
        self.settings_txt.insert("1.0", settings_doc.build(self.info, opts))

    # ------------------------------------------------------------ 生成

    def backend_cfg(self):
        name = self.cfg.get("backend", "local")
        return name, self.cfg["backends"].get(name, {})

    def _reload_backend_label(self):
        name, cfg = self.backend_cfg()
        label = "本地 LM Studio" if name == "local" else "云端 API"
        self.backend_lbl.configure(
            text=f"后端：{label}  ·  {cfg.get('base_url', '')}")

    def status_set(self, text, color="#5F5E5A"):
        self.status.configure(text=text, foreground=color)

    def generate(self):
        if self.busy:
            return
        if not self.cur or not self.info:
            messagebox.showinfo("提示", "先在左边选一张图片。")
            return
        name, cfg = self.backend_cfg()
        if name == "cloud" and not cfg.get("api_key"):
            if not messagebox.askyesno(
                    "还没填 API key",
                    "云端后端没有配置 API key，现在去设置吗？\n"
                    "（也可以点「否」改用本地 LM Studio）"):
                return
            self.open_settings()
            return

        try:
            backend = vision.make_backend(cfg)
        except Exception as e:
            messagebox.showerror("后端配置有问题", str(e))
            return

        tier = self.info["tier"]
        # 记下这次生成用的参数，回来时对一下有没有变过
        self._gen_key = self.size_key()
        self.busy = True
        self.btn_gen.configure(state="disabled")
        self.status_set(f"正在用 {name} 分析图片…（本地 7B 大约 10–60 秒）", "#185FA5")
        self.root.update_idletasks()

        # 注意：tkinter 不是线程安全的，工作线程只能往队列里放结果，
        # 由主线程轮询后更新界面（直接在子线程调 root.after 会炸）。
        self._q = queue.Queue()

        def work():
            try:
                data = vision.analyze_image(backend, self.cur, tier, self.info)
                self._q.put(("ok", data, tier))
            except Exception as e:
                self._q.put(("err", f"{e}", tier))

        threading.Thread(target=work, daemon=True).start()
        self.root.after(120, self._poll_result)

    def _poll_result(self):
        if self._q is None:
            return
        try:
            kind, payload, tier = self._q.get_nowait()
        except queue.Empty:
            self.root.after(120, self._poll_result)
            return
        self._q = None
        if kind == "ok":
            self.on_generated(payload, tier, None)
        else:
            self.on_generated(None, tier, payload)

    def on_generated(self, data, tier, err):
        self.busy = False
        self.btn_gen.configure(state="normal")
        if err:
            self.status_set("生成失败", "#A32D2D")
            messagebox.showerror("生成失败", err)
            return

        override = data.get("tier_override")
        if override in ("full_build", "texture", "subject_less") and override != tier:
            tier = override
            self.status_set(f"AI 把档位改成了 {tier}（{data.get('notes', '')[:40]}）", "#854F0B")
        else:
            self.status_set("生成完成", "#0F6E56")

        data["multi_reference"] = self.info["multi_reference"]
        prompt = templates.assemble(data, tier)

        hint = data.get("high_freq_reduce_hint")
        if isinstance(hint, (int, float)) and float(self.v_reduce.get()) == 0.0 \
                and float(hint) > 0.05:
            self.v_reduce.set(f"{min(1.0, max(0.0, float(hint))):.2f}")
            self.rebuild_settings()
            self.status_set(
                f"生成完成 · AI 建议 tile_high_freq_reduce = {float(hint):.2f}"
                f"（已填入参数区）", "#0F6E56")

        self.prompt = prompt
        self.prompt_key = self._gen_key if self._gen_key else self.size_key()
        self._mark_prompt_tab(True)
        self.prompt_txt.delete("1.0", "end")
        self.prompt_txt.insert("1.0", prompt)

        # 把 data 一起传进去：只看 prompt 查不出「md 规定 Always 的块没填」
        # —— 块缺失时 prompt 里什么都没留下（第 4 块材质是 md 的 core slot）。
        ok, warn = templates.validate(prompt, tier, data)
        # vision 那边的提醒（如 texture 档「模型扫得不够全」）也走同一个通道，
        # 免得再开一块 UI。_warnings 不是模板槽位，assemble 会忽略它。
        warn = list(warn) + list(data.get("_warnings") or [])
        # 提醒走**独立面板**，绝不写进 prompt_txt（理由见 warn_frame 处的注释）。
        self._show_warnings(warn, ok)
        if warn:
            self.status_set(f"生成完成，但有 {len(warn)} 条自检提醒（见下）", "#854F0B")

    def _show_warnings(self, warn, ok):
        """把自检结果放进提示词下方的只读面板；没有提醒就整块收起来。"""
        if not getattr(self, "warn_frame", None):
            return
        if not warn:
            self._hide_warnings()
            return
        # 表头别写死成「缺了 md 规定 Always 的块」—— 提醒有好几类
        # （缺块、光照没写具体、用了禁用动词、槽位填歪、重复内容…），
        # 写死会对着「禁用动词」那条说「缺了 Always 的块」，误导。
        self.warn_head.configure(
            text=f"【自检提醒】{len(warn)} 条 —— 提示词可能有跑偏的地方，逐条看")
        body = "\n".join("· " + w for w in warn)
        if ok:
            body += "\n\n【通过项】\n" + "\n".join("· " + g for g in ok)
        self.warn_txt.configure(state="normal")
        self.warn_txt.delete("1.0", "end")
        self.warn_txt.insert("1.0", body)
        self.warn_txt.configure(state="disabled")
        self.warn_txt.see("1.0")
        self.warn_frame.pack(side="bottom", fill="x", pady=(8, 0))

    def _hide_warnings(self):
        if not getattr(self, "warn_frame", None):
            return
        self.warn_frame.pack_forget()
        self.warn_txt.configure(state="normal")
        self.warn_txt.delete("1.0", "end")
        self.warn_txt.configure(state="disabled")

    def copy_prompt(self):
        txt = self.prompt_txt.get("1.0", "end").strip()
        if not txt:
            messagebox.showinfo("提示", "还没有提示词，先点「生成提示词」。")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(txt)
        self.status_set("提示词已复制到剪贴板", "#0F6E56")

    def copy_settings(self):
        txt = self.settings_txt.get("1.0", "end").strip()
        if not txt:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(txt)
        self.status_set("设置说明已复制到剪贴板", "#0F6E56")

    def export(self):
        if not self.cur or not self.info:
            messagebox.showinfo("提示", "先选一张图片。")
            return
        default = os.path.splitext(os.path.basename(self.cur))[0] + "_hildegard.txt"
        path = filedialog.asksaveasfilename(
            title="导出", initialfile=default, defaultextension=".txt",
            filetypes=[("文本", "*.txt")])
        if not path:
            return
        body = [f"# 源图：{self.cur}", "",
                self.settings_txt.get("1.0", "end").strip(), "",
                "═" * 62, "提示词（直接粘进 CLIPTextEncode）", "═" * 62, "",
                self.prompt_txt.get("1.0", "end").strip(), ""]
        # 自检报告另起一段、并写明「不属于提示词」：它只是留档，
        # 上面那一段才是要整段复制进 CLIPTextEncode 的东西。
        wtxt = self.warn_txt.get("1.0", "end").strip() \
            if getattr(self, "warn_txt", None) else ""
        if wtxt:
            body += ["═" * 62, "自检报告（**不属于提示词**，别一起复制）", "═" * 62,
                     "", wtxt, ""]
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(body))
            self.status_set(f"已导出到 {path}", "#0F6E56")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    # ------------------------------------------------------------ 设置

    def open_settings(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("后端设置")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.resizable(False, False)

        ttk.Label(dlg, text="用哪个后端分析图片").grid(
            row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(18, 6))

        v_name = tk.StringVar(value=self.cfg.get("backend", "local"))
        f1 = ttk.Frame(dlg)
        f1.grid(row=1, column=0, columnspan=2, sticky="w", padx=18)
        ttk.Radiobutton(f1, text="本地 LM Studio（免费、离线、图片不出本机）",
                        variable=v_name, value="local").pack(anchor="w")
        ttk.Radiobutton(f1, text="云端 API（质量更好，按次付费，图片会上传）",
                        variable=v_name, value="cloud").pack(anchor="w")

        ttk.Separator(dlg, orient="horizontal").grid(
            row=2, column=0, columnspan=2, sticky="ew", padx=18, pady=14)

        v_proto = tk.StringVar()
        v_url = tk.StringVar()
        v_key = tk.StringVar()
        v_model = tk.StringVar()
        v_to = tk.StringVar()

        fields = [("协议", v_proto, ["openai", "anthropic"]),
                  ("base_url", v_url, None),
                  ("api_key", v_key, None),
                  ("model", v_model, None),
                  ("超时（秒）", v_to, None)]
        for i, (label, var, choices) in enumerate(fields):
            ttk.Label(dlg, text=label).grid(row=3 + i, column=0, sticky="w",
                                            padx=(18, 10), pady=4)
            if choices:
                w = ttk.Combobox(dlg, textvariable=var, values=choices, width=44,
                                 state="readonly")
            else:
                w = ttk.Entry(dlg, textvariable=var, width=46)
            w.grid(row=3 + i, column=1, sticky="w", padx=(0, 18), pady=4)

        hint = ttk.Label(dlg, text="", style="Hint.TLabel", justify="left")
        hint.grid(row=9, column=0, columnspan=2, sticky="w", padx=18, pady=(6, 0))

        loading = {"flag": False}

        def load_profile(*_):
            if loading["flag"]:
                return
            loading["flag"] = True
            c = self.cfg["backends"].get(v_name.get(), {})
            v_proto.set(c.get("protocol", "openai"))
            v_url.set(c.get("base_url", ""))
            v_key.set(c.get("api_key", ""))
            v_model.set(c.get("model", ""))
            v_to.set(str(c.get("timeout", 300)))
            if v_name.get() == "local":
                hint.configure(text="本地要先把 LM Studio 打开，并在 Developer → "
                                    "Local Server 里 Start Server（默认端口 1234）。\n"
                                    "model 留空则自动用加载的第一个模型。")
            else:
                hint.configure(text="Anthropic 填 https://api.anthropic.com；"
                                    "其它 OpenAI 兼容服务填到 /v1 结尾。\n"
                                    "key 会明文存在 config.json 里。")
            loading["flag"] = False

        v_name.trace_add("write", load_profile)
        load_profile()

        def store():
            c = self.cfg["backends"].setdefault(v_name.get(), {})
            c["protocol"] = v_proto.get() or "openai"
            c["base_url"] = v_url.get().strip()
            try:
                c["timeout"] = int(float(v_to.get() or 300))
            except Exception:
                c["timeout"] = 300
            c["api_key"] = v_key.get()
            c["model"] = v_model.get().strip()
            self.cfg["backend"] = v_name.get()
            save_config(self.cfg)
            self._reload_backend_label()

        def test():
            store()
            try:
                b = vision.make_backend(self.cfg["backends"][v_name.get()])
                models = b.list_models()
                lines = []
                if models:
                    lines.append(f"后端可用。检测到 {len(models)} 个模型：")
                    lines += ["· " + m for m in models[:12]]
                else:
                    lines.append("后端有响应，但没列出模型。")

                # LM Studio 的上下文长度是「回答被截断」的头号原因，顺手探一下
                ctx = None
                probe = getattr(b, "probe_context", None)
                if probe:
                    ctx = probe()
                    if ctx:
                        lines.append("")
                        lines.append(f"已加载上下文长度：{ctx} token")
                        if ctx < 8192:
                            lines.append("⚠ 偏小！图片+指令大约要占 2700，"
                                         "留给回答的不到 1400，可能输出半截 JSON。")
                            lines.append("   请在 LM Studio 里把模型的 Context Length "
                                         "调到 8192 以上（模型支持 128k）。")
                        else:
                            lines.append("✓ 够用。")
                messagebox.showinfo("连接成功", "\n".join(lines))
            except Exception as e:
                messagebox.showerror("连接失败", str(e))

        btns = ttk.Frame(dlg)
        btns.grid(row=10, column=0, columnspan=2, sticky="e", padx=18, pady=(16, 18))
        ttk.Button(btns, text="测试连接", command=test).pack(side="left")
        ttk.Button(btns, text="取消", command=dlg.destroy).pack(side="left", padx=(10, 0))
        ttk.Button(btns, text="保存", command=lambda: (store(), dlg.destroy())
                   ).pack(side="left", padx=(10, 0))


def main():
    enable_dpi_awareness()          # 必须在第一个 Tk() 之前
    dpi = get_system_dpi()
    root = tk.Tk()
    try:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except Exception:
        pass
    App(root, dpi=dpi)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        tb = traceback.format_exc()
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(tb + "\n")
        except Exception:
            pass
        try:
            import tkinter.messagebox as mb
            mb.showerror("启动失败", tb)
        except Exception:
            print(tb)
        raise
