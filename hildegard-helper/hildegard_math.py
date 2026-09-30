"""Hildegard Plan 切块算法移植版（纯数学，无依赖）。

逻辑逐行对应 42lux/ComfyUI-42lux-Hildegard-Refiner 的
_hildegard_compute_upscale()，用来在本地离线预演"这张图进去会变成什么"。
"""

import math

OVERLAP_DICT = {
    "None": 0,
    "1/64 Tile": 0.015625,
    "1/32 Tile": 0.03125,
    "1/16 Tile": 0.0625,
    "1/8 Tile": 0.125,
    "1/4 Tile": 0.25,
    "1/2 Tile": 0.5,
}

LATENT_ALIGN = 16
MIN_SCALE_FACTOR_THRESHOLD = 1.0


def snap(value, multiple=LATENT_ALIGN):
    return max(multiple, (int(value) // multiple) * multiple)


def calc_overlap(tile_size, overlap_fraction):
    return int(overlap_fraction * tile_size)


def plan(width, height, tile_width=2048, tile_height=2048,
         overlap=0.25, min_scale_factor=1.3):
    """返回放大后的尺寸、网格、以及每块的实际坐标。

    与 ComfyUI 节点完全一致的输入输出；额外返回 tiles 列表便于校验。
    """
    tile_width = snap(tile_width)
    tile_height = snap(tile_height)
    overlap_x = calc_overlap(tile_width, overlap)
    overlap_y = calc_overlap(tile_height, overlap)
    min_scale_factor = max(min_scale_factor, MIN_SCALE_FACTOR_THRESHOLD)

    if width <= height:
        multiply_factor = math.ceil(min_scale_factor * width / tile_width)
        while True:
            upscaled_width = tile_width * multiply_factor
            grid_x = math.ceil(upscaled_width / tile_width)
            upscaled_width = tile_width * grid_x - overlap_x * (grid_x - 1)
            upscale_ratio = upscaled_width / width
            if upscale_ratio >= min_scale_factor:
                break
            multiply_factor += 1
        upscaled_height = int(height * upscale_ratio)
        grid_y = math.ceil((upscaled_height - overlap_y) / (tile_height - overlap_y))
        if grid_y > 1:
            overlap_y = round((tile_height * grid_y - upscaled_height) / (grid_y - 1))
    else:
        multiply_factor = math.ceil(min_scale_factor * height / tile_height)
        while True:
            upscaled_height = tile_height * multiply_factor
            grid_y = math.ceil(upscaled_height / tile_height)
            upscaled_height = tile_height * grid_y - overlap_y * (grid_y - 1)
            upscale_ratio = upscaled_height / height
            if upscale_ratio >= min_scale_factor:
                break
            multiply_factor += 1
        upscaled_width = int(width * upscale_ratio)
        grid_x = math.ceil((upscaled_width - overlap_x) / (tile_width - overlap_x))
        if grid_x > 1:
            overlap_x = round((tile_width * grid_x - upscaled_width) / (grid_x - 1))

    # --- 防御：原版节点在这两种极端情况下会产出非法网格 ---
    if grid_x < 1:
        grid_x = 1
    if grid_y < 1:
        grid_y = 1
    if upscaled_width < tile_width:
        upscaled_width = tile_width
    if upscaled_height < tile_height:
        upscaled_height = tile_height

    # 复算实际坐标（对应 _create_tile_coordinates，linear 顺序）
    tiles = []
    for row in range(grid_y):
        y = row * (tile_height - overlap_y)
        if row == grid_y - 1:
            y = upscaled_height - tile_height
        for col in range(grid_x):
            x = col * (tile_width - overlap_x)
            if col == grid_x - 1:
                x = upscaled_width - tile_width
            tiles.append((x, y))

    return {
        "src_width": width,
        "src_height": height,
        "upscaled_width": upscaled_width,
        "upscaled_height": upscaled_height,
        "tile_width": tile_width,
        "tile_height": tile_height,
        "overlap_x": overlap_x,
        "overlap_y": overlap_y,
        "grid_x": grid_x,
        "grid_y": grid_y,
        "num_tiles": grid_x * grid_y,
        "effective_scale_x": round(upscaled_width / width, 3),
        "effective_scale_y": round(upscaled_height / height, 3),
        "megapixels": round(upscaled_width * upscaled_height / 1e6, 1),
        "tiles": tiles,
    }


# --- 提示词档位判定 -------------------------------------------------------
# 仓库的判定规则是"每块 tile 装了多少主体"，落到数字上就是 tile 数量。
# 阈值取自 llm_prompt_templates/README.md 的决策表（结果 ≲4K / 4K–8K / 8K+）。

TIER_FULL = "full_build"
TIER_TEXTURE = "texture"
TIER_SUBJECTLESS = "subject_less"

TIER_LABEL = {
    TIER_FULL: "完整构建（逐元素点名）",
    TIER_TEXTURE: "材质普查（单段）",
    TIER_SUBJECTLESS: "无主体通用（防幻觉）",
}


def pick_tier(num_tiles, upscaled_long_edge):
    """按块数判档（阈值取自仓库给的网格范围），再用结果长边做二次校正。

    仓库 llm_prompt_templates/README.md 的 decision rule 原文：

        Result ≲ 4K   → grid 1×1 到 3×2-ish  → full_build
        Result 4K–8K  → grid 4×3 到 6×4      → texture
        Result 8K+    → subject_less

    它明确说「判档依据是每块 tile 装了多少主体，而这由**结果尺寸 + tile 尺寸**
    决定」——换成能直接算的量就是**块数**。所以块数是一级判据，结果长边只做校正。

    ⚠ 上界取仓库给的网格上界（6×4 = 24），**不**取「结果 8K」对应的 ~35 块。
      因为主 README 还有一条：「If a pass straddles two tiers, pick the less
      specific one.」——卡在 texture / subject_less 之间时宁可选更不具体的那个，
      所以用较小的那个上界。

    注意这不是「按输入像素判」：输入像素只是间接因素。同一张源图，把 tile 调大
    → 块数变少 → 档位就会往前跳。
    """
    if num_tiles <= 6:          # 仓库：1×1 ~ 3×2-ish（"few tiles, under ~6"）
        tier = TIER_FULL
    elif num_tiles <= 24:       # 仓库：4×3 ~ 6×4
        tier = TIER_TEXTURE
    else:
        tier = TIER_SUBJECTLESS

    # 二次校正：结果极大时，即使块数不多，单块也已经只是一块表面裁切。
    # 「8K」那条是按官方 1024–1536 的 tile 推出来的；换超大 tile 时块数很少，
    # 硬套 8K 会误伤（2 块的流程不该退到无主体档），所以再加一个块数门槛。
    if upscaled_long_edge > 12000:
        tier = TIER_SUBJECTLESS
    elif upscaled_long_edge > 8192 and num_tiles >= 12:
        tier = TIER_SUBJECTLESS

    return tier


def tier_reason(num_tiles, upscaled_long_edge, tier):
    # 注意：整串里别留空格。tkinter 的 wraplength 优先在空格处折行，
    # 中文串里只要有一个空格，就会把那一行折得只剩几个字。
    if tier == TIER_FULL:
        return (f"{num_tiles}块，每块基本装得下主体，可以逐元素点名材质并给正向守卫")
    if tier == TIER_TEXTURE:
        return (f"{num_tiles}块，多数块只是一两块表面的裁切，"
                f"点名主体容易串味，退到材质普查")
    return (f"{num_tiles}块（输出长边{upscaled_long_edge}px），"
            f"相当一部分块是没有上下文的裁切，必须去掉主体名以防幻觉")


def analyze(width, height, tile_width=2048, tile_height=2048,
            overlap=0.25, min_scale_factor=1.3):
    """plan() 的封装：额外附上档位判定，供界面直接消费。"""
    r = plan(width, height, tile_width, tile_height, overlap, min_scale_factor)
    long_edge = max(r["upscaled_width"], r["upscaled_height"])
    tier = pick_tier(r["num_tiles"], long_edge)
    r["long_edge"] = long_edge
    r["tier"] = tier
    r["tier_label"] = TIER_LABEL[tier]
    r["tier_reason"] = tier_reason(r["num_tiles"], long_edge, tier)
    # 1x1 网格时整个工作流退化为单张精修，没有拼缝
    r["single_pass"] = r["num_tiles"] == 1
    return r


if __name__ == "__main__":
    import sys

    print("=" * 96)
    print("A. 常见尺寸在 tile=2048 / overlap=1/4 / min_scale=1.3 下的实际结果")
    print("=" * 96)
    print(f"{'源尺寸':>12} {'→ 放大后':>14} {'网格':>7} {'块数':>5} {'实际倍数':>9} {'MP':>6}")
    for w, h in [(512, 512), (768, 1024), (1024, 1536), (1024, 1024),
                 (1200, 1600), (1600, 1200), (1920, 1080), (2048, 2048),
                 (2400, 3000), (3000, 2400), (4000, 3000), (6000, 4000)]:
        r = plan(w, h)
        print(f"{w}x{h:<8} {r['upscaled_width']}x{r['upscaled_height']:<8} "
              f"{r['grid_x']}x{r['grid_y']:<4} {r['num_tiles']:>5} "
              f"{r['effective_scale_x']:>8}x {r['megapixels']:>6}")

    print()
    print("=" * 96)
    print("B. 同一张 1024x1536，改 min_scale_factor 会怎样（tile 2048）")
    print("=" * 96)
    for sf in [1.0, 1.3, 1.5, 2.0, 2.5, 3.0, 4.0]:
        r = plan(1024, 1536, min_scale_factor=sf)
        print(f"  min_scale={sf:<4} → {r['upscaled_width']}x{r['upscaled_height']} "
              f"({r['grid_x']}x{r['grid_y']}={r['num_tiles']}块, 实际 {r['effective_scale_x']}x, "
              f"{r['megapixels']}MP)")

    print()
    print("=" * 96)
    print("C. tile 尺寸的影响（1024x1536, min_scale=1.3, overlap=1/4）")
    print("=" * 96)
    for t in [1024, 1536, 2048, 2560, 3048]:
        r = plan(1024, 1536, tile_width=t, tile_height=t)
        print(f"  tile={t:<5} → {r['upscaled_width']}x{r['upscaled_height']} "
              f"({r['grid_x']}x{r['grid_y']}={r['num_tiles']}块, 实际 {r['effective_scale_x']}x)")

    print()
    print("=" * 96)
    print("D. 全尺寸扫描：找非法网格 / 越界 tile（width<=height 分支）")
    print("=" * 96)
    bad = 0
    for w in range(256, 4000, 37):
        for h in range(w, 4000, 53):
            for t in (1024, 1536, 2048):
                for ov in (0.0, 0.0625, 0.25, 0.5):
                    r = plan(w, h, tile_width=t, tile_height=t,
                             overlap=ov, min_scale_factor=1.3)
                    tw, th = r["tile_width"], r["tile_height"]
                    if r["grid_x"] * r["grid_y"] != len(r["tiles"]):
                        bad += 1
                    for x, y in r["tiles"]:
                        if x < 0 or y < 0 or x + tw > r["upscaled_width"] or y + th > r["upscaled_height"]:
                            bad += 1
                            break
    print(f"  扫描组合数 ≈ {len(range(256,4000,37))*len(range(1,4000,53))*12}，异常 {bad} 例")
    print()
    print("E. 极端长宽比（宽>高 分支 + 极窄图）")
    for w, h in [(4000, 300), (300, 4000), (8000, 1000), (1000, 8000)]:
        r = plan(w, h)
        print(f"  {w}x{h} → {r['upscaled_width']}x{r['upscaled_height']} "
              f"({r['grid_x']}x{r['grid_y']}={r['num_tiles']}块, {r['effective_scale_x']}x)")

    print()
    print("=" * 96)
    print("F. 档位自动判定（tile=2048 / overlap=1/4 / min_scale=1.3）")
    print("=" * 96)
    for w, h in [(512, 512), (1024, 1536), (1200, 1600), (1920, 1080),
                 (2048, 2048), (2400, 3000), (3000, 2400), (4000, 3000),
                 (5000, 4000), (6000, 4000), (8000, 6000)]:
        a = analyze(w, h)
        flag = "  ← 单张精修，无拼缝" if a["single_pass"] else ""
        print(f"  {w}x{h:<7} → {a['upscaled_width']}x{a['upscaled_height']:<7} "
              f"{a['grid_x']}x{a['grid_y']}={a['num_tiles']:>3}块  "
              f"[{a['tier']}]{flag}")
        print(f"          理由: {a['tier_reason']}")
