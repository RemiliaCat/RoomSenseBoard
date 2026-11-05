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
@Desc      :   对外访问地址解析：手工配置、公网出口、监听地址与本机网卡的优先级。
'''

from __future__ import annotations

import ipaddress
import queue
import re
import socket
import threading
import time
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

WILDCARD_HOSTS = frozenset({'', '0.0.0.0', '::', '[::]', '*'})
LOOPBACK_HOSTS = frozenset({'127.0.0.1', 'localhost', '::1', '[::1]'})

_PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(network) for network in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')
)

# TEST-NET-1；UDP connect 不会真的发包。
_ROUTE_PROBE = ('192.0.2.1', 9)

# 云厂商元数据服务（内网/链路本地，不经过公网）与通用回显服务。
# 元数据确认的地址更可信：它说明这台机器确实绑定了这个公网 IP。
_METADATA_SOURCES = (
    ('tencent', 'http://metadata.tencentyun.com/latest/meta-data/public-ipv4'),
    # 腾讯云元数据服务的链路本地地址；DNS 慢或被拦时仍能拿到公网 IP。
    ('tencent', 'http://169.254.0.23/latest/meta-data/public-ipv4'),
    ('aliyun', 'http://100.100.100.200/latest/meta-data/eipv4'),
)
_ECHO_SOURCES = (
    ('ipip', 'https://myip.ipip.net'),
    ('3322', 'http://ip.3322.net'),
    ('ifconfig', 'https://ifconfig.me/ip'),
    ('ipify', 'https://api.ipify.org'),
)

_IPV4_PATTERN = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b')

_PUBLIC_CACHE_SECONDS = 600.0
_PUBLIC_TIMEOUT = 1.2
_PUBLIC_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


@dataclass(frozen=True)
class Endpoint:
    base_url: str
    '''展示给用户的主地址，形如 ``http://192.168.1.20:20500``（不带结尾斜杠）。'''

    candidates: tuple[str, ...]
    '''全部可用地址，第一个即 ``base_url``；多网卡时按优先级排列。'''

    source: str
    '''来源：``configured``（手工配置）、``public``（公网出口探测）、
    ``listen``（监听地址）、``lan``（本机网卡）、``loopback``（本机回环）。'''


@dataclass(frozen=True)
class PublicAddress:
    address: str
    '''探测到的公网 IPv4 地址。'''

    source: str
    '''来源：``tencent`` / ``aliyun`` 元数据，或 ``ipip`` / ``3322`` / ``ifconfig`` /
    ``ipify`` 回显。'''

    authoritative: bool = False
    '''云厂商元数据确认的地址可以优先展示；通用回显只作为候选地址。'''


_public_lock = threading.Lock()
_public_cache: PublicAddress | None = None
_public_cached_at = 0.0


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
        hostname = parsed.hostname
    except ValueError as error:
        raise ValueError('对外地址格式不正确，请填写完整的 http:// 或 https:// 地址') from error
    if parsed.scheme not in ('http', 'https') or not hostname:
        raise ValueError('对外地址必须是 http:// 或 https:// 开头的完整地址')
    if parsed.query or parsed.fragment:
        raise ValueError('对外地址不能带查询串或锚点')
    if port is not None and not 0 < port < 65536:
        raise ValueError('对外访问地址的端口不合法')

    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and address.is_unspecified:
        # 0.0.0.0 / :: 是“监听所有网卡”的写法，不能写进浏览器或节点固件。
        raise ValueError(
            f'{hostname} 是监听地址，不是访问地址；请填写公网 IP、域名或局域网 IP'
        )
    if hostname == '*':
        raise ValueError('* 不是有效的访问地址；请填写公网 IP 或域名')

    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip('/'), '', ''))


def join_url(base: str, path: str) -> str:
    if not base:
        return path
    return base.rstrip('/') + '/' + path.lstrip('/')


def local_base_url(host: str, port: int) -> str:
    '''本机打开面板时使用的地址，不会返回公网探测结果。

    监听所有网卡或回环地址时用本机回环；绑定具体网卡时用该网卡地址。
    '''
    text = str(host or '').strip()
    if text in ('::', '[::]', '::1', '[::1]'):
        text = '::1'
    elif text in WILDCARD_HOSTS or text in LOOPBACK_HOSTS:
        text = '127.0.0.1'
    return f'http://{_format_host(text)}:{int(port)}'


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


def _fetch_text(url: str, timeout: float) -> str:
    request = urllib.request.Request(url, headers={'User-Agent': 'RoomSenseBoard/1.0'})
    with _PUBLIC_OPENER.open(request, timeout=timeout) as response:
        return response.read(256).decode('ascii', 'ignore').strip()


def _valid_public_ipv4(text: str) -> str | None:
    for match in _IPV4_PATTERN.findall(text):
        try:
            address = ipaddress.ip_address(match)
        except ValueError:
            continue
        if address.version == 4 and address.is_global:
            return str(address)
    return None


def _strict_public_ipv4(text: str) -> str | None:
    '''云厂商元数据应只返回一个地址；避免把错误页里的数字当成公网 IP。'''
    token = text.strip()
    if not token or any(char.isspace() for char in token):
        return None
    try:
        address = ipaddress.ip_address(token)
    except ValueError:
        return None
    if address.version != 4 or not address.is_global:
        return None
    return str(address)


def _race_public_sources(
    sources: tuple[tuple[str, str], ...], timeout: float, strict: bool = False
) -> tuple[str, str] | None:
    '''并发请求同一组地址，取最先返回的有效结果，避免某一个服务慢拖住启动。'''
    results: queue.Queue[tuple[str, str | None]] = queue.Queue()
    parse = _strict_public_ipv4 if strict else _valid_public_ipv4

    def worker(name: str, url: str) -> None:
        try:
            address = parse(_fetch_text(url, timeout))
        except Exception:
            address = None
        results.put((name, address))

    for name, url in sources:
        threading.Thread(target=worker, args=(name, url), daemon=True).start()

    deadline = time.time() + timeout
    for _ in sources:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        try:
            name, address = results.get(timeout=remaining)
        except queue.Empty:
            break
        if address:
            return name, address
    return None


def detect_public_address(
    timeout: float = _PUBLIC_TIMEOUT, refresh: bool = False
) -> PublicAddress | None:
    '''探测本机出口公网 IPv4；结果缓存十分钟，失败时返回 None。

    云厂商元数据（腾讯云/阿里云）优先，通用回显服务兜底。整个过程只读取一个很小的
    文本响应，不会上传任何业务数据。
    '''
    global _public_cache, _public_cached_at
    now = time.time()
    if not refresh:
        with _public_lock:
            if _public_cached_at and now - _public_cached_at < _PUBLIC_CACHE_SECONDS:
                return _public_cache

    result: PublicAddress | None = None
    metadata = _race_public_sources(_METADATA_SOURCES, timeout, strict=True)
    if metadata is not None:
        result = PublicAddress(metadata[1], metadata[0], True)
    else:
        echo = _race_public_sources(_ECHO_SOURCES, timeout)
        if echo is not None:
            result = PublicAddress(echo[1], echo[0], False)

    with _public_lock:
        _public_cache = result
        _public_cached_at = now
    return result


def cached_public_address() -> PublicAddress | None:
    '''返回最近一次探测结果；未探测时返回 None，不发起网络请求。'''
    with _public_lock:
        return _public_cache


def _format_host(host: str) -> str:
    if ':' in host and not host.startswith('['):
        return f'[{host}]'
    return host


def resolve_endpoint(
    public_url: str | None,
    host: str,
    port: int,
    public: PublicAddress | None = None,
) -> Endpoint:
    '''按配置 → 监听地址 → 公网出口 → 本机网卡 → 回环的顺序给出展示地址。'''
    try:
        configured = normalize_public_url(public_url)
    except ValueError:
        # 配置里的地址非法时回退到自动推断，不影响启动。
        configured = ''
    if configured:
        return Endpoint(configured, (configured,), 'configured')

    listen_host = str(host or '').strip()
    if listen_host and listen_host not in WILDCARD_HOSTS:
        listen_base = f'http://{_format_host(listen_host)}:{port}'
        if listen_host in LOOPBACK_HOSTS:
            # 只监听回环时公网地址不可能直连，不能作为展示 / 上报地址。
            return Endpoint(listen_base, (listen_base,), 'listen')
        bases = [listen_base]
        source = 'listen'
        if public and public.address:
            public_base = f'http://{_format_host(public.address)}:{port}'
            if public_base != listen_base:
                if public.authoritative:
                    bases.insert(0, public_base)
                    source = 'public'
                else:
                    bases.append(public_base)
        return Endpoint(bases[0], tuple(bases), source)

    addresses = local_ipv4_addresses()
    bases: list[str] = []
    public_base = f'http://{_format_host(public.address)}:{port}' if public and public.address else ''
    if public_base:
        if public and public.authoritative:
            bases.append(public_base)
        bases.extend(f'http://{address}:{port}' for address in addresses)
        if public and not public.authoritative:
            bases.append(public_base)
    else:
        bases.extend(f'http://{address}:{port}' for address in addresses)

    unique: list[str] = []
    for base in bases:
        if base not in unique:
            unique.append(base)
    if unique:
        source = 'public' if public_base and unique[0] == public_base else 'lan'
        return Endpoint(unique[0], tuple(unique), source)

    if public_base:
        return Endpoint(public_base, (public_base,), 'public')
    base = f'http://127.0.0.1:{port}'
    return Endpoint(base, (base,), 'loopback')
