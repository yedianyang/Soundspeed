#!/bin/bash
# PreToolUse hook: prevent main (lead) agent from writing code.
# Subagents (generator/frontend/quality/docs) are allowed.
#
# Input: JSON on stdin with tool_name, agent_id, agent_type, etc.
# Output: JSON with permissionDecision if blocking, or nothing to allow.

INPUT=$(cat)
AGENT_ID=$(echo "$INPUT" | jq -r '.agent_id // empty')

# If agent_id is set, this is a subagent — allow
if [ -n "$AGENT_ID" ]; then
  exit 0
fi

# Main agent trying to write code — deny
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name')
jq -n --arg tool "$TOOL_NAME" '{
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    permissionDecision: "deny",
    permissionDecisionReason: ("Lead agent cannot use " + $tool + ". Delegate to a subagent (generator/frontend/quality/docs) via the Agent tool.")
  }
}'
exit 0
