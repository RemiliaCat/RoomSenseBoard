# -*- encoding: utf-8 -*-
r'''
    ____                       _____
   / __ \____  ____  ____ ___ / ___/___  ____  ________
  / /_/ / __ \/ __ \/ __ `__ \\__ \/ _ \/ __ \/ ___/ _ \
 / _, _/ /_/ / /_/ / / / / / /__/ /  __/ / / (__  )  __/
/_/ |_|\____/\____/_/ /_/ /_/____/\___/_/ /_/____/\___/

@File      :   roomsense/api/ingest.py
@Author    :   RmCat
@Contact   :   RemiliaCatNero@gmail.com
@License   :   AGPL
@Copyright :   (C) 2026, RoomSense-Team
@Desc      :   设备平面：按设备密钥鉴权接收节点上报。
'''

from __future__ import annotations

import hmac
from typing import Any, Callable, Mapping

from ..core.gateway import DeviceGateway, ValidationError
from .errors import ApiError

INGEST_PATH = '/api/ingest'


class DeviceIngestApi:
    def __init__(self, gateway: DeviceGateway, device_key: str) -> None:
        self.gateway = gateway
        self.device_key = device_key

    @property
    def paths(self) -> tuple[str, ...]:
        return (INGEST_PATH,)

    def handle(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        read_json: Callable[[], dict[str, Any]],
    ) -> dict[str, Any] | None:
        '''命中设备路由时返回响应体，否则返回 None 交回管理平面。'''
        if path != INGEST_PATH:
            return None
        if method != 'POST':
            raise ApiError(405, '设备上报只接受 POST')
        if not self._authorized(headers):
            raise ApiError(401, '设备密钥不正确')
        payload = read_json()
        try:
            return self.gateway.submit(payload, source='http')
        except ValidationError as error:
            raise ApiError(400, str(error)) from error

    def _authorized(self, headers: Mapping[str, str]) -> bool:
        key = str(headers.get('X-Device-Key') or '').strip()
        if not key:
            authorization = str(headers.get('Authorization') or '')
            if authorization.lower().startswith('bearer '):
                key = authorization[7:].strip()
        if not key or not self.device_key:
            return False
        return hmac.compare_digest(key, self.device_key)
