# -*- encoding: utf-8 -*-
r'''
    ____                       _____
   / __ \____  ____  ____ ___ / ___/___  ____  ________
  / /_/ / __ \/ __ \/ __ `__ \\__ \/ _ \/ __ \/ ___/ _ \
 / _, _/ /_/ / /_/ / / / / / /__/ /  __/ / / (__  )  __/
/_/ |_|\____/\____/_/ /_/ /_/____/\___/_/ /_/____/\___/

@File      :   roomsense/api/panel.py
@Author    :   RmCat
@Contact   :   RemiliaCatNero@gmail.com
@License   :   AGPL
@Copyright :   (C) 2026, RoomSense-Team
@Desc      :   管理平面 HTTP 入口：静态页面、管理 REST API 与 SSE 实时流。
'''

from __future__ import annotations

import json
import mimetypes
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from ..app import App
from ..core.auth import PASSWORD_MIN_LENGTH
from ..netinfo import join_url, resolve_endpoint
from ..core.gateway import ValidationError
from .errors import ApiError

# 时间范围 → (窗口秒数, 聚合桶秒数)
RANGES: dict[str, tuple[int, int]] = {
    '30m': (1800, 30),
    '1h': (3600, 60),
    '6h': (21600, 300),
    '24h': (86400, 900),
    '7d': (604800, 3600),
    '30d': (2592000, 14400),
}

MAX_BODY_BYTES = 1024 * 1024
DEFAULT_CORS_HEADERS = 'Content-Type, X-Auth-Token, X-Device-Key, Authorization'
DEFAULT_CORS_METHODS = 'GET, POST, PUT, DELETE, OPTIONS'


class PanelHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], app: App) -> None:
        self.app = app
        super().__init__(address, RequestHandler)


class RequestHandler(BaseHTTPRequestHandler):
    server_version = 'RoomSenseBoard'
    protocol_version = 'HTTP/1.1'

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        self._dispatch('GET')

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch('POST')

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch('PUT')

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch('DELETE')

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch('HEAD')

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._dispatch('OPTIONS')

    def log_message(self, format: str, *args: Any) -> None:
        if self.server.app.config['server'].get('verbose'):
            super().log_message(format, *args)

    @property
    def app(self) -> App:
        return self.server.app  # type: ignore[attr-defined]

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        try:
            if method == 'OPTIONS':
                self._preflight()
                return
            if path.startswith('/api/'):
                self._handle_api(method, path, query)
            elif method in ('GET', 'HEAD'):
                self._serve_static(path, head_only=method == 'HEAD')
            else:
                raise ApiError(405, '不支持的请求方法')
        except ApiError as error:
            self._send_json({'ok': False, 'error': error.message}, status=error.status, head_only=method == 'HEAD')
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as error:  # pragma: no cover - 兜底保护
            try:
                self.app.storage.add_log('ERROR', 'api', f'{method} {path} 处理失败：{error!r}')
            except Exception:
                pass
            self._send_json({'ok': False, 'error': '服务器内部错误'}, status=500)

    def _handle_api(self, method: str, path: str, query: dict[str, list[str]]) -> None:
        app = self.app

        if path == '/api/health' and method in ('GET', 'HEAD'):
            self._send_json(app.status())
            return

        if path == '/api/login' and method == 'POST':
            self._login()
            return

        if path == '/api/stream' and method == 'GET':
            # SSE 用 token 查询参数鉴权，不经过会话检查。
            self._serve_stream(query)
            return

        device_response = app.ingest_api.handle(method, path, self.headers, self._read_json)
        if device_response is not None:
            self._send_json(device_response)
            return

        username = self._require_session(query)

        if path == '/api/overview' and method in ('GET', 'HEAD'):
            payload = app.gateway.overview()
            payload['ok'] = True
            payload['server'] = app.status()
            payload['account'] = app.accounts.describe(username)
            self._send_json(payload)
            return

        if path == '/api/account' and method in ('GET', 'HEAD'):
            self._send_json(
                {
                    'ok': True,
                    'account': app.accounts.describe(username),
                    'users': app.accounts.usernames(),
                    'sessions': app.sessions.count,
                }
            )
            return

        if path == '/api/account/password' and method == 'POST':
            self._change_password(username)
            return

        if path == '/api/nodes' and method in ('GET', 'HEAD'):
            nodes = app.storage.list_nodes(offline_timeout=int(app.runtime_settings().get('offline_timeout', 90)))
            self._send_json({'ok': True, 'nodes': nodes, 'server_time': int(time.time())})
            return

        node_match = re.fullmatch(r'/api/nodes/([^/]+)', path)
        if node_match:
            device_id = node_match.group(1)
            if method in ('GET', 'HEAD'):
                self._node_detail(device_id)
                return
            if method == 'PUT':
                self._update_node(device_id)
                return
            if method == 'DELETE':
                self._delete_node(device_id, query)
                return

        if path == '/api/readings' and method in ('GET', 'HEAD'):
            self._readings(query)
            return

        alert_ack = re.fullmatch(r'/api/alerts/(\d+)/ack', path)
        if alert_ack and method == 'POST':
            alert = app.storage.acknowledge_alert(int(alert_ack.group(1)))
            if alert is None:
                raise ApiError(404, '告警不存在')
            app.bus.publish('alert', alert)
            app.storage.add_log('INFO', 'alert', f"告警 #{alert['id']} 已确认")
            self._send_json({'ok': True, 'alert': alert})
            return

        if path == '/api/alerts' and method in ('GET', 'HEAD'):
            states = {
                'active': ('open', 'acknowledged'),
                'open': ('open',),
                'acknowledged': ('acknowledged',),
                'resolved': ('resolved',),
                'all': None,
            }.get((query.get('state') or ['active'])[0], ('open', 'acknowledged'))
            limit = _clamp_int((query.get('limit') or ['200'])[0], 200, 1, 1000)
            alerts = app.storage.list_alerts(states=states, limit=limit)
            self._send_json(
                {
                    'ok': True,
                    'alerts': alerts,
                    'counts': app.storage.alert_counts(),
                    'server_time': int(time.time()),
                }
            )
            return

        if path == '/api/alerts/ack' and method == 'POST':
            changed = app.storage.acknowledge_all()
            app.bus.publish('alerts_ack', {'count': changed})
            app.storage.add_log('INFO', 'alert', f'已确认 {changed} 条未处理告警')
            self._send_json({'ok': True, 'count': changed, 'counts': app.storage.alert_counts()})
            return

        if path == '/api/logs' and method in ('GET', 'HEAD'):
            levels = [item for item in (query.get('levels') or [''])[0].split(',') if item]
            limit = _clamp_int((query.get('limit') or ['500'])[0], 500, 1, 2000)
            since_id = _clamp_int((query.get('since_id') or ['0'])[0], 0, 0, 2 ** 31)
            logs = app.storage.list_logs(levels=levels or None, limit=limit, since_id=since_id)
            self._send_json({'ok': True, 'logs': logs, 'server_time': int(time.time())})
            return

        if path == '/api/settings':
            if method in ('GET', 'HEAD'):
                self._send_json(self._settings_payload())
                return
            if method == 'PUT':
                body = self._read_json()
                try:
                    app.update_settings(body)
                except ValidationError as error:
                    raise ApiError(400, str(error)) from error
                self._send_json(self._settings_payload())
                return

        if path == '/api/device-key/regenerate' and method == 'POST':
            key = app.regenerate_device_key()
            self._send_json({'ok': True, 'device_key': key})
            return

        if path == '/api/logout' and method == 'POST':
            app.sessions.revoke(self._session_token(query))
            self._send_json({'ok': True})
            return

        if path == '/api/session' and method == 'POST':
            self._send_json(
                {
                    'ok': True,
                    'username': username,
                    'server_time': int(time.time()),
                    'sessions': app.sessions.count,
                }
            )
            return

        raise ApiError(404, '接口不存在')

    def _login(self) -> None:
        app = self.app
        client = self.client_address[0] if self.client_address else 'unknown'
        if app.throttle.blocked(client):
            raise ApiError(429, '登录失败次数过多，请稍后再试')
        body = self._read_json()
        username = body.get('username')
        password = body.get('password')
        if not isinstance(username, str) or not isinstance(password, str) or not username.strip() or not password:
            raise ApiError(400, '请输入账号与密码')
        if not app.accounts.verify(username, password):
            app.throttle.record_failure(client)
            app.storage.add_log('WARN', 'auth', f'面板登录失败（账号 {username.strip()[:32]}，来自 {client}）')
            raise ApiError(401, '账号或密码不正确')
        app.throttle.reset(client)
        session = app.sessions.create(str(username).strip())
        app.storage.add_log('INFO', 'auth', f'面板登录成功（账号 {username.strip()[:32]}，来自 {client}）')
        status = app.status()
        self._send_json(
            {
                'ok': True,
                'session': session,
                'username': str(username).strip(),
                'expires_hours': round(app.sessions.ttl / 3600, 1),
                'server_time': status['server_time'],
                'version': status['version'],
                'simulator': status['simulator'],
            }
        )

    def _change_password(self, username: str) -> None:
        app = self.app
        body = self._read_json()
        current = body.get('current_password')
        new_password = body.get('new_password')
        if not isinstance(current, str) or not isinstance(new_password, str):
            raise ApiError(400, '请填写当前密码与新密码')
        if not app.accounts.verify(username, current):
            app.storage.add_log('WARN', 'auth', f'账号 {username} 修改密码时当前密码校验失败')
            raise ApiError(403, '当前密码不正确')
        if len(new_password) < PASSWORD_MIN_LENGTH:
            raise ApiError(400, f'新密码至少需要 {PASSWORD_MIN_LENGTH} 位')
        try:
            app.accounts.set_password(username, new_password)
        except ValueError as error:
            raise ApiError(400, str(error)) from error
        revoked = app.sessions.revoke_user(username, keep=self._session_token(None))
        app.storage.add_log('INFO', 'auth', f'账号 {username} 已更新密码，注销其他 {revoked} 个会话')
        self._send_json({'ok': True, 'revoked_sessions': revoked, 'account': app.accounts.describe(username)})

    def _node_detail(self, device_id: str) -> None:
        app = self.app
        node = app.storage.get_node(device_id)
        if node is None:
            raise ApiError(404, '节点不存在')
        minutes = int(app.runtime_settings().get('live_window_minutes', 30))
        end = int(time.time())
        readings = app.storage.readings_between(end - minutes * 60, end, device_id=device_id, limit=2000)
        node = dict(node)
        node['online'] = (time.time() - node['last_seen']) <= int(
            app.runtime_settings().get('offline_timeout', 90)
        )
        alerts = [item for item in app.storage.list_alerts(limit=1000) if item['device_id'] == device_id][:20]
        self._send_json(
            {
                'ok': True,
                'node': node,
                'readings': readings,
                'alerts': alerts,
                'totals': app.storage.node_totals(device_id),
                'window_minutes': minutes,
                'server_time': end,
            }
        )

    def _update_node(self, device_id: str) -> None:
        app = self.app
        body = self._read_json()
        name = body.get('name')
        room = body.get('room')
        if name is not None and (not isinstance(name, str) or len(name.strip()) > 64):
            raise ApiError(400, '名称必须是 64 字以内的文本')
        if room is not None and (not isinstance(room, str) or len(room.strip()) > 64):
            raise ApiError(400, '位置必须是 64 字以内的文本')
        node = app.storage.update_node(device_id, name=name, room=room)
        if node is None:
            raise ApiError(404, '节点不存在')
        app.storage.add_log('INFO', 'node', f"节点 {device_id} 信息已更新为 {node['name']}（{node['room']}）")
        app.bus.publish('node', node)
        self._send_json({'ok': True, 'node': node})

    def _delete_node(self, device_id: str, query: dict[str, list[str]]) -> None:
        '''删除节点；purge=True 时连历史读数与告警一起删除。'''
        app = self.app
        purge = (query.get('purge') or ['0'])[0].lower() in ('1', 'true', 'yes', 'on')
        result = app.storage.delete_node(device_id, purge=purge)
        if result is None:
            raise ApiError(404, '节点不存在')
        if purge:
            message = (
                f"节点 {result['name']}（{result['device_id']}）已删除，"
                f"同时清理 {result['deleted_readings']} 条读数与 {result['deleted_alerts']} 条告警"
            )
            app.storage.add_log('WARN', 'node', message)
        else:
            message = (
                f"节点 {result['name']}（{result['device_id']}）已从列表移除，"
                f"保留 {result['kept_readings']} 条历史读数，关闭 {result['closed_alerts']} 条活跃告警"
            )
            app.storage.add_log('WARN', 'node', message)
        app.bus.publish('node_deleted', result)
        self._send_json({'ok': True, **result, 'message': message})

    def _readings(self, query: dict[str, list[str]]) -> None:
        app = self.app
        range_key = (query.get('range') or ['1h'])[0]
        window, default_bucket = RANGES.get(range_key, RANGES['1h'])
        bucket = _clamp_int((query.get('bucket') or [str(default_bucket)])[0], default_bucket, 5, 86400)
        device_id = (query.get('device_id') or [''])[0] or None
        end = int(time.time())
        start = end - window
        rows = app.storage.readings_bucketed(start, end, bucket, device_id=device_id)
        nodes = {
            node['device_id']: node
            for node in app.storage.list_nodes(
                offline_timeout=int(app.runtime_settings().get('offline_timeout', 90))
            )
        }
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            entry = grouped.setdefault(
                row['device_id'],
                {
                    'device_id': row['device_id'],
                    'name': (nodes.get(row['device_id']) or {}).get('name') or row['device_id'],
                    'room': (nodes.get(row['device_id']) or {}).get('room') or '',
                    'points': [],
                },
            )
            entry['points'].append(row)
        self._send_json(
            {
                'ok': True,
                'range': range_key if range_key in RANGES else '1h',
                'bucket': bucket,
                'start': start,
                'end': end,
                'series': list(grouped.values()),
            }
        )

    def _settings_payload(self) -> dict[str, Any]:
        app = self.app
        host, port = self.server.server_address[0], self.server.server_address[1]  # type: ignore[attr-defined]
        status = app.status()
        settings = app.runtime_settings()
        endpoint = resolve_endpoint(str(settings.get('public_url') or ''), str(host), int(port))
        return {
            'ok': True,
            'settings': settings,
            'device': {
                'key': app.device_key,
                'ingest_url': join_url(endpoint.base_url, '/api/ingest'),
                'ingest_urls': [join_url(item, '/api/ingest') for item in endpoint.candidates],
                'offline_timeout': int(settings.get('offline_timeout', 90)),
                'max_batch': int(settings.get('max_batch', 256)),
            },
            'server': {
                'name': status['name'],
                'version': status['version'],
                'uptime': status['uptime'],
                'simulator': status['simulator'],
                'host': host,
                'port': port,
                'public_url': endpoint.base_url,
                'public_url_source': endpoint.source,
                'address_candidates': list(endpoint.candidates),
            },
            'server_time': status['server_time'],
        }

    def _serve_stream(self, query: dict[str, list[str]]) -> None:
        app = self.app
        token = (query.get('token') or [''])[0] or self.headers.get('X-Auth-Token', '')
        if app.sessions.user(token) is None:
            raise ApiError(401, '登录已失效，请重新登录')

        subscriber = app.bus.subscribe()
        self.close_connection = True
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.send_header('X-Accel-Buffering', 'no')
        for name, value in self._cors_headers():
            self.send_header(name, value)
        self.end_headers()
        try:
            self._write_stream('hello', json.dumps(app.status(), ensure_ascii=False))
            while True:
                item = subscriber.get(timeout=15.0)
                if item is None:
                    self.wfile.write(b': keep-alive\n\n')
                    self.wfile.flush()
                    continue
                event, data = item
                self._write_stream(event, data)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            app.bus.unsubscribe(subscriber)

    def _write_stream(self, event: str, data: str) -> None:
        payload = f'event: {event}\ndata: {data}\n\n'.encode('utf-8')
        self.wfile.write(payload)
        self.wfile.flush()

    def _serve_static(self, path: str, head_only: bool = False) -> None:
        static_dir = Path(self.app.config['web']['static_dir'])
        if path in ('/', '/index.html'):
            target = static_dir / 'index.html'
        elif path.startswith('/static/'):
            relative = path[len('/static/'):]
            target = (static_dir / relative).resolve()
            if not _inside(target, static_dir):
                raise ApiError(403, '非法的资源路径')
        else:
            raise ApiError(404, '页面不存在')
        if not target.is_file():
            raise ApiError(404, '资源不存在')

        content_type = mimetypes.guess_type(str(target))[0] or 'application/octet-stream'
        if target.suffix in ('.html', '.js', '.css', '.svg'):
            content_type += '; charset=utf-8'
        body = target.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        for name, value in self._cors_headers():
            self.send_header(name, value)
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _allowed_origins(self) -> list[str]:
        origins = self.app.config['server'].get('cors_origins') or []
        return [str(item).strip() for item in origins if str(item).strip()]

    def _cors_headers(self) -> list[tuple[str, str]]:
        origin = str(self.headers.get('Origin') or '').strip()
        if not origin or origin not in self._allowed_origins():
            return []
        return [
            ('Access-Control-Allow-Origin', origin),
            ('Vary', 'Origin'),
            ('Access-Control-Allow-Headers', DEFAULT_CORS_HEADERS),
            ('Access-Control-Allow-Methods', DEFAULT_CORS_METHODS),
            ('Access-Control-Max-Age', '600'),
        ]

    def _preflight(self) -> None:
        headers = self._cors_headers()
        origin = str(self.headers.get('Origin') or '').strip()
        if origin and not headers:
            raise ApiError(403, '该来源不在 server.cors_origins 白名单内')
        self.send_response(204)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def _session_token(self, query: dict[str, list[str]] | None = None) -> str:
        token = self.headers.get('X-Auth-Token', '')
        if not token and query:
            token = (query.get('token') or [''])[0]
        return token

    def _require_session(self, query: dict[str, list[str]] | None = None) -> str:
        username = self.app.sessions.user(self._session_token(query))
        if username is None:
            raise ApiError(401, '登录已失效，请重新登录')
        return username

    def _read_json(self, limit: int = MAX_BODY_BYTES) -> dict[str, Any]:
        length_header = self.headers.get('Content-Length', '0')
        try:
            length = int(length_header)
        except ValueError as error:
            raise ApiError(400, 'Content-Length 不合法') from error
        if length <= 0:
            return {}
        if length > limit:
            raise ApiError(413, '请求体过大')
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ApiError(400, '请求体必须是 UTF-8 编码的 JSON') from error
        if not isinstance(payload, dict):
            raise ApiError(400, '请求体必须是 JSON 对象')
        return payload

    def _send_json(self, payload: dict[str, Any], status: int = 200, head_only: bool = False) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        for name, value in self._cors_headers():
            self.send_header(name, value)
        self.end_headers()
        if not head_only:
            self.wfile.write(body)


def _clamp_int(value: str, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, parsed))


def _inside(target: Path, root: Path) -> bool:
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return False
    return True
