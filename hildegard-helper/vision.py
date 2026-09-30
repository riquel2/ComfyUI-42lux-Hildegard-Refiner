# -*- coding: utf-8 -*-
"""视觉模型适配层：本地 LM Studio / 云端 API 可切换。

对上层只暴露一个 analyze()，返回填好的变量槽位字典（JSON），
再由 templates.py 组装成最终提示词。

支持的协议：
  openai    —— OpenAI 兼容 /chat/completions（LM Studio、DeepSeek、通义、OpenAI…）
  anthropic —— Anthropic 原生 /v1/messages
"""

import base64
import io
import json
import re
import urllib.error
import urllib.request

try:
    from PIL import Image
    HAS_PIL = True
except Exception:  # pragma: no cover
    HAS_PIL = False

from templates import (LIST_TIER_RULES, TEXTURE_DESCRIPTORS, WRITING_RULES,
                       survey_is_copied)

MAX_EDGE = 1280          # 送进模型的图片长边上限（省时间，足够看清材质）
JPEG_QUALITY = 88


# ---------------------------------------------------------------- 图片编码

def encode_image(path, max_edge=MAX_EDGE):
    """读图 → 等比缩到长边上限 → JPEG → base64。返回 (b64, mime, (w, h))。"""
    if not HAS_PIL:
        with open(path, "rb") as f:
            raw = f.read()
        return base64.b64encode(raw).decode(), "image/jpeg", None

    with Image.open(path) as im:
        im = im.convert("RGB")
        w, h = im.size
        scale = min(1.0, max_edge / max(w, h))
        if scale < 1.0:
            im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                           Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=JPEG_QUALITY)
        return base64.b64encode(buf.getvalue()).decode(), "image/jpeg", (w, h)


# ---------------------------------------------------------------- 指令构造

# 注意：这是压缩版。完整 35 条词库留在 templates.py 里备查。
# 本地模型上下文常常只有 4096 token，塞整表会把回答挤没（输出半截 JSON），
# 所以这里只留最常见的 12 条（含子特征/守卫），其余让模型照结构自己写。
BANK_TABLE = """- feathers ｜ sub = barbs, downy texture, flyaway strands ｜ guard = each barb a separate, cleanly defined strand
- sheer fabric ｜ sub = layered weave, panel translucency, beadwork ｜ guard = each layer crisp and individually defined
- lace / openwork ｜ sub = floral pattern, mesh ground, scalloped edges ｜ guard = pattern faithful to the source
- patterned silk / printed fabric ｜ sub = woven sheen, folds, the print ｜ guard = pattern faithful to the source, weave intact
- velvet / matte fabric ｜ sub = directional nap, soft surface ｜ guard = soft matte nap, no false sheen
- short animal fur ｜ sub = individual hairs, directional flow, clumping ｜ guard = fine, distinct hairs with natural flow
- polished metal ｜ sub = specular highlights, reflections, seams ｜ guard = reflections accurate and true to the source
- translucent glass ｜ sub = refraction, caustics, surface highlights ｜ guard = clean, clear, and fully transparent
- carved stone ｜ sub = relief, scrollwork, patina, lichen ｜ guard = sharp, deep-cut detail
- foliage / bark ｜ sub = leaves, bark, growth rings, twigs ｜ guard = each surface its own distinct texture
- glossy paint ｜ sub = specular highlights, panel reflections ｜ guard = accurate finish true to the source
- lips / skin detail ｜ sub = natural surface texture, soft sheen ｜ guard = natural texture with controlled, realistic sheen

**别的材质自己写**，照这个结构（仓库原文句式，别改）：
> On the {{material}}, resolve {{sub-features}} as crisp, well-defined detail, {{guard}}。"""

# texture 档的措辞词典。**由 templates.TEXTURE_DESCRIPTORS 生成**，不手写。
#
# 理由：这份表有两个用途 —— ① 给模型看的查词表；② survey_is_copied() 判
# 「照抄」时的比对库。手写两份必然会漂移（之前就漂过一次），漂了以后检测
# 就认不出提示词里给出去的措辞，等于失效。现在同源，不可能对不上。
#
# 写法刻意做成「左边是观察、右边是措辞」的查词表，且表头明确写「这是词典，
# 不是答案」。老版本是「皮肤：…／毛发：…」这种紧凑罗列，标题还写着
# 「优先取这里的措辞」——结果本地 7B 直接把它当成答案整段抄回来：
# 图是手绘壁纸，输出里却出现皮肤、头发、嘴唇。
TEX_TABLE = (
    # ⚠ 这里刻意只留一句短说明：上面的小标题已经写着「查词用，不是答案」，
    #   旧版这里又重复了一遍（53 字符）。2026-09-27 按 md 把描述符复原成
    #   逐字之后，texture 系统提示涨到 4650，超过 4600 预算 —— 所以从这段
    #   **工具自己写的**文字里让出空间，而不是再去截 md 的措辞。
    "（左=观察，右=措辞；照抄整张表 = 失败）\n"
    + "\n".join(f"- {zh} → {en}" for zh, en in TEXTURE_DESCRIPTORS))

def _numbered(rules):
    return "\n".join(f"{i+1}. {r}" for i, r in enumerate(rules))


# full_build 用全部 8 条（顺序就是 templates.WRITING_RULES 的原顺序）
RULES = _numbered(WRITING_RULES)
# texture / subject_less 只输出一个「分号列表」槽位，没有环境块 / 光照块 /
# clean-up 块，只带通用那三条。把分块规则塞给它们不但白占上下文，
# 还会诱导模型去写光照 / 环境从句。
RULES_LIST = _numbered(LIST_TIER_RULES)

_TAIL_TEMPLATE = """
## 硬性写作规则
{rules}

## 输出格式
只输出一个 JSON 对象，不要任何解释、不要 markdown 代码围栏。
"""

COMMON_TAIL = _TAIL_TEMPLATE.format(rules=RULES)
COMMON_TAIL_LIST = _TAIL_TEMPLATE.format(rules=RULES_LIST)

SYSTEM_FULL = """你是图像放大提示词的填槽助手。为 Flux2 Klein 的 RFNTILE 分块精修流程填写「变量槽位」。

调用方已写好固定块（触发词、参考说明、clean-up、scope lock）。你**只看图填槽位**，绝不输出完整提示词。
流程会给模型三张图：图1 = 要精修的 tile，图2 = 该 tile 在全图的位置图，图3 = 全图。当前给你的是**全图原图**。

## mode（可多选）
- "person" 人或人脸 / "object" 产品·车辆·动物·核心物件 / "scene" 宽幅密集场景、无单一主体
可组合，如「人像 + 手持产品」= ["person","object"]。

## skin_variant（仅 mode 含 person）
按**图里实际看到的**选，不要套默认：young_adult / older / child /
glossy（微距·汗·油·强光，皮肤本身就有光泽）/ creature（非人生物）。

## painterly（是不是「画」出来的，不是照片）
手绘壁纸、壁画、油画、水彩、国画、概念图、动漫插画、印刷画册页，以及任何能看到笔触、
平涂色块、勾勒线条的画面，都是 true。只有真实摄影（含产品棚拍、人像、风景实拍）是 false。
拿不准就看有没有笔触和均匀平涂的底色——有就是 true。
painterly = true 时：style_medium 填画种（如 hand-painted chinoiserie wallpaper）；
lighting_style 单独填一个风格介词短语（如 over the flat hand-painted colour fields），
**不要把它写进 lighting**；style_mood 只在情绪明确时填（serene / cold cinematic），否则留空。

## materials 材质（每行：材质 ｜ sub = 子特征 ｜ guard = 正向守卫）
{bank}

⚠ 三条硬规矩：
1. **只列图里真实存在的材质**。没有金属就别写 metal，没有塑料就别写 plastic。
   写进提示词的材质会被模型真的「造」出来——宁可少列两三条，也不要凑数。
2. **前景的材质排在前面**，最多 6 条。
3. 每条必须凑齐「材质 + 子特征 + 正向守卫」三样，缺一条就别写这条。

## environment 只填槽位，不要自己写整句
调用方用固定句式拼：Keep the {{background}} naturally detailed within its existing {{depth}},
holding the {{soft_elements}} as {{state}}。四个字段都填**名词短语**，
不要写成整句（别出现 is / are / there is）。
**background 是必填的**——它为空时整块会被丢掉。画面里确实没有背景才填 null。

## 输出 JSON（只输出 JSON，不要解释、不要代码围栏）
{{
  "scene": "1-3 词英文名词短语，如 this portrait / this wallpaper",
  "mode": ["person"],
  "skin_variant": "young_adult",
  "creature": null,
  "eyes": true,
  "hair_visible": true,
  "hair_detail": "如 the silver-grey and dark hairs；没特殊细节就填空字符串",
  "object": "仅 object 模式：核心物件英文名",
  "object_surfaces": "仅 object 模式：主要表面，逗号分隔",
  "materials": [{{"material": "...", "sub_features": "...", "guard": "..."}}],
  "fine_details": ["短正向从句（不是命令句），如 each one separate with its own defined edge；别和材质句重复"],
  "environment": {{"background": "...", "depth": "...", "soft_elements": "...", "state": "..."}},
  "lighting": "光源 + 质地 + 方向 + 色温（名词短语，如 soft overhead daylight）",
  "lighting_style": "painterly 时的风格介词短语，如 over the flat hand-painted colour fields；否则空",
  "grade": "简短调色，如 muted desaturated / warm sunlit",
  "painterly": false,
  "luminance_noise": true,
  "film_grain": false,
  "style_medium": "painterly 时填画种，否则空",
  "style_mood": "情绪词，否则空",
  "high_freq_reduce_hint": 0.0,
  "tier_override": null,
  "notes": "中文，一两句说明你的判断依据"
}}

## tier_override
默认档位由块数算出。**只有**看到「密集重复主体（人群、羊群、贴纸阵）」或「大面积空白 / 负空间」
时才填 "subject_less"（点名主体会被幻觉进空白块里）。否则填 null。

{lens}
{tail}"""

SYSTEM_TEXTURE = """你是图像放大提示词的填槽助手，为 Flux2 Klein 的 RFNTILE 分块精修流程填「材质普查」槽位。
这一档用于**块数多**的流程：每块 tile 只是一两块表面的裁切。所以**不点名主体**（不写孔雀、不写花瓶），也不写守卫从句，只列材质表面。

## 分两步，不许跳步

**第一步 · 观察（surfaces_seen）**：用中文列出**你在这张图里真正看到的**表面，按画面
占比从大到小，3-7 条。不写「这类图通常会有」的。
⚠ 手绘 / 壁纸 / 绘画 / 印刷 / 风景画面里**没有皮肤、没有头发、没有嘴唇、没有天鹅绒**
——除非真的看到了人。

**第二步 · 措辞（texture_survey）**：把 surfaces_seen **逐条**译成英文材质短语，分号
分隔。**只译第一步列出来的：不许增补，也不许漏。**

## 材质措辞词典
{tex}

## ⚠ 整幅是画 / 印刷品时（最伤保真度的一条）
画面本身就是手绘 / 壁画 / 印刷 / 版画——花、鸟、树、人物**都是画出来的**——这时普查
以 **`flat painted pigment fields with their brushwork`** 为主体，画出来的东西一律写
**`painted …`**（`painted foliage, flowers, and plumage`），**不要**写 `bark` /
`barbs` / `downy` / `veining` / `wood-grain` / `pore-level`：这些词是「请给我写实
微观结构」的请求，模型会照着在平涂画面上叠一层写实材质，出图明显不像原图。
**保真优先于描述详尽**；画面是照片时才用写实措辞。

另外三处易错：**「树」是活着的植物**，用 `leaves, bark, and twigs, each its own
texture`，`wood-grain texture` 是**木料**（家具 / 地板 / 画框）；**`flat painted
pigment fields…` 只代表「底色 / 墙面」那一层**，不是「整幅是画所以一条就够」，画里的
花鸟树石要单独再列；别因为某个表面「看起来像织物」就写 fabric。

## 反例 / 正例
✗ 手绘壁纸（没有皮肤 / 头发 / 嘴唇）却写成——
   "natural matte skin with pore-level texture; fine individual hair strands with
    flyaways; the soft sheen of the lips"
   ← 把词典当答案抄，模型会把这些真的画到墙上，等于凭空造内容。

✗ 有树、有花、有鸟，却写成——
   "flat painted pigment fields with their brushwork; wood-grain texture;
    leaves, bark, and twigs, each its own texture;
    soft petals with their delicate surface and fine veining"
   ← 三错叠加：拿底色概括了整幅画（花鸟树石全漏）、活树写成了木料、还给画出来的
     东西安上 bark / veining 这类写实微观结构。实测这一版最不像原图。

✓ 孔雀 + 树 + 花 + 平涂底色，画面本身是手绘——
   "flat painted pigment fields with their brushwork; painted foliage, flowers,
    and plumage"

## 槽位要求
{rules}

## tier_override（这一档唯一的「改档」出口）
默认档位由块数算出。但仓库还有一条**看图才能判断**的规则：画面如果是
**「密集重复主体」（人群、羊群、贴纸阵）**或**「大面积空白 / 负空间」**，
就降到 "subject_less"——这两种画面里点名主体，会被幻觉进根本不含它的块里。
符合就填 "subject_less"，否则填 null。

## 输出 JSON
{{
  "surfaces_seen": ["中文，按占比排序，3-7 条"],
  "texture_survey": "分号分隔的英文材质短语，逐条对应 surfaces_seen",
  "high_freq_reduce_hint": 0.0,
  "tier_override": null,
  "notes": "中文，说明你为什么这么填"
}}

{lens}
{tail}"""

SYSTEM_SUBJECTLESS = """你是一个图像放大提示词的填写助手。你要为 Flux2 Klein 的 RFNTILE 分块精修流程，填一个「无主体材质族」槽位。

这一档用于：块数很多、大量块是「没有上下文的裁切」，或者画面有密集重复主体、大面积空白。**点名任何具体主体都会被模型幻觉进不含它的块里**，所以这里只写材质**类别**。

## 槽位要求
1. 绝不出现主体、物件、具体实例。不能写 "the car"、"the sheep face"、"the dress"。
2. 用类别名词，不要「形容词+名词」。写 "fabric weave" 不写 "red canvas fabric"；写 "metal and reflections" 不写 "polished steel helmet"。
3. 覆盖图里可能出现的材质族，3-7 个即可，不要求穷举。宁短勿长。

参考范例：
- 人像：fabric weave, skin pores, hair, reflections, and fine grain
- 动物：fur and wool, fleece, animal hide, fine hair, and skin
- 车辆：glossy painted surfaces, glass, metal, reflections, textured ground, foliage, and fine grain
- 风景：foliage, bark, stone, grass and ground cover, water, and atmospheric depth
- 混合：fabric, skin, hair, metal, reflections, and natural surfaces

## 输出 JSON
{{
  "texture_families": "3-7 个材质类别，逗号分隔，末尾用 and 连接",
  "high_freq_reduce_hint": 0.0,
  "notes": "中文，说明你为什么这么填"
}}

{lens}
{tail}"""

TEXTURE_SURVEY_RULES = """1. **3-7 条，宁短勿长。** 只写这张图里真的有的那几条。
2. **只写眼睛看到的。** 图里没有皮肤 / 头发 / 嘴唇 / 天鹅绒就一条都别提。
3. **占画面大宗的表面一条都不能漏**，漏了那几块 tile 就完全没有指引。但措辞要跟画面本身一致：整幅是手绘 / 印刷时，画出来的花鸟树石写 `painted foliage, flowers, and plumage`，别安 `bark` / `veining` 这类写实微观结构。
4. 普查整幅画面，不只主体。背景、地面、墙面、天空、环境物件都要按占比列出来。
5. 两套精细纹理相接时（羽毛挨着平涂颜料、毛皮挨着织物）要分开写，让模型守住边界。
6. 用材质族，不要笼统的 "texture"。亮光漆、哑光绒、粗麻、抛光金属、粗石表现各异，要分开写。
7. 这是「名词 + 短修饰」的列表，分号分隔。**绝不写守卫从句**——出现 "keeping it faithful and…" 就跑到 full_build 档去了。"""

LENS_HINT = """## high_freq_reduce_hint（0.0 - 1.0）
**默认填 0.0**（保真）。只有当源图**确实**过锐、有可见噪点、有压缩伪影，
或明显是 AI 生成图（Nano Banana、GPT-image 这类带假高频颗粒的）时，才调到 0.3-0.6，
让模型重造干净细节。
注意：干净的摄影照片、以及**手绘 / 绘画类**源图都应填 0.0——
绘画的笔触和颗粒是真内容，降高频会把画味洗掉。"""


def build_system_prompt(tier):
    if tier == "full_build":
        body = SYSTEM_FULL.format(
            bank=BANK_TABLE, lens=LENS_HINT, tail=COMMON_TAIL)
    elif tier == "texture":
        body = SYSTEM_TEXTURE.format(
            rules=TEXTURE_SURVEY_RULES, tex=TEX_TABLE,
            lens=LENS_HINT, tail=COMMON_TAIL_LIST)
    else:
        body = SYSTEM_SUBJECTLESS.format(
            lens=LENS_HINT, tail=COMMON_TAIL_LIST)
    return body


USER_TEXT = (
    "请分析这张图片，按系统提示的 JSON 格式填写槽位。"
    "当前流程信息：源图 {src}，放大后 {out}，切块 {gx}x{gy} = {n} 块，"
    "每块 {tw}x{th}，overlap {ox}x{oy}px，实际放大 {scale}x。"
    "提示词档位：{tier}。"
)


# ---------------------------------------------------------------- JSON 解析

def _looks_truncated(t):
    """粗略判断 JSON 是不是被「截断」了（而不是单纯写错）。

    本地模型上下文只有 4096 token 时，prompt 吃太多就会输出半截 JSON，
    报错信息得说清楚是「被截断」，不然用户只看到一句 JSONDecodeError。
    """
    if t.count("{") > t.count("}"):
        return True
    if t.count("[") > t.count("]"):
        return True
    if t.count('"') % 2:                      # 引号没配对，多半断在字符串中间
        return True
    return False


def parse_json(text):
    """从模型输出里抠出 JSON。7B 本地模型经常加前后废话或围栏，要能兜住。"""
    if not text:
        raise ValueError("模型返回空内容")
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except Exception:
        pass
    start = t.find("{")
    end = t.rfind("}")
    if start >= 0 and end > start:
        chunk = t[start:end + 1]
        try:
            return json.loads(chunk)
        except Exception:
            fixed = re.sub(r",\s*([}\]])", r"\1", chunk)   # 去尾逗号
            try:
                return json.loads(fixed)
            except Exception as e:
                if _looks_truncated(t):
                    raise ValueError(
                        "模型输出被截断了（JSON 没写完），拿不到完整槽位。\n"
                        "最常见的原因是本地模型上下文太小：LM Studio 默认可能只给 4096，"
                        "而图片+指令就要占掉 3700 多。\n"
                        "解决：LM Studio 里把模型的 Context Length 调到 8192 以上再重试。\n"
                        f"（原始输出尾部：…{t[-60:]!r}）") from e
                raise ValueError(f"模型输出的 JSON 解析失败：{e}\n"
                                 f"（原始输出尾部：…{t[-120:]!r}）") from e
    if _looks_truncated(t):
        raise ValueError(
            "模型输出被截断了，找不到完整 JSON。\n"
            "多半是本地模型上下文太小（LM Studio 的 Context Length 调到 8192 以上再试）。\n"
            f"（原始输出尾部：…{t[-60:]!r}）")
    raise ValueError("模型输出里找不到 JSON")


# ---------------------------------------------------------------- 后端

class BackendError(RuntimeError):
    pass


def _post_json(url, payload, headers, timeout):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:600]
        raise BackendError(f"HTTP {e.code}：{detail}") from None
    except urllib.error.URLError as e:
        raise BackendError(
            f"连不上 {url}（{e.reason}）。本地后端请确认 LM Studio 已启动并打开 "
            f"Developer → Local Server；云端后端请检查网络和 base_url。") from None
    except Exception as e:
        raise BackendError(f"{type(e).__name__}: {e}") from None


class OpenAICompatBackend:
    """LM Studio / DeepSeek / 通义 / OpenAI 等一切 OpenAI 兼容端点。"""

    protocol = "openai"

    def __init__(self, base_url, api_key="", model="", timeout=300):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or "lm-studio"
        self.model = model
        self.timeout = timeout

    def list_models(self):
        url = f"{self.base_url}/models"
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {self.api_key}")
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                d = json.loads(r.read().decode("utf-8"))
            return [m.get("id") for m in d.get("data", []) if m.get("id")]
        except Exception as e:
            raise BackendError(f"取模型列表失败：{e}") from None

    def probe_context(self, model=None):
        """查 LM Studio 实际加载的上下文长度（其它后端返回 None）。

        LM Studio 的模型卡片上写着 128k，但实际加载常常只有 4096。
        上下文太小 → 回答被截断 → JSON 半截。所以这里主动探一下。

        注意：同一个模型可以同时加载多个实例（id 会带 `:2` 后缀），
        旧实例可能是 4096、新实例是 8192。所以**先按模型名匹配**，
        而不是把所有已加载实例取 min —— 那样会被一个忘了卸掉的旧实例误报。
        """
        root = self.base_url
        if root.endswith("/v1"):
            root = root[:-3]
        try:
            with urllib.request.urlopen(root + "/api/v0/models", timeout=8) as r:
                d = json.loads(r.read().decode("utf-8"))
        except Exception:
            return None
        entries = [m for m in d.get("data", []) if m.get("state") == "loaded"]
        if not entries:
            return None

        want = (model or self.model or "").split(":")[0].strip().lower()
        if want:
            for m in entries:
                if str(m.get("id", "")).split(":")[0].strip().lower() == want:
                    v = m.get("loaded_context_length")
                    if isinstance(v, int):
                        return v

        # 不知道会用哪个（模型留空）→ 取最大的。
        # 因为 _pick_model 也正是挑上下文最大的那个，口径一致。
        sizes = [m.get("loaded_context_length") for m in entries
                 if isinstance(m.get("loaded_context_length"), int)]
        return max(sizes) if sizes else None

    def _pick_model(self):
        """没指定模型时自动挑一个：优先「已加载的、上下文最大的」。

        以前是直接取 models[0]，在用户同时留着一个 4096 的旧实例时，
        会把请求发给 4096 那个，于是回答被截断、JSON 半截。
        """
        models = self.list_models()
        if not models:
            raise BackendError("后端没有可用模型")
        root = self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url
        try:
            with urllib.request.urlopen(root + "/api/v0/models", timeout=8) as r:
                d = json.loads(r.read().decode("utf-8"))
            loaded = [m for m in d.get("data", []) if m.get("state") == "loaded"]
            if loaded:
                loaded.sort(key=lambda m: m.get("loaded_context_length") or 0,
                            reverse=True)
                best = loaded[0].get("id")
                if best:
                    return best
        except Exception:
            pass
        return models[0]

    def analyze(self, image_path, tier, ctx_text, system_prompt):
        b64, mime, _ = encode_image(image_path)
        model = self.model
        if not model:
            model = self._pick_model()
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": [
                    {"type": "text", "text": ctx_text},
                    {"type": "image_url", "image_url": {
                        "url": f"data:{mime};base64,{b64}"}},
                ]},
            ],
            "temperature": 0.2,
            "max_tokens": 1200,
            "stream": False,
        }
        d = _post_json(f"{self.base_url}/chat/completions", payload,
                       {"Authorization": f"Bearer {self.api_key}"}, self.timeout)
        try:
            content = d["choices"][0]["message"]["content"]
        except Exception:
            raise BackendError(f"返回结构异常：{json.dumps(d)[:400]}") from None
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content
                              if isinstance(p, dict))
        return parse_json(content)


class AnthropicBackend:
    """Anthropic 原生 Messages API。"""

    protocol = "anthropic"

    def __init__(self, base_url="https://api.anthropic.com", api_key="",
                 model="claude-sonnet-4-5", timeout=300):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def list_models(self):
        return [self.model]

    def analyze(self, image_path, tier, ctx_text, system_prompt):
        if not self.api_key:
            raise BackendError("云端后端缺少 API key")
        b64, mime, _ = encode_image(image_path)
        payload = {
            "model": self.model,
            "max_tokens": 2000,
            "temperature": 0.2,
            "system": system_prompt,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": mime, "data": b64}},
                {"type": "text", "text": ctx_text},
            ]}],
        }
        d = _post_json(f"{self.base_url}/v1/messages", payload, {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }, self.timeout)
        try:
            blocks = d["content"]
            content = "".join(b.get("text", "") for b in blocks
                              if b.get("type") == "text")
        except Exception:
            raise BackendError(f"返回结构异常：{json.dumps(d)[:400]}") from None
        return parse_json(content)


def make_backend(cfg):
    """按配置字典造后端。cfg 结构见 config.json。"""
    proto = (cfg.get("protocol") or "openai").lower()
    if proto == "anthropic":
        return AnthropicBackend(
            base_url=cfg.get("base_url") or "https://api.anthropic.com",
            api_key=cfg.get("api_key", ""),
            model=cfg.get("model", ""),
            timeout=int(cfg.get("timeout", 300)),
        )
    return OpenAICompatBackend(
        base_url=cfg.get("base_url") or "http://127.0.0.1:1234/v1",
        api_key=cfg.get("api_key", ""),
        model=cfg.get("model", ""),
        timeout=int(cfg.get("timeout", 300)),
    )


def _texture_problem(data):
    """texture 档输出的体检。返回 (类型, 给模型的重问说明)；没问题返回 (None, "")。

    两类问题，处置不同：
      "copy" —— 把措辞词典当答案抄了回来。凭空造材质，会被模型真的画上去，
                属于内容污染，必须硬拦。
      "thin" —— 只扫了一两条就交卷，漏了占画面大宗的材质。属于质量差，
                重问一次就行，不该拦住用户干活。
    """
    survey = str(data.get("texture_survey") or "")
    seen = data.get("surfaces_seen")
    seen = seen if isinstance(seen, list) else []

    if survey_is_copied(survey):
        return "copy", (
            "上一次你输出的 texture_survey 是把「材质措辞词典」整段抄了回来，"
            "里面出现了这张图根本没有的材质。\n"
            f"你上次写的是：{survey[:220]}\n"
            "现在重新看图，只回答一个问题：**这张图里真的有**哪些表面？"
            "先填 surfaces_seen（中文，按占比排序），再只把这几条译成英文写进 "
            "texture_survey。图里没有的，一条都不要写。")

    if len(seen) < 3:
        # 关键证据：它自己的 notes 往往已经说出了没列进 surfaces_seen 的东西
        # （实测过：notes 写「有树叶、树枝和树干」，surfaces_seen 却只有两条）。
        # 把这句话原样贴回去，比空讲道理有效得多。
        #
        # ⚠ 措辞刻意收着：只要求补「占面积最大的」那几条，并明说别凑数。
        #   每多写一条材质，就多给模型一块可以「发明」的地方 —— 保真优先。
        return "thin", (
            f"上一次你只列出了 {len(seen)} 条 surfaces_seen，但规则要求 3-7 条 —— "
            "说明你扫得不够全。\n"
            f"你自己的 notes 里写着：{str(data.get('notes') or '')[:160]}\n"
            "把**占面积最大的**那几个表面补上，补到 3-5 条就够。"
            "**别为了凑数把细小的东西也写进去** —— 多写一条材质，模型就多一处"
            "可以「造」的地方。主体之外，只看背景、地面、枝叶、花瓣、果实里"
            "哪几个面积大。\n"
            "注意 `flat painted pigment fields` 只代表**底色**那一层，"
            "不是「整幅是画所以一条就够」。\n"
            "然后逐条译成英文写进 texture_survey。")

    return None, ""


def analyze_image(backend, image_path, tier, info):
    """一站式：造指令 → 调模型 → 返回槽位字典。

    texture 档多一道保险：本地 7B 有两个顽固毛病 ——
      ① 把「材质措辞词典」当答案整段抄回来（图是手绘壁纸，却输出皮肤/头发/嘴唇）；
      ② 只扫一两条就交卷，把占画面大宗的植物 / 花漏掉。
    两种都带着它自己的错误重问一次。照抄的再犯就报错（绝不交付凭空造的材质）；
    只是扫不全的就交付 + 带回提醒（不为了「不够全」拦住用户干活）。
    """
    ctx = USER_TEXT.format(
        src=f"{info['src_width']}x{info['src_height']}",
        out=f"{info['upscaled_width']}x{info['upscaled_height']}",
        gx=info["grid_x"], gy=info["grid_y"], n=info["num_tiles"],
        tw=info["tile_width"], th=info["tile_height"],
        ox=info["overlap_x"], oy=info["overlap_y"],
        scale=info["effective_scale_x"], tier=tier,
    )
    sys_prompt = build_system_prompt(tier)
    data = backend.analyze(image_path, tier, ctx, sys_prompt)

    if tier != "texture":
        return data

    kind, why = _texture_problem(data)
    if kind is None:
        return data

    # 带着它自己的错误重问一次（图片重发，逼它真的再看一遍）
    retry = backend.analyze(image_path, tier, f"{ctx}\n\n⚠ {why}", sys_prompt)
    if retry.get("texture_survey"):
        data = retry

    kind2, _ = _texture_problem(data)
    if kind2 == "copy":
        # 照抄 = 凭空造材质，会被模型真的画上去。绝不交付。
        raise BackendError(
            "模型连续两次都把「材质措辞词典」当成答案抄了回来，没有真的看图。\n"
            "这一档需要模型能读懂画面内容，7B 在块数多的时候容易偷懒。建议：\n"
            "  · 换一个更强的视觉模型；或\n"
            "  · 在界面上改用 full_build 档，用「材质 + 子特征 + 守卫」逐条点名。\n"
            f"（模型最后一次输出：{str(data.get('texture_survey'))[:180]!r}）")
    if kind2 == "thin":
        # 只是扫得不全 —— 至少是照着图写的，交付，但把提醒带出去让界面显示。
        # （不在这里抛错：为了「不够全」而拦住用户干活，代价比收益大。）
        data.setdefault("_warnings", []).append(
            "模型两次都只扫到很少的材质表面（surfaces_seen 不足 3 条），"
            "提示词中间那段可能漏了画面里的部分材质，建议对照原图核对一下。")
    return data


if __name__ == "__main__":
    import sys
    for t in ("full_build", "texture", "subject_less"):
        s = build_system_prompt(t)
        print(f"[{t}] system prompt {len(s)} 字符")
    print()
    print("JSON 容错测试：")
    for sample in [
        '{"a":1}',
        '```json\n{"a":1}\n```',
        '好的，这是结果：\n{"a": 1, "b": [2,3],}\n希望有帮助！',
    ]:
        print("  ", parse_json(sample))
