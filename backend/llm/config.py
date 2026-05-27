"""TASK_CONFIG 映射：每个 task_type 的生成参数与元数据。

来源：llm-service-design v1.1 §Task Config 映射。
字段语义见 1.F 实施 spec §5。
"""

# task_type -> 配置字典
# 字段：max_tokens, temperature, priority, system, _reserved（可选）
TASK_CONFIG: dict[str, dict] = {}
