#!/usr/bin/env python
"""3.G 拍照路径探针：Gemma4 原生多模态直读剧本照片（不接外部 OCR 模块）。

用户要求：OCR 用 Gemma4 原生视觉能力，每次调用新上下文 session。

机制：text gguf + mmproj（vision projector）经 llama-cpp 的 mtmd 接口（Llava15ChatHandler
封装），图片直喂模型。两种模式：
  --mode ocr     图片 → 纯文本（Gemma4 当 OCR，输出可再喂 sp_material_probe 走 3.B）
  --mode struct  图片 → 直接结构化 JSON（图片一步到 ParsedScene，跳过中间文本）

评估：转录/结构质量 + 图像 token 用量（vision token 占 n_ctx 多少）。

⚠ 已知阻塞（3.G 待解，2026-06-04 实测）：
  llama-cpp-python 0.3.23 无 gemma 专用 vision ChatHandler。本脚本用的 Llava15ChatHandler
  套的是 llava/vicuna 模板（"USER: <image> ... ASSISTANT:"）+ llava 的 <|image|> marker，
  而 gemma4 需要 gemma 模板（<start_of_turn>）+ <start_of_image> marker。模板错配 → 模型
  收到畸形多模态上下文 → 整段幻觉 + 重复循环（非图像问题：mmproj 已成功把图编码成 ~266
  vision token，vision 链路本身通）。要正确跑 gemma4 视觉需：① 自定义 gemma vision handler
  或换工具（llama.cpp 自带 llama-mtmd-cli）或升级到带 gemma handler 的版本；
  ② 测试图物理旋转 90°（EXIF orientation=nil，非 viewer 可纠正），需先 de-rotate。

用法（worktree 根目录）：
  GEMMA_MODEL_PATH=/Users/yedianyang/Documents/GitHub/Soundspeed/models/gemma-4-E4B-it-Q4_K_M.gguf \\
  GEMMA_MMPROJ_PATH=/Users/yedianyang/.cache/huggingface/hub/models--unsloth--gemma-4-E4B-it-GGUF/snapshots/653803f092503c04a65164346f3208a36e707693/mmproj-F16.gguf \\
  /Users/yedianyang/Documents/GitHub/Soundspeed/.venv/bin/python \\
  scripts/sp_vision_probe.py <image> [--mode ocr|struct]
"""
from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

N_CTX = 8192

_OCR_PROMPT = (
    "这是一页剧本的照片。请把照片里的剧本内容逐字转成纯文本，"
    "保留场次号、角色名、对白与舞台指示的原始换行格式，每句对白单独成行。"
    "只输出剧本正文，忽略屏幕上的系统通知/水印/界面文字，不要任何解释。"
)

# struct 模式复用 3.B 的 system prompt（schema + few-shot），图片当输入
from backend.pipelines.sp_script import _build_system_prompt  # noqa: E402


def _data_uri(path: str) -> str:
    raw = Path(path).read_bytes()
    ext = Path(path).suffix.lower().lstrip(".") or "jpeg"
    if ext == "jpg":
        ext = "jpeg"
    return f"data:image/{ext};base64,{base64.b64encode(raw).decode()}"


def main() -> None:
    model = os.environ.get("GEMMA_MODEL_PATH")
    mmproj = os.environ.get("GEMMA_MMPROJ_PATH")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    mode = "ocr"
    if "--mode" in sys.argv:
        mode = sys.argv[sys.argv.index("--mode") + 1]
    if not model or not Path(model).exists():
        sys.exit("需设 GEMMA_MODEL_PATH")
    if not mmproj or not Path(mmproj).exists():
        sys.exit("需设 GEMMA_MMPROJ_PATH 指向 mmproj vision projector")
    if not args:
        sys.exit("用法: sp_vision_probe.py <image> [--mode ocr|struct]")
    image = args[0]

    from llama_cpp import Llama
    from llama_cpp.llama_chat_format import Llava15ChatHandler

    print(f">>> 新 session：text={Path(model).name}  mmproj={Path(mmproj).name}  mode={mode}")
    handler = Llava15ChatHandler(clip_model_path=mmproj, verbose=False)
    llm = Llama(
        model_path=model,
        chat_handler=handler,
        n_ctx=N_CTX,
        n_gpu_layers=-1,
        seed=42,
        verbose=False,
    )

    if mode == "struct":
        system = _build_system_prompt()
        user_text = "解析这页剧本照片，按上述 JSON 格式输出，忽略屏幕系统通知/界面文字。"
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": _data_uri(image)}},
            ]},
        ]
        max_tokens = 4096
    else:
        messages = [
            {"role": "user", "content": [
                {"type": "text", "text": _OCR_PROMPT},
                {"type": "image_url", "image_url": {"url": _data_uri(image)}},
            ]},
        ]
        max_tokens = 2048

    resp = llm.create_chat_completion(messages=messages, max_tokens=max_tokens, temperature=0.1)
    content = resp["choices"][0]["message"]["content"]
    u = resp.get("usage") or {}
    pin, pout = int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))

    print(f"\n{'=' * 72}\n图片: {image}  ({Path(image).stat().st_size // 1024} KB)")
    print(f"模式: {mode}")
    print(f"\n--- Gemma4 输出 ---\n{content}\n--- /输出 ---")
    flag = "  ⚠ 撞顶" if pin + pout >= N_CTX else ""
    print(
        f"\nTOKEN: in={pin}（含图像 vision token）  out={pout}  total={pin + pout}{flag}"
        f"\n  注：纯文本 prompt 只有几十 token，in 的绝大部分=图像编码的 vision token，"
        f"即「一张照片占多少 n_ctx」的答案。"
    )


if __name__ == "__main__":
    main()
