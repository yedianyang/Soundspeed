#!/usr/bin/env python
"""3.B 真实素材探针（文本路径）：每个 raw-text 文件独立 session 跑 SP 解析逻辑，
报告 ParsedScene 结构 + token 用量（in/out/total vs n_ctx=8192）。

为什么直连 GemmaClient 而非 LLMService：
  service.infer 只返回 content，丢了 usage（prompt_tokens/completion_tokens）。
  本探针要评估「全量载入需要多少 token」，必须拿 usage，故直连 create_chat_completion。
  解析逻辑仍复用 run_sp_parse 的内部函数（_build_system_prompt / _build_user_message /
  _split_into_chunks / _parse_chunk_output），与生产单块/多块路径等价。

每次调用新上下文 session（用户要求，防 pdf/word/txt 历史串味）：
  每个文件新建 GemmaClient（独立 Llama 实例），文件间零上下文残留；
  文件内多块时每块 reset() 清 KV cache。

用法（worktree 根目录）：
  GEMMA_MODEL_PATH=/Users/yedianyang/Documents/GitHub/Soundspeed/models/gemma-4-E4B-it-Q4_K_M.gguf \\
  /Users/yedianyang/Documents/GitHub/Soundspeed/.venv/bin/python \\
  scripts/sp_material_probe.py <raw_text.txt> [<raw_text.txt> ...]

拍照（图像）走 Gemma4 原生多模态，不在本探针——见 sp_vision_probe.py。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.llm.client import GemmaClient  # noqa: E402
from backend.llm.config import TASK_CONFIG  # noqa: E402
from backend.llm.service import _META_KEYS  # noqa: E402  复用生产 gen_kwargs 口径
from backend.pipelines.sp_script import (  # noqa: E402
    _DEFAULT_CHUNK_SIZE,
    _build_system_prompt,
    _build_user_message,
    _parse_chunk_output,
    _split_into_chunks,
)

N_CTX = 8192


def _gen_kwargs() -> dict:
    """复制 service.infer 的口径：TASK_CONFIG 去掉元字段（priority/_reserved/system）。"""
    cfg = TASK_CONFIG["script_parse"]
    return {k: v for k, v in cfg.items() if k not in _META_KEYS}


def _probe_file(model_path: str, path: str, chunk_size: int) -> dict:
    raw = Path(path).read_text(encoding="utf-8")
    system = _build_system_prompt()
    chunks = _split_into_chunks(raw, chunk_size)
    kw = _gen_kwargs()

    # 每文件新 session（独立 Llama 实例），防文件间上下文串味
    client = GemmaClient(model_path=model_path)
    llm = client._llm

    def ntok(s: str) -> int:
        return len(llm.tokenize(s.encode("utf-8")))

    sys_tok = ntok(system)
    raw_tok = ntok(raw)
    print(f"\n{'=' * 72}\n文件: {path}")
    print(
        f"  字符={len(raw)}  raw_token={raw_tok}  system_prompt_token={sys_tok}  "
        f"分块={len(chunks)}（chunk_size={chunk_size}）"
    )

    all_scenes = []
    tot_in = tot_out = 0
    for i, chunk in enumerate(chunks):
        if not chunk.strip():
            continue
        llm.reset()  # 每次调用清 KV cache，确保独立上下文
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": _build_user_message(chunk)},
        ]
        resp = client.create_chat_completion(messages=messages, **kw)
        u = resp.get("usage") or {}
        pin, pout = int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))
        tot_in += pin
        tot_out += pout
        flag = "  ⚠ 撞顶 n_ctx" if pin + pout >= N_CTX else ""
        print(f"  块{i}: in={pin} out={pout} total={pin + pout}{flag}")
        content = resp["choices"][0]["message"]["content"]
        try:
            all_scenes.extend(_parse_chunk_output(content))
        except Exception as e:  # noqa: BLE001
            print(f"    ✗ 解析失败: {e}\n    raw[:280]={content[:280]!r}")

    print(f"\n  → 解析出 {len(all_scenes)} 场：")
    for s in all_scenes:
        sl = s.slugline
        print(
            f"  ├ scene_code={s.scene_code!r}  int_ext={sl.int_ext!r}  "
            f"time={sl.time_of_day!r}  location={sl.location!r}  lines={len(s.lines)}"
        )
        for ln in s.lines:
            t = ln.text[:34].replace("\n", " ")
            print(f"  │    character={ln.character!r}  text={t!r}")

    ratio = (tot_out / tot_in) if tot_in else 0.0
    per_char = (tot_in + tot_out) / len(raw) if raw else 0.0
    print(
        f"\n  TOKEN: in={tot_in} out={tot_out} total={tot_in + tot_out}  "
        f"out/in={ratio:.2f}  total/字符={per_char:.2f}"
    )

    del client  # 释放模型，下一文件重新 load（独立 session）
    return {
        "name": Path(path).name,
        "chars": len(raw),
        "raw_tok": raw_tok,
        "sys_tok": sys_tok,
        "in": tot_in,
        "out": tot_out,
        "ratio": ratio,
        "per_char": per_char,
        "scenes": len(all_scenes),
        "chunks": len(chunks),
    }


def main() -> None:
    model = os.environ.get("GEMMA_MODEL_PATH")
    if not model or not Path(model).exists():
        sys.exit("需设 GEMMA_MODEL_PATH 指向 .gguf")
    files = sys.argv[1:]
    if not files:
        sys.exit("用法: sp_material_probe.py <raw_text.txt> ...")

    rows = []
    for f in files:
        print(f"\n>>> 新 session 加载模型 {Path(model).name} ...")
        rows.append(_probe_file(model, f, _DEFAULT_CHUNK_SIZE))

    print(f"\n{'=' * 72}\n汇总（n_ctx={N_CTX}, chunk_size={_DEFAULT_CHUNK_SIZE} 字符）:")
    for r in rows:
        print(
            f"  {r['name']}: {r['chars']}字符/{r['raw_tok']}tok → "
            f"in {r['in']}/out {r['out']}/total {r['in'] + r['out']}  "
            f"{r['scenes']}场 {r['chunks']}块  out/in={r['ratio']:.2f}  total/字符={r['per_char']:.2f}"
        )
    if rows:
        avg_pc = sum(r["per_char"] for r in rows) / len(rows)
        sys_tok = rows[0]["sys_tok"]
        # 单块约束：sys固定 + 变动(随字符) ，total ≈ sys_tok + 变动；变动/字符 ≈ avg_pc - sys/chars
        # 粗外推：1 块装满 n_ctx*0.9 时的字符数
        print(
            f"\n  平均 total/字符≈{avg_pc:.2f}（含 system 固定 {sys_tok} tok，小样本偏高）。"
            f"\n  外推见报告（system 固定项需从斜率分离，单样本字符量级一致无法回归，由 Lead 口算）。"
        )


if __name__ == "__main__":
    main()
