# -*- encoding: utf-8 -*-
r'''
    ____                       _____
   / __ \____  ____  ____ ___ / ___/___  ____  ________
  / /_/ / __ \/ __ \/ __ `__ \\__ \/ _ \/ __ \/ ___/ _ \
 / _, _/ /_/ / /_/ / / / / / /__/ /  __/ / / (__  )  __/
/_/ |_|\____/\____/_/ /_/ /_/____/\___/_/ /_/____/\___/

@File      :   roomsense/config.py
@Author    :   RmCat
@Contact   :   RemiliaCatNero@gmail.com
@License   :   AGPL
@Copyright :   (C) 2026, RoomSense-Team
@Desc      :   运行期配置：默认值、config.json 合并、环境变量覆盖与路径解析。
'''

from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any


def is_frozen() -> bool:
    return bool(getattr(sys, 'frozen', False))


BASE_DIR = (
    Path(sys.executable).resolve().parent if is_frozen() else Path(__file__).resolve().parent.parent
)

BUNDLE_DIR = Path(getattr(sys, '_MEIPASS', BASE_DIR)).resolve()

DEFAULT_CONFIG: dict[str, Any] = {
    'server': {
        # 打包后的 exe 面向“双击即用”：默认监听所有网卡，首次运行释放的配置也用它；
        # 源码运行仍只监听本机，避免开发机无意中把面板暴露到局域网。
        'host': '0.0.0.0' if is_frozen() else '127.0.0.1',
        'port': 20500,
        # 对外访问地址，留空表示按监听地址或本机网卡推断。
        'public_url': '',
        'accounts_path': './conf/accounts.json',
        'session_hours': 12,
        'cors_origins': [],
        'name': 'RoomSenseBoard',
    },
    'storage': {
        'db_path': './data/roomsense.db',
        'retention_days': 30,
        'live_window_minutes': 25,
    },
    'logging': {
        'console_level': 'INFO',
    },
    'web': {
        'static_dir': './web',
    },
    'device': {
        'key_path': './data/device_key.txt',
        'offline_timeout': 90,
        'max_batch': 256,
    },
    'alerts': {
        'temperature': {
            'low': 18.0,
            'high': 28.0,
            'critical_low': 12.0,
            'critical_high': 32.0,
            'hysteresis': 0.5,
        },
        'humidity': {
            'low': 30.0,
            'high': 70.0,
            'critical_low': 20.0,
            'critical_high': 85.0,
            'hysteresis': 2.0,
        },
        'co2': {
            'high': 1000.0,
            'critical_high': 1500.0,
            'hysteresis': 50.0,
        },
        'pm25': {
            'high': 75.0,
            'critical_high': 150.0,
            'hysteresis': 10.0,
        },
        'noise': {
            'high': 70.0,
            'critical_high': 85.0,
            'hysteresis': 5.0,
        },
        'light': {
            'low': 80.0,
            'critical_low': 30.0,
            'hysteresis': 15.0,
        },
        'battery': {
            'low': 3.5,
            'critical_low': 3.3,
            'hysteresis': 0.05,
        },
    },
    'simulator': {
        # 默认关闭；演示时用 --simulator 或 ROOMSENSE_SIMULATE=1 打开。
        'enable': False,
        'interval': 5.0,
        'seed': 20260926,
        'devices': [
            {'device_id': 'classroom-301', 'name': '301 教室', 'room': '教学楼 3F', 'profile': 'classroom'},
            {'device_id': 'office-201', 'name': '201 办公室', 'room': '行政楼 2F', 'profile': 'office'},
            {
                'device_id': 'meeting-a',
                'name': 'A 会议室',
                'room': '行政楼 1F',
                'profile': 'meeting',
                'offline_demo': True,
            },
            {'device_id': 'lab-3f', 'name': '3F 实验室', 'room': '实验楼 3F', 'profile': 'lab'},
            {'device_id': 'server-room-1f', 'name': '1F 机房', 'room': '信息楼 1F', 'profile': 'server'},
        ],
    },
}


def deep_merge(base: Any, patch: Any) -> Any:
    if isinstance(base, dict) and isinstance(patch, dict):
        merged = dict(base)
        for key, value in patch.items():
            merged[key] = deep_merge(base.get(key), value) if key in base else value
        return merged
    return patch


def resolve_path(value: str | os.PathLike[str]) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


def resolve_resource(value: str | os.PathLike[str]) -> Path:
    '''优先用运行目录下的副本，其次用打包进程序的副本。'''
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    external = (BASE_DIR / path).resolve()
    if not external.exists():
        bundled = (BUNDLE_DIR / path).resolve()
        if bundled.exists():
            return bundled
    return external


def load_config(config_path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    '''读取 conf/config.json，合并默认值并叠加环境变量。'''
    path = resolve_path(config_path or './conf/config.json')
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    if path.is_file():
        with path.open('r', encoding='utf-8') as handle:
            user_config = json.load(handle)
        if not isinstance(user_config, dict):
            raise ValueError(f'配置文件必须是 JSON 对象：{path}')
        config = deep_merge(config, user_config)

    host = os.environ.get('ROOMSENSE_HOST', '').strip()
    if host:
        config['server']['host'] = host
    port = os.environ.get('ROOMSENSE_PORT', '').strip()
    if port:
        try:
            parsed_port = int(port)
        except ValueError:
            parsed_port = -1
        if 0 <= parsed_port <= 65535:
            config['server']['port'] = parsed_port
    simulate = os.environ.get('ROOMSENSE_SIMULATE', '').strip().lower()
    if simulate in {'0', 'false', 'off', 'no'}:
        config['simulator']['enable'] = False
    elif simulate in {'1', 'true', 'on', 'yes'}:
        config['simulator']['enable'] = True
    log_level = os.environ.get('ROOMSENSE_LOG_LEVEL', '').strip().upper()
    if log_level:
        config['logging']['console_level'] = log_level

    config['server']['accounts_path'] = str(resolve_path(config['server']['accounts_path']))
    config['storage']['db_path'] = str(resolve_path(config['storage']['db_path']))
    config['web']['static_dir'] = str(resolve_resource(config['web']['static_dir']))
    config['device']['key_path'] = str(resolve_path(config['device']['key_path']))
    admin_user = os.environ.get('ROOMSENSE_ADMIN_USER', '').strip()
    admin_password = os.environ.get('ROOMSENSE_ADMIN_PASSWORD', '')
    if admin_user:
        config['server']['admin_user'] = admin_user
    if admin_password:
        config['server']['admin_password'] = admin_password
    return config


def read_or_create_secret(path: str | os.PathLike[str], length: int = 32) -> str:
    secret_path = Path(path)
    try:
        secret = secret_path.read_text(encoding='utf-8').strip()
    except FileNotFoundError:
        secret = ''
    if secret:
        return secret
    secret = secrets.token_urlsafe(length)
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    secret_path.write_text(secret + '\n', encoding='utf-8')
    try:
        secret_path.chmod(0o600)
    except OSError:
        # Windows 可能不支持该权限位，不影响功能。
        pass
    return secret


def write_secret(path: str | os.PathLike[str], length: int = 32) -> str:
    secret = secrets.token_urlsafe(length)
    secret_path = Path(path)
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    secret_path.write_text(secret + '\n', encoding='utf-8')
    return secret
