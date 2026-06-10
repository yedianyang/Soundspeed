"""ConnectionManager 广播保序测试。

bug：每条 ASR 消息走独立 run_coroutine_threadsafe 协程、各自 await ws.send_json，背压下并发
send 同一连接 → 乱序 → 前端「替换最后一条 partial」定位错乱、某些段卡早期 partial。
修：send 锁串行化广播，保证到达顺序 == 发送顺序。
"""
import asyncio

import pytest

from backend.api.ws import ConnectionManager
from backend.core.events import ASR_PARTIAL_CH1, AsrPartialPayload


class _SlowWS:
    """假 WebSocket：send_json 延迟与序号反相关 —— 无锁时后发的先完成 → 乱序。"""

    def __init__(self) -> None:
        self.received: list[str] = []

    async def send_json(self, data: dict) -> None:
        i = int(data["payload"]["text"])
        await asyncio.sleep((30 - i) * 0.001)  # 后发睡得短，无锁必抢先
        self.received.append(data["payload"]["text"])


@pytest.mark.asyncio
async def test_broadcast_preserves_order_under_backpressure():
    cm = ConnectionManager()
    cm.set_loop(asyncio.get_running_loop())
    ws = _SlowWS()
    cm._active.add(ws)

    n = 30
    for i in range(n):
        cm.broadcast(
            ASR_PARTIAL_CH1,
            AsrPartialPayload(
                text=str(i), start_frame=0, end_frame=1, speaker=None, take_id=None, is_partial=True
            ),
        )

    for _ in range(200):
        await asyncio.sleep(0.01)
        if len(ws.received) >= n:
            break

    assert ws.received == [str(i) for i in range(n)]  # 严格保序，无乱序
