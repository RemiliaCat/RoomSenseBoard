# -*- encoding: utf-8 -*-
r'''
    ____                       _____
   / __ \____  ____  ____ ___ / ___/___  ____  ________
  / /_/ / __ \/ __ \/ __ `__ \\__ \/ _ \/ __ \/ ___/ _ \
 / _, _/ /_/ / /_/ / / / / / /__/ /  __/ / / (__  )  __/
/_/ |_|\____/\____/_/ /_/ /_/____/\___/_/ /_/____/\___/

@File      :   roomsense/core/storage.py
@Author    :   RmCat
@Contact   :   RemiliaCatNero@gmail.com
@License   :   AGPL
@Copyright :   (C) 2026, RoomSense-Team
@Desc      :   SQLite 持久化层：节点、读数、告警、日志与运行设置的 SQL。
'''

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

LOG_LEVELS = ('TRACE', 'DEBUG', 'NOTE', 'INFO', 'WARN', 'ERROR', 'FATAL')
LOG_SEVERITY = {level: index for index, level in enumerate(LOG_LEVELS)}

SCHEMA = '''
CREATE TABLE IF NOT EXISTS nodes (
    device_id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    room TEXT NOT NULL DEFAULT '',
    first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    last_temperature REAL,
    last_humidity REAL,
    last_co2 REAL,
    last_pm25 REAL,
    last_noise REAL,
    last_light REAL,
    last_battery REAL,
    last_rssi INTEGER,
    last_seq INTEGER,
    reading_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    ts INTEGER NOT NULL,
    temperature REAL,
    humidity REAL,
    co2 REAL,
    pm25 REAL,
    noise REAL,
    light REAL,
    battery REAL,
    rssi INTEGER,
    seq INTEGER,
    UNIQUE (device_id, ts)
);
CREATE INDEX IF NOT EXISTS idx_readings_device_ts ON readings (device_id, ts);
CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings (ts);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    value REAL,
    threshold REAL,
    opened_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'open',
    resolved_at INTEGER,
    acknowledged_at INTEGER,
    acknowledged_by TEXT,
    resolved_by TEXT,
    muted_until INTEGER,
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_device_kind ON alerts (device_id, kind, state);
CREATE INDEX IF NOT EXISTS idx_alerts_state ON alerts (state, opened_at);

CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    level TEXT NOT NULL,
    source TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs (ts);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
'''

RUNTIME_SETTINGS_KEY = 'runtime'


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _merge_settings(base: dict[str, Any], patch: dict[str, Any]) -> None:
    '''递归合并，保证旧数据库里没有的新告警指标仍能取到默认值。'''
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge_settings(base[key], value)
        else:
            base[key] = value


class Storage:
    def __init__(self, db_path: str | os.PathLike[str]) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=15)
        self._conn.row_factory = sqlite3.Row
        self.console_level: str | None = None
        with self._lock:
            self._conn.execute('PRAGMA journal_mode=WAL')
            self._conn.execute('PRAGMA synchronous=NORMAL')
            self._conn.executescript(SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        '''为早期数据库补齐后加的列。'''
        additions = {
            'readings': (('pm25', 'REAL'), ('noise', 'REAL')),
            'nodes': (('last_pm25', 'REAL'), ('last_noise', 'REAL')),
            'alerts': (
                ('acknowledged_at', 'INTEGER'),
                ('acknowledged_by', 'TEXT'),
                ('resolved_by', 'TEXT'),
                ('muted_until', 'INTEGER'),
                ('note', 'TEXT'),
            ),
        }
        for table, columns in additions.items():
            existing = {row['name'] for row in self._conn.execute(f'PRAGMA table_info({table})')}
            for name, column_type in columns:
                if name not in existing:
                    self._conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {column_type}')

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def set_console_level(self, level: str | None) -> None:
        '''把不低于该级别的日志同步到终端；None / OFF 关闭。'''
        normalized = str(level or '').strip().upper()
        self.console_level = normalized if normalized in LOG_LEVELS else None

    def _console(self, entry: dict[str, Any]) -> None:
        threshold = LOG_SEVERITY.get(self.console_level or '', len(LOG_LEVELS))
        if LOG_SEVERITY[entry['level']] < threshold:
            return
        stamp = time.strftime('%H:%M:%S', time.localtime(int(entry['ts'])))
        try:
            print(f"[{stamp}] {entry['level']:<5} {entry['source']}: {entry['message']}", flush=True)
        except (OSError, ValueError):
            self.console_level = None

    def _query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            cursor = self._conn.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]

    def _query_one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        with self._lock:
            cursor = self._conn.execute(sql, params)
            return _row_to_dict(cursor.fetchone())

    def _execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._conn.execute(sql, params)
            self._conn.commit()
            return cursor

    def record_reading(self, reading: dict[str, Any]) -> dict[str, Any]:
        '''写入一条读数并更新节点快照；同一 (device_id, ts) 覆盖。'''
        now = int(time.time())
        ts = int(reading['ts'])
        device_id = reading['device_id']
        with self._lock:
            node_row = self._conn.execute(
                'SELECT last_seen FROM nodes WHERE device_id = ?',
                (device_id,),
            ).fetchone()
            if node_row is None:
                self._conn.execute(
                    'INSERT INTO nodes (device_id, name, room, first_seen, last_seen) VALUES (?, ?, ?, ?, ?)',
                    (device_id, reading.get('name') or device_id, reading.get('room') or '', ts, ts),
                )
                update_snapshot = True
                inserted_reading = True
            else:
                update_snapshot = ts >= int(node_row['last_seen'])
                duplicate = self._conn.execute(
                    'SELECT 1 FROM readings WHERE device_id = ? AND ts = ?',
                    (device_id, ts),
                ).fetchone()
                inserted_reading = duplicate is None

            values = (
                reading.get('temperature'),
                reading.get('humidity'),
                reading.get('co2'),
                reading.get('pm25'),
                reading.get('noise'),
                reading.get('light'),
                reading.get('battery'),
                reading.get('rssi'),
                reading.get('seq'),
            )
            if inserted_reading:
                self._conn.execute(
                    '''
                    INSERT INTO readings
                        (device_id, ts, temperature, humidity, co2, pm25, noise, light, battery, rssi, seq)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (device_id, ts) + values,
                )
            else:
                # 同一秒的后续上报只覆盖非空字段。
                self._conn.execute(
                    '''
                    UPDATE readings SET
                        temperature = COALESCE(?, temperature),
                        humidity = COALESCE(?, humidity),
                        co2 = COALESCE(?, co2),
                        pm25 = COALESCE(?, pm25),
                        noise = COALESCE(?, noise),
                        light = COALESCE(?, light),
                        battery = COALESCE(?, battery),
                        rssi = COALESCE(?, rssi),
                        seq = COALESCE(?, seq)
                    WHERE device_id = ? AND ts = ?
                    ''',
                    values + (device_id, ts),
                )

            if update_snapshot:
                self._conn.execute(
                    '''
                    UPDATE nodes SET
                        last_seen = MAX(last_seen, ?),
                        reading_count = reading_count + ?,
                        last_temperature = COALESCE(?, last_temperature),
                        last_humidity = COALESCE(?, last_humidity),
                        last_co2 = COALESCE(?, last_co2),
                        last_pm25 = COALESCE(?, last_pm25),
                        last_noise = COALESCE(?, last_noise),
                        last_light = COALESCE(?, last_light),
                        last_battery = COALESCE(?, last_battery),
                        last_rssi = COALESCE(?, last_rssi),
                        last_seq = COALESCE(?, last_seq),
                        name = CASE WHEN name = '' OR name = device_id
                                    THEN COALESCE(NULLIF(?, ''), name) ELSE name END,
                        room = CASE WHEN room = ''
                                    THEN COALESCE(NULLIF(?, ''), room) ELSE room END
                    WHERE device_id = ?
                    ''',
                    (ts, 1 if inserted_reading else 0) + values + (
                        reading.get('name') or '',
                        reading.get('room') or '',
                        device_id,
                    ),
                )
            else:
                self._conn.execute(
                    'UPDATE nodes SET last_seen = MAX(last_seen, ?) WHERE device_id = ?',
                    (ts, device_id),
                )
            self._conn.commit()
            node = _row_to_dict(
                self._conn.execute('SELECT * FROM nodes WHERE device_id = ?', (device_id,)).fetchone()
            )
        result = dict(reading)
        result['stored'] = True
        result['inserted'] = inserted_reading
        result['current'] = bool(update_snapshot)
        result['node'] = node
        result['received_at'] = now
        return result

    def list_nodes(self, offline_timeout: int = 90, now: int | None = None) -> list[dict[str, Any]]:
        current = int(now if now is not None else time.time())
        rows = self._query(
            '''
            SELECT *,
                   CASE WHEN last_seen >= ? THEN 1 ELSE 0 END AS online
            FROM nodes
            ORDER BY room, name, device_id
            ''',
            (current - offline_timeout,),
        )
        for row in rows:
            row['online'] = bool(row['online'])
            row['age'] = max(0, current - int(row['last_seen']))
        return rows

    def get_node(self, device_id: str) -> dict[str, Any] | None:
        return self._query_one('SELECT * FROM nodes WHERE device_id = ?', (device_id,))

    def update_node(self, device_id: str, name: str | None = None, room: str | None = None) -> dict[str, Any] | None:
        node = self.get_node(device_id)
        if node is None:
            return None
        self._execute(
            'UPDATE nodes SET name = ?, room = ? WHERE device_id = ?',
            (
                (name if name is not None else node['name']).strip(),
                (room if room is not None else node['room']).strip(),
                device_id,
            ),
        )
        return self.get_node(device_id)

    def node_totals(self, device_id: str) -> dict[str, int]:
        readings = self._query_one('SELECT COUNT(*) AS total FROM readings WHERE device_id = ?', (device_id,))
        alerts = self._query_one('SELECT COUNT(*) AS total FROM alerts WHERE device_id = ?', (device_id,))
        active = self._query_one(
            'SELECT COUNT(*) AS total FROM alerts WHERE device_id = ? AND state IN (?, ?, ?)',
            (device_id, *self.ACTIVE_STATES),
        )
        return {
            'readings': int((readings or {}).get('total') or 0),
            'alerts': int((alerts or {}).get('total') or 0),
            'active_alerts': int((active or {}).get('total') or 0),
        }

    def delete_node(self, device_id: str, purge: bool = False, ts: int | None = None) -> dict[str, Any] | None:
        '''删除节点；purge=True 时连读数与告警一起删。'''
        node = self.get_node(device_id)
        if node is None:
            return None
        now = int(ts if ts is not None else time.time())
        totals = self.node_totals(device_id)
        with self._lock:
            if purge:
                deleted_readings = self._conn.execute('DELETE FROM readings WHERE device_id = ?', (device_id,)).rowcount
                deleted_alerts = self._conn.execute('DELETE FROM alerts WHERE device_id = ?', (device_id,)).rowcount
            else:
                deleted_readings = 0
                deleted_alerts = 0
                self._conn.execute(
                    '''
                    UPDATE alerts SET state = 'resolved', resolved_at = ?, updated_at = ?,
                                      resolved_by = 'system',
                                      message = '节点已从系统移除，告警关闭'
                    WHERE device_id = ? AND state IN (?, ?, ?)
                    ''',
                    (now, now, device_id, *self.ACTIVE_STATES),
                )
            self._conn.execute('DELETE FROM nodes WHERE device_id = ?', (device_id,))
            self._conn.commit()
        return {
            'device_id': device_id,
            'name': node.get('name') or device_id,
            'purged': bool(purge),
            'deleted_readings': int(deleted_readings or 0),
            'deleted_alerts': int(deleted_alerts or 0),
            'kept_readings': totals['readings'] if not purge else 0,
            'closed_alerts': totals['active_alerts'] if not purge else 0,
        }

    def readings_between(
        self,
        start: int,
        end: int,
        device_id: str | None = None,
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        '''按时间升序返回窗口内读数；超过上限时保留最新的 N 条。'''
        sql = '''
            SELECT * FROM (
                SELECT device_id, ts, temperature, humidity, co2, pm25, noise, light, battery, rssi
                FROM readings
                WHERE ts >= ? AND ts <= ?
        '''
        params: list[Any] = [start, end]
        if device_id:
            sql += ' AND device_id = ?'
            params.append(device_id)
        sql += ' ORDER BY ts DESC LIMIT ?) ORDER BY ts ASC'
        params.append(max(1, int(limit)))
        return self._query(sql, params)

    def readings_bucketed(
        self,
        start: int,
        end: int,
        bucket: int,
        device_id: str | None = None,
    ) -> list[dict[str, Any]]:
        '''按时间桶聚合读数，供历史曲线使用。'''
        bucket = max(1, int(bucket))
        sql = '''
            SELECT device_id,
                   (ts / ?) * ? AS ts,
                   AVG(temperature) AS temperature,
                   AVG(humidity) AS humidity,
                   AVG(co2) AS co2,
                   AVG(pm25) AS pm25,
                   AVG(noise) AS noise,
                   AVG(light) AS light,
                   MIN(temperature) AS temperature_min,
                   MAX(temperature) AS temperature_max,
                   MIN(humidity) AS humidity_min,
                   MAX(humidity) AS humidity_max,
                   COUNT(*) AS samples
            FROM readings
            WHERE ts >= ? AND ts <= ?
        '''
        params: list[Any] = [bucket, bucket, start, end]
        if device_id:
            sql += ' AND device_id = ?'
            params.append(device_id)
        sql += ' GROUP BY device_id, (ts / ?) ORDER BY device_id, ts ASC'
        params.append(bucket)
        rows = self._query(sql, params)
        for row in rows:
            for key in ('temperature', 'humidity', 'co2', 'pm25', 'noise', 'light',
                        'temperature_min', 'temperature_max', 'humidity_min', 'humidity_max'):
                if row[key] is not None:
                    row[key] = round(float(row[key]), 2)
        return rows

    ACTIVE_STATES = ('open', 'acknowledged', 'muted')

    def active_alert(self, device_id: str, kind: str) -> dict[str, Any] | None:
        return self._query_one(
            '''
            SELECT * FROM alerts
            WHERE device_id = ? AND kind = ? AND state IN (?, ?, ?)
            ORDER BY opened_at DESC LIMIT 1
            ''',
            (device_id, kind, *self.ACTIVE_STATES),
        )

    def active_alert_kinds(self, device_id: str) -> set[str]:
        rows = self._query(
            'SELECT kind FROM alerts WHERE device_id = ? AND state IN (?, ?, ?)',
            (device_id, *self.ACTIVE_STATES),
        )
        return {row['kind'] for row in rows}

    def open_alert(
        self,
        device_id: str,
        kind: str,
        severity: str,
        message: str,
        value: float | None,
        threshold: float | None,
        ts: int | None = None,
    ) -> dict[str, Any]:
        '''新建或刷新一条活跃告警；已确认 / 已静默的状态保持不变。'''
        now = int(ts if ts is not None else time.time())
        active = self.active_alert(device_id, kind)
        if active is None:
            cursor = self._execute(
                '''
                INSERT INTO alerts (device_id, kind, severity, message, value, threshold,
                                    opened_at, updated_at, state)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open')
                ''',
                (device_id, kind, severity, message, value, threshold, now, now),
            )
            alert_id = int(cursor.lastrowid or 0)
        else:
            alert_id = int(active['id'])
            self._execute(
                '''
                UPDATE alerts
                SET severity = ?, message = ?, value = ?, threshold = ?, updated_at = ?
                WHERE id = ?
                ''',
                (severity, message, value, threshold, now, alert_id),
            )
        return self.get_alert(alert_id)  # type: ignore[return-value]

    def resolve_alert(self, device_id: str, kind: str, message: str, ts: int | None = None) -> dict[str, Any] | None:
        '''指标恢复正常：关闭该指标的活跃告警（含已确认与已静默）。'''
        active = self.active_alert(device_id, kind)
        if active is None:
            return None
        now = int(ts if ts is not None else time.time())
        self._execute(
            '''
            UPDATE alerts SET state = 'resolved', message = ?, resolved_at = ?, updated_at = ?,
                              muted_until = NULL
            WHERE id = ?
            ''',
            (message, now, now, active['id']),
        )
        return self.get_alert(int(active['id']))

    def acknowledge_alert(
        self,
        alert_id: int,
        username: str = '',
        note: str | None = None,
    ) -> dict[str, Any] | None:
        '''确认告警：记录处理人与备注，不再计入未处理角标。'''
        now = int(time.time())
        self._execute(
            '''
            UPDATE alerts SET state = 'acknowledged', updated_at = ?, muted_until = NULL,
                              acknowledged_at = COALESCE(acknowledged_at, ?),
                              acknowledged_by = CASE WHEN acknowledged_by IS NULL OR acknowledged_by = ''
                                                     THEN ? ELSE acknowledged_by END,
                              note = COALESCE(NULLIF(?, ''), note)
            WHERE id = ? AND state IN (?, ?, ?)
            ''',
            (now, now, username, note or '', alert_id, *self.ACTIVE_STATES),
        )
        return self.get_alert(alert_id)

    def mute_alert(
        self,
        alert_id: int,
        minutes: int,
        username: str = '',
        note: str | None = None,
    ) -> dict[str, Any] | None:
        '''静默告警：到期前不进入未处理统计，但继续接收数值刷新。'''
        now = int(time.time())
        until = now + max(1, int(minutes)) * 60
        self._execute(
            '''
            UPDATE alerts SET state = 'muted', muted_until = ?, updated_at = ?,
                              acknowledged_at = COALESCE(acknowledged_at, ?),
                              acknowledged_by = CASE WHEN acknowledged_by IS NULL OR acknowledged_by = ''
                                                     THEN ? ELSE acknowledged_by END,
                              note = COALESCE(NULLIF(?, ''), note)
            WHERE id = ? AND state IN (?, ?, ?)
            ''',
            (until, now, now, username, note or '', alert_id, *self.ACTIVE_STATES),
        )
        return self.get_alert(alert_id)

    def expire_mutes(self, now: int | None = None) -> list[dict[str, Any]]:
        '''静默到期后恢复为未处理。'''
        current = int(now if now is not None else time.time())
        rows = self._query(
            "SELECT id FROM alerts WHERE state = 'muted' AND muted_until IS NOT NULL AND muted_until <= ?",
            (current,),
        )
        if not rows:
            return []
        self._execute(
            "UPDATE alerts SET state = 'open', muted_until = NULL, updated_at = ? "
            "WHERE state = 'muted' AND muted_until IS NOT NULL AND muted_until <= ?",
            (current, current),
        )
        return [
            alert
            for alert in (self.get_alert(int(row['id'])) for row in rows)
            if alert and alert['state'] == 'open'
        ]

    def resolve_alert_manual(
        self,
        alert_id: int,
        username: str = '',
        note: str | None = None,
    ) -> dict[str, Any] | None:
        '''人工关闭本次告警；若越限持续，下一条读数会重新开一条新告警。'''
        now = int(time.time())
        self._execute(
            '''
            UPDATE alerts SET state = 'resolved', resolved_at = ?, resolved_by = ?, updated_at = ?,
                              muted_until = NULL, note = COALESCE(NULLIF(?, ''), note)
            WHERE id = ? AND state IN (?, ?, ?)
            ''',
            (now, username, now, note or '', alert_id, *self.ACTIVE_STATES),
        )
        return self.get_alert(alert_id)

    def acknowledge_all(self, username: str = '') -> int:
        now = int(time.time())
        cursor = self._execute(
            '''
            UPDATE alerts SET state = 'acknowledged', updated_at = ?,
                              acknowledged_at = COALESCE(acknowledged_at, ?),
                              acknowledged_by = CASE WHEN acknowledged_by IS NULL OR acknowledged_by = ''
                                                     THEN ? ELSE acknowledged_by END
            WHERE state = 'open'
            ''',
            (now, now, username),
        )
        return int(cursor.rowcount or 0)

    def get_alert(self, alert_id: int) -> dict[str, Any] | None:
        return self._query_one(
            '''
            SELECT alerts.*, COALESCE(NULLIF(nodes.name, ''), alerts.device_id) AS device_name,
                   nodes.room AS room
            FROM alerts LEFT JOIN nodes ON nodes.device_id = alerts.device_id
            WHERE alerts.id = ?
            ''',
            (alert_id,),
        )

    def list_alerts(self, states: Iterable[str] | None = None, limit: int = 200) -> list[dict[str, Any]]:
        sql = '''
            SELECT alerts.*, COALESCE(NULLIF(nodes.name, ''), alerts.device_id) AS device_name,
                   nodes.room AS room
            FROM alerts LEFT JOIN nodes ON nodes.device_id = alerts.device_id
        '''
        params: list[Any] = []
        selected = [state for state in (states or []) if state]
        if selected:
            placeholders = ', '.join('?' for _ in selected)
            sql += f' WHERE alerts.state IN ({placeholders})'
            params.extend(selected)
        sql += '''
            ORDER BY CASE alerts.state
                         WHEN 'open' THEN 0
                         WHEN 'acknowledged' THEN 1
                         WHEN 'muted' THEN 2
                         ELSE 3
                     END,
                     alerts.opened_at DESC
            LIMIT ?
        '''
        params.append(max(1, int(limit)))
        return self._query(sql, params)

    def alert_counts(self) -> dict[str, int]:
        rows = self._query('SELECT state, COUNT(*) AS total FROM alerts GROUP BY state')
        counts = {'open': 0, 'acknowledged': 0, 'muted': 0, 'resolved': 0}
        for row in rows:
            counts[str(row['state'])] = int(row['total'])
        counts['active'] = counts['open'] + counts['acknowledged'] + counts['muted']
        return counts

    def alert_severity_counts(self) -> dict[str, int]:
        rows = self._query(
            'SELECT severity, COUNT(*) AS total FROM alerts WHERE state IN (?, ?, ?) GROUP BY severity',
            self.ACTIVE_STATES,
        )
        counts = {'warning': 0, 'critical': 0}
        for row in rows:
            counts[str(row['severity'])] = int(row['total'])
        return counts

    def add_log(self, level: str, source: str, message: str, ts: int | None = None) -> dict[str, Any]:
        level = level.upper()
        if level not in LOG_LEVELS:
            level = 'INFO'
        now = int(ts if ts is not None else time.time())
        cursor = self._execute(
            'INSERT INTO logs (ts, level, source, message) VALUES (?, ?, ?, ?)',
            (now, level, source, message),
        )
        entry = {
            'id': int(cursor.lastrowid or 0),
            'ts': now,
            'level': level,
            'source': source,
            'message': message,
        }
        if self.console_level:
            self._console(entry)
        return entry

    def list_logs(
        self,
        levels: Iterable[str] | None = None,
        limit: int = 500,
        since_id: int = 0,
    ) -> list[dict[str, Any]]:
        sql = 'SELECT id, ts, level, source, message FROM logs WHERE id > ?'
        params: list[Any] = [max(0, int(since_id))]
        selected = [level.upper() for level in (levels or []) if level]
        if selected:
            placeholders = ', '.join('?' for _ in selected)
            sql += f' AND level IN ({placeholders})'
            params.extend(selected)
        sql += ' ORDER BY id DESC LIMIT ?'
        params.append(max(1, int(limit)))
        rows = self._query(sql, params)
        rows.reverse()
        return rows

    def prune_logs(self, keep: int = 20000) -> int:
        cursor = self._execute(
            '''
            DELETE FROM logs WHERE id <= (
                SELECT MAX(id) - ? FROM logs
            )
            ''',
            (max(0, int(keep)),),
        )
        return int(cursor.rowcount or 0)

    def get_runtime_settings(self, defaults: dict[str, Any]) -> dict[str, Any]:
        row = self._query_one('SELECT value FROM settings WHERE key = ?', (RUNTIME_SETTINGS_KEY,))
        stored: dict[str, Any] = {}
        if row:
            try:
                loaded = json.loads(row['value'])
                if isinstance(loaded, dict):
                    stored = loaded
            except json.JSONDecodeError:
                stored = {}
        merged = json.loads(json.dumps(defaults))
        _merge_settings(merged, stored)
        return merged

    def save_runtime_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        self._execute(
            '''
            INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            ''',
            (RUNTIME_SETTINGS_KEY, json.dumps(settings, ensure_ascii=False), int(time.time())),
        )
        return settings

    def stats(self, offline_timeout: int = 90) -> dict[str, Any]:
        nodes = self.list_nodes(offline_timeout=offline_timeout)
        readings = self._query_one('SELECT COUNT(*) AS total, MAX(ts) AS latest FROM readings') or {}
        cutoff = int(time.time()) - 3600
        recent = self._query_one('SELECT COUNT(*) AS total FROM readings WHERE ts >= ?', (cutoff,)) or {}
        try:
            db_bytes = self.path.stat().st_size
        except OSError:
            db_bytes = 0
        counts = self.alert_counts()
        return {
            'nodes': len(nodes),
            'nodes_online': sum(1 for node in nodes if node['online']),
            'readings': int(readings.get('total') or 0),
            'readings_latest': int(readings.get('latest') or 0),
            'readings_last_hour': int(recent.get('total') or 0),
            'alerts': counts,
            'db_bytes': db_bytes,
        }

    def latest_by_node(self) -> list[dict[str, Any]]:
        return self._query(
            '''
            SELECT device_id, ts, temperature, humidity, co2, pm25, noise, light, battery, rssi
            FROM readings
            WHERE id IN (SELECT MAX(id) FROM readings GROUP BY device_id)
            '''
        )

    def prune(self, retention_days: int) -> dict[str, int]:
        cutoff = int(time.time()) - max(1, int(retention_days)) * 86400
        readings = self._execute('DELETE FROM readings WHERE ts < ?', (cutoff,))
        alerts = self._execute(
            "DELETE FROM alerts WHERE state = 'resolved' AND resolved_at < ?",
            (cutoff,),
        )
        logs = self._execute('DELETE FROM logs WHERE ts < ?', (cutoff,))
        return {
            'readings': int(readings.rowcount or 0),
            'alerts': int(alerts.rowcount or 0),
            'logs': int(logs.rowcount or 0),
        }
