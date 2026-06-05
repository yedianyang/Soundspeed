"""TASK_CONFIG 映射：每个 task_type 的生成参数与元数据。

来源：llm-service-design v1.1 §Task Config 映射。
字段语义见 1.F 实施 spec §5。

注：system prompt 模板由 Pipeline 在构建 messages 时插入（role="system"），
TASK_CONFIG 的 system 字段仅作参考模板，service 层不自动注入。

tools / tool_choice 字段（Tier 1 function calling）：
  service 层会把不在 _META_KEYS 的字段透传给 client.create_chat_completion。
  tools 和 tool_choice 故意不加入 _META_KEYS，由 infer_tool 透传。
  l2_take 的 tools 取自 build_l2_tool()（backend/llm/tools/script.py），该构造器
  只依赖中性的 l2_constants（不 import config / l2_take），无循环 import 风险，
  故此处 module 级直接 import。
  note_struct 的 tools 取自 build_note_tool()（backend/llm/tools/note.py），该构造器
  会 lazy import backend.pipelines.np_note（取 category enum），module 级 eager import
  会触发 config → tools.note → pipelines → config 循环，故经 _build_note_task_config()
  函数级 lazy import 构造。
"""

from backend.llm.tools.script import build_l2_no_script_tool, build_l2_tool
from backend.llm.tools.transcript import build_qp_tools


def _build_note_task_config() -> dict:
    """构造 note_struct 配置，含 tools/tool_choice（文本/语音 NP forced tool-call，对标 l2_take）。

    note 短（512 token 够）；system 仅参考模板，真 system prompt 由 run_np_note/run_np_voice 组装。
    build_note_tool 函数级 lazy import，避免 config → tools.note → pipelines → config 循环。
    """
    from backend.llm.tools.note import NOTE_TOOL_NAME, build_note_tool  # noqa: PLC0415

    return {
        "max_tokens": 512,
        "temperature": 0.2,
        "priority": 2,
        "system": "将录音师备注归置到正确 take 并结构化为 take_id/category/content。",
        # Tier 1 function calling：强制走 structure_note 工具
        "tools": [build_note_tool()],
        "tool_choice": {
            "type": "function",
            "function": {"name": NOTE_TOOL_NAME},
        },
    }


# task_type -> 配置字典
# 字段：max_tokens, temperature, priority, system, _reserved（可选）；
# l2_take 含 tools/tool_choice（Tier 1 forced FC）；query_session 含 tools/tool_choice（Tier 2 auto 路由）。
TASK_CONFIG: dict[str, dict] = {
    "query_session": {
        "max_tokens": 1024,
        "temperature": 0.3,
        "priority": 1,
        "system": "你是一个场记查询助手，帮助导演和录音师快速查找场记信息。",
        # QP Tier 2 多工具 auto 路由（D-QP-09）。已 rebase 到含 4.x 的 main：
        # query_session/l2_take 仍 eager（只有 note_struct 因 np_note 依赖才 lazy），
        # transcript.py import-neutral，eager 挂 build_qp_tools() 安全，无须 lazy。
        "tools": build_qp_tools(),
        "tool_choice": "auto",
    },
    "l2_take": {
        # v0.2 schema 含 corrected_segments，实测短 take 输出 ~2000 token；
        # 长 take（剧本 100+ 行 / 5+ 分钟转录）需要 4096 上限保底。
        # 配合 n_ctx=8192（client.py），input ~3000 + output 4096 仍有余量。
        "max_tokens": 4096,
        "temperature": 0.2,
        "priority": 2,
        # prompt v1：hill-climb 最优（combined 0.465），见 experiments/2026-05-28-asr-publisher-smoke/prompt_autoresearch/log.md
        "system": (
            "整合 take 信息，生成剧本 diff 和摘要。\n\n"
            "职责：\n"
            "1. 剧本偏差检测：对比剧本台词与转录记录，识别漏词/改词/加词。\n"
            "2. 错别字修正：检查转录文本中的明显错别字（同音字、形近字误识别），输出修正结果到 corrected_segments 字段。\n\n"
            "输出格式要求（严格遵守）：\n"
            "- 只输出合法 JSON，不要 markdown 代码块，不要注释，不要额外解释。\n"
            "- JSON schema：\n"
            "  {\n"
            '    "script_diff_summary": "<str 或 null>",\n'
            '    "line_matches": [\n'
            '      {"line_no": <int>, "diff_type": "<match|missing|substitution|insertion>", "detail": "<str 或 null>"}\n'
            "    ],\n"
            '    "corrected_segments": [\n'
            '      {"idx": <int>, "original": "<str>", "corrected": "<str>"}\n'
            "    ]\n"
            "  }\n"
            "- line_matches 只列出 script_lines 提供的行，不自创行号。\n"
            "- insertion 类型（演员台词剧本无对应行）line_no 必须填 -1，禁止填剧本行号。\n"
            "- missing 类型 detail 必须为 null，禁止填任何字符串。\n"
            "- match 类型 detail 必须为 null。\n"
            "- corrected_segments 每条的 corrected 必须是修正后的字符串，禁止为 null；无法确认修正时直接不输出该条。\n"
            "- corrected_segments 只列出真正有修改的 segment，未改动的不出现；无需修正时输出空列表 []。\n"
            "- idx 是转录记录列表的下标（从 0 开始），对应 user message 中转录记录前的序号。"
        ),
        # Tier 1 function calling（spec §6.1 D-FC-01）
        "tools": [build_l2_tool()],
        "tool_choice": {
            "type": "function",
            "function": {"name": "report_script_analysis"},
        },
    },
    "l2_take_no_script": {
        # 无剧本只做纠错，输出更短，2048 上限足够
        "max_tokens": 2048,
        "temperature": 0.2,
        "priority": 2,
        "system": (
            "整合 take 信息，修正转录错别字。\n\n"
            "职责：\n"
            "检查转录文本中的明显错别字（同音字、形近字误识别），输出修正结果。\n\n"
            "输出格式要求（严格遵守）：\n"
            "- 只调用工具，不额外输出文字。\n"
            "- corrected_segments 只列出真正有修改的 segment，未改动的不出现；无需修正时输出空列表 []。\n"
            "- corrected 必须是修正后的字符串，禁止为 null；无法确认修正时直接不输出该条。\n"
            "- idx 是转录记录列表的下标（从 0 开始），对应 user message 中转录记录前的序号。"
        ),
        "tools": [build_l2_no_script_tool()],
        "tool_choice": {
            "type": "function",
            "function": {"name": "report_corrections_only"},
        },
    },
    "script_parse": {
        "max_tokens": 2048,
        "temperature": 0.1,
        "priority": 3,
        # TODO(1.G): 接入时按剧本结构化需求细化 system prompt
        "system": "将剧本解析为结构化 JSON。",
    },
    # note_struct：带 tools + 强制 tool_choice，文本（run_np_note/infer_tool）与语音
    # （run_np_voice/infer_voice_tool）NP 共用——两者都走 forced tool-call，无 content-mode 调用。
    "note_struct": _build_note_task_config(),
    "agent_init": {
        "_reserved": True,
        "max_tokens": 1024,
        "temperature": 0.3,
        "priority": 3,
        # TODO(agent_init 落地时): 补充 5 轮循环 Agent 的 system prompt
        # TASK_CONFIG system 模板需 agent_init 落地时补，当前为占位符
        "system": "You are a helpful assistant.",
    },
}
