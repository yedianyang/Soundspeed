#!/bin/bash
# SessionStart hook (source=compact)
# 压缩后注入 "你有 teammates" 的上下文提醒
#
# 输入：stdin JSON 包含 source 字段
# 输出：additionalContext JSON（Claude 会看到并响应）

INPUT=$(cat)
SOURCE=$(echo "$INPUT" | grep -o '"source":"[^"]*"' | cut -d'"' -f4)

# 只在 compact 后触发
if [ "$SOURCE" = "compact" ]; then
  cat << 'EOF'
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "⚠️ Context was just compacted. You are the Team Lead. IMPORTANT REMINDERS:\n\n1. You have 4 teammates: generator, frontend, quality, docs. Do NOT write code yourself.\n2. To delegate work: use TaskCreate to create a task, then SendMessage to notify the teammate.\n3. To check progress: use TaskList.\n4. To communicate: SendMessage(to='generator'/'frontend'/'quality'/'docs', message='...').\n5. Your only job is: coordinate, delegate, track. Never implement.\n\nPlease run TaskList now to see current task status."
  }
}
EOF
fi

exit 0
