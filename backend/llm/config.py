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
        "max_tokens": 512,
        "temperature": 0.2,
        "priority": 2,
        # TODO(1.G): 接入时按 L2 整合需求细化 system prompt
        "system": "整合 take 信息，生成剧本 diff 和摘要。",
    },
    "script_parse": {
        "max_tokens": 2048,
        "temperature": 0.1,
        "priority": 3,
        # TODO(1.G): 接入时按剧本结构化需求细化 system prompt
        "system": "将剧本解析为结构化 JSON。",
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
