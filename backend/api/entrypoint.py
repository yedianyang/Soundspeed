"""可测试的 app 装配工厂（1.J-1.L §2.3）。

build_app() 读 env 并完成 DAL + LLMService + Orchestrator + FastAPI 装配，返回 app。
不调用 uvicorn.run——这样测试可以直接 import build_app() 通过 TestClient 验证。

__main__.py 从此模块 import build_app()，再调 uvicorn.run()。
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
      SOUNDSPEED_DEV dev 模式（=1 时挂载 /api/v1/debug/asr）

    llm_service 使用 get_service() 单例（codex P6），lazy 不触发模型加载。
    create_orchestrator 自动绑定 run_l2_take（llm_service 非 None 时）。
    """
    db_path = Path(os.environ.get("SOUNDSPEED_DB", "./soundspeed.db"))
    dal = DAL(db_path)
    llm_service = get_service()
    orchestrator = create_orchestrator(dal, llm_service=llm_service)
    return create_app(orchestrator, llm_service=llm_service)
