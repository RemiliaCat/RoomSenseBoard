'''运行期配置。

配置优先级（后者覆盖前者）：

1. ``DEFAULT_CONFIG``：本文件内置默认值，缺省即可运行；
2. ``conf/config.json``：只写需要覆盖的字段，深合并到默认值；
3. 环境变量 ``ROOMSENSE_HOST`` / ``ROOMSENSE_PORT`` / ``ROOMSENSE_SIMULATE`` /
   ``ROOMSENSE_ADMIN_USER`` / ``ROOMSENSE_ADMIN_PASSWORD``（后两个只在首次创建账号时生效）。

相对路径一律相对项目根目录解析，因此从任何工作目录启动行为一致。
'''

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_CONFIG: dict[str, Any] = {
    'server': {
        'host': '127.0.0.1',
        'port': 20500,
        'accounts_path': './conf/accounts.json',
        'session_hours': 12,
        'cors_origins': [],
        'name': 'RoomSenseBoard',
    },
    'storage': {
        'db_path': './data/roomsense.db',
        'retention_days': 30,
        'live_window_minutes': 30,
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
    },
    'simulator': {
        'enable': True,
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
    '''递归合并字典，``patch`` 中的叶子值覆盖 ``base``。'''
    if isinstance(base, dict) and isinstance(patch, dict):
        merged = dict(base)
        for key, value in patch.items():
            merged[key] = deep_merge(base.get(key), value) if key in base else value
        return merged
    return patch


def resolve_path(value: str | os.PathLike[str]) -> Path:
    '''把配置里的相对路径解析为项目根目录下的绝对路径。'''
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def load_config(config_path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    '''读取 ``conf/config.json`` 并与默认值合并，再叠加环境变量。'''
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

    config['server']['accounts_path'] = str(resolve_path(config['server']['accounts_path']))
    config['storage']['db_path'] = str(resolve_path(config['storage']['db_path']))
    config['web']['static_dir'] = str(resolve_path(config['web']['static_dir']))
    config['device']['key_path'] = str(resolve_path(config['device']['key_path']))
    admin_user = os.environ.get('ROOMSENSE_ADMIN_USER', '').strip()
    admin_password = os.environ.get('ROOMSENSE_ADMIN_PASSWORD', '')
    if admin_user:
        config['server']['admin_user'] = admin_user
    if admin_password:
        config['server']['admin_password'] = admin_password
    return config


def read_or_create_secret(path: str | os.PathLike[str], length: int = 32) -> str:
    '''读取密钥文件；不存在时生成并写入。用于节点接入密钥。'''
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
        # Windows 等平台可能不支持该权限位，密钥文件本身仍可正常工作。
        pass
    return secret


def write_secret(path: str | os.PathLike[str], length: int = 32) -> str:
    '''重新生成密钥并写入文件。'''
    secret = secrets.token_urlsafe(length)
    secret_path = Path(path)
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    secret_path.write_text(secret + '\n', encoding='utf-8')
    return secret
