"""TASK_CONFIG 映射：每个 task_type 的生成参数与元数据。

来源：llm-service-design v1.1 §Task Config 映射。
字段语义见 1.F 实施 spec §5。

注：system prompt 模板由 Pipeline 在构建 messages 时插入（role="system"），
TASK_CONFIG 的 system 字段仅作参考模板，service 层不自动注入。
"""

# task_type -> 配置字典
# 字段：max_tokens, temperature, priority, system, _reserved（可选）
TASK_CONFIG: dict[str, dict] = {
    "query_session": {
        "max_tokens": 1024,
        "temperature": 0.3,
        "priority": 1,
        # TODO(1.G): 接入时按实际场记查询需求细化 system prompt
        "system": "你是一个场记查询助手，帮助导演和录音师快速查找场记信息。",
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
    },
    "script_parse": {
        "max_tokens": 2048,
        "temperature": 0.1,
        "priority": 3,
        # prompt v1（3.B）：极简版，只输出 schema + 1 个 few-shot。
        # 4B Gemma 实测越长越乱，不堆细则。
        "system": (
            "你是剧本解析器。把输入的剧本片段解析为 JSON，直接输出合法 JSON，不要 markdown 代码块。\n\n"
            "输出格式：\n"
            '{"scenes": [{"scene_code": "string或null", "slugline": {"int_ext": "string或null", '
            '"time_of_day": "string或null", "location": "string或null"}, '
            '"lines": [{"character": "string或null", "text": "string"}]}]}\n\n'
            "规则：scene_code 是剧本中明确写出的场次号（如「场3」「3A」），没有就填 null。"
            "character 是说话角色名，舞台指示行填 null。\n\n"
            "示例输入：\n"
            "内 咖啡馆 日\n"
            "罗湘：我们先聊聊。\n"
            "（罗湘坐下）\n\n"
            "示例输出：\n"
            '{"scenes": [{"scene_code": null, "slugline": {"int_ext": "内", "time_of_day": "日", '
            '"location": "咖啡馆"}, "lines": [{"character": "罗湘", "text": "我们先聊聊。"}, '
            '{"character": null, "text": "罗湘坐下"}]}]}'
        ),
    },
    "note_struct": {
        "max_tokens": 512,
        "temperature": 0.2,
        "priority": 2,
        # TODO(1.G): 接入时按备注结构化需求细化 system prompt
        "system": "将录音师备注解析为结构化字段。",
    },
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
