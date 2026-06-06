"""TASK_CONFIG 映射：每个 task_type 的生成参数与元数据。

来源：llm-service-design v1.1 §Task Config 映射。
字段语义见 1.F 实施 spec §5。

注：system prompt 模板由 Pipeline 在构建 messages 时插入（role="system"），
TASK_CONFIG 的 system 字段仅作参考模板，service 层不自动注入。

剧本解析（script_parse）为何不用 grammar / response_format（2026-06-06 实测拍板）：
  实测 grammar（GBNF 约束采样）在 Gemma（~25 万词表）上每 token 多花大量 CPU →
  吞吐从 ~84 tok/s 掉到 ~15 tok/s（5.6×）。故解析热路径**不用 grammar**，改 classify：
  Gemma 只输出"每行说话人"短数组，台词由代码从原文取（见 sp_script.parse_scene_block）。
  容错解析 + 兜底（解析失败 → 该场全部按描述，不崩）。
  tool-calling/grammar 留给只调一两次的路由 / 场记分析，不进逐场热循环。
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
        # 完整输出（v5，无 grammar）：模型逐行吐 [说话人,台词]。比 classify 准（边吐台词边
        # 判断动作/对白，实测含人名的描述行也能标对）；无 grammar 故快。max_tokens 给足防截断。
        "max_tokens": 4096,
        "temperature": 0.1,
        "priority": 3,
        # 不设 response_format：grammar 在 Gemma 上每 token CPU 开销大（实测 5.6× 慢），不用。
        # prompt v5：强调"即使句子含人名，叙述动作/神态的也是描述"——这是 classify 误判的点。
        "system": (
            "你是剧本解析器。逐行把剧本解析成 [说话人, 内容] 的数组。\n"
            "- 对白行 → [\"角色名\", \"台词\"]\n"
            "- 非对白行（动作、场景描述、舞台指示，即使句子里出现人名）→ [\"\", \"原文\"]\n"
            "判断依据：有「角色：台词」形式、或明显是某人说出口的话，才算对白；"
            "叙述某人动作/神态/场景的是描述。\n"
            "只输出一个 JSON 数组，不要解释。\n\n"
            "示例输入：\n罗湘：我们先聊聊。\n罗湘走到窗边。\n\n"
            "示例输出：\n"
            '[["罗湘", "我们先聊聊。"], ["", "罗湘走到窗边。"]]'
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
