"""FastAPI app 工厂（1.I 切片 A REST 基座 + 切片 B WS 转发 + 1.J-1.L GET 端点 / llm.status）。

create_app(orchestrator, llm_service=None) → FastAPI 实例：
  - CORS 中间件（hackathon 现场 dev 友好，允许全部 origin）
  - GET /healthz（无鉴权）返 200
  - orchestrator 存 app.state.orchestrator，供路由读取
  - llm_service 存 app.state.llm_service（可选，lifespan shutdown 时 aclose）
  - admin_token 在构造时解析（resolve_admin_token）存 app.state.admin_token
  - 挂载 takes 路由（/api/v1/take/start | /take/end | GET /takes | GET /takes/{id}）
  - ConnectionManager 存 app.state.connection_manager（切片 B）
  - 挂载 /ws 路由（切片 B）
  - lifespan startup：设 loop ref + 把 cm.broadcast 订阅到 orchestrator 的
    asr.* + take.changed + llm.status（切片 B + 1.J-1.L）
  - lifespan shutdown：await llm_service.aclose()（codex P7，避免 _worker_task 泄漏）
  - SOUNDSPEED_DEV=1 时额外挂载 /api/v1/debug/asr（dev 合成 ASR 注入，1.J 验收用）

CORS allow_credentials=False：admin 走 Bearer header 不用 cookie credentials，
wildcard origin（allow_origins=["*"]）+ allow_credentials=True 本就矛盾
（浏览器会拒），这里改 False 修正配置。
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.auth import resolve_admin_token
from backend.api.routes.takes import router as takes_router
from backend.api.ws import ConnectionManager
from backend.api.ws import router as ws_router
from backend.core.events import (
    ASR_FINAL_CH1,
    ASR_FINAL_CH2,
    ASR_PARTIAL_CH1,
    ASR_PARTIAL_CH2,
    LLM_STATUS,
    TAKE_CHANGED,
)
from backend.core.orchestrator import Orchestrator


def create_app(orchestrator: Orchestrator, llm_service: Any = None) -> FastAPI:
    """构造 FastAPI app，注入 orchestrator 与可选的 llm_service。

    admin_token / SOUNDSPEED_DEV 在此解析（不在 import 时），保证测试 monkeypatch.setenv 生效。
    llm_service 不为 None 时：存 app.state.llm_service；lifespan shutdown 时 await aclose()。
    SOUNDSPEED_DEV=1 时挂载 /api/v1/debug/asr（dev 合成 ASR 注入端点）。
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """startup：记录 event loop 引用 + 把 CM 订阅到 orchestrator 转发 topic。

        必须在 lifespan（而非 create_app）里 set_loop / subscribe：只有 ASGI 服务端
        启动后才有 running loop，且 TestClient 仅在 `with` 块触发 lifespan。
        """
        cm: ConnectionManager = app.state.connection_manager
        cm.set_loop(asyncio.get_running_loop())
        orch: Orchestrator = app.state.orchestrator
        for topic in (
            ASR_PARTIAL_CH1,
            ASR_PARTIAL_CH2,
            ASR_FINAL_CH1,
            ASR_FINAL_CH2,
            TAKE_CHANGED,
            LLM_STATUS,
        ):
            # t=topic 默认参数闭包绑定：避免循环变量后期被覆盖，每个 handler 锁定各自
            # topic。用具名函数而非 lambda——带默认参数的 lambda 传给 Callable 时
            # mypy 无法推断类型（已知限制），具名函数可正常推断，免去 type: ignore。
            def _forward(p: object, t: str = topic) -> None:
                cm.broadcast(t, p)

            orch.subscribe(topic, _forward)
        yield
        # shutdown：清 loop 引用。loop 停后若仍有同步 handler 触发 broadcast
        # （后台线程尚未收束），_loop is None 守卫使其安全 no-op，防 coroutine 泄漏。
        cm.set_loop(None)
        # codex P7：lifespan shutdown 时 await llm_service.aclose()，避免 _worker_task 泄漏。
        if app.state.llm_service is not None:
            await app.state.llm_service.aclose()

    app = FastAPI(title="Soundspeed API", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.orchestrator = orchestrator
    app.state.llm_service = llm_service
    app.state.admin_token = resolve_admin_token()
    app.state.connection_manager = ConnectionManager()

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """健康检查（无鉴权）。"""
        return {"status": "ok"}

    app.include_router(takes_router)
    app.include_router(ws_router)

    # dev-only：SOUNDSPEED_DEV=1 时挂载合成 ASR 注入端点（1.C 落地前验收 1.J transcript 面板）。
    # 在 create_app 调用时读 env（不在 import 时），保证测试 monkeypatch.setenv 生效。
    if os.environ.get("SOUNDSPEED_DEV") == "1":
        from backend.api.routes.debug import router as debug_router  # noqa: PLC0415

        app.include_router(debug_router)

    return app
