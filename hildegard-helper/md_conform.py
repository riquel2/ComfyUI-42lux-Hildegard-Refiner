# -*- coding: utf-8 -*-
"""md 模板一致性对账 —— 把仓库的 llm_prompt_templates/*.md 当**唯一事实来源**，
逐条比对 templates.py 里的固定块和两张表。

为什么要单独一个模块：用户反馈过「提示词跑偏了」。固定块一旦被改写/截短，
LoRA 的训练信号就被稀释，而且**不会报错**——只能靠机器对账发现。
md 已经随项目一起存档在 llm_prompt_templates/，所以这个检查离线可跑。

两类检查：
  ① 引用块（模板句）：md 里每一条**规格**引用块，都要能在代码常量里找到
     逐字对应的那一条；反过来代码里多出来的常量也要说明出处。
  ② 表格：材质词库 / 纹理描述符逐行 diff（含子特征和守卫列）。

槽位（{...}）一律渲染成一个哨兵字符再比，所以 md 的 `{SCENE}` 和代码的
`{scene}{painterly}` 视为同一处（相邻槽位折叠成一个）。
"""

import io
import os
import re

import templates as T

MD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "llm_prompt_templates")
MD_FILES = {
    "full_build": "full_build_upscale_prompt.md",
    "texture": "texture_upscale_prompt.md",
    "subject_less": "subject_less_upscale_prompt.md",
}

SLOT = "\x01"

# ---------------------------------------------------------------- 归一化

# md 的规格引用块按「所在小标题」筛选 —— 只比规格，不比例子。
# 例子（Worked example）里的中段是**看图填的**，本来就不该和代码常量相等；
# 而且例子自己偶有笔误（full_build 例子里写 "clean catchlights"，
# 而规格第 2 块写的是 "clean specular catchlights" —— 以规格为准）。
SPEC_HEADINGS = {
    "full_build": ("### 0 + 1.", "#### Person mode", "#### Object mode",
                   "#### Scene / no-hero mode", "### 3.", "### 4.",
                   "### 5.", "### 6.", "### 7.", "### 8.", "### 9.",
                   "### 10."),
    "texture": ("## The prompt",),
    "subject_less": ("## The prompt",),
}

# md 里给人看的交叉引用，不能进提示词（见 templates.MD_DROPPED_NOTES）
DROP_NOTES = getattr(T, "MD_DROPPED_NOTES", [])

# md 的 `{creature's}` 把「所有格」写进了槽位里；代码写成 `{creature}'s`，
# 渲染结果相同（都得到 "orc's"）。统一成后一种再比。
NORMALIZE = [("{creature's}", "{creature}'s")]


# md 的某一行被**有意拆成多行**（附理由）。对账时按「已覆盖」处理，
# 但要逐条验证替代行真的都在。
#   Foliage / ground 那一行太笼统：模型会把「活着的树」认成「木料」，
#   只输出 wood-grain texture，植物和花全丢（用户实测过）。所以拆成
#   活体枝叶 / 花瓣 / 地被 / 砾土 / 木料五行。
MD_ROW_SPLIT = {
    "leaves, bark, gravel, soil, each its own texture": [
        "leaves, bark, and twigs, each its own texture",
        "soft petals with their delicate surface",
        "grass, ground cover, and moss, each its own texture",
        "gravel and soil, each its own texture",
        "wood-grain texture",
    ],
}


def render(s):
    """把 md / 代码里的模板串渲染成可比较的骨架。"""
    for a, b in NORMALIZE:
        s = s.replace(a, b)
    for note in DROP_NOTES:
        s = s.replace(note, "")
    s = re.sub(r"\{[^}]*\}", SLOT, s)
    s = re.sub(re.escape(SLOT) + "+", SLOT, s)   # 相邻槽位折叠成一个
    return re.sub(r"\s+", " ", s).strip()


def read_md(tier):
    with io.open(os.path.join(MD_DIR, MD_FILES[tier]), encoding="utf-8") as fh:
        return fh.read()


def blockquotes(text):
    """返回 [(最近的小标题, 引用块文本), ...]。"""
    out, buf, head = [], [], ""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            if buf:
                out.append((head, " ".join(buf)))
                buf = []
            head = s
        elif s.startswith(">"):
            buf.append(s.lstrip(">").strip())
        elif buf:
            out.append((head, " ".join(buf)))
            buf = []
    if buf:
        out.append((head, " ".join(buf)))
    return out


def md_table(text, first_col):
    rows, started = [], False
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            if started and rows:
                break
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if not started:
            started = cells[0] == first_col
            continue
        if set(cells[0]) <= set("-: "):
            continue
        rows.append(tuple(cells))
    return rows


# ---------------------------------------------------------------- 代码侧清单

def code_blocks():
    """{名字: 骨架}，覆盖 md 规格里出现的每一个固定块。"""
    blocks = {
        "OPEN_MULTI": T.OPEN_MULTI,
        "OPEN_SINGLE": T.OPEN_SINGLE,
        "EYES_CLAUSE": T.EYES_CLAUSE,
        "HAIR_TEMPLATE": T.HAIR_TEMPLATE,
        "SUBJECT_OBJECT": T.SUBJECT_OBJECT,
        "SUBJECT_SCENE": T.SUBJECT_SCENE,
        "MATERIAL_TEMPLATE": T.MATERIAL_TEMPLATE,
        "ENVIRONMENT_TEMPLATE": T.ENVIRONMENT_TEMPLATE,
        "LIGHTING_TEMPLATE": T.LIGHTING_TEMPLATE,
        "CLEANUP_BASE": T.CLEANUP_BASE,
        "SCOPE_LOCK_MULTI": T.SCOPE_LOCK_MULTI,
        "SCOPE_LOCK_SINGLE": T.SCOPE_LOCK_SINGLE,
        "STYLE_TAG": T.STYLE_TAG,
        "TEXTURE_PROMPT": T.TEXTURE_PROMPT,
        "SUBJECTLESS_PROMPT": T.SUBJECTLESS_PROMPT,
    }
    for k, v in T.SKIN_VARIANTS.items():
        blocks[f"SKIN_VARIANTS[{k}]"] = v
    return {k: render(v) for k, v in blocks.items()}


# ---------------------------------------------------------------- 对账

def check():
    """返回 (ok 列表, 问题列表)。问题列表为空即完全一致。"""
    ok, bad = [], []
    blocks = code_blocks()
    used = set()

    for tier, heads in SPEC_HEADINGS.items():
        text = read_md(tier)
        for head, bq in blockquotes(text):
            # ⚠ 必须按**前缀**匹配：md 的小标题是
            #   "### 0 + 1. Trigger, reference handling & master framing — FIXED"
            #   而 SPEC_HEADINGS 里只写 "### 0 + 1."。写成 `head not in heads`
            #   会一条都匹配不到，然后所有代码常量都被误报成「自己加的话」。
            if not any(head.startswith(h) for h in heads):
                continue
            # `> **Pending validation:** …` 这类是给人看的备注，不是模板句
            if bq.startswith("**"):
                continue
            want = render(bq)
            hit = [n for n, s in blocks.items() if s == want]
            if not hit:
                bad.append(f"[{tier}] md 的规格引用块在代码里找不到逐字对应："
                           f"\n      {bq[:170]}")
                near = max(blocks.items(),
                           key=lambda kv: _ratio(kv[1], want))
                if _ratio(near[1], want) > 0.6:
                    bad.append(f"      最接近的是 {near[0]}（相似度 "
                               f"{_ratio(near[1], want):.2f}）："
                               f"\n        {_diff(want, near[1])}")
            else:
                used.update(hit)
                ok.append(f"[{tier}] {head} → {', '.join(hit)}")

    # 代码里多出来的常量：必须能对上 md 某处，否则就是自己加的措辞
    extra = sorted(set(blocks) - used)
    # 这几条在 md 里是**内联引号**给的（不是引用块），由 _check_inline 单独核对
    inline_derived = {"PAINTERLY_SUFFIX", "SCOPE_LOCK_MULTI"}
    for name in extra:
        if name in inline_derived:
            continue
        bad.append(f"代码常量 {name} 没有对应的 md 规格引用块"
                   f"（要么补进 md，要么就是自己加的话）")

    ok.extend(_check_tables(bad))
    ok.extend(_check_inline(bad))
    return ok, bad


def _ratio(a, b):
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio()


def _diff(a, b):
    import difflib
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if tag == "equal":
            continue
        out.append(f"md={a[i1:i2]!r} 代码={b[j1:j2]!r}")
    return " | ".join(out) or "（仅空白差异）"


def _check_tables(bad):
    ok = []

    # ① 材质词库（full_build 的 Material vocabulary bank）
    md_rows = md_table(read_md("full_build"), "Material")
    code = {render(r[0]): (render(r[1]), render(r[2])) for r in T.MATERIAL_BANK}
    for name, sub, guard in md_rows:
        k = render(name)
        if k not in code:
            bad.append(f"材质词库：md 有、代码没有 → {name}")
            continue
        cs, cg = code[k]
        if cs != render(sub):
            bad.append(f"材质词库 sub-features 不一致 → {name}\n"
                       f"      {_diff(render(sub), cs)}")
        if cg != render(guard):
            bad.append(f"材质词库 guard 不一致 → {name}\n"
                       f"      {_diff(render(guard), cg)}")
    ok.append(f"材质词库 {len(md_rows)} 行逐行核对")
    for k in code:
        if k not in {render(r[0]) for r in md_rows}:
            bad.append(f"材质词库：代码有、md 没有 → {k}")

    # ② 纹理描述符（texture 的 Texture descriptors）
    md_desc = {render(r[1]) for r in md_table(read_md("texture"), "Surface")}
    code_desc = {render(en) for _, en in T.TEXTURE_DESCRIPTORS}
    for d in sorted(md_desc - code_desc):
        # 被有意拆成多行的，验证替代行齐了就算过
        subs = MD_ROW_SPLIT.get(d)
        if subs is None:
            bad.append(f"纹理描述符：md 有、代码没有 → {d!r}")
            continue
        miss = [s for s in subs if render(s) not in code_desc]
        if miss:
            bad.append(f"纹理描述符：md 的 {d!r} 被有意拆分，但替代行缺 {miss}")
        else:
            ok.append(f"纹理描述符 {d!r} 按 md 注释拆成 {len(subs)} 行，替代行齐全")
    # 代码多出来的行（自然材质细分）是有意的扩展，但要能报出来
    extra_desc = sorted(code_desc - md_desc)
    ok.append(f"纹理描述符 md {len(md_desc)} 条 / 代码 {len(code_desc)} 条"
              f"（代码多出 {len(extra_desc)} 条＝自然材质细分，见 templates.py 注释）")
    return ok


def _check_inline(bad):
    """md 里以内联引号给出的措辞（painterly 变体、film grain）。"""
    ok = []
    full = read_md("full_build")
    # Block 1 painterly 变体
    m = re.search(r'\*\*Block 1:\*\*\s*"([^"]+)"', full)
    if m:
        want = render(m.group(1).strip(". "))
        got = render(T.PAINTERLY_SUFFIX.strip(", "))
        if want != got:
            bad.append("painterly 变体与 md 不一致\n      " + _diff(want, got))
        else:
            ok.append("painterly 变体（Block 1 内联引用）一致")
    # film grain
    if "preserving the natural film grain as part of the look" in full:
        if "Preserve the natural film grain as part of the look" \
                not in T.FILM_GRAIN_CLAUSE:
            bad.append("film grain 从句与 md 不一致："
                       f"{T.FILM_GRAIN_CLAUSE!r}")
        else:
            ok.append("film grain 从句一致")
    # scope lock 的多参考形式：md 第 9 块正文说
    #   "pin to "identical to image 1" instead of "the source image""
    #   —— 是内联指令，不是独立引用块。必须真的只差这一处替换。
    if "the source image" in T.SCOPE_LOCK_SINGLE:
        want = render(T.SCOPE_LOCK_SINGLE.replace("the source image", "image 1"))
        got = render(T.SCOPE_LOCK_MULTI)
        if want != got:
            bad.append("scope lock 的多参考形式与 md 指令不一致（只该把 "
                       "'the source image' 换成 'image 1'）\n      "
                       + _diff(want, got))
        else:
            ok.append("scope lock 多参考形式（Block 9 内联指令）一致")
    else:
        bad.append("scope lock 单参考形式里没有 'the source image'，"
                   "md 的替换指令无从落地")

    # luminance noise 增补
    if "luminance noise" not in T.CLEANUP_WITH_NOISE:
        bad.append("md 要求真照片在 clean-up 里加 luminance noise，代码没有")
    else:
        ok.append("clean-up 的 luminance noise 增补一致")
    return ok


if __name__ == "__main__":
    import sys
    _ok, _bad = check()
    for line in _ok:
        print("  ✓", line)
    print()
    if _bad:
        for line in _bad:
            print("  ✗", line)
        print(f"\n对账失败：{len(_bad)} 处不一致")
        sys.exit(1)
    print("对账通过：md 模板与代码完全一致")
