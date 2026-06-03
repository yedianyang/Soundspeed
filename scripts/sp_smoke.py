"""SP Pipeline 端到端真模型冒烟探针（手动验收用，非自动化测试）。

用真 Gemma + 真 run_sp_parse 跑一段多场剧本，验证：
  1) LLM 能在 max_tokens=8192 + n_ctx=8192 配置下稳定输出不被截断
  2) 分场边界是否合理（场数与预期大致一致）
  3) scene_code 抽取（有场次号的场是否抽到，无场次号的场是否 null）
  4) 孤儿场实际发生率（scene_code=null 但 slugline 非全 null 的场）
  5) ⚠️ n_ctx 边界风险：prompt_tokens + max_tokens > n_ctx 时 overflow 检测

【验证输入设计】
  3 场：场 1 带场次号、场 2 带场次号、场 3 无场次号（只有内外景 slugline）。
  部分场有舞台指示行（character=null），部分对白含全角冒号（角色：台词）。
  刻意插入脏数据（页码、空行噪声）。

用法（worktree 根目录）：
  GEMMA_MODEL_PATH=/Users/yedianyang/Documents/GitHub/Soundspeed/models/gemma-4-E4B-it-Q4_K_M.gguf \\
  /Users/yedianyang/Documents/GitHub/Soundspeed/.venv/bin/python scripts/sp_smoke.py

注意：
  - 不需要 SOUNDSPEED_DB（纯解析，不写库）。
  - 首次运行含模型加载，耗时 30-120s（取决于模型是否已在内存）。
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
from backend.pipelines.sp_script import ParsedScene, SPParseError, run_sp_parse  # noqa: E402


# ---------------------------------------------------------------------------
# 验证输入（手工造，多场混合场次号/无场次号/脏数据）
# ---------------------------------------------------------------------------

SMOKE_SCRIPT = """
场 1  内 咖啡馆 日
罗湘：我们先聊聊你的背景吧。
访谈者：好的，我从哪里开始说好呢？
罗湘：就从你第一次来北京说起。
（罗湘拿起录音笔放在桌上）

                        1

场 2  外 广场 傍晚
罗湘：今天的广场人很多。
访谈者：是啊，我来的时候已经有很多人了。
（两人并肩走向广场中央）
罗湘：你对这个地方有什么特别的记忆吗？

内 录音棚 夜
罗湘：我们继续之前的话题。
访谈者：关于家乡那一段？
罗湘：对，你说到一半就停了。
（访谈者沉默片刻）
访谈者：那段记忆不太好说。
""".strip()

# 预期：3 场，场 1/2 有 scene_code，场 3 无 scene_code
EXPECTED_SCENES = 3
EXPECTED_CODED = 2  # scene_code 非 null 的场数


# ---------------------------------------------------------------------------
# 主逻辑
# ---------------------------------------------------------------------------


async def main() -> int:
    model_path = os.environ.get("GEMMA_MODEL_PATH", "(未设置，将走 HF cache/下载)")
    print("=" * 68)
    print("SP Pipeline 端到端真模型冒烟探针")
    print(f"  模型路径  : {model_path}")
    print(f"  输入字符数: {len(SMOKE_SCRIPT)}")
    print("=" * 68)

    svc: LLMService = get_service()
    print(f"LLMService: model_present={svc.model_present} model_loaded={svc.model_loaded}")

    t_start = time.perf_counter()
    scenes: list[ParsedScene] | None = None
    err: BaseException | None = None

    try:
        scenes = await run_sp_parse(SMOKE_SCRIPT, svc, timeout=300.0)
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

    print(f"\n  推理耗时: {elapsed:.1f}s")

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
        print("  ✅ 未触发 context overflow（输出正常完成）")

    if scenes is None:
        print("\n❌ FAIL：run_sp_parse 未返回结果（见上方异常）")
        print("=" * 68)
        return 1

    # ── 分场结果 ─────────────────────────────────────────────────────────
    print(f"\n【分场结果】共 {len(scenes)} 场（预期 {EXPECTED_SCENES}）")
    coded_count = 0
    orphan_count = 0  # scene_code=null 但 slugline 有非 null 字段（孤儿场候选）

    for i, scene in enumerate(scenes, start=1):
        sl = scene.slugline
        has_slugline = any(v is not None for v in (sl.int_ext, sl.time_of_day, sl.location))
        if scene.scene_code is not None:
            coded_count += 1
        if scene.scene_code is None and has_slugline:
            orphan_count += 1

        slug_str = (
            f"{sl.int_ext or '?'} {sl.location or '?'} {sl.time_of_day or '?'}"
            if has_slugline
            else "（无 slugline）"
        )
        print(
            f"  场 {i:2d}: scene_code={scene.scene_code!r:8}  "
            f"slugline={slug_str}  lines={len(scene.lines)}"
        )
        for j, line in enumerate(scene.lines, start=1):
            char = line.character if line.character is not None else "（舞台指示）"
            print(f"         [{j:2d}] {char}：{line.text[:40]}")

    print(f"\n  有场次号: {coded_count} 场（预期 {EXPECTED_CODED}）")
    print(f"  孤儿场候选（scene_code=null + 有 slugline）: {orphan_count} 场")

    # ── 判定 ─────────────────────────────────────────────────────────────
    scene_count_ok = len(scenes) >= 1  # 至少解析出 1 场就算不全崩
    json_ok = err is None
    overflow_ok = not (isinstance(err, (ValueError, RuntimeError)))

    passed = json_ok and scene_count_ok and overflow_ok

    print("\n" + "=" * 68)
    if passed:
        scene_match = "✅" if len(scenes) == EXPECTED_SCENES else "⚠️（场数与预期不符，见上）"
        code_match = "✅" if coded_count == EXPECTED_CODED else "⚠️（场次号抽取数与预期不符）"
        print("✅ PASS：run_sp_parse 在真 Gemma 下端到端正常运行")
        print(f"  分场数   : {scene_match}")
        print(f"  场次号   : {code_match}")
        print(f"  孤儿场数 : {orphan_count}（空行切后实际发生率，供调参参考）")
    else:
        print(f"❌ FAIL：json_ok={json_ok}, scene_count_ok={scene_count_ok}, "
              f"overflow_ok={overflow_ok}")
    print("=" * 68)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
