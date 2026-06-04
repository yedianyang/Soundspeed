#!/usr/bin/env python
"""3.G 拍照路径探针：Gemma4 原生多模态直读剧本照片（不接外部 OCR 模块）。

用户要求：OCR 用 Gemma4 原生视觉能力，每次调用新上下文 session。

机制：text gguf + mmproj（vision projector）经 llama-cpp 的 mtmd 接口，图片直喂模型。
  --mode ocr     图片 → 纯文本（Gemma4 当 OCR，输出可再喂 sp_material_probe 走 3.B）
  --mode struct  图片 → 直接结构化 JSON（图片一步到 ParsedScene，跳过中间文本）

两个关键修复（2026-06-04，真模型实测）：
  1) 模板：llama-cpp-python 0.3.23 无 gemma vision handler，直接用 Llava15ChatHandler
     套 vicuna 模板 → gemma4 幻觉。Gemma4ChatHandler 只 override CHAT_FORMAT 为 gemma
     模板（<start_of_turn>user/model），mtmd 图像处理全复用（libmtmd 原生支持 gemma4
     vision，image marker 由 mtmd_default_marker 统一处理）。
  2) 分辨率：gemma4 vision 有 5 档 token 预算——70/140(分类)、280/560(通用聊天)、
     1120(OCR/文档/小字)。默认 image_min/max_tokens=-1 走 280 档(~266 token)，密集
     中文小字读不清(磨→静)。OCR 必须 override _init_mtmd_context 设 image_max_tokens
     =1120(4 倍分辨率)。gemma4 vision 用 non-causal attention，1120 token 要在单个
     ubatch 内，故 Llama 需 n_batch=n_ubatch=2048。llama.cpp **无 pan&scan**，token 档
     是唯一 scaling 机制（不必应用层切块）。

评估：转录/结构质量 + 图像 token 用量（vision token 占 n_ctx 多少）。

用法（worktree 根目录）：
  GEMMA_MODEL_PATH=/Users/yedianyang/Documents/GitHub/Soundspeed/models/gemma-4-E4B-it-Q4_K_M.gguf \\
  GEMMA_MMPROJ_PATH=/Users/yedianyang/.cache/huggingface/hub/models--unsloth--gemma-4-E4B-it-GGUF/snapshots/653803f092503c04a65164346f3208a36e707693/mmproj-F16.gguf \\
  [GEMMA_IMAGE_TOKENS=1120] \\
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


class Gemma4ChatHandler(Llava15ChatHandler):
    """Gemma4 原生多模态 chat handler（libmtmd 原生支持 gemma4 vision）。

    复用 Llava15ChatHandler 的 mtmd 图像处理（bitmap / media_marker / chunk eval），
    两处 override：
    - CHAT_FORMAT → gemma 模板（Llava 的 vicuna 模板会让 gemma4 幻觉）。
    - _init_mtmd_context → 设 image_min/max_tokens=IMAGE_TOKENS（默认 1120=OCR 档）。
      gemma4 vision 默认走 280 档(~266 token)，密集小字读不清；OCR 要 1120 档。
    """

    DEFAULT_SYSTEM_MESSAGE = None  # gemma 无 system role，调用方把 system 拼进 user

    IMAGE_TOKENS = 1120  # gemma4 OCR 档（vs 默认 280；70/140/280/560/1120 五档）

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

    def _init_mtmd_context(self, llama_model) -> None:  # noqa: ANN001
        """复制父类逻辑，额外设 image token 预算档（OCR 需要 1120）。"""
        if self.mtmd_ctx is not None:
            return
        ctx_params = self._mtmd_cpp.mtmd_context_params_default()
        ctx_params.use_gpu = True
        ctx_params.print_timings = self.verbose
        ctx_params.n_threads = llama_model.n_threads
        ctx_params.image_min_tokens = self.IMAGE_TOKENS
        ctx_params.image_max_tokens = self.IMAGE_TOKENS
        self.mtmd_ctx = self._mtmd_cpp.mtmd_init_from_file(
            self.clip_model_path.encode(), llama_model.model, ctx_params
        )
        if self.mtmd_ctx is None:
            raise ValueError(f"Failed to load mtmd context from: {self.clip_model_path}")
        if not self._mtmd_cpp.mtmd_support_vision(self.mtmd_ctx):
            raise ValueError("Vision is not supported by this model")

        def mtmd_free() -> None:
            if self.mtmd_ctx is not None:
                self._mtmd_cpp.mtmd_free(self.mtmd_ctx)
                self.mtmd_ctx = None

        self._exit_stack.callback(mtmd_free)


def _ocr_prompt() -> str:
    """OCR prompt。可选注入 cast-list（GEMMA_OCR_CAST），帮模型认准剧本专有名词
    （生僻人名 4B 会拉向常见字，如 枯禅→桔神；提供已知角色名是高天花板纠正）。"""
    base = (
        "这是一页剧本的照片。请把照片里的剧本内容逐字转成纯文本，"
        "保留场次号、角色名、对白与舞台指示的原始换行格式，每句对白单独成行。"
        "只输出剧本正文，忽略屏幕上的系统通知/水印/界面文字，不要任何解释。"
    )
    cast = os.environ.get("GEMMA_OCR_CAST", "").strip()
    if cast:
        base = f"本剧角色与专有名词（遇到时以此为准）：{cast}。\n" + base
    return base


def _data_uri(path: str) -> str:
    raw = Path(path).read_bytes()
    ext = Path(path).suffix.lower().lstrip(".") or "jpeg"
    if ext == "jpg":
        ext = "jpeg"
    return f"data:image/{ext};base64,{base64.b64encode(raw).decode()}"


def main() -> None:
    model = os.environ.get("GEMMA_MODEL_PATH")
    mmproj = os.environ.get("GEMMA_MMPROJ_PATH")
    img_tokens = int(os.environ.get("GEMMA_IMAGE_TOKENS", "1120"))
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

    print(
        f">>> 新 session：text={Path(model).name}  mmproj={Path(mmproj).name}  "
        f"mode={mode}  image_tokens={img_tokens}"
    )
    handler = Gemma4ChatHandler(clip_model_path=mmproj, verbose=False)
    handler.IMAGE_TOKENS = img_tokens
    llm = Llama(
        model_path=model,
        chat_handler=handler,
        n_ctx=N_CTX,
        n_batch=2048,    # gemma4 vision non-causal：image token 要在单 ubatch 内
        n_ubatch=2048,
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
        user_text = _ocr_prompt()
        max_tokens = 2048

    messages = [
        {"role": "user", "content": [
            {"type": "text", "text": user_text},
            {"type": "image_url", "image_url": {"url": _data_uri(image)}},
        ]},
    ]

    resp = llm.create_chat_completion(
        messages=messages, max_tokens=max_tokens, temperature=0.1, repeat_penalty=1.3
    )
    content = resp["choices"][0]["message"]["content"]
    u = resp.get("usage") or {}
    pin, pout = int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))

    print(f"\n{'=' * 72}\n图片: {image}  ({Path(image).stat().st_size // 1024} KB)")
    print(f"模式: {mode}  image_tokens 档={img_tokens}")
    print(f"\n--- Gemma4 输出 ---\n{content}\n--- /输出 ---")
    flag = "  ⚠ 撞顶" if pin + pout >= N_CTX else ""
    print(
        f"\nTOKEN: in={pin}（含图像 vision token）  out={pout}  total={pin + pout}{flag}"
        f"\n  注：in 的绝大部分=图像编码的 vision token（档={img_tokens}），即一张照片占 n_ctx 的量。"
    )


if __name__ == "__main__":
    main()
