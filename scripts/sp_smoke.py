"""SP Pipeline 端到端真模型冒烟探针（手动验收用，非自动化测试）。

用真 Gemma + 真 run_sp_parse 跑一段多场剧本，验证：
  1) LLM 能在 max_tokens=8192 + n_ctx=8192 配置下稳定输出不被截断
  2) **分块路径**：输入 >chunk_size 时确实分块，多块结果顺序 append 正确
  3) 分场边界是否合理（场数与预期大致一致）
  4) scene_code 抽取（有场次号的场抽到，无场次号的场 null）
  5) 真孤儿场发生数（切块把 slugline 与对白切到两块 → 后块成 scene_code=null
     且 slugline 全 null 的对白残段）。注意：有 slugline 的无号场是**正常无号场**，
     不算孤儿——空行优先切若生效，孤儿数应为 0。
  6) ⚠️ n_ctx 边界风险：prompt_tokens + max_tokens > n_ctx 时 overflow 检测

【验证输入设计】
  6 场、约 1000 字符。chunk_size 在本探针里缩到 600（生产默认 1500）以确保切成 ≥2 块、
  触发分块路径——分块逻辑/空行切/多块 append 的正确性与 chunk_size 数值无关，600 验到的与 1500 一致；
  且块更小时单块 prompt 更小，8192 边界更不易撞顶（撞顶检测仍有效）。
  场 1/2/3/5 带场次号，「内 出租屋 清晨」「内 老宅院 午后」无场次号（只有 slugline）。
  含舞台指示行（character=null）、全角冒号对白、脏数据（页码噪声）。
  场间均有空行 → 空行优先切应在场边界落刀，不产生孤儿场（孤儿数预期 0）。

用法（worktree 根目录）：
  GEMMA_MODEL_PATH=/Users/yedianyang/Documents/GitHub/Soundspeed/models/gemma-4-E4B-it-Q4_K_M.gguf \\
  /Users/yedianyang/Documents/GitHub/Soundspeed/.venv/bin/python scripts/sp_smoke.py

注意：
  - 不需要 SOUNDSPEED_DB（纯解析，不写库）。
  - 首次运行含模型加载，耗时 30-120s；分块后会多次 infer，总耗时更长。
  - 若 LLMService 报 context overflow / ValueError，检查 n_ctx 配置，
    参考 backend/llm/config.py 注释（max_tokens=8192 边界风险）。
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

# 确保 worktree 根在 sys.path（PYTHONPATH=. 也可）
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.llm.service import LLMService, get_service  # noqa: E402
from backend.pipelines.sp_script import (  # noqa: E402
    ParsedScene,
    SPParseError,
    _split_into_chunks,
    run_sp_parse,
)


# ---------------------------------------------------------------------------
# 验证输入（手工造，6 场 >1500 字符触发分块，混合场次号/无场次号/脏数据）
# ---------------------------------------------------------------------------

SMOKE_SCRIPT = """
场 1  内 咖啡馆 日
罗湘：我们先从你这些年的经历聊起吧，从最开始的地方说，慢慢来，不用着急。
访谈者：好，那我就从第一次离开家乡、坐了三十多个小时火车来北京的那个冬天说起。
罗湘：那一年的北京据说特别冷，你刚来的时候住在什么样的地方？
访谈者：城中村里一个十平米的隔断间，窗户关不严实，半夜的风一直往屋里灌。
罗湘：所以你后来常挂在嘴边的那个"漂"字，其实是从那间小屋子里开始的。
（罗湘起身往访谈者的杯子里续了些热水，又重新坐下）

                        — 1 —

场 2  外 广场 傍晚
访谈者：今天这广场上的人可真不少，比我上回路过的时候要热闹太多了。
罗湘：傍晚这个时段最舒服，光线是软的，人也都松弛下来，最适合说点心里话。
（两人沿着广场的边缘慢慢地走，路灯一盏接一盏地亮起来）
罗湘：你刚才提到家乡，那一段上次你一直没讲完，今天还愿意接着往下说吗？
访谈者：愿意的，只是有些事情一张嘴，就觉得胸口堵得慌，连气都喘不匀。

场 3  内 录音棚 夜
罗湘：我们换到这个安静些的地方接着聊，这里不会有人来打断我们。
访谈者：其实这阵子我一直在反反复复地想，记忆这个东西到底可不可靠。
罗湘：你是担心自己把当年的事情记错了吗？
访谈者：不是怕记错，是怕我牢牢记住的那些，其实只是我自己愿意去记住的那一部分。
（访谈者沉默了很久，手指无意识地一下一下敲着桌面）

内 出租屋 清晨
罗湘：天都快亮了，没想到我们俩竟然就这样聊了整整一夜。
访谈者：好久没有这样把话一口气说尽了，说完之后，心里反倒踏实下来。
罗湘：那今天就先到这儿，剩下的那些，留着我们下一次再慢慢说。
（窗外的天色一点一点泛白，楼下的城市开始有了第一声响动）

场 5  外 老城墙下 夜
访谈者：最后能陪我走到这儿吗，我想在离开之前，再看一眼这堵老墙。
罗湘：这堵墙，你以前来过很多回吧？
访谈者：每次要离开北京之前我都会来一趟，像是给自己的一场小小的告别仪式。
（远处传来零星的车声，两个人并肩站着，谁都没有再开口）

内 老宅院 午后
罗湘：这就是你从小长大的那个院子吗，比我想象中要敞亮不少。
访谈者：小时候总觉得这院子大得像是整个世界，如今回来看，其实就这么一方小天井。
罗湘：你在信里写过的那棵枣树呢，还在不在？
访谈者：还在，每年照样结一树的果，只是再没人爬上去摘了。
（午后的阳光斜斜地照进院子，枣树的影子被拉得很长很长）
""".strip()

# 预期：6 场；场 1/2/3/5 带 scene_code，出租屋/老宅院 无 scene_code
EXPECTED_SCENES = 6
EXPECTED_CODED = 4  # scene_code 非 null 的场数
_CHUNK_SIZE = 600  # 缩小以确保 ~1000 字输入触发分块（生产默认 1500）


# ---------------------------------------------------------------------------
# 主逻辑
# ---------------------------------------------------------------------------


async def main() -> int:
    model_path = os.environ.get("GEMMA_MODEL_PATH", "(未设置，将走 HF cache/下载)")
    chunks = _split_into_chunks(SMOKE_SCRIPT, _CHUNK_SIZE)

    print("=" * 68)
    print("SP Pipeline 端到端真模型冒烟探针")
    print(f"  模型路径  : {model_path}")
    print(f"  输入字符数: {len(SMOKE_SCRIPT)}")
    print(f"  分块数    : {len(chunks)}（chunk_size={_CHUNK_SIZE}；>1 表示触发了分块路径）")
    print("=" * 68)

    if len(chunks) < 2:
        print("⚠️ 警告：输入未触发分块（分块数 < 2），本次未验证分块路径。")

    svc: LLMService = get_service()
    print(f"LLMService: model_present={svc.model_present} model_loaded={svc.model_loaded}")

    t_start = time.perf_counter()
    scenes: list[ParsedScene] | None = None
    err: BaseException | None = None

    try:
        scenes = await run_sp_parse(SMOKE_SCRIPT, svc, chunk_size=_CHUNK_SIZE, timeout=300.0)
    except SPParseError as exc:
        err = exc
        print(f"\n⚠️ SPParseError: {exc}")
        if exc.cause is not None:
            print(f"   cause: {exc.cause!r}")
    except Exception as exc:  # noqa: BLE001
        err = exc
        print(f"\n⚠️ 未预期异常: {type(exc).__name__}: {exc}")
    finally:
        elapsed = time.perf_counter() - t_start
        await svc.aclose()

    print(f"\n  推理耗时: {elapsed:.1f}s（{len(chunks)} 块）")

    # ── n_ctx / overflow 检测 ─────────────────────────────────────────────
    print("\n【n_ctx 边界风险检测】")
    # 若 llama-cpp 触发 context overflow，会在推理时抛 ValueError 或 RuntimeError，
    # 已在上方 except Exception 捕获并打印。
    # 正常情况下 LLM 输出 EOS 后截止，实际 output token < max_tokens=8192 - prompt_tokens。
    if err is not None and isinstance(err, (ValueError, RuntimeError)):
        print(f"  ❌ 触发 context overflow 或 llama-cpp 报错：{err!r}")
        print("  建议：降低 max_tokens（如 4096）留 input 余量，或提升 n_ctx（如 16384）。")
    elif err is not None:
        print(f"  ⚠️ 其他异常（非 overflow）：{type(err).__name__}: {err}")
    else:
        print(f"  ✅ 未触发 context overflow（{len(chunks)} 块逐块输出均正常完成）")

    if scenes is None:
        print("\n❌ FAIL：run_sp_parse 未返回结果（见上方异常）")
        print("=" * 68)
        return 1

    # ── 分场结果 ─────────────────────────────────────────────────────────
    print(f"\n【分场结果】共 {len(scenes)} 场（预期 {EXPECTED_SCENES}）")
    coded_count = 0
    orphan_count = 0  # 真孤儿：scene_code=null 且 slugline 全 null 且有对白（切块残段）

    for i, scene in enumerate(scenes, start=1):
        sl = scene.slugline
        has_slugline = any(v is not None for v in (sl.int_ext, sl.time_of_day, sl.location))
        if scene.scene_code is not None:
            coded_count += 1
        # 真孤儿场：无场次号 + 无 slugline + 有对白 → slugline 被切到上一块的残段。
        # 有 slugline 的无号场是正常无号场（设计合法），不计入。
        is_orphan = scene.scene_code is None and not has_slugline and len(scene.lines) > 0
        if is_orphan:
            orphan_count += 1

        slug_str = (
            f"{sl.int_ext or '?'} {sl.location or '?'} {sl.time_of_day or '?'}"
            if has_slugline
            else "（无 slugline）"
        )
        orphan_tag = "  ⚠️孤儿" if is_orphan else ""
        print(
            f"  场 {i:2d}: scene_code={scene.scene_code!r:8}  "
            f"slugline={slug_str}  lines={len(scene.lines)}{orphan_tag}"
        )
        for j, line in enumerate(scene.lines, start=1):
            char = line.character if line.character is not None else "（舞台指示）"
            print(f"         [{j:2d}] {char}：{line.text[:40]}")

    print(f"\n  有场次号: {coded_count} 场（预期 {EXPECTED_CODED}）")
    print(f"  真孤儿场（无号+无slugline+有对白，切块残段）: {orphan_count} 场（空行切生效应为 0）")

    # ── 判定 ─────────────────────────────────────────────────────────────
    scene_count_ok = len(scenes) >= 1  # 至少解析出 1 场就算不全崩
    json_ok = err is None
    overflow_ok = not (isinstance(err, (ValueError, RuntimeError)))
    split_ok = len(chunks) >= 2  # 本探针的目的就是验分块路径

    passed = json_ok and scene_count_ok and overflow_ok

    print("\n" + "=" * 68)
    if passed:
        scene_match = "✅" if len(scenes) == EXPECTED_SCENES else "⚠️（场数与预期不符，见上）"
        code_match = "✅" if coded_count == EXPECTED_CODED else "⚠️（场次号抽取数与预期不符）"
        split_match = "✅" if split_ok else "⚠️（未触发分块，分块路径未验证）"
        orphan_match = "✅" if orphan_count == 0 else f"⚠️（{orphan_count} 个切块孤儿场，空行切未完全生效）"
        print("✅ PASS：run_sp_parse 在真 Gemma 下端到端正常运行")
        print(f"  分块路径 : {split_match}（{len(chunks)} 块）")
        print(f"  分场数   : {scene_match}")
        print(f"  场次号   : {code_match}")
        print(f"  孤儿场   : {orphan_match}")
    else:
        print(f"❌ FAIL：json_ok={json_ok}, scene_count_ok={scene_count_ok}, "
              f"overflow_ok={overflow_ok}")
    print("=" * 68)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
