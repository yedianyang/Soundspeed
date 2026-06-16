---
name: verify-app
description: 真把 Soundspeed 跑起来验 UI 行为 —— 起后端(dev)+前端,用 Playwright 在真浏览器里点。验前端功能时用。仅本地(需模型/浏览器),不进 CI。
---

# verify-app:端到端验 UI

验前端 UI 行为时,光跑 vitest 验不了真交互,按下面跑真浏览器。

## 步骤

1. 起后端(dev,固定 token,挂 /debug/asr):
   `SOUNDSPEED_DEV=1 ADMIN_TOKEN=devtoken uv run python -m backend.api`
   等日志打印 ADMIN_TOKEN、active scene 已 seed。
2. seed 一条 take(绕过真音频,字段/端口以 backend/api/routes/debug.py 为准):
   `curl -s -X POST localhost:8000/api/v1/debug/asr -H "Authorization: Bearer devtoken" -H "Content-Type: application/json" -d '{"ch":0,"text":"测试一条","speaker":"A","is_partial":false}'`
3. 起前端:`pnpm -C frontend dev`(localhost:5173)。
4. 首次跑前装浏览器:`pnpm -C frontend exec playwright install chromium`。
5. 跑 e2e:`pnpm -C frontend test:e2e`。全绿=通过;红=看 trace/截图,打回 implementer。

## 坑
- Gemma 首次加载冻 event loop 数秒,headed 模式确认黄点「Loading」时序。
- token 必须固定 devtoken,否则前端存的 token 失效。
- 真模型权重不进 git、要本地;此 skill 只能本地跑,别进 CI。
