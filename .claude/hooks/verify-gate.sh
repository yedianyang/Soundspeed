#!/bin/bash
# Stop hook: 退出前跑确定性验证门。任一红 → decision:block 打回去修。
# 减负:ruff + 前端 lint/test 每次跑(快);backend pytest 只在 backend/ 有改动时跑。
set -uo pipefail
INPUT=$(cat)
ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active // false')
if [ "$ACTIVE" = "true" ]; then exit 0; fi

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT" || exit 0
FAILS=""

run() {
  if ! out=$("${@:2}" 2>&1); then
    FAILS="${FAILS}\n[$1] FAILED:\n$(echo "$out" | tail -n 20)"
  fi
}

# 快门:每次都跑
run "ruff"     uv run ruff check backend/
run "fe-lint"  pnpm -C frontend lint
run "fe-test"  pnpm -C frontend test

# 重门:仅当 backend/ 相对 main 有改动才跑 pytest
BASE=$(git merge-base HEAD main 2>/dev/null || echo main)
if ! git diff --quiet "$BASE" -- backend/ 2>/dev/null; then
  run "pytest" uv run pytest backend/tests -q
fi

if [ -n "$FAILS" ]; then
  REASON=$(printf "验证闸未过,修绿再停:%b" "$FAILS")
  jq -n --arg r "$REASON" '{decision:"block", reason:$r}'
fi
exit 0
