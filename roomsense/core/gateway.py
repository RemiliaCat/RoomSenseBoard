'''设备数据网关：校验上报、写入存储、联动告警并广播实时事件。'''

from __future__ import annotations

import re
import time
from typing import Any, Callable

from . import alerts as alert_rules
from .bus import EventBus
from .storage import Storage

DEVICE_ID_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$')

FIELD_RANGES: dict[str, tuple[float, float]] = {
    'temperature': (-40.0, 125.0),
    'humidity': (0.0, 100.0),
    'co2': (0.0, 20000.0),
    'pm25': (0.0, 1000.0),
    'noise': (0.0, 140.0),
    'light': (0.0, 200000.0),
    'battery': (0.0, 15.0),
}

# 至少需要上报其中之一，否则这条数据没有监控意义。
PRIMARY_FIELDS = ('temperature', 'humidity', 'co2', 'pm25', 'noise')


class ValidationError(ValueError):
    '''上报数据不合法。'''


def normalize_reading(payload: dict[str, Any], now: int | None = None) -> dict[str, Any]:
    '''校验并归一化单条读数，非法字段直接拒绝'''
    if not isinstance(payload, dict):
        raise ValidationError('上报内容必须是 JSON 对象')
    current = int(now if now is not None else time.time())

    device_id = payload.get('device_id')
    if not isinstance(device_id, str) or not DEVICE_ID_PATTERN.match(device_id.strip()):
        raise ValidationError('device_id 必须为 1-64 位字母、数字或 _ . : - 组成的字符串')
    device_id = device_id.strip()

    reading: dict[str, Any] = {'device_id': device_id}

    name = payload.get('name')
    if isinstance(name, str) and name.strip():
        reading['name'] = name.strip()[:64]
    room = payload.get('room')
    if isinstance(room, str) and room.strip():
        reading['room'] = room.strip()[:64]

    ts = payload.get('ts')
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        ts_value = int(ts)
        if ts_value > current + 300:
            ts_value = current
        elif ts_value < current - 86400:
            raise ValidationError('ts 超出可接受范围（最多回溯 24 小时）')
    else:
        ts_value = current
    reading['ts'] = ts_value

    for field, (low, high) in FIELD_RANGES.items():
        value = payload.get(field)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValidationError(f'{field} 必须是数字')
        numeric = float(value)
        if not low <= numeric <= high:
            raise ValidationError(f'{field} 超出合理范围 {low} ~ {high}')
        reading[field] = round(numeric, 3)

    if not any(field in reading for field in PRIMARY_FIELDS):
        raise ValidationError('至少需要提供 temperature、humidity、co2、pm25 或 noise 之一')

    rssi = payload.get('rssi')
    if rssi is not None:
        if isinstance(rssi, bool) or not isinstance(rssi, (int, float)):
            raise ValidationError('rssi 必须是数字')
        numeric_rssi = int(rssi)
        if not -150 <= numeric_rssi <= 0:
            raise ValidationError('rssi 超出合理范围 -150 ~ 0 dBm')
        reading['rssi'] = numeric_rssi

    seq = payload.get('seq')
    if seq is not None:
        if isinstance(seq, bool) or not isinstance(seq, (int, float)):
            raise ValidationError('seq 必须是整数')
        reading['seq'] = int(seq)

    return reading


class DeviceGateway:
    '''设备上报的统一入口，HTTP 与模拟器共用。'''

    def __init__(
        self,
        storage: Storage,
        bus: EventBus,
        settings_provider: Callable[[], dict[str, Any]],
    ) -> None:
        self.storage = storage
        self.bus = bus
        self.settings_provider = settings_provider

    # ------------------------------------------------------------------ 上报

    def settings(self) -> dict[str, Any]:
        return self.settings_provider()

    def submit(self, payload: dict[str, Any], source: str = 'device') -> dict[str, Any]:
        '''处理单条上报或批量上报（``{"readings": [...]}``）。'''
        max_batch = int(self.settings().get('max_batch', 256))
        if isinstance(payload, dict) and isinstance(payload.get('readings'), list):
            items = payload['readings']
        else:
            items = [payload]
        if not items:
            raise ValidationError('上报列表为空')
        if len(items) > max_batch:
            raise ValidationError(f'单次上报不能超过 {max_batch} 条')

        now = int(time.time())
        accepted = 0
        stored = 0
        opened: list[dict[str, Any]] = []
        resolved: list[dict[str, Any]] = []
        for item in items:
            reading = normalize_reading(item, now=now)
            detail = self._store(reading, source=source)
            accepted += 1
            stored += 1 if detail['stored'] else 0
            opened.extend(detail['opened'])
            resolved.extend(detail['resolved'])
        return {
            'ok': True,
            'accepted': accepted,
            'stored': stored,
            'opened': opened,
            'resolved': resolved,
            'server_time': now,
        }

    def _store(self, reading: dict[str, Any], source: str) -> dict[str, Any]:
        settings = self.settings()
        timeout = int(settings.get('offline_timeout', 90))
        before = self.storage.get_node(reading['device_id'])
        was_offline = bool(before) and (time.time() - before['last_seen']) > timeout

        result = self.storage.record_reading(reading)
        if result['inserted']:
            summary = (
                f"节点 {reading['device_id']} 上报 温度 {reading.get('temperature', '--')} "
                f"湿度 {reading.get('humidity', '--')}"
            )
            self._log(
                'DEBUG',
                source,
                summary,
            )
        else:
            self._log('DEBUG', source, f"同一时间戳重复上报，已覆盖：{reading['device_id']} @ {reading['ts']}")

        if before is None:
            node_name = result['node'].get('name') or reading['device_id']
            self._log('INFO', 'node', f"发现新节点 {reading['device_id']}（{node_name}）")
            self._publish_node(result['node'])
        elif was_offline and result['stored']:
            self._log('INFO', 'node', f"节点 {reading['device_id']} 恢复上报")
            self._publish_node(result['node'])

        opened: list[dict[str, Any]] = []
        resolved: list[dict[str, Any]] = []

        offline_alert = self.storage.active_alert(reading['device_id'], 'device_offline')
        if offline_alert is not None and result['current']:
            done = self.storage.resolve_alert(
                reading['device_id'],
                'device_offline',
                '节点已恢复上报',
                ts=reading['ts'],
            )
            if done:
                resolved.append(done)
                self._publish_alert(done)
                self._log('INFO', 'alert', f"[恢复] {done['device_name']} 恢复上报")

        if result['current']:
            active_kinds = self.storage.active_alert_kinds(reading['device_id'])
            for item in alert_rules.evaluate_reading(reading, settings, active_kinds):
                device_name = result['node'].get('name') or reading['device_id']
                if item['action'] == 'open':
                    is_new = item['kind'] not in active_kinds
                    alert = self.storage.open_alert(
                        reading['device_id'],
                        item['kind'],
                        item['severity'],
                        item['message'],
                        item['value'],
                        item['threshold'],
                        ts=reading['ts'],
                    )
                    opened.append(alert)
                    self._publish_alert(alert)
                    # 持续越限时只刷新告警数值，不重复写 WARN 日志，避免刷屏。
                    if is_new:
                        self._log('WARN', 'alert', f"[{alert['severity'].upper()}] {device_name} {item['message']}")
                else:
                    alert = self.storage.resolve_alert(
                        reading['device_id'],
                        item['kind'],
                        item['message'],
                        ts=reading['ts'],
                    )
                    if alert:
                        resolved.append(alert)
                        self._publish_alert(alert)
                        self._log('INFO', 'alert', f"[恢复] {alert['device_name']} {item['message']}")

        payload = {
            'device_id': reading['device_id'],
            'name': result['node'].get('name') or reading['device_id'],
            'room': result['node'].get('room') or '',
            'ts': reading['ts'],
            'temperature': reading.get('temperature'),
            'humidity': reading.get('humidity'),
            'co2': reading.get('co2'),
            'pm25': reading.get('pm25'),
            'noise': reading.get('noise'),
            'light': reading.get('light'),
            'battery': reading.get('battery'),
            'rssi': reading.get('rssi'),
            'stored': result['stored'],
        }
        self.bus.publish('reading', payload)
        return {'stored': result['stored'], 'opened': opened, 'resolved': resolved, 'reading': payload}

    # ------------------------------------------------------------ 离线检测

    def check_offline(self, now: float | None = None) -> list[dict[str, Any]]:
        '''周期任务：为超时未上报的节点开告警。'''
        current = int(now if now is not None else time.time())
        settings = self.settings()
        timeout = int(settings.get('offline_timeout', 90))
        opened: list[dict[str, Any]] = []
        for node in self.storage.list_nodes(offline_timeout=timeout, now=current):
            if node['online'] or node['reading_count'] <= 0:
                continue
            if self.storage.active_alert(node['device_id'], 'device_offline') is not None:
                continue
            age = max(0, current - int(node['last_seen']))
            alert = self.storage.open_alert(
                node['device_id'],
                'device_offline',
                'warning',
                f"已 {self._format_age(current - int(node['last_seen']))} 未上报数据（阈值 {timeout} 秒）",
                float(age),
                float(timeout),
                ts=current,
            )
            opened.append(alert)
            self._publish_alert(alert)
            self._publish_node(node)
            self._log('WARN', 'node', f"节点 {node['device_id']} 离线 {self._format_age(age)}")
        return opened

    # ------------------------------------------------------------ 面板数据

    def overview(self) -> dict[str, Any]:
        settings = self.settings()
        timeout = int(settings.get('offline_timeout', 90))
        nodes = self.storage.list_nodes(offline_timeout=timeout)
        stats = self.storage.stats(offline_timeout=timeout)
        recent = self.storage.list_alerts(states=('open', 'acknowledged'), limit=8)
        temperatures = [node['last_temperature'] for node in nodes if node['last_temperature'] is not None]
        humidities = [node['last_humidity'] for node in nodes if node['last_humidity'] is not None]
        return {
            'server_time': int(time.time()),
            'stats': stats,
            'nodes': nodes,
            'alerts': recent,
            'alert_counts': self.storage.alert_counts(),
            'averages': {
                'temperature': round(sum(temperatures) / len(temperatures), 2) if temperatures else None,
                'humidity': round(sum(humidities) / len(humidities), 2) if humidities else None,
            },
            'settings': {
                'offline_timeout': timeout,
                'retention_days': settings.get('retention_days', 30),
                'alerts': settings.get('alerts', {}),
            },
            'live_window_minutes': settings.get('live_window_minutes', 30),
        }

    # ------------------------------------------------------------ 设置更新

    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise ValidationError('设置必须是 JSON 对象')
        current = self.settings()
        merged = {
            'offline_timeout': patch.get('offline_timeout', current.get('offline_timeout', 90)),
            'retention_days': patch.get('retention_days', current.get('retention_days', 30)),
            'alerts': _merge_rules(current.get('alerts') or {}, patch.get('alerts') or {}),
        }
        try:
            merged['offline_timeout'] = max(15, min(3600, int(merged['offline_timeout'])))
            merged['retention_days'] = max(1, min(365, int(merged['retention_days'])))
        except (TypeError, ValueError) as error:
            raise ValidationError('离线超时与保留天数必须是整数') from error
        self.storage.save_runtime_settings(merged)
        self._log('INFO', 'settings', '告警阈值与运行参数已更新')
        self.bus.publish('settings', merged)
        return merged

    # ------------------------------------------------------------------ 工具

    def _publish_alert(self, alert: dict[str, Any]) -> None:
        self.bus.publish('alert', alert)

    def _publish_node(self, node: dict[str, Any]) -> None:
        self.bus.publish('node', node)

    def _log(self, level: str, source: str, message: str) -> None:
        entry = self.storage.add_log(level, source, message)
        self.bus.publish('log', entry)

    @staticmethod
    def _format_age(seconds: float) -> str:
        seconds = max(0, int(seconds))
        if seconds < 60:
            return f'{seconds} 秒'
        if seconds < 3600:
            return f'{seconds // 60} 分 {seconds % 60} 秒'
        return f'{seconds // 3600} 小时 {(seconds % 3600) // 60} 分'


def default_runtime_settings(config: dict[str, Any]) -> dict[str, Any]:
    '''把配置文件里的默认值转成可在页面上编辑的运行期设置。'''
    return {
        'offline_timeout': int(config['device']['offline_timeout']),
        'retention_days': int(config['storage']['retention_days']),
        'alerts': config['alerts'],
        'live_window_minutes': int(config['storage']['live_window_minutes']),
        'max_batch': int(config['device']['max_batch']),
    }


def _merge_rules(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for family in ('temperature', 'humidity', 'co2'):
        base = dict(current.get(family) or {})
        update = patch.get(family) or {}
        if not isinstance(update, dict):
            raise ValidationError(f'{family} 阈值必须是对象')
        for key, value in update.items():
            if key not in base:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValidationError(f'{family}.{key} 必须是数字')
            base[key] = round(float(value), 2)
        merged[family] = base
    limits = {
        'temperature': (-20.0, 60.0),
        'humidity': (0.0, 100.0),
        'co2': (200.0, 5000.0),
    }
    for family, (low, high) in limits.items():
        rule = merged[family]
        for key in ('low', 'high', 'critical_low', 'critical_high'):
            if key in rule and not low <= float(rule[key]) <= high:
                raise ValidationError(f'{family}.{key} 超出合理范围 {low} ~ {high}')
        if 'low' in rule and 'high' in rule and float(rule['low']) >= float(rule['high']):
            raise ValidationError(f'{family} 下限必须小于上限')
        if 'critical_low' in rule and 'low' in rule and float(rule['critical_low']) > float(rule['low']):
            raise ValidationError(f'{family} 严重下限不能高于下限')
        if 'critical_high' in rule and 'high' in rule and float(rule['critical_high']) < float(rule['high']):
            raise ValidationError(f'{family} 严重上限不能低于上限')
    return merged
