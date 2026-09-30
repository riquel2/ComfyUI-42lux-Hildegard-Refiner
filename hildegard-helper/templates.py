# -*- coding: utf-8 -*-
"""三档提示词模板 + 组装器。

设计要点：仓库模板里的「固定块」（触发词、参考说明、clean-up、scope lock）
一律由代码逐字输出，AI 只负责填「变量槽位」（主体、材质、光照）。
这样既保证 LoRA 训练信号不被改写，又让 AI 只做它真正擅长的事——看图。

模板原文逐字取自：
  llm_prompt_templates/full_build_upscale_prompt.md
  llm_prompt_templates/texture_upscale_prompt.md
  llm_prompt_templates/subject_less_upscale_prompt.md
"""

import re

TRIGGER = "RFNTILE."

# ---------------------------------------------------------------- 固定块

OPEN_MULTI = (
    "RFNTILE. refine and add detail to this upscaled tile. Image 1 is the tile to "
    "refine — resolve fine detail and crisp, clean edges across image 1 only, as a "
    "high-resolution delivery master of {scene}{painterly}. Image 2 shows where this "
    "tile sits within the full frame; image 3 is the full source photo — draw on "
    "images 2 and 3 only for matching colour, lighting, and edge continuity, keeping "
    "all rendered content sourced from image 1."
)

OPEN_SINGLE = (
    "RFNTILE. refine and add detail to this upscaled tile. Finish this image as a "
    "high-resolution delivery master. Resolve fine detail and crisp, clean edges "
    "throughout {scene}{painterly}."
)

# painterly 变体是插进 Block 1 句内的（仓库原文：Block 1: "...while preserving..."），
# 不能另起一句，否则会出现 "…image 1. while preserving…" 这种断句。
PAINTERLY_SUFFIX = (", while preserving its painterly, concept-art rendering "
                    "and brushwork")

EYES_CLAUSE = (
    "Resolve the eyes with sharp, lifelike clarity — crisp irises, clean specular "
    "catchlights, and individual eyelashes."
)

HAIR_TEMPLATE = "Resolve the hair as fine, individual strands{hair_detail}, with soft, defined flyaways."

SUBJECT_OBJECT = (
    "Resolve the {obj} as the sharp focal subject — render {surfaces} with crisp form, "
    "clean edges, and accurate detail true to the source."
)

SUBJECT_SCENE = (
    "Treat this as a layered scene with no single subject — resolve each depth layer at "
    "the clarity it already holds, recovering detail that is present rather than "
    "inventing new content."
)

MATERIAL_TEMPLATE = (
    "On the {material}, resolve {sub_features} as crisp, well-defined detail, {guard}."
)

ENVIRONMENT_TEMPLATE = (
    "Keep the {background} naturally detailed within its existing {depth}, holding the "
    "{soft_elements} as {state}."
)

LIGHTING_TEMPLATE = (
    "Maintain the {lighting} and the {grade} colour grade, with the existing contrast, "
    "white balance, framing, and depth of field exactly as shown."
)

CLEANUP_BASE = (
    "Clean up degradation from the source: suppress compression artifacts, colour "
    "banding, and aliasing, and render smooth, even tonal gradients and clean, clear "
    "shadow detail."
)
CLEANUP_WITH_NOISE = (
    "Clean up degradation from the source: suppress compression artifacts, colour "
    "banding, aliasing, and luminance noise, and render smooth, even tonal gradients "
    "and clean, clear shadow detail."
)
FILM_GRAIN_CLAUSE = " Preserve the natural film grain as part of the look."

SCOPE_LOCK_MULTI = (
    "Keep the content, composition, framing, colours, and style identical to image 1 — "
    "change only resolution, sharpness, and fine detail."
)
SCOPE_LOCK_SINGLE = (
    "Keep the content, composition, framing, colours, and style identical to the source "
    "image — change only resolution, sharpness, and fine detail."
)

# Block 10 风格 / 情绪标签。md 原文（full_build 第 10 块）：
#     > Style: {medium}. Mood: {existing mood}.
#   例子：Style: digital concept art. Mood: cold, cinematic.
#   ⚠ 每个标签自带句号。之前这里写的是 "Style: {medium} Mood: {mood}"（没句号），
#     而且**是个死常量**（组装时用的是另写的 f-string）。两者格式不一致，
#     谁照着常量改就会把句号弄丢。现在这里是唯一事实来源，组装处直接用它。
STYLE_TAG = "Style: {medium}. Mood: {mood}."
STYLE_ONLY = "Style: {medium}."
MOOD_ONLY = "Mood: {mood}."

# md 表格里带「(see Limitations)」这种**给人看的交叉引用**，不能进提示词
# —— 提示词是给模型看的，"（见限制章节）" 对它没有任何意义，只会占 token。
# 所以这几处是**有意的例外**：措辞按 md，但去掉末尾的括号备注。
# 对账脚本（md_conform.py）用这张表把它们和「真截短」区分开。
MD_DROPPED_NOTES = ["(see Limitations)"]

# texture 档：整段固定，只有 {TEXTURE SURVEY} 一个槽位
TEXTURE_PROMPT = (
    "RFNTILE. refine and add detail to this upscaled tile. Restore the image quality "
    "and resolve it to a sharp, high-resolution result. Remove compression artifacts, "
    "banding, and noise, and clarify soft or blurred areas into crisp, clean edges and "
    "definition. Enrich existing textures and surfaces with fine, intricate, "
    "physically accurate detail — {texture_survey} — recovering realistic, lifelike "
    "micro-detail only where detail is present in the source, matching the existing "
    "grain, focus, and material properties of each surface. Keep in-focus subjects "
    "crisp and sharp, and keep softly blurred or out-of-focus areas soft, holding "
    "their existing depth of field. Preserve the original lighting, colour, contrast, "
    "and composition exactly as shown, leaving evenly-toned areas clean and untouched. "
    "Produce a clean, photorealistic result faithful to the source."
)

# subject_less 档：同样整段固定，只有 {TEXTURE FAMILIES} 一个槽位
SUBJECTLESS_PROMPT = (
    "RFNTILE. refine and add detail to this upscaled tile. Restore the image quality "
    "and resolve it to a sharp, high-resolution result. Remove compression artifacts, "
    "banding, and noise, and clarify soft or blurred areas into crisp, clean edges and "
    "definition. Enrich the existing textures and surfaces already present in this "
    "tile with fine, intricate, physically accurate detail — refining whatever "
    "materials appear, sharpening fine texture, edges, and surface structure, and "
    "resolving {texture_families} wherever they occur. Recover realistic, lifelike "
    "micro-detail only where detail is already present, matching the existing grain, "
    "focus, colour, and material properties of each surface. Keep in-focus areas crisp "
    "and sharp, keep softly blurred areas soft, and leave flat or evenly-toned areas "
    "clean and smooth. Add no new objects, elements, or content — refine only what is "
    "already in the tile. Preserve the original lighting, colour, contrast, and "
    "composition exactly as shown. Produce a clean, photorealistic result faithful to "
    "the source."
)

# ---------------------------------------------------------------- 皮肤变体

SKIN_VARIANTS = {
    "young_adult": (
        "Render the skin with a realistic matte finish and lifelike, pore-level "
        "texture — keep visible pores, fine skin detail, freckles, and natural soft "
        "highlights, so the skin reads as real, textured, and matte."
    ),
    "older": (
        "Render the skin with a realistic matte finish and lifelike, pore-level "
        "texture — keep the wrinkles, crow's-feet, skin pigmentation, and full age "
        "character, so the face keeps its real, lived-in detail."
    ),
    "child": (
        "Render the skin with a soft, natural matte finish appropriate to a young "
        "child — keep the skin smooth, soft, and lifelike with realistic fine detail."
    ),
    "glossy": (
        "Render the skin with realistic pore-level texture and its natural sheen — "
        "keep the visible pores, fine skin detail, and the existing wet/oily "
        "highlights as part of the source."
    ),
    "creature": (
        "Render the {creature}'s skin/hide with a realistic matte finish and "
        "weathered, pore-level texture — keep the deep wrinkles, coarse leathery "
        "surface, painted markings, scars, and blemishes, so the face keeps its real, "
        "lived-in character."
    ),
}

SKIN_LABEL = {
    "young_adult": "青年 / 常规人像光",
    "older": "年长者",
    "child": "儿童",
    "glossy": "微距 / 汗 / 油 / 强方向光（覆盖哑光默认值）",
    "creature": "非人生物（兽人、怪物等）",
}

# ---------------------------------------------------------------- 材质词库

MATERIAL_BANK = [
    ("Polished metal", "specular highlights, mirror reflections, scratches, rivets, hinges, seams", "reflections accurate and true to the source"),
    ("Aged / weathered metal", "worn patina, cracked finish, tarnish, scratches", "aged, weathered character intact"),
    ("Translucent glass", "refraction, internal caustics, surface highlights, content within", "clean, clear, and fully transparent"),
    ("Sheer fabric", "layered weave, chiffon/tulle, beadwork, panel translucency", "each layer crisp and individually defined"),
    ("Lace / fine openwork fabric", "floral lace pattern, mesh ground, scalloped edges, skin visible through the weave", "pattern faithful to the source, skin visible through the open weave"),
    ("Feathers", "barbs, downy texture, flyaway strands", "each barb a separate, cleanly defined strand"),
    ("Short animal fur", "individual hairs, directional flow, natural clumping", "fine, distinct hairs with natural flow and clumping"),
    ("Reptilian / scaled hide", "overlapping scales, scale texture, edges", "crisp, distinct overlapping scales"),
    ("Antlers / bone", "velvet and bone texture, tine tips", "crisp surface texture with sharp tips"),
    ("Carved stone", "relief, scrollwork, patina, lichen", "sharp, deep-cut geometric detail"),
    ("Foliage / ground", "moss, bark, growth rings, rock, soil", "each surface its own distinct texture"),
    ("Sparkles / particles", "points of light", "clean, sharp, well-defined points of light"),
    ("Bokeh discs", "smooth defocused circles", "smooth discs, evenly filled, with soft edges"),
    ("Lips", "natural lip texture, soft sheen", "natural texture with controlled, realistic sheen"),
    ("Tattoos / body art", "linework, shading, colour of the artwork", "faithful to the source as drawn, not redrawn"),
    ("Glitter makeup", "sparkle points across the skin", "clean, distinct points of light over matte skin"),
    ("Painted nails", "smooth nail surface, polish colour", "clean, smooth, defined surfaces"),
    ("Automotive paint", "metallic flake, specular rolloff, panel reflections", "reflections clean and true to the source"),
    ("Glossy spheres / clustered objects", "each object, single clean highlight", "each object separate with its own defined edge"),
    ("Printed text / labels", "letterforms, layout, logo marks", "reproduce the existing lettering exactly"),
    ("Matte plastic", "moulded form, fine surface grain, panel seams", "even matte surface with crisp moulded edges"),
    ("Glossy plastic", "smooth shell, highlights, moulded detail", "clean even gloss with sharp moulded edges"),
    ("Brushed / chrome metal", "brushed grain or mirror finish, edge highlights", "accurate finish true to the source"),
    ("Rubber / tyres", "moulded surface, tread pattern, sidewall texture", "defined detail and even matte rubber surface"),
    ("Liquid / fluid", "surface highlights, translucency, meniscus, bubbles", "clean, translucent, with crisp surface detail"),
    ("Water surface / caustics", "ripples, refraction, caustic light patterns", "crisp, clean detail held as source content"),
    ("Air bubbles", "rounded bubble shapes, highlights", "clean, well-defined rounded shapes"),
    ("Screens / displays", "pixels, UI elements, emitted glow", "crisp display content with even emitted light"),
    ("Paper / packaging", "fibre texture, print, folds and creases, embossing", "crisp print and clean material texture"),
    ("Translucent / backlit organic", "subsurface glow, veining, colour gradients", "luminous translucency, smooth clean gradients"),
    ("Regular tiled patterns", "tile grid, straight edges, joints", "clean, straight tile edges"),
    ("Candle / small flame", "flame shape, emitted glow", "clean, even flames with soft, intact glow"),
    ("Patterned silk / printed fabric", "woven sheen, fabric folds, the print", "pattern faithful to the source, weave intact"),
    ("Velvet / matte fabric", "directional nap, soft surface", "soft matte nap, no false sheen"),
    ("Lens distortion / optical aberration", "barrel curvature, warped reflections, soft aberration", "preserved exactly as in the source"),
]

# 材质措辞词典 —— texture 档的**唯一事实来源**。
#
# 每行 (中文观察 → 仓库官方英文措辞)：
#   · vision.TEX_TABLE 直接由这张表生成（不会再出现「提示词里的词库和检测
#     用的词库对不上」这种漂移）；
#   · survey_is_copied() 也用它做「照抄」判定。
#
# ⚠ 顺序有意义。survey_is_copied 的主判据是「条目顺着这张表的行号一路往下
#   走」——照抄的回答会沿表序走，真看图的回答按画面占比排序。所以同族材质
#   要挨着排，别按字母序打散。
#
# 自然材质那一段（植物 / 花瓣 / 地被 / 砾土 / 木料）是 2026-09-24 补的：
# 用户实测「植物、树木、花、鸟」的画面，输出里只有 wood-grain texture，
# 植物和花全丢了。原因是旧表只有一条笼统的 "Foliage / ground"，而
# "Wood" 写的就是 wood-grain —— 模型把「活树」认成了「木料」。
# 现在把活体植物 / 花瓣 / 地被 / 砾土 / 木料分开写。
TEXTURE_DESCRIPTORS = [
    ("哑光皮肤（常规光）", "natural matte skin with pore-level texture"),
    ("微距 / 汗 / 油 / 强光下的皮肤", "skin with pore-level texture and its natural sheen"),
    ("嘴唇", "the soft sheen of the lips"),
    ("眼睛", "detailed eyes with iris fibres"),
    ("睫毛与眉毛", "fine individual eyelashes and eyebrow hairs"),
    ("直发", "fine individual hair strands with flyaways"),
    ("卷发", "dense curly hair with fine individual coils"),
    ("动物毛", "fine individual fur with natural directional flow"),
    ("羽毛", "soft layered feathers with their barbs and downy texture"),
    # ⚠ 措辞必须与 llm_prompt_templates/texture_upscale_prompt.md 的
    #   「Texture descriptors」表**逐字一致**（槽位例外见 MD_DROPPED_NOTES）。
    #   2026-09-27 对账时发现 5 条被截短过 —— 少了 "and reflections" /
    #   "folds," / "pattern" / "surface" 这些词。丢掉的是子特征，不是修饰：
    #   "specular highlights and reflections" 里 reflections 是**另一件事**，
    #   截短等于少点了一类要恢复的细节。已按 md 复原。
    ("抛光金属", "polished reflective metal with specular highlights and reflections"),
    ("金饰雕工", "engraved gold filework with its set stones"),
    ("亮光漆", "glossy paint with specular highlights and reflections"),
    ("玻璃", "clear glass with refraction and highlights"),
    ("亮面塑料", "glossy translucent plastic with creases, folds, and highlights"),
    ("哑光模压面", "even matte moulded surface"),
    ("印花绸缎", "glossy patterned fabric with its print and folds"),
    ("天鹅绒", "soft matte velvet with directional nap"),
    ("粗织物 / 麻 / 针织", "coarse woven fabric texture"),
    ("蕾丝", "fine openwork lace pattern with mesh ground"),
    ("珠绣 / 亮片", "dense beaded surface, each bead a point of light"),
    ("石头", "rough textured stone surface"),
    ("枝叶 / 树皮 / 枝桠（活树、灌木）", "leaves, bark, and twigs, each its own texture"),
    ("花瓣 / 花朵", "soft petals with their delicate surface"),
    ("草 / 地被 / 苔藓", "grass, ground cover, and moss, each its own texture"),
    ("砾石 / 泥土", "gravel and soil, each its own texture"),
    ("木料 / 木板（家具、地板、画框）", "wood-grain texture"),
    ("水面波纹", "rippled water with reflections"),
    ("火焰", "bright flame with its glow"),
    ("印刷图文 / 字迹", "printed artwork and lettering"),
    # ── 下面两行是「整幅是画」的落脚点。措辞是调过的，别改。 ──────────
    # 踩过的坑（2026-09-24）：为了补「植物 / 花」的漏项，我给花瓣加了
    #   and fine veining，并让模型把画里的树写成 leaves, bark, and twigs。
    # 但源图是 de Gournay 手绘壁纸（孔雀 + 石榴树）—— 花和叶子都是**画出来的**。
    # bark / 枝桠 / 叶脉 / 羽枝 这类词是「请给我写实微观结构」的请求，
    # 模型会照着在平涂画面上叠一层写实材质，出图明显不像原图。
    # 用户实测反馈：「没改之前的提示词好，生成的图片更符合原图」。
    # → 画面本身是手绘 / 印刷时，画出来的东西一律写 painted …；
    #   brushwork 要**留着** —— 它是「保住平涂笔触」的信号，不是发明许可证，
    #   拿掉它反而会让底色被当照片处理。保真的保证是模板里
    #   "only where detail is present in the source" 那句。
    ("画出来的花鸟树石（手绘 / 印刷图案本身）", "painted foliage, flowers, and plumage"),
    ("平涂颜料（手绘、壁纸、绘画的底色）", "flat painted pigment fields with their brushwork"),
]

# 写作规则（full_build 用）。
# ⚠ 这 8 条的顺序**不要动**：这是原先就调好、并且已经出过图的顺序。
#   上一轮我为了给列表档做子集，把它拆成两组再拼回去，顺序变成
#   1,2,6,3,4,5,7,8 —— 内容一样但 full_build 的提示词编号变了。
#   既然用户反馈「旧的更好」，就不要在这种地方留无谓的差异。
WRITING_RULES = [
    "正向措辞，不写否定。唯一例外是 clean-up 句，那里可以用 suppress / clean up。",
    "只用 resolve / recover / preserve / sharpen / render / hold / maintain；"
    "禁用 enhance / improve / make better / fix。",
    "点名具体物件，不堆形容词。",
    "光照要写清光源、质地、方向、色温。",
    "柔和渐变元素要在环境块和 clean-up 块各提一次。",
    "没点名具体东西的从句一律删掉。",
    "皮肤按实际观察写：默认哑光，微距 / 汗 / 油 / 强光下就是有光泽的。",
    "长度随画面复杂度：简单主体就短，多材质才长。",
]

# texture / subject_less 只输出一个「分号列表」槽位，**没有环境块、光照块、
# clean-up 块**，所以上面第 3/4/5/7/8 条对它们不适用 —— 留着不但白占上下文
# （本地模型常常只有 4096），还会诱导模型去写光照 / 环境从句，跑到别的档位。
# 只保留通用的 1/2/6 三条。（用下标而不是复制一份，免得两边改不同步。）
LIST_TIER_RULES = [WRITING_RULES[0], WRITING_RULES[1], WRITING_RULES[5]]


# ---------------------------------------------------------------- 组装

def _clean(text):
    """压掉多余空白，保证句间单空格。"""
    return re.sub(r"\s+", " ", str(text or "").strip())


def _sentence(text):
    """补句号（模板里每个从句都要独立成句）。"""
    t = _clean(text)
    if not t:
        return ""
    if t[-1] not in ".!?":
        t += "."
    return t


_ARTICLE_RE = re.compile(r"^(?:the|a|an)\s+", re.I)
_PREP_RE = re.compile(
    r"^(?:over|under|on|in|into|with|across|throughout|around|amid|against|"
    r"beneath|above|below)\b", re.I)

# 槽位里模型经常**写成整句**，而模板要求的是名词短语。
# 实测（2026-09-30）：environment 四个槽位都写成整句时，拼出来是
#   "Keep the there is a marble counter naturally detailed within its existing
#    it is shallow, holding the bokeh is soft as discs are smooth."
# —— 语法破碎，模型的遵循度会掉。下面这些开头都是「存在句 / 系表句」，
# 切掉之后剩下的才是要的名词短语。
_COPULA_RE = re.compile(
    r"^(?:there\s+(?:is|are|was|were)\s+|there'?s\s+|"
    r"it\s+(?:is|was)\s+|it'?s\s+|they\s+(?:are|were)\s+|"
    r"this\s+(?:is|was)\s+|that\s+(?:is|was)\s+|"
    r"you\s+can\s+see\s+|we\s+see\s+|i\s+see\s+|"
    r"the\s+image\s+shows?\s+|you\s+see\s+)", re.I)
# 槽位尾部带句号会把句子劈成两半：
#   "Keep the marble counter. naturally detailed …"
_TRAIL_PUNCT_RE = re.compile(r"[\s.,;:!?]+$")


def _strip_copula(text):
    """切掉槽位开头的存在句 / 系表句，并去掉尾标点。

    只用于**名词短语**槽位（材质 / 背景 / 光照 / 场景…）。
    从句槽位（fine_details、environment.clause）**不要**用这个 ——
    它们本来就该是完整从句，"It is sharp." 是合法的。
    """
    t = _clean(text)
    for _ in range(3):
        prev = t
        t = _COPULA_RE.sub("", t)
        if t == prev:
            break
    return _TRAIL_PUNCT_RE.sub("", t).strip()


# 主语在前的系表句：「<名词> is/are <形容词>」→「<形容词> <名词>」。
#   "bokeh is soft" → "soft bokeh"
# ⚠ 只在**能确定尾部是短形容词短语**时才翻，否则宁可不动、交给 validate 报出来：
#   尾部超过 4 个词、含介词、或含 -ing 分词，都说明它不是形容词短语，
#   翻了会出笑话（"light is coming from the left" → "coming from the left light"）。
_FLIP_RE = re.compile(r"^(.+?)\s+(?:is|are|was|were)\s+(.+)$", re.I)
_FLIP_BAD_TAIL_RE = re.compile(
    r"\b(?:from|with|into|to|of|in|on|at|by|for|about|through|that|which)\b"
    r"|\w+ing\b", re.I)


def _copula_flip(t):
    m = _FLIP_RE.match(t)
    if not m:
        return t
    # 头部要先把冠词剥掉，否则会翻出 "soft the bokeh"
    head = _ARTICLE_RE.sub("", m.group(1).strip()).strip()
    tail = m.group(2).strip()
    if not head or not tail:
        return t
    if len(tail.split()) > 4 or _FLIP_BAD_TAIL_RE.search(tail):
        return t
    return f"{tail} {head}"


def _bare(text):
    """模板里已经带冠词时（On the {material} / Keep the {background}），
    槽位必须给「裸名词短语」。模型经常顺手多写一个 the，
    这里统一兜掉，免得拼出 "Keep the the blush-pink ground"。

    2026-09-30 扩充两步（都是实测出来的，见 _strip_copula 的注释）：
      ① _strip_copula：切存在句/系表句开头 + 去尾标点；
      ② _copula_flip：把主语在前的系表句翻回名词短语。
    顺序不能反 —— "there is a marble counter" 必须先切 "there is "
    才能露出 "a marble counter" 里的那个 a。
    """
    t = _copula_flip(_strip_copula(text))
    if not t:
        return t
    for _ in range(3):
        prev = t
        t = _ARTICLE_RE.sub("", t)
        if t == prev:
            break
    return _TRAIL_PUNCT_RE.sub("", t).strip()


# ------------------------------------------------- 照抄词典检测（texture 档）

def survey_items(survey):
    """把 texture_survey 拆成条目。这一档规定用分号分隔，所以只按分号拆。"""
    t = _clean(survey).lower().rstrip(".")
    out = []
    for p in re.split(r"[;；]", t):
        p = _clean(p).strip(" .;,")
        if p:
            out.append(p)
    return out


def _table_index(item, bank):
    """item 对应词典里的行号；匹配不到返回 None。"""
    for i, b in enumerate(bank):
        if item == b or item in b or b in item:
            return i
    return None


def survey_is_copied(survey, run_min=5, run_frac=0.6,
                     dump_hits=6, dump_ratio=0.95):
    """判断 texture_survey 是不是把材质词典整段抄了回来。

    本地 7B 有个顽固毛病：把「材质措辞词典」当成「答案」直接抄。图里是
    手绘壁纸（没有皮肤、没有头发、没有嘴唇），它照样输出一整串皮肤 / 头发 /
    嘴唇 —— 而 texture 档的槽位是**直接拼进提示词**的，模型会照着把这些
    材质真的画到墙上，等于内容污染。

    ⚠ 判据**不是**「用没用词典措辞」。用词典措辞本来就是这一档的设计意图
    （那是仓库给的官方句式），所以「命中率高」不能当抄写的证据 —— 否则
    「平涂颜料 + 羽毛 + 花瓣 + 叶片」这种**完全正确**的回答会被误杀。

    真正的特征是**顺着词典的行号一路往下抄**：真看图的回答按「画面占比」
    排序，和词典行序无关；照抄的回答会沿着词典顺序走。所以主判据是
    「最长的一段行号递增序列」。另加一条兜底：条目几乎全部逐字命中且
    数量很多时，不论顺序都算整表搬运（防打乱顺序的抄法）。
    """
    items = survey_items(survey)
    if len(items) < 4:
        return False
    bank = [en.lower() for _, en in TEXTURE_DESCRIPTORS]
    idx = [_table_index(it, bank) for it in items]
    hits = [i for i in idx if i is not None]

    # 兜底：几乎全命中 + 条目多 → 整表搬运（打乱顺序也拦得住）
    if len(hits) >= dump_hits and len(hits) / len(items) >= dump_ratio:
        return True

    # 主判据：最长递增段（照抄的典型轨迹）
    best = cur = 0
    prev = -1
    for i in idx:
        if i is None:
            cur, prev = 0, -1
        elif i > prev:
            cur += 1
            prev = i
        else:
            cur, prev = 1, i
        best = max(best, cur)
    return best >= run_min and best / len(items) >= run_frac


def assemble_full_build(d, multi_reference=True):
    """按 0→10 顺序拼装 full_build 档。"""
    parts = []

    # --- 0 + 1 触发词 / 参考说明 / 主框架（固定块） ---
    # scene 是名词短语，但模板里 "…delivery master of {scene}" **不带冠词**，
    # 所以只切存在句/系表句，**保留**冠词（"a portrait" 不能变成 "portrait"）。
    scene = _strip_copula(d.get("scene") or "this image") or "this image"
    opener = (OPEN_MULTI if multi_reference else OPEN_SINGLE).format(
        scene=scene,
        painterly=(PAINTERLY_SUFFIX if d.get("painterly") else ""))
    parts.append(opener)

    # --- 2 主体 ---
    modes = d.get("mode") or ["scene"]
    if isinstance(modes, str):
        modes = [modes]
    if "person" in modes:
        variant = d.get("skin_variant") or "young_adult"
        skin = SKIN_VARIANTS.get(variant, SKIN_VARIANTS["young_adult"])
        if "{creature}" in skin:
            skin = skin.format(creature=_clean(d.get("creature") or "creature"))
        if d.get("painterly"):
            skin = ("Render the skin with clear, well-rendered facial detail in keeping "
                    "with the painted style.")
        parts.append(_sentence(skin))
        if d.get("eyes", True):
            parts.append(EYES_CLAUSE)
    if "object" in modes:
        parts.append(_sentence(SUBJECT_OBJECT.format(
            obj=_bare(d.get("object") or "focal object"),
            surfaces=_bare(d.get("object_surfaces") or "its primary surfaces"),
        )))
    if "scene" in modes or not ("person" in modes or "object" in modes):
        parts.append(SUBJECT_SCENE)

    # --- 3 头发 ---
    hair_detail = _clean(d.get("hair_detail"))
    if "person" in modes and d.get("hair_visible", bool(hair_detail)):
        parts.append(HAIR_TEMPLATE.format(
            hair_detail=(", " + hair_detail.lstrip(", ")) if hair_detail else ""))

    # --- 4 特殊材质（核心槽位） ---
    # 同一条材质写两遍会输出两句一模一样的 "On the …"，纯冗余，还会把同一个
    # 概念重复加权。这里按（材质+子特征+守卫）三元组去重；
    # validate() 会把「去掉了 N 条」和「同一材质写了多次」报给用户。
    _seen_mat = set()
    for m in (d.get("materials") or []):
        if not isinstance(m, dict):
            continue
        mat = _bare(m.get("material"))
        sub = _clean(m.get("sub_features"))
        guard = _clean(m.get("guard"))
        if not (mat and sub and guard):
            continue
        _key = (mat.lower(), sub.lower(), guard.lower())
        if _key in _seen_mat:
            continue
        _seen_mat.add(_key)
        parts.append(_sentence(MATERIAL_TEMPLATE.format(
            material=mat, sub_features=sub, guard=guard)))

    # --- 5 精细元素 ---
    _seen_fine = set()
    for f in (d.get("fine_details") or []):
        s = _sentence(f)
        if not s:
            continue
        # 同样去重：重复的从句会原样输出两遍（模型常把同一条写两次）
        if s.lower() in _seen_fine:
            continue
        _seen_fine.add(s.lower())
        # 这几条是独立成句的，首字母要大写（模型常给小写从句）
        parts.append(s[0].upper() + s[1:])

    # --- 6 环境（仓库规定：没有背景就不写这一块） ---
    env_raw = d.get("environment")
    if env_raw:
        if isinstance(env_raw, dict):
            clause = _clean(env_raw.get("clause"))
            background = _bare(env_raw.get("background"))
            if clause:
                parts.append(_sentence(clause))
            elif background:
                # 四个槽位缺哪个就补个安全的默认值，绝不能让句子出现空位
                parts.append(_sentence(ENVIRONMENT_TEMPLATE.format(
                    background=background,
                    depth=_bare(env_raw.get("depth")) or "depth",
                    soft_elements=_bare(env_raw.get("soft_elements")) or "soft gradients",
                    state=_bare(env_raw.get("state")) or "soft and intact",
                )))
        else:
            s = _sentence(env_raw)
            if s:
                parts.append(s)

    # --- 7 光照与调色 ---
    lighting_raw = _bare(d.get("lighting"))
    lighting_style = _bare(d.get("lighting_style"))

    # 7B 常把「风格介词短语」写进 lighting（因为提示里说"在光照里点一次风格"）。
    # 介词开头的显然不是光照描述，就挪到 lighting_style，
    # 免得拼出 "Maintain the over the flat colour fields and the …" 这种句子。
    if lighting_raw and _PREP_RE.match(lighting_raw):
        if not lighting_style:
            lighting_style = lighting_raw
        lighting_raw = ""

    # lighting_style 跟 style_medium 说的是同一件事时，只留一处
    medium = _clean(d.get("style_medium"))
    if lighting_style and medium and lighting_style.lower() == medium.lower():
        lighting_style = ""

    lighting = lighting_raw or "existing lighting"
    if lighting_style:
        lighting = f"{lighting} {lighting_style}"
    parts.append(_sentence(LIGHTING_TEMPLATE.format(
        lighting=lighting,
        grade=_bare(d.get("grade")) or "existing",
    )))

    # --- 8 源头清理（固定块，可选加词） ---
    cleanup = CLEANUP_WITH_NOISE if d.get("luminance_noise") else CLEANUP_BASE
    if d.get("film_grain"):
        cleanup += FILM_GRAIN_CLAUSE
    parts.append(cleanup)

    # --- 9 范围锁定（固定块，必须是最后一句） ---
    parts.append(SCOPE_LOCK_MULTI if multi_reference else SCOPE_LOCK_SINGLE)

    # --- 10 风格 / 情绪标签（可选，且只输出真正填了的那个） ---
    medium = _clean(d.get("style_medium"))
    mood = _clean(d.get("style_mood"))
    if medium and mood:
        parts.append(STYLE_TAG.format(medium=medium, mood=mood))
    elif medium:
        parts.append(STYLE_ONLY.format(medium=medium))
    elif mood:
        parts.append(MOOD_ONLY.format(mood=mood))

    return " ".join(p for p in parts if p)


def _list_slot(text):
    """列表类槽位（texture_survey / texture_families）的收尾清理。

    模板是 `— {texture_survey} —`，槽位结尾多留一个分号就会拼出
    "… its own texture; — recovering …"。模型写列表时非常爱留尾分号，
    所以这里统一把结尾的 , ; ， ； 、 和悬空的 and 去掉。
    """
    t = _clean(text).strip(" ,;，；、")
    t = re.sub(r"[\s,;，；、]+$", "", t)
    t = re.sub(r"(?:[\s,]+(?:and|和))$", "", t, flags=re.I)
    return re.sub(r"[\s,;，；、]+$", "", t)


def assemble_texture(d):
    # 模型有可能从 subject_less 档改档过来（tier_override），那给的是
    # texture_families 而不是 texture_survey —— 直接拿来用，别退回通用兜底串。
    survey = _list_slot(d.get("texture_survey") or d.get("texture_families"))
    if not survey:
        survey = "the natural textures and surfaces present"
    return TEXTURE_PROMPT.format(texture_survey=survey)


def assemble_subject_less(d):
    # 同理：texture 档判出「密集重复主体 / 大面积空白」时会改档到这一档，
    # 但它手里只有 texture_survey。**绝不能退回下面那个通用兜底串** ——
    # 那等于把用户这张图的信息全丢掉，拼出一段「fabric, skin, hair, metal…」
    # 的万能话。实测过这个坑。
    fams = _list_slot(d.get("texture_families") or d.get("texture_survey"))
    if not fams:
        fams = "fabric, skin, hair, metal, reflections, and natural surfaces"
    return SUBJECTLESS_PROMPT.format(texture_families=fams)


def assemble(d, tier):
    if tier == "full_build":
        return assemble_full_build(d, d.get("multi_reference", True))
    if tier == "texture":
        return assemble_texture(d)
    return assemble_subject_less(d)


# ---------------------------------------------------------------- 自检

# md 写作规则 2 原文：Never "enhance", "improve", "make better", "fix" ——
# 这四个会把模型推离源图。旧版漏了 fix。
# ⚠ 必须按**词边界**匹配，不能用子串（旧版是 `v in low`）：
#   子串会把 prefix / suffix / fixture 里的 fix 全判成违规。
#   只收**动词形态** —— "enhancement" / "improvement" 是名词，
#   它们不像动词那样构成「去改这张图」的指令，收进来只会变成误报。
BANNED_VERB_RE = re.compile(
    r"\b(?:enhanc(?:e|es|ed|ing)|improv(?:e|es|ed|ing)|"
    r"make\s+(?:it\s+)?better|fix(?:es|ed|ing)?)\b", re.I)
NEGATION_RE = re.compile(r"\b(without|no|never|avoid)\b", re.I)

# md「Skeleton」表里标 Always 的块 —— 少一块就是提示词跑偏了。
#   #0 参考说明 / #1 主框架 / #2 主体 / #4 特殊材质（core slot）/
#   #5 精细元素 / #7 光照与调色 / #8 clean-up / #9 scope lock
# #3 头发只在 person 且看得见时写，#6 环境只在有背景时写，#10 可选。
MD_ALWAYS_BLOCKS = {
    2: "主体（person / object / scene 至少一种）",
    4: "特殊材质（md 标的 core slot）",
    5: "精细元素",
    7: "光照与调色",
}


def validate(prompt, tier, data=None):
    """把仓库 checklist 变成可自动跑的检查，返回 (通过项, 警告列表)。

    data 可选：给了 data（组装用的那份字典）才能查「md 规定 Always 的块
    到底有没有内容」—— 只看 prompt 是查不出来的，因为块缺失时 prompt 里
    什么都没留下。不给 data 就退回纯文本检查（自检里大量这么用）。
    """
    ok, warn = [], []

    if prompt.startswith(TRIGGER):
        ok.append("触发词逐字开头")
    else:
        warn.append(f"开头不是触发词 {TRIGGER!r}")

    if tier == "full_build":
        # 仓库规定：scope lock 是「最后一个固定句」，其后只允许可选的 Style/Mood 标签。
        tail = prompt.rstrip()
        marker = "change only resolution, sharpness, and fine detail."
        at = tail.rfind(marker)
        if at < 0:
            warn.append("找不到 scope lock 句")
        else:
            after = tail[at + len(marker):].strip()
            if not after:
                ok.append("scope lock 是最后一句")
            elif re.fullmatch(r"Style:.*", after, re.S):
                ok.append("scope lock 在最后，其后只有可选的 Style/Mood 标签（仓库允许）")
            else:
                warn.append(f"scope lock 之后还有别的内容：{after[:60]!r}")
        if "identical to image 1" in prompt:
            ok.append("scope lock 已锁定到 image 1（三参考形态）")
        elif "identical to the source image" in prompt:
            ok.append("scope lock 已锁定到源图（单参考形态）")
        else:
            warn.append("找不到 scope lock 句")
        if "suppress compression artifacts" in prompt:
            ok.append("clean-up 固定块存在")
        else:
            warn.append("clean-up 固定块缺失")
    else:
        if "Add no new objects, elements, or content" in prompt and tier == "subject_less":
            ok.append("防幻觉句存在（这一档的立身之本）")
        if "Preserve the original lighting, colour, contrast, and composition" in prompt:
            ok.append("scope lock 存在")

    hit = sorted({m.group(0).lower() for m in BANNED_VERB_RE.finditer(prompt)})
    if hit:
        warn.append(f"出现变换类动词（会推模型偏离源图）: {', '.join(hit)}")
    else:
        ok.append("无变换类动词")

    # 否定式：只允许出现在 clean-up 句里
    allowed_zone = re.search(
        r"Clean up degradation[^.]*\.|Add no new objects[^.]*\.", prompt)
    body = prompt
    if allowed_zone:
        body = prompt.replace(allowed_zone.group(0), "")
    if tier == "full_build" and NEGATION_RE.search(body):
        warn.append("clean-up 之外出现了否定式（without / no / avoid）")
    else:
        ok.append("否定式只出现在允许的位置")

    if tier == "subject_less":
        for bad in ("the cat", "the woman", "the man", "the car", "the sheep"):
            if bad in low:
                warn.append(f"无主体档里出现了具体主体名: {bad!r}")
                break
        else:
            ok.append("无具体主体名")

    if tier == "full_build":
        if re.search(r"On the .+?, resolve .+? as crisp, well-defined detail, .+?\.", prompt):
            ok.append("材质句符合 子特征+正向守卫 结构")

    # ---- md「Skeleton」表里标 Always 的块：逐块确认真的落进了提示词 ----
    # 之前只查了 clean-up / scope lock，于是「一条材质都没填」这种跑偏
    # 会静默通过 —— 而第 4 块是 md 明确标注的 core slot。
    #
    # ⚠ 整段都必须在 data 存在时才跑。只看 prompt 无法区分
    #   「这张图确实没有这一块」和「模型忘了填」—— 块缺失时 prompt 里
    #   什么都没留下。没有 data 还照报，会把大量只测文本的调用误伤。
    if data is not None and tier == "full_build":
        # 第 2 块三种模式各有一个稳定标记：scene 整句、object 的 "as the sharp
        # focal subject"、person 的皮肤从句（含 painterly 覆盖版，都以
        # "Render the skin with" 开头）。
        if (SUBJECT_SCENE in prompt
                or "as the sharp focal subject" in prompt
                or "Render the skin with" in prompt):
            ok.append("第 2 块 主体：存在")
        else:
            warn.append(f"第 2 块（{MD_ALWAYS_BLOCKS[2]}）缺失 —— "
                        f"md 标的是 Always")
        if re.search(r"On the .+?, resolve .+? as crisp, well-defined detail, .+?\.", prompt):
            ok.append("第 4 块 特殊材质：存在（md 的 core slot）")
        else:
            warn.append(f"第 4 块（{MD_ALWAYS_BLOCKS[4]}）一条都没有 —— "
                        f"md 标的是 Always / core slot，"
                        f"这版提示词会明显偏弱")
        if LIGHTING_TEMPLATE.split("{")[0].strip() in prompt:
            ok.append("第 7 块 光照与调色：存在")
        else:
            warn.append(f"第 7 块（{MD_ALWAYS_BLOCKS[7]}）缺失 —— md 标的是 Always")

        # 块「在」但内容是空壳，同样算跑偏：md 写作规则 4 要求光照写具体
        # （光源 / 质地 / 方向 / 色温），退化文案 "existing lighting" 不满足。
        if not str(data.get("lighting") or "").strip():
            warn.append("第 7 块的光照没写具体（光源/质地/方向/色温）——"
                        "md 写作规则 4 要求；现在退化成 'existing lighting'")
        else:
            ok.append("光照描述具体（md 写作规则 4）")
        if not (data.get("fine_details") or []):
            warn.append(f"第 5 块（{MD_ALWAYS_BLOCKS[5]}）为空 —— "
                        f"md 标的是 Always（高光点 / 粒子 / 珠绣 / 飞发这类"
                        f"小元素），没有就明确说明这张图确实没有")
        else:
            ok.append("第 5 块 精细元素：存在")
        # md：画家 / 插画源图必须带 Style 标签（第 10 块）
        if data.get("painterly") and not str(data.get("style_medium") or "").strip():
            warn.append("源图是绘画 / 插画，但没给 style_medium —— "
                        "md「Painterly / illustration variant」要求这一档"
                        "始终使用 Style 标签")
        elif data.get("painterly"):
            ok.append("绘画源图带了 Style 标签（md painterly 变体）")

        # ---- 静默填充：environment 缺槽位时组装器会补通用词 ----
        # 拼出来是「Keep the … within its existing depth, holding the soft
        # gradients as soft and intact.」—— 明显偏弱，但旧版**不提醒**，
        # 用户分不清「模型真这么写的」和「工具补出来的」。
        # 而 md 写作规则 6 正是要求删掉这种没点名具体东西的填充。
        env = data.get("environment")
        if (isinstance(env, dict) and not _clean(env.get("clause"))
                and _bare(env.get("background"))):
            missing = [k for k in ("depth", "soft_elements", "state")
                       if not _bare(env.get(k))]
            if missing:
                warn.append(
                    "第 6 块 environment 缺了 " + " / ".join(missing)
                    + " —— 组装器已用通用词顶上（depth / soft gradients / "
                      "soft and intact）。这句会明显偏弱，md 写作规则 6 "
                      "要求只留点了具体东西的措辞")
            else:
                ok.append("environment 四个槽位都填了具体内容")
            # _copula_flip 只翻「短形容词短语」那一种，翻不动的（尾部含
            # 介词 / 分词的）会原样留下 —— 那就别硬翻，报出来让人改。
            clauseish = [k for k in ("background", "depth", "soft_elements", "state")
                         if re.search(r"\b(?:is|are|was|were|has|have)\b",
                                      _bare(env.get(k)), re.I)]
            if clauseish:
                warn.append("第 6 块的 " + " / ".join(clauseish)
                            + " 看起来是整句（含 is/are），拼进去会不自然 —— "
                              "改成名词短语更稳")

        # ---- 重复内容：组装器已去重，这里告诉用户去掉了什么 ----
        # 静默去重同样是「看不出来发生了什么」，所以去重必须报出来。
        mats = [m for m in (data.get("materials") or []) if isinstance(m, dict)]
        keys = [(_bare(m.get("material")).lower(),
                 _clean(m.get("sub_features")).lower(),
                 _clean(m.get("guard")).lower()) for m in mats]
        keys = [k for k in keys if all(k)]
        n_dup = len(keys) - len(set(keys))
        if n_dup:
            warn.append(f"有 {n_dup} 条材质完全重复 —— 已自动去重"
                        f"（重复会把同一个概念重复加权）")
        else:
            ok.append("材质无完全重复")
        uniq = list(dict.fromkeys(keys))
        names = [k[0] for k in uniq]
        rep = sorted({n for n in names if names.count(n) > 1})
        if rep:
            warn.append("同一材质写了多条（子特征不同，都保留了）："
                        + " / ".join(rep)
                        + " —— 合成一条更紧凑，但会丢掉其中一条的子特征")
        fines = [s for s in (_sentence(f).lower()
                             for f in (data.get("fine_details") or [])) if s]
        n_fdup = len(fines) - len(set(fines))
        if n_fdup:
            warn.append(f"有 {n_fdup} 条精细元素重复 —— 已自动去重")

    return ok, warn


if __name__ == "__main__":
    demo = {
        "scene": "this portrait",
        "mode": ["person"],
        "skin_variant": "young_adult",
        "eyes": True,
        "hair_detail": "the silver-grey and dark hairs",
        "materials": [
            {"material": "polished steel armour",
             "sub_features": "the specular highlights, mirror reflections, surface scratches, rivets, hinges, and plate seams",
             "guard": "reflections accurate and true to the source"},
            {"material": "feathered wings",
             "sub_features": "individual barbs and downy texture",
             "guard": "each barb reads as a separate, cleanly defined strand"},
        ],
        "fine_details": ["Hold the glowing halo as a smooth, even ring of light"],
        "environment": "Keep the background produce and shelving naturally detailed within the existing shallow depth of field, holding the soft bokeh as smooth, evenly filled discs",
        "lighting": "warm, soft directional interior light",
        "grade": "existing",
        "multi_reference": True,
    }
    p = assemble(demo, "full_build")
    print(p)
    print()
    good, bad = validate(p, "full_build")
    print("通过:", good)
    print("警告:", bad)
    print()
    print("--- texture 档 ---")
    p2 = assemble({"texture_survey": "natural matte skin with pore-level texture; fine individual blonde hair strands with flyaways; polished reflective steel armour with its specular highlights and rivets"}, "texture")
    print(p2[:300] + "...")
    print()
    print("--- subject_less 档 ---")
    p3 = assemble({"texture_families": "fur and wool, fleece, animal hide, fine hair, and skin"}, "subject_less")
    print(p3[:300] + "...")
