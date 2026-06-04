#!/usr/bin/env python
"""3.G 拍照路径探针：Gemma4 原生多模态直读剧本照片（不接外部 OCR 模块）。

用户要求：OCR 用 Gemma4 原生视觉能力，每次调用新上下文 session。

机制：text gguf + mmproj（vision projector）经 llama-cpp 的 mtmd 接口，图片直喂模型。
  --mode ocr     图片 → 纯文本（Gemma4 当 OCR，输出可再喂 sp_material_probe 走 3.B）
  --mode struct  图片 → 直接结构化 JSON（图片一步到 ParsedScene，跳过中间文本）

关键修复（2026-06-04，见下 Gemma3ChatHandler）：
  llama-cpp-python 0.3.23 无 gemma 专用 vision handler。直接用 Llava15ChatHandler 会套
  llava/vicuna 模板（"USER: <image> ... ASSISTANT:"）→ gemma4 收到畸形上下文 → 整段幻觉
  + 重复循环（非图像问题：mmproj 已成功编码图像）。根因只在 CHAT_FORMAT 模板，image
  marker 那层是对的（libmtmd 的 mtmd_default_marker 统一处理）。故只需 override 模板为
  gemma 格式（<start_of_turn>user/model），其余 mtmd 图像处理全复用 Llava15ChatHandler。

评估：转录/结构质量 + 图像 token 用量（vision token 占 n_ctx 多少）。

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

from llama_cpp.llama_chat_format import Llava15ChatHandler  # noqa: E402

from backend.pipelines.sp_script import _build_system_prompt  # noqa: E402

N_CTX = 8192


class Gemma3ChatHandler(Llava15ChatHandler):
    """Gemma3/4 原生多模态 chat handler。

    复用 Llava15ChatHandler 的 mtmd 图像处理（bitmap / media_marker / chunk eval），
    只 override CHAT_FORMAT 为 gemma 模板。Llava 的 vicuna 模板会让 gemma4 幻觉。
    image_url 在 content 遍历里原样输出，由 __call__ 替换成 libmtmd 的 media_marker，
    故模板只管 turn 结构。image 前置于 text（gemma vision 习惯）。
    gemma 无 system role：DEFAULT_SYSTEM_MESSAGE=None 不注入，调用方把 system 拼进 user。
    """

    DEFAULT_SYSTEM_MESSAGE = None

    CHAT_FORMAT = (
        "{% for message in messages %}"
        "{% if message.role == 'system' or message.role == 'user' %}"
        "<start_of_turn>user\n"
        "{% if message.content is string %}{{ message.content }}"
        "{% else %}"
        "{% for content in message.content %}"
        "{% if content.type == 'image_url' and content.image_url is string %}{{ content.image_url }}{% endif %}"
        "{% if content.type == 'image_url' and content.image_url is mapping %}{{ content.image_url.url }}{% endif %}"
        "{% endfor %}"
        "{% for content in message.content %}"
        "{% if content.type == 'text' %}{{ content.text }}{% endif %}"
        "{% endfor %}"
        "{% endif %}"
        "<end_of_turn>\n"
        "{% endif %}"
        "{% if message.role == 'assistant' and message.content is not none %}"
        "<start_of_turn>model\n{{ message.content }}<end_of_turn>\n"
        "{% endif %}"
        "{% endfor %}"
        "{% if add_generation_prompt %}<start_of_turn>model\n{% endif %}"
    )


_OCR_PROMPT = (
    "这是一页剧本的照片。请把照片里的剧本内容逐字转成纯文本，"
    "保留场次号、角色名、对白与舞台指示的原始换行格式，每句对白单独成行。"
    "只输出剧本正文，忽略屏幕上的系统通知/水印/界面文字，不要任何解释。"
)


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

    print(f">>> 新 session：text={Path(model).name}  mmproj={Path(mmproj).name}  mode={mode}")
    handler = Gemma3ChatHandler(clip_model_path=mmproj, verbose=False)
    llm = Llama(
        model_path=model,
        chat_handler=handler,
        n_ctx=N_CTX,
        n_gpu_layers=-1,
        seed=42,
        verbose=False,
    )

    if mode == "struct":
        # gemma 无 system role：把 3.B 的 schema prompt 拼进 user text
        user_text = (
            _build_system_prompt()
            + "\n\n解析这页剧本照片，按上述 JSON 格式输出，忽略屏幕系统通知/界面文字。"
        )
        max_tokens = 4096
    else:
        user_text = _OCR_PROMPT
        max_tokens = 2048

    messages = [
        {"role": "user", "content": [
            {"type": "text", "text": user_text},
            {"type": "image_url", "image_url": {"url": _data_uri(image)}},
        ]},
    ]

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
