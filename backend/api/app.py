"""FastAPI app 工厂（1.I 切片 A：REST 基座）。

create_app(orchestrator) → FastAPI 实例：
  - CORS 中间件（hackathon 现场 dev 友好，允许全部 origin）
  - GET /healthz（无鉴权）返 200
  - orchestrator 存 app.state.orchestrator，供路由读取
  - admin_token 在构造时解析（resolve_admin_token）存 app.state.admin_token
  - 挂载 takes 路由（/api/v1/take/start | /take/end）

本切片不含 WS（切片 B）：不实现 ws / broadcast / 转发。
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.auth import resolve_admin_token
from backend.api.routes.takes import router as takes_router
from backend.core.orchestrator import Orchestrator


def create_app(orchestrator: Orchestrator) -> FastAPI:
    """构造 FastAPI app，注入 orchestrator。

    admin_token 在此解析（不在 import 时），保证测试 monkeypatch.setenv 生效。
    """
    app = FastAPI(title="Soundspeed API")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.orchestrator = orchestrator
    app.state.admin_token = resolve_admin_token()

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """健康检查（无鉴权）。"""
        return {"status": "ok"}

    app.include_router(takes_router)

    return app
