'''进程内事件总线，用于把实时数据推送给 SSE 客户端。

每个订阅者持有一个有界队列：队列满时丢弃最旧事件，保证慢客户端不会拖住采集与入库。
'''

from __future__ import annotations

import json
import queue
import threading
from typing import Any


class Subscriber:
    '''单个 SSE 连接的订阅句柄。'''

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
        '''取出下一条事件；超时返回 ``None``（调用方应发送保活心跳）。'''
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
    '''极简发布/订阅实现。'''

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
        '''广播事件；事件类型与 JSON 文本只序列化一次。'''
        data = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            subscriber.offer(event, data)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)
