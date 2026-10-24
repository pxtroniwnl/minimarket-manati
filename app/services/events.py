"""Pub/sub en memoria para SSE. Requiere un solo proceso (uvicorn con 1 worker)."""

import asyncio
import json
import threading
from dataclasses import dataclass


@dataclass
class Event:
    kind: str  # new_order | new_message | status_changed | sim_message
    data: dict

    def sse(self) -> str:
        return f"event: {self.kind}\ndata: {json.dumps(self.data)}\n\n"


class Broker:
    def __init__(self):
        self._subs: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = set()
        self._lock = threading.Lock()

    def subscribe(self) -> tuple[asyncio.AbstractEventLoop, asyncio.Queue]:
        sub = (asyncio.get_running_loop(), asyncio.Queue(maxsize=100))
        with self._lock:
            self._subs.add(sub)
        return sub

    def unsubscribe(self, sub) -> None:
        with self._lock:
            self._subs.discard(sub)

    def publish(self, kind: str, **data) -> None:
        """Se puede llamar desde cualquier hilo (endpoints sync corren en un threadpool)."""
        event = Event(kind, data)
        with self._lock:
            subs = list(self._subs)
        for loop, queue in subs:
            loop.call_soon_threadsafe(_put, queue, event)


def _put(queue: asyncio.Queue, event: Event) -> None:
    if not queue.full():
        queue.put_nowait(event)


broker = Broker()
