# -*- encoding: utf-8 -*-
r'''
    ____                       _____
   / __ \____  ____  ____ ___ / ___/___  ____  ________
  / /_/ / __ \/ __ \/ __ `__ \\__ \/ _ \/ __ \/ ___/ _ \
 / _, _/ /_/ / /_/ / / / / / /__/ /  __/ / / (__  )  __/
/_/ |_|\____/\____/_/ /_/ /_/____/\___/_/ /_/____/\___/

@File      :   roomsense/app.py
@Author    :   RmCat
@Contact   :   RemiliaCatNero@gmail.com
@License   :   AGPL
@Copyright :   (C) 2026, RoomSense-Team
@Desc      :   应用装配：把存储、网关、账号、模拟器与维护任务组装成可启动的 App。
'''

from __future__ import annotations

import threading
import time
from typing import Any

from . import __version__
from .api.ingest import DeviceIngestApi
from .config import read_or_create_secret, write_secret
from .core.auth import AccountStore, LoginThrottle, SessionStore, generate_password
from .core.bus import EventBus
from .core.gateway import DeviceGateway, default_runtime_settings
from .core.simulator import Simulator
from .core.storage import Storage


class Maintenance(threading.Thread):
    def __init__(self, app: 'App', interval: float = 5.0, prune_interval: float = 3600.0) -> None:
        super().__init__(name='roomsense-maintenance', daemon=True)
        self.app = app
        self.interval = interval
        self.prune_interval = prune_interval
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:  # pragma: no cover - 后台循环
        last_prune = 0.0
        while not self._stop_event.wait(self.interval):
            try:
                self.app.gateway.check_offline()
            except Exception as error:
                self.app.storage.add_log('ERROR', 'maintenance', f'离线检测失败：{error}')
            if time.time() - last_prune >= self.prune_interval:
                last_prune = time.time()
                self.app.prune()


class App:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.version = __version__
        self.started_at = time.time()
        self.storage = Storage(config['storage']['db_path'])
        self.bus = EventBus()

        server = config['server']
        self.accounts = AccountStore(server['accounts_path'])
        self.sessions = SessionStore(ttl=float(server.get('session_hours', 12)) * 3600)
        self.throttle = LoginThrottle()
        self.admin_user, self.initial_password = self.accounts.ensure_user(
            str(server.get('admin_user') or 'admin'),
            server.get('admin_password'),
        )
        self.device_key = read_or_create_secret(config['device']['key_path'], length=24)

        defaults = default_runtime_settings(config)
        self.settings: dict[str, Any] = self.storage.get_runtime_settings(defaults)
        self.gateway = DeviceGateway(self.storage, self.bus, lambda: dict(self.settings))
        self.ingest_api = DeviceIngestApi(self.gateway, self.device_key)

        simulator_config = config.get('simulator') or {}
        self.simulator: Simulator | None = None
        if simulator_config.get('enable') and simulator_config.get('devices'):
            self.simulator = Simulator(
                self.gateway,
                simulator_config['devices'],
                interval=float(simulator_config.get('interval', 5.0)),
                seed=simulator_config.get('seed'),
                offline_demo=bool(simulator_config.get('offline_demo', True)),
            )
        self.maintenance = Maintenance(self)
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self.storage.add_log(
            'INFO',
            'server',
            f"{self.config['server'].get('name', 'RoomSenseBoard')} v{self.version} 已启动",
        )
        self.maintenance.start()
        if self.simulator is not None:
            self.simulator.start()
            self.storage.add_log(
                'INFO', 'simulator', f'模拟器已启动，共 {self.simulator.device_count} 个虚拟节点'
            )

    def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        if self.simulator is not None:
            self.simulator.stop()
            self.simulator.join(timeout=3)
        self.maintenance.stop()
        self.maintenance.join(timeout=3)
        self.storage.add_log('INFO', 'server', '服务已停止')
        self.storage.close()

    def reset_password(self, username: str | None = None) -> tuple[str, str]:
        target = username or self.admin_user
        if self.accounts.find(target) is None:
            raise ValueError(f'账号不存在：{target}')
        password = generate_password()
        self.accounts.set_password(target, password)
        self.sessions.revoke_user(target)
        self.storage.add_log('WARN', 'auth', f'账号 {target} 的密码已被重置')
        return target, password

    def runtime_settings(self) -> dict[str, Any]:
        return dict(self.settings)

    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        updated = self.gateway.update_settings(patch)
        merged = dict(self.settings)
        merged.update(updated)
        self.settings = merged
        return self.runtime_settings()

    def regenerate_device_key(self) -> str:
        self.device_key = write_secret(self.config['device']['key_path'], length=24)
        self.ingest_api.device_key = self.device_key
        self.storage.add_log('WARN', 'settings', '节点接入密钥已重新生成，旧密钥立即失效')
        self.bus.publish('device_key', {'regenerated': True})
        return self.device_key

    def prune(self) -> dict[str, int]:
        result = self.storage.prune(int(self.settings.get('retention_days', 30)))
        self.storage.prune_logs(20000)
        return result

    def status(self) -> dict[str, Any]:
        stats = self.storage.stats(offline_timeout=int(self.settings.get('offline_timeout', 90)))
        return {
            'ok': True,
            'name': self.config['server'].get('name', 'RoomSenseBoard'),
            'version': self.version,
            'server_time': int(time.time()),
            'uptime': int(time.time() - self.started_at),
            'simulator': bool(self.simulator),
            'streams': self.bus.subscriber_count,
            'sessions': self.sessions.count,
            'accounts': len(self.accounts.usernames()),
            'stats': stats,
        }
