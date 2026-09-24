"""In-process fan-out of engine events to every open browser tab."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

log = logging.getLogger(__name__)

QUEUE_SIZE = 500  # a tab that falls this far behind is dropped; it reconnects and gets a fresh snapshot


class EventBus:
    def __init__(self) -> None:
        self._queues: set[asyncio.Queue[str | None]] = set()

    @property
    def listeners(self) -> int:
        return len(self._queues)

    def subscribe(self) -> asyncio.Queue[str | None]:
        queue: asyncio.Queue[str | None] = asyncio.Queue(QUEUE_SIZE)
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str | None]) -> None:
        self._queues.discard(queue)

    def publish(self, event: str, data: Any) -> None:
        if not self._queues:
            return
        frame = sse_frame(event, data)
        for queue in list(self._queues):
            try:
                queue.put_nowait(frame)
            except asyncio.QueueFull:
                log.info("dropping a browser tab that fell behind")
                self._queues.discard(queue)
                queue.get_nowait()
                queue.put_nowait(None)  # tells the stream to close


def sse_frame(event: str, data: Any) -> str:
    payload = json.dumps(data, separators=(",", ":"), default=str)
    return f"event: {event}\ndata: {payload}\n\n"
