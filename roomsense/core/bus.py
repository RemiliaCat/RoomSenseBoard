# -*- encoding: utf-8 -*-
r'''
    ____                       _____
   / __ \____  ____  ____ ___ / ___/___  ____  ________
  / /_/ / __ \/ __ \/ __ `__ \\__ \/ _ \/ __ \/ ___/ _ \
 / _, _/ /_/ / /_/ / / / / / /__/ /  __/ / / (__  )  __/
/_/ |_|\____/\____/_/ /_/ /_/____/\___/_/ /_/____/\___/

@File      :   roomsense/core/bus.py
@Author    :   RmCat
@Contact   :   RemiliaCatNero@gmail.com
@License   :   AGPL
@Copyright :   (C) 2026, RoomSense-Team
@Desc      :   进程内事件总线，把实时数据推送给 SSE 客户端。
'''

from __future__ import annotations

import json
import queue
import threading
from typing import Any


class Subscriber:
    def __init__(self, limit: int = 256) -> None:
        self._queue: queue.Queue[tuple[str, str] | None] = queue.Queue(maxsize=max(8, limit))

    def offer(self, event: str, data: str) -> None:
        try:
            self._queue.put_nowait((event, data))
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait((event, data))
            except queue.Full:
                pass

    def get(self, timeout: float = 15.0) -> tuple[str, str] | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: set[Subscriber] = set()

    def subscribe(self) -> Subscriber:
        subscriber = Subscriber()
        with self._lock:
            self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)

    def publish(self, event: str, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            subscriber.offer(event, data)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)
