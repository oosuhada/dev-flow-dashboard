from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DashboardEvent:
    repo: str
    event: str
    action: str | None = None
    number: int | None = None

    def encode(self) -> str:
        payload = {
            "repo": self.repo,
            "event": self.event,
            "action": self.action,
            "number": self.number,
        }
        return json.dumps(payload, separators=(",", ":"))


class EventHub:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[DashboardEvent]] = set()

    def subscribe(self) -> asyncio.Queue[DashboardEvent]:
        queue: asyncio.Queue[DashboardEvent] = asyncio.Queue(maxsize=32)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[DashboardEvent]) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: DashboardEvent) -> None:
        stale: list[asyncio.Queue[DashboardEvent]] = []
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                stale.append(queue)
        for queue in stale:
            self._subscribers.discard(queue)


event_hub = EventHub()
