# -*- encoding: utf-8 -*-
r'''
    ____                       _____
   / __ \____  ____  ____ ___ / ___/___  ____  ________
  / /_/ / __ \/ __ \/ __ `__ \\__ \/ _ \/ __ \/ ___/ _ \
 / _, _/ /_/ / /_/ / / / / / /__/ /  __/ / / (__  )  __/
/_/ |_|\____/\____/_/ /_/ /_/____/\___/_/ /_/____/\___/

@File      :   roomsense/netinfo.py
@Author    :   RmCat
@Contact   :   RemiliaCatNero@gmail.com
@License   :   AGPL
@Copyright :   (C) 2026, RoomSense-Team
@Desc      :   对外访问地址解析：手工配置、监听地址与本机网卡的优先级。
'''

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

WILDCARD_HOSTS = frozenset({'', '0.0.0.0', '::', '[::]', '*'})

_PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(network) for network in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')
)

# TEST-NET-1；UDP connect 不会真的发包。
_ROUTE_PROBE = ('192.0.2.1', 9)


@dataclass(frozen=True)
class Endpoint:
    base_url: str
    '''展示给用户的主地址，形如 ``http://192.168.1.20:20500``（不带结尾斜杠）。'''

    candidates: tuple[str, ...]
    '''全部可用地址，第一个即 ``base_url``；多网卡时按优先级排列。'''

    source: str
    '''来源：``configured``（手工配置）、``listen``（监听地址）、``lan``（本机网卡）、
    ``loopback``（本机回环）。'''


def normalize_public_url(value: str | None) -> str:
    '''校验并归一化对外地址，空串表示未配置。'''
    text = str(value or '').strip()
    if not text:
        return ''
    if '://' not in text:
        text = 'http://' + text
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError as error:
        raise ValueError('对外地址格式不正确，请填写完整的 http:// 或 https:// 地址') from error
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise ValueError('对外地址必须是 http:// 或 https:// 开头的完整地址')
    if parsed.query or parsed.fragment:
        raise ValueError('对外地址不能带查询串或锚点')
    if port is not None and not 0 < port < 65536:
        raise ValueError('对外地址的端口不合法')
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip('/'), '', ''))


def join_url(base: str, path: str) -> str:
    if not base:
        return path
    return base.rstrip('/') + '/' + path.lstrip('/')


def _is_private(address: str) -> bool:
    parsed = ipaddress.ip_address(address)
    return any(parsed in network for network in _PRIVATE_NETWORKS)


def local_ipv4_addresses() -> list[str]:
    '''本机可对外使用的 IPv4 地址：默认路由地址在前，其余私网优先。'''
    addresses: list[str] = []

    def add(candidate: str) -> None:
        if not candidate or candidate in addresses:
            return
        try:
            parsed = ipaddress.ip_address(candidate)
        except ValueError:
            return
        if parsed.version != 4 or parsed.is_loopback or parsed.is_link_local or parsed.is_unspecified:
            return
        addresses.append(candidate)

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(_ROUTE_PROBE)
        add(str(probe.getsockname()[0]))
    except OSError:
        pass
    finally:
        probe.close()

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(str(info[4][0]))
    except OSError:
        pass

    ordered = sorted(
        enumerate(addresses), key=lambda item: (0 if _is_private(item[1]) else 1, item[0])
    )
    return [address for _, address in ordered]


def _format_host(host: str) -> str:
    if ':' in host and not host.startswith('['):
        return f'[{host}]'
    return host


def resolve_endpoint(public_url: str | None, host: str, port: int) -> Endpoint:
    '''按配置 → 监听地址 → 本机网卡 → 回环的顺序给出展示地址。'''
    try:
        configured = normalize_public_url(public_url)
    except ValueError:
        # 配置里的地址非法时回退到自动推断，不影响启动。
        configured = ''
    if configured:
        return Endpoint(configured, (configured,), 'configured')

    listen_host = str(host or '').strip()
    if listen_host and listen_host not in WILDCARD_HOSTS:
        base = f'http://{_format_host(listen_host)}:{port}'
        return Endpoint(base, (base,), 'listen')

    addresses = local_ipv4_addresses()
    if addresses:
        bases = tuple(f'http://{address}:{port}' for address in addresses)
        return Endpoint(bases[0], bases, 'lan')

    base = f'http://127.0.0.1:{port}'
    return Endpoint(base, (base,), 'loopback')
