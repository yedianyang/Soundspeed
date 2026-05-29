"""可测试的 app 装配工厂（1.J-1.L §2.3 + v0.3 §2.5）。

build_app() 读 env 并完成 DAL + LLMService + Orchestrator + FastAPI 装配，返回 app。
不调用 uvicorn.run——这样测试可以直接 import build_app() 通过 TestClient 验证。

__main__.py 从此模块 import build_app()，再调 uvicorn.run()。

DEV 自动播种（仅在此函数，不在 create_app）：
  SOUNDSPEED_DEV=1 + DB 为空 → 自动 create_scene("Scene_1") + set_active_scene。
  幂等：list_scenes() 非空时跳过，持久 DB 重启不重复播种。
  播种落在 create_orchestrator 之前，确保 orchestrator.dal 与 app 共用同一连接。
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI

from backend.api.app import create_app
from backend.core.orchestrator import create_orchestrator
from backend.db.dal import DAL
from backend.llm.service import get_service


def build_app() -> FastAPI:
    """装配完整 FastAPI app 并返回（不启动 uvicorn）。

    读取 env：
      SOUNDSPEED_DB  数据库文件路径（默认 ./soundspeed.db）
      ADMIN_TOKEN    管理员 token（缺失则 resolve_admin_token 随机生成）
      SOUNDSPEED_DEV dev 模式（=1 时挂载 /api/v1/debug/asr + 自动播种 active scene）

    llm_service 使用 get_service() 单例（codex P6），lazy 不触发模型加载。
    create_orchestrator 自动绑定 run_l2_take（llm_service 非 None 时）。
    """
    db_path = Path(os.environ.get("SOUNDSPEED_DB", "./soundspeed.db"))
    dal = DAL(db_path)

    # DEV 自动播种：保证 dev server 启动后即有 active scene，1.K 可直接 take/start。
    # 仅在 SOUNDSPEED_DEV=1 且 DB 为空时执行（幂等，重启不重复播种）。
    if os.environ.get("SOUNDSPEED_DEV") == "1" and not dal.list_scenes():
        seed_id = dal.create_scene("Scene_1")
        dal.set_active_scene(seed_id)

    llm_service = get_service()
    orchestrator = create_orchestrator(dal, llm_service=llm_service)
    return create_app(orchestrator, llm_service=llm_service)
