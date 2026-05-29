"""python -m backend.api 启动入口。

读取 env：
  SOUNDSPEED_DB  数据库文件路径（默认 ./soundspeed.db）
  ADMIN_TOKEN    管理员 token（缺失则随机生成 + console 打印）
  HOST           监听地址（默认 0.0.0.0）
  PORT           监听端口（默认 8000）
  SOUNDSPEED_DEV dev 模式（=1 时挂载 /api/v1/debug/asr）

跨平台（pathlib + env，无 shell 分支）。
"""
from __future__ import annotations

import os

if __name__ == "__main__":
    import uvicorn

    from backend.api.entrypoint import build_app

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(build_app(), host=host, port=port)
