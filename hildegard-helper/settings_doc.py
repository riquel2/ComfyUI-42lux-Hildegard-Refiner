# -*- coding: utf-8 -*-
"""把算出来的数字写成一份可以照着设的 ComfyUI 设置清单。

纯离线，不依赖任何模型。内容对应官方示例工作流
example_workflows/42lux-hildegard-refiner.json 的节点结构。
"""

DEFAULTS = {
    "unet": "flux-2-klein-9b-fp8.safetensors",
    "clip": "qwen_3_8b.safetensors",
    "clip_type": "flux2",
    "vae": "fl2vae.safetensors",
    "lora": "42lux_hildegard-refiner.safetensors",
    "lora_strength": 1.0,
    "steps": 4,
    "sampler": "euler",
    "w_tile": 0.9,
    "w_position": 0.8,
    "w_global": 0.8,
    "cell_size": 512,
    "ctrl3_max_size": 768,
}

LINE = "─" * 62


def build(info, opts=None):
    """info 来自 hildegard_math.analyze()，opts 覆盖 DEFAULTS。"""
    o = dict(DEFAULTS)
    o.update(opts or {})
    reduce_ = float(o.get("tile_high_freq_reduce", 0.0))
    radius = int(o.get("low_freq_radius", 256))

    out = []
    A = out.append

    A("══════════════════ ComfyUI 设置说明 ══════════════════")
    A("")
    A(f"【源图】 {info['src_width']} × {info['src_height']}"
      f"  ({info['src_width'] * info['src_height'] / 1e6:.1f} MP)")
    A(f"【输出】 {info['upscaled_width']} × {info['upscaled_height']}"
      f"  ({info['megapixels']} MP)   实际放大 {info['effective_scale_x']}×")
    A(f"【网格】 {info['grid_x']} × {info['grid_y']}"
      f" = {info['num_tiles']} 块，每块 {info['tile_width']} × {info['tile_height']}")
    A(f"【重叠】 {info['overlap_x']} × {info['overlap_y']} px")
    A(f"【档位】 {info['tier']}  —— {info['tier_label']}")
    A("")

    A(LINE)
    A("① 模型与 LoRA（一次性装好，之后不用动）")
    A(LINE)
    A(f"  UNETLoader              {o['unet']}")
    A(f"  CLIPLoader              {o['clip']}")
    A(f"                          type = {o['clip_type']}   ← 必须选 flux2")
    A(f"  VAELoader               {o['vae']}")
    A(f"  LoraLoaderModelOnly     {o['lora']}")
    A(f"                          strength_model = {o['lora_strength']}")
    A("")
    A("  ⚠ LoRA 是必需品。基础 Klein 不认识三个参考槽位，")
    A("    没有它这三路 reference latent 会被直接忽略。")
    A("")

    A(LINE)
    A("② Hildegard Plan（决定尺寸和网格，就是上面那三个数字的来源）")
    A(LINE)
    A(f"  tile_width            {info['tile_width']}")
    A(f"  tile_height           {info['tile_height']}")
    A(f"  min_overlap           {info.get('overlap_label', '1/4 Tile')}")
    A(f"                        ← 它也决定输出尺寸和块数，别只当成「接缝软硬」")
    A(f"  min_scale_factor      {info.get('min_scale_factor', 1.3)}")
    A(f"  tile_order            {o.get('tile_order', 'spiral')}")
    A(f"  scaling_method        lanczos")
    A("")
    A("  → 输出 " + f"{info['upscaled_width']} × {info['upscaled_height']}，"
      f"{info['grid_x']}×{info['grid_y']} 网格")
    A("")
    # 「默认 2048」是对齐用的，不是画质推荐值 —— 这两件事必须分开说，
    # 否则用户会以为 2048 画质最好（实测反馈过）。
    _mp = info["tile_width"] * info["tile_height"] / 1e6
    A(f"  ⚠ 画质提示：这一档每遍画布 {_mp:.2f} MP、采样 {o['steps']} 步。")
    if _mp >= 4.0:
        A("     这是各档里每遍画布最大的一段 —— 每像素分到的去噪预算最薄，")
        A("     出图偏平滑。本工具默认 2048 只是为了和官方示例工作流的实际")
        A("     取值**对齐**，**不是画质推荐值**。")
        A("     想更精细 → tile 改 1536（2.36 MP）或 1792（3.21 MP）。")
    A("     ⚠ 单独调大 min_scale_factor 不能提高精细度：它只改输出尺寸和")
    A("       倍数，每遍画布一点没变 —— 等于把同样的平滑度放大到更多像素上。")
    A("")
    A("  ⚠ 节点自身的默认值和上面**不一样**，拖新节点必须逐项改：")
    A("     min_overlap 节点默认 1/32 Tile（不是 1/4）")
    A("     min_scale_factor 节点默认 3.0（不是 1.3）")
    A(f"     tile 尺寸节点默认 1536，上面填的是 {info['tile_width']}")
    A("     尤其 min_overlap：它和 tile 尺寸一样会改变输出尺寸和块数，")
    A("     不改的话算出来的输出尺寸和上面【输出】那一行对不上。")
    A("")

    # ── 重叠 vs tile：单位是「tile 的几分之几」，所以自动跟着 tile 走；
    #    但算法会把网格凑到输出尺寸上，实际重叠 ≠ 名义值：
    #        实际重叠 = (tile × 块数 − 输出尺寸) ÷ (块数 − 1)
    #    而且它独立决定输出尺寸/块数/档位。这里现算一个「换成 1/4 会怎样」
    #    的对比 —— 用户实测里就出现过「1/2 和 1/4 输出完全相同，但 1/2
    #    要 28 块、1/4 只要 15 块」的白花时间情况。
    _cur_lab = info.get("overlap_label", "1/4 Tile")
    try:
        import hildegard_math as _hm
        _cur_ov = _hm.OVERLAP_DICT.get(_cur_lab, 0.25)
        _alt_lab = "1/4 Tile"
        _alt_ov = 0.25
        A("  ── 重叠和 tile 怎么配合（这一项最容易白花时间）──")
        A("  重叠的单位是「tile 的几分之几」，所以它自动跟着 tile 走；")
        A("  但算法会把网格凑到输出尺寸上，实际重叠不等于名义值：")
        A("     实际重叠 = (tile × 块数 − 输出尺寸) ÷ (块数 − 1)")
        A(f"  你这一档算出来是 {info['overlap_x']} × {info['overlap_y']} px。")
        A("")
        if abs(_cur_ov - _alt_ov) > 1e-9:
            _a = _hm.analyze(info["src_width"], info["src_height"],
                             tile_width=info["tile_width"],
                             tile_height=info["tile_height"],
                             overlap=_alt_ov,
                             min_scale_factor=info.get("min_scale_factor", 1.3))
            A(f"  同一张源图、同一个 tile，只把重叠换成 {_alt_lab}：")
            A(f"     {_alt_lab:<10} → {_a['upscaled_width']}×{_a['upscaled_height']}"
              f"  {_a['grid_x']}×{_a['grid_y']}={_a['num_tiles']}块"
              f"  实际 {_a['effective_scale_x']}×  [{_a['tier']}]")
            A(f"     {_cur_lab:<10} → {info['upscaled_width']}×"
              f"{info['upscaled_height']}"
              f"  {info['grid_x']}×{info['grid_y']}={info['num_tiles']}块"
              f"  实际 {info['effective_scale_x']}×  [{info['tier']}]  ← 你现在")
            _same_out = (_a["upscaled_width"] == info["upscaled_width"]
                         and _a["upscaled_height"] == info["upscaled_height"])
            if _same_out and _a["num_tiles"] < info["num_tiles"]:
                A("")
                A("     ⚠⚠ 两者输出尺寸**完全相同**，但你现在这档要多跑 "
                  f"{info['num_tiles'] - _a['num_tiles']} 块")
                A(f"        （{info['num_tiles']} vs {_a['num_tiles']}，约 "
                  f"{info['num_tiles'] / max(_a['num_tiles'], 1):.1f} 倍时间），"
                  "档位也从")
                A(f"        {_a['tier']} 变成了 {info['tier']} —— 建议把 Plan 的")
                A(f"        min_overlap 改回 {_alt_lab}，提示词也要重新生成。")
            elif not _same_out:
                A("")
                A("     （两者输出尺寸不同，不是白花时间，是取舍：")
                A("       重叠越小 → 输出越大、块越少、但缝越硬。详见使用说明。）")
        A("")
    except Exception as _e:                                   # pragma: no cover
        A(f"  （重叠对比没算出来：{_e}）")
        A("")
    A("  ⚠⚠ 官方示例工作流里 Plan 节点显示的是 1536，那是**死值**：")
    A("     tile_width / tile_height 被 PrimitiveInt 节点连走了（值 2048），")
    A("     在 ComfyUI 里输入一旦被连线，节点上的 widget 就被忽略。")
    A("     同一条链上 EmptyLatentImage 的宽高也来自同两个 PrimitiveInt，")
    A("     所以实际 tile 一定是 2048（否则 Combine 的尺寸对不上、拼不回去）。")
    A("     → 你的工作流若也这样连，以 PrimitiveInt 的值为准。")
    A("")
    A("  → 官方示例里这两个 PrimitiveInt 就是 #25 / #24，要改 tile 就改它们：")
    A("       #25 数值 → Plan.tile_width  + EmptyLatentImage.width"
      "  + Flux2Scheduler.width")
    A("       #24 数值 → Plan.tile_height + EmptyLatentImage.height"
      " + Flux2Scheduler.height")
    A("    两个必须改成同一个值；采样画布和调度器分辨率会自动跟着走，")
    A("    别的节点不用动。只改一个的话 tile 会变成长方形，网格和位置")
    A("    参考图都会变怪。")
    A("    Plan 节点上另外四个参数（min_overlap / min_scale_factor /")
    A("    tile_order / scaling_method）没有连线，是真正的活值。")

    # 量化提醒：这是最容易踩的坑
    asked = float(info.get("min_scale_factor", 1.3))
    got = float(info["effective_scale_x"])
    if abs(got - asked) > 0.05:
        A("")
        A(f"  ⚠ 你填的 min_scale_factor 是 {asked}，但实际会得到 {got}×。")
        A("    这个参数只是**下限**，真实倍数会被 tile 网格向上取整吸附。")
        if got > asked + 0.2:
            A(f"    想更接近 {asked}× 的话，把 tile 尺寸调小试试（见界面上的试算）。")
    if info["single_pass"]:
        A("")
        A("  ℹ 网格是 1×1，整条流程退化成**单张精修**：")
        A("    只有一块，没有拼缝问题，Hildegard Combine 实际不起作用。")
    A("")

    A(LINE)
    A("③ Hildegard References Split（生成三路参考）")
    A(LINE)
    A(f"  tile                  0      ← 0 = 一次处理全部块；填 N 就只处理第 N 块")
    A(f"  cell_size             {o['cell_size']}    ← 位置图每格边长，最终 3× = "
      f"{o['cell_size'] * 3}×{o['cell_size'] * 3}")
    A(f"  ctrl3_max_size        {o['ctrl3_max_size']}    ← 全局缩略图长边上限")
    A(f"  tile_high_freq_reduce {reduce_:.2f}")
    A(f"  low_freq_radius       {radius}")
    A("")
    A("  tile_high_freq_reduce 是「保真 ↔ 让模型重造细节」的旋钮：")
    A("     0.00  源图细节全保留，模型几乎没有发挥空间（最保真）")
    A("     0.50  色彩结构保留，微观细节交给模型重造（平衡）")
    A("     1.00  只剩大色块和大形状，其余全由模型生成")
    A("  只影响 tile_latent，位置图和全局图不受影响，结构依然锁死。")
    if reduce_ == 0.0:
        A("  当前 0.00：源图干净，走保真路线。")
        A(f"  ⚠ 因为 reduce = 0，源码不会进入那个分支 —— low_freq_radius")
        A(f"     （你填 {radius}）**完全不参与计算**，现在是个死值。")
    else:
        A(f"  当前 {reduce_:.2f}：源图偏噪/偏锐/AI 生成痕迹重，让模型重造细节。")
    A("")

    _cs = o["cell_size"]
    _pos_px = (_cs * 3) ** 2
    _pos_all = info["num_tiles"] * _pos_px / 1e6
    _tile_all = info["num_tiles"] * info["tile_width"] * info["tile_height"] / 1e6
    _all_mp = _pos_all + _tile_all
    _share = (_pos_all / _all_mp * 100) if _all_mp else 0.0
    _cap = o["ctrl3_max_size"]
    _uw, _uh = info["upscaled_width"], info["upscaled_height"]
    _long = max(_uw, _uh)
    _sc = min(_cap / _uw, _cap / _uh)
    A("  ⚠ 这两个参数都**不是「精细度」旋钮** —— 精细度只有 tile 一个")
    A("     直接杠杆（见【tile 越小细节就越好吗】）。")
    A(f"     cell_size     决定位置图分辨率，改的是「近邻环境多清楚」。")
    A(f"                   位置图 {_cs * 3}×{_cs * 3}，每块编码一次 ——")
    A(f"                   {info['num_tiles']} 块 × {_pos_px / 1e6:.2f} MP = "
      f"{_pos_all:.1f} MP，")
    A(f"                   占本轮 VAE 编码量的约 {_share:.0f}%。")
    A("                   512 是节点默认 + 训练默认，保持即可；")
    A("                   想省时间可试 256（位置图开销降到 1/4）。")
    A("     ctrl3_max_size 决定全局缩略图长边，改的是跨块色彩/结构一致性，")
    A("                   **不改块画布、不改输出尺寸、不改块数、不改档位**。")
    if _sc >= 1.0:
        A(f"                   ⚠ 你填的 {_cap} ≥ 输出长边 {_long} → scale ≥ 1，")
        A("                     源码直接原样返回 —— 这个参数现在等于**没接**。")
    else:
        A(f"                   你填 {_cap} → 缩略图 {int(_uw * _sc)}×{int(_uh * _sc)}"
          f"（输出长边 {_long} 的 {_sc:.2f} 倍）")
        A("                   节点默认 / 训练默认是 2048。tooltip 与 README 都")
        A("                   **没写**「越大越好」的方向；作者自己的示例工作流")
        A("                   填的就是 768。")
        A("                   → 看到块间色彩漂移时试 2048（免费 A/B）。")
    A("")

    A(LINE)
    A("④ 采样（每个 tile 跑一次，块数 = 采样次数）")
    A(LINE)
    A(f"  Flux2Scheduler        steps = {o['steps']}")
    A(f"  KSamplerSelect        {o['sampler']}")
    A(f"  EmptyLatentImage      {info['tile_width']} × {info['tile_height']}")
    A(f"                        ← 从纯噪声开始重画，不是给原图加噪")
    A("")
    # 参考形态决定接几路 —— 必须跟着「参考形态」下拉变，
    # 否则选「单参考」时这里还写着「这三路就是提示词里的 Image 1/2/3」，
    # 而那份提示词里根本没有 Image 编号，两边自相矛盾。
    _mr = info.get("multi_reference", True)
    A(f"  Flux2KleinRefLatentWeight × {3 if _mr else 1}"
      f"（相似度 / 创造力的主旋钮）")
    A(f"     tile_latent       权重 {o['w_tile']}   ← 最重要，调它")
    if _mr:
        A(f"     position_latent   权重 {o['w_position']}")
        A(f"     global_latent     权重 {o['w_global']}")
    else:
        A("     position_latent   —— 不接（你选的是「单参考」）")
        A("     global_latent     —— 不接（你选的是「单参考」）")
    A("")
    if _mr:
        A("     ↑ 这三路就是提示词里写的 Image 1 / Image 2 / Image 3：")
        A("       Image 1 = tile_latent      （这个块自己的裁切）")
        A("       Image 2 = position_latent  （这个块在全图位置图里的那一格）")
        A("       Image 3 = global_latent    （整张放大图的缩略图）")
        A("       ⚠ 跟你在左边列表里放了几张图**毫无关系** —— 工具永远只把你")
        A("         选中的那**一张**当「全图原图」用，三路参考都是 ComfyUI")
        A("         在现场从那一张图切出来的。只放一张图却在提示词里看到")
        A("         Image 1/2/3，是正常的，不是清除没生效。")
    else:
        A("     ⚠ 你选的是「单参考」，所以那份提示词里**没有** Image 编号 ——")
        A("       它只说 this image / the source image。要让两边对得上，")
        A("       ComfyUI 里就只接 tile_latent，另两路别接。")
        A("       代价：模型拿不到「整幅该是什么色」的全局参考，")
        A("       跨块偏色会比三参考更明显（见第 ⑥ 节）。")
    A("")
    A("  调高 = 更贴源图（保真、少漂移、少新细节）")
    A("  调低 = 更放开（模型自己发挥，大胆但可能结构漂移）")
    A("  界面提示原文：Lower values give higher creativity but can lower resemblance.")
    A("")
    A(f"  ⚠ tile 尺寸同时就是「每一遍的画布大小」：{o['steps']} 步 + "
      f"{info['tile_width'] * info['tile_height'] / 1e6:.2f} MP 画布，")
    A("     意味着每像素分到的去噪预算很薄 —— 这是「平滑 / 塑料感」的")
    A("     另一个来源（和提示词里的 photorealistic 那几句并列）。")
    A("     想更锐、更有笔触，把 tile 降到 1536（一遍 2.36 MP）试一次；")
    A("     代价是块数变多（采样次数按块数翻倍），且**提示词要重新生成**")
    A("     （块数变了档位就会换）。反过来，tile 调大只省时间，")
    A("     不会让输出变大 —— 输出尺寸被格点吸附，两者都受同一套约束。")
    A("")

    A(LINE)
    A("⑤ Hildegard Combine（不用设置）")
    A(LINE)
    A("  自动读 dac_data 拼回整图。每条与邻块相接的边留 overlap/4 宽的")
    A("  羽化带再做模糊，所以接缝是软的，画布外边缘保持硬边。")
    A("")

    A(LINE)
    A("⑥ 防漂移 / 防塑料感（多数人栽在这两处）")
    A(LINE)
    A("  ── 色调变暗、大面积底色掉色 ──")
    A("  最典型的症状：**平涂的大面积底色掉色 + 变暗**，而画面里有结构的")
    A("  东西（叶子、羽毛、花）颜色还留着。直接原因就是提示词里这句：")
    A("      leave flat or evenly-toned areas clean and smooth")
    A("  它把「平涂均匀的区域」定义成「没什么需要保留的」，模型于是拿自己")
    A("  的先验去填 —— 而先验是中性灰。所以粉底变灰、变暗；有结构的地方")
    A("  有参考可依，颜色就守住了。每多跑一趟就再抹一次，所以「每次都会")
    A("  暗很多」。")
    A("")
    A("  机制：每个 tile 都是**从纯噪声重画**，只靠三路参考约束，")
    A("        流程里没有任何一步把颜色拉回源图。而 Hildegard Combine 是")
    A("        「后画的盖住先画的」（source-over，不是加权平均）：")
    A(f"        羽化带从 overlap/4 处才开始、模糊半径 = sqrt(overlap)，")
    A(f"        于是重叠的 {info['overlap_x']}px 里只有约 "
      f"{2 * int(info['overlap_x'] ** 0.5)}px 在做真正的交叉淡入。")
    A("        块与块之间那点色调差不会被抹平，会留下色带。")
    A("")
    A("  官方解药就在工作流里，但**默认是关着的**：")
    A("    Flux2KleinColorAnchor（官方示例里 mode = BYPASS）")
    A("    官方便签原文：If you see color drifts between tiles you can")
    A("                   activate the color anchoring node.")
    A("    它是第三方节点（ComfyUI-Flux2Klein-Enhancer）：拦住模型每一步")
    A("    的 x0 预测，把每通道均值往参考 latent 的均值上拉。")
    A("")
    A("  开它要三个动作，缺一个都跑不起来：")
    A("    1. 选中节点 → Ctrl+B（或右键 Set Mode → Always）取消 BYPASS")
    A("    2. **把 conditioning 接上** —— 它是必填输入，官方示例里空着")
    A("       （link: null），直接开只会报「缺输入」。接最后一个")
    A("       ReferenceLatent 的输出（示例里是节点 8）即可。")
    A("    3. ramp_curve 从默认 1.5 调到 **2~4** —— 节点自己的说明写着")
    A("       For few-step schedules (4-8 steps) values of 2-4 work well，")
    A("       本流程正是 4 步。strength 保持 0.5（推荐区间 0.3-0.6）。")
    A("       ref_index 保持 0 = 锚在 tile_latent（本块自己的裁切），")
    A("       正好是「每块都跟自己对得上的源图区域」那一路参考。")
    A("")
    A("  另外两个放大器：")
    A("    · RandomNoise 的 seed 控制默认 randomize —— 每次种子都变，")
    A("      这就是「每次色调都不一样」。要比对效果先改成 fixed。")
    A("    · ctrl3_max_size 官方示例填 768，而节点自身默认是 2048。")
    A("      768 表示全图缩略参考只有 768px，模型对「整幅该是什么色」")
    A("      的印象很粗 → 更容易漂。调到 2048 试试。")
    A("    · 每多跑一趟就多一次 VAE 重编码 + 重画，偏色累加 ——")
    A("      这就是「每次都会暗很多」。趟数越少越稳。")
    A("")
    A("  ── 塑料感重（纸纤维 / 笔触 / 哑光颜料没了）──")
    A("  根因是提示词里那几句固定话术，它们对「手绘 / 印刷」源图是反的：")
    A("    Remove compression artifacts, banding, and noise")
    A("      → 纸纤维、颜料颗粒在模型眼里就是 noise，被抹掉")
    A("    clarify soft or blurred areas into crisp, clean edges")
    A("      → 手绘的软边被硬化成数码锐边")
    A("    leave flat or evenly-toned areas clean and smooth")
    A("      → 粉底被擦成一张光滑的塑料面")
    A("    Produce a clean, photorealistic result")
    A("      → 目标被定成「照片」，而源图是画")
    A("  而这一档没有任何一句「保持手绘感」：仓库的 painterly 子句")
    A("  （while preserving its painterly, concept-art rendering and")
    A("  brushwork）只写在 full_build 模板里。")
    A("")
    A("  按风险从低到高试：")
    if _mr:
        A("    1. 调高三个 RefLatentWeight（纯数值，最安全）")
        A(f"       tile {o['w_tile']} → 1.0   position {o['w_position']} → 0.9   "
          f"global {o['w_global']} → 0.9")
    else:
        A("    1. 调高 tile_latent 权重（纯数值，最安全）")
        A(f"       tile {o['w_tile']} → 1.0")
        A("       （单参考下只有这一路可调；想更贴源图也可以改回三参考，")
        A("         位置图 / 全局图那两路对「保持整幅一致」帮助更大）")
    A("       官方便签：Lower values give higher creativity but can lower")
    A("       resemblance. 调高 = 更贴源图、更少自由发挥。")
    A("    2. ctrl3_max_size 768 → 2048（同上）")
    A("    3. 让**材质普查那一段**去承担「这是画」的信息，例如写成")
    A("       flat painted pigment fields with their brushwork")
    A("       —— 这是唯一不违反「只有普查段可变」这条规矩的写法。")
    A("    4. 还不行就走 full_build 档（把块数压到 6 块以内）：那一档有")
    A("       仓库官方的 painterly 子句和 Style: 标签。")
    A("")
    A("  · 分 2~3 次小幅（每次约 2×）放大，别一次跳 4~8×。")
    A("  · 提示词必须带触发词 RFNTILE.，不要改写。")
    A("  · 想要更多创造力可以降 LoRA 强度，但可能出现拼缝。")
    if info["num_tiles"] >= 20:
        A(f"  · ⚠ 本次 {info['num_tiles']} 块 = 采样跑 {info['num_tiles']} 次，"
          f"输出 {info['megapixels']} MP，云上耗时和费用都不低。")
    if info["num_tiles"] >= 7:
        A(f"  · ℹ {info['num_tiles']} 块：很多块只是表面裁切，提示词已自动退到"
          f"「{info['tier_label']}」，避免把主体幻觉进不含它的块。")
    A("")
    return "\n".join(out)


if __name__ == "__main__":
    import hildegard_math as hm
    a = hm.analyze(1024, 1536)
    a["overlap_label"] = "1/4 Tile"
    a["min_scale_factor"] = 1.3
    print(build(a))
