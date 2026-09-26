'''RoomSenseBoard 核心测试（标准库 unittest，无需额外依赖）。

运行：``python -m unittest discover -s tests -v``
'''

from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from roomsense.api.panel import PanelHTTPServer
from roomsense.app import App
from roomsense.config import DEFAULT_CONFIG, load_config, read_or_create_secret
from roomsense.core.alerts import evaluate_reading
from roomsense.core.auth import AccountStore, generate_password, hash_password, verify_password
from roomsense.core.bus import EventBus
from roomsense.core.gateway import DeviceGateway, ValidationError, default_runtime_settings, normalize_reading
from roomsense.core.simulator import Simulator
from roomsense.core.storage import Storage


def make_config(directory: Path, simulate: bool = False) -> dict:
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    config['storage']['db_path'] = str(directory / 'test.db')
    config['server']['accounts_path'] = str(directory / 'accounts.json')
    config['device']['key_path'] = str(directory / 'device_key.txt')
    config['simulator']['enable'] = simulate
    return config


class StorageTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        config = make_config(Path(self._tmp.name))
        self.storage = Storage(config['storage']['db_path'])

    def tearDown(self) -> None:
        self.storage.close()
        self._tmp.cleanup()

    def test_reading_updates_node_snapshot(self) -> None:
        now = int(time.time())
        self.storage.record_reading(
            {'device_id': 'classroom-301', 'ts': now, 'temperature': 24.5, 'humidity': 51.0, 'name': '301 教室'}
        )
        node = self.storage.get_node('classroom-301')
        self.assertEqual(node['name'], '301 教室')
        self.assertEqual(node['reading_count'], 1)
        self.assertAlmostEqual(node['last_temperature'], 24.5)

    def test_same_timestamp_is_merged_not_dropped(self) -> None:
        now = int(time.time())
        self.storage.record_reading({'device_id': 'node-a', 'ts': now, 'temperature': 20.0})
        result = self.storage.record_reading({'device_id': 'node-a', 'ts': now, 'temperature': 26.0})
        self.assertTrue(result['stored'])
        self.assertFalse(result['inserted'])
        node = self.storage.get_node('node-a')
        self.assertEqual(node['reading_count'], 1, '同一秒重复上报不应重复计数')
        self.assertAlmostEqual(node['last_temperature'], 26.0)

    def test_backfilled_reading_does_not_override_snapshot(self) -> None:
        now = int(time.time())
        self.storage.record_reading({'device_id': 'node-a', 'ts': now, 'temperature': 25.0})
        result = self.storage.record_reading({'device_id': 'node-a', 'ts': now - 600, 'temperature': 10.0})
        self.assertFalse(result['current'])
        self.assertAlmostEqual(self.storage.get_node('node-a')['last_temperature'], 25.0)

    def test_bucketed_readings_aggregate(self) -> None:
        start = (int(time.time()) // 300) * 300 - 600
        for index in range(6):
            self.storage.record_reading(
                {
                    'device_id': 'node-a',
                    'ts': start + index * 60,
                    'temperature': 20.0 + index,
                    'humidity': 50.0,
                    'pm25': 10.0 + index,
                    'noise': 40.0 + index,
                    'light': 300.0 + index,
                }
            )
        rows = self.storage.readings_bucketed(start - 10, start + 600, 300, 'node-a')
        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(rows[0]['temperature'], 22.0)
        self.assertAlmostEqual(rows[1]['temperature'], 25.0)
        self.assertAlmostEqual(rows[0]['pm25'], 12.0)
        self.assertAlmostEqual(rows[0]['noise'], 42.0)
        self.assertAlmostEqual(rows[0]['light'], 302.0)

    def test_legacy_database_gets_new_columns(self) -> None:
        '''旧版本数据库缺少 pm25/noise 列时，打开存储应自动补列且不影响写读。'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'legacy.db'
            connection = sqlite3.connect(path)
            connection.executescript(
                '''
                CREATE TABLE nodes (
                    device_id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', room TEXT NOT NULL DEFAULT '',
                    first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL,
                    last_temperature REAL, last_humidity REAL, last_co2 REAL, last_light REAL,
                    last_battery REAL, last_rssi INTEGER, last_seq INTEGER, reading_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE readings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL, ts INTEGER NOT NULL,
                    temperature REAL, humidity REAL, co2 REAL, light REAL, battery REAL, rssi INTEGER, seq INTEGER,
                    UNIQUE (device_id, ts)
                );
                '''
            )
            connection.commit()
            connection.close()

            storage = Storage(path)
            try:
                storage.record_reading(
                    {
                        'device_id': 'legacy-node',
                        'ts': int(time.time()),
                        'temperature': 22.0,
                        'pm25': 12.0,
                        'noise': 45.0,
                    }
                )
                node = storage.get_node('legacy-node')
                self.assertAlmostEqual(node['last_pm25'], 12.0)
                self.assertAlmostEqual(node['last_noise'], 45.0)
                rows = storage.readings_between(0, int(time.time()) + 10, device_id='legacy-node')
                self.assertAlmostEqual(rows[0]['pm25'], 12.0)
            finally:
                storage.close()

    def test_delete_node_keeps_history_and_closes_alerts(self) -> None:
        now = int(time.time())
        self.storage.record_reading({'device_id': 'node-x', 'ts': now, 'temperature': 24.0})
        self.storage.open_alert('node-x', 'device_offline', 'warning', '节点离线', 120.0, 90.0, ts=now)

        result = self.storage.delete_node('node-x')
        self.assertIsNotNone(result)
        self.assertFalse(result['purged'])
        self.assertGreaterEqual(result['kept_readings'], 1)
        self.assertEqual(result['closed_alerts'], 1)
        self.assertIsNone(self.storage.get_node('node-x'))
        self.assertTrue(self.storage.readings_between(0, now + 10, device_id='node-x'), '默认保留历史读数')
        self.assertEqual(self.storage.alert_counts()['active'], 0, '活跃告警应被关闭')
        resolved = self.storage.list_alerts(states=['resolved'])
        self.assertEqual(resolved[0]['message'], '节点已从系统移除，告警关闭')
        self.assertIsNone(self.storage.delete_node('node-x'), '节点已删除，再删返回 None')

    def test_delete_node_with_purge_removes_readings_and_alerts(self) -> None:
        now = int(time.time())
        self.storage.record_reading({'device_id': 'node-y', 'ts': now, 'temperature': 24.0})
        self.storage.open_alert('node-y', 'temperature_high', 'warning', '温度过高', 30.0, 28.0, ts=now)

        result = self.storage.delete_node('node-y', purge=True)
        self.assertTrue(result['purged'])
        self.assertEqual(result['deleted_readings'], 1)
        self.assertEqual(result['deleted_alerts'], 1)
        self.assertIsNone(self.storage.get_node('node-y'))
        self.assertEqual(self.storage.readings_between(0, now + 10, device_id='node-y'), [])
        self.assertEqual(self.storage.list_alerts(states=None), [])


class AlertRuleTest(unittest.TestCase):
    RULES = default_runtime_settings(json.loads(json.dumps(DEFAULT_CONFIG)))['alerts']

    def test_high_temperature_opens_warning_and_critical(self) -> None:
        warning = evaluate_reading({'temperature': 29.0}, {'alerts': self.RULES}, set())
        self.assertEqual([item['kind'] for item in warning], ['temperature_high'])
        self.assertEqual(warning[0]['severity'], 'warning')
        critical = evaluate_reading({'temperature': 33.0}, {'alerts': self.RULES}, set())
        self.assertEqual(critical[0]['severity'], 'critical')

    def test_hysteresis_keeps_alert_active_near_threshold(self) -> None:
        active = {'temperature_high'}
        inside_hysteresis = evaluate_reading({'temperature': 27.8}, {'alerts': self.RULES}, active)
        self.assertEqual(
            [item['action'] for item in inside_hysteresis],
            ['open'],
            '回差范围内告警保持活跃，并刷新当前值',
        )
        recovered = evaluate_reading({'temperature': 27.4}, {'alerts': self.RULES}, active)
        self.assertEqual(recovered[0]['action'], 'resolve')

    def test_missing_optional_metric_is_ignored(self) -> None:
        results = evaluate_reading({'temperature': 25.0, 'humidity': 50.0}, {'alerts': self.RULES}, set())
        self.assertEqual(results, [])

    def test_co2_only_evaluated_when_reported(self) -> None:
        results = evaluate_reading({'temperature': 25.0, 'co2': 1800.0}, {'alerts': self.RULES}, set())
        kinds = [item['kind'] for item in results]
        self.assertEqual(kinds, ['co2_high'])
        self.assertEqual(results[0]['severity'], 'critical')


class GatewayTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        config = make_config(Path(self._tmp.name))
        self.storage = Storage(config['storage']['db_path'])
        self.settings = default_runtime_settings(config)
        self.gateway = DeviceGateway(self.storage, EventBus(), lambda: dict(self.settings))

    def tearDown(self) -> None:
        self.storage.close()
        self._tmp.cleanup()

    def test_submit_opens_alert_and_recovery_resolves_it(self) -> None:
        opened = self.gateway.submit({'device_id': 'node-a', 'temperature': 31.0, 'humidity': 50.0})
        self.assertEqual([item['kind'] for item in opened['opened']], ['temperature_high'])
        self.assertEqual(self.storage.alert_counts()['open'], 1)
        recovered = self.gateway.submit({'device_id': 'node-a', 'temperature': 24.0, 'humidity': 50.0})
        self.assertEqual([item['kind'] for item in recovered['resolved']], ['temperature_high'])
        self.assertEqual(self.storage.alert_counts()['open'], 0)

    def test_offline_alert_opens_and_clears_after_report(self) -> None:
        self.gateway.submit({'device_id': 'node-a', 'temperature': 24.0})
        self.storage._execute(
            'UPDATE nodes SET last_seen = ? WHERE device_id = ?', (int(time.time()) - 600, 'node-a')
        )
        opened = self.gateway.check_offline()
        self.assertEqual([item['kind'] for item in opened], ['device_offline'])
        self.gateway.submit({'device_id': 'node-a', 'temperature': 24.5})
        self.assertEqual(self.storage.alert_counts()['active'], 0)

    def test_batch_submit_accepts_multiple_readings(self) -> None:
        now = int(time.time())
        result = self.gateway.submit(
            {
                'readings': [
                    {'device_id': 'node-a', 'ts': now - 10, 'temperature': 23.0},
                    {'device_id': 'node-a', 'ts': now - 5, 'temperature': 23.5},
                ]
            }
        )
        self.assertEqual(result['accepted'], 2)
        self.assertEqual(result['stored'], 2)

    def test_settings_rejects_invalid_thresholds(self) -> None:
        with self.assertRaises(ValidationError):
            self.gateway.update_settings({'alerts': {'temperature': {'low': 40.0}}})
        with self.assertRaises(ValidationError):
            self.gateway.update_settings({'offline_timeout': 'soon'})

    def test_settings_clamps_offline_timeout(self) -> None:
        updated = self.gateway.update_settings({'offline_timeout': 1})
        self.assertEqual(updated['offline_timeout'], 15)

    def test_normalize_rejects_bad_values(self) -> None:
        with self.assertRaises(ValidationError):
            normalize_reading({'device_id': 'bad id', 'temperature': 20.0})
        with self.assertRaises(ValidationError):
            normalize_reading({'device_id': 'node-a', 'humidity': 140.0})
        with self.assertRaises(ValidationError):
            normalize_reading({'device_id': 'node-a', 'battery': 4.0})
        with self.assertRaises(ValidationError):
            normalize_reading({'device_id': 'node-a', 'pm25': 5000.0})
        with self.assertRaises(ValidationError):
            normalize_reading({'device_id': 'node-a', 'noise': 200.0})

    def test_new_metrics_are_stored_and_snapshotted(self) -> None:
        result = self.gateway.submit(
            {
                'device_id': 'node-b',
                'temperature': 23.0,
                'humidity': 48.0,
                'pm25': 14.5,
                'noise': 47.2,
                'light': 320.0,
            }
        )
        self.assertEqual(result['accepted'], 1)
        node = self.storage.get_node('node-b')
        self.assertAlmostEqual(node['last_pm25'], 14.5)
        self.assertAlmostEqual(node['last_noise'], 47.2)
        self.assertAlmostEqual(node['last_light'], 320.0)
        rows = self.storage.readings_between(0, int(time.time()) + 10, device_id='node-b')
        self.assertAlmostEqual(rows[0]['pm25'], 14.5)
        self.assertAlmostEqual(rows[0]['noise'], 47.2)


class AccountTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = AccountStore(Path(self._tmp.name) / 'accounts.json')

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_password_hash_roundtrip(self) -> None:
        record = hash_password('correct horse battery')
        self.assertEqual(record['algorithm'], 'pbkdf2_sha256')
        self.assertTrue(verify_password('correct horse battery', record))
        self.assertFalse(verify_password('wrong password', record))
        self.assertNotIn('correct horse battery', json.dumps(record))

    def test_first_run_creates_account_with_generated_password(self) -> None:
        username, password = self.store.ensure_user('admin')
        self.assertEqual(username, 'admin')
        self.assertIsNotNone(password)
        self.assertTrue(self.store.verify('admin', password or ''))
        # 再次调用不会重置密码
        self.assertEqual(self.store.ensure_user('admin'), ('admin', None))

    def test_password_change_and_unknown_user(self) -> None:
        self.store.add_user('teacher', 'initial-pass')
        self.assertTrue(self.store.verify('teacher', 'initial-pass'))
        self.assertTrue(self.store.set_password('teacher', 'new-password'))
        self.assertFalse(self.store.verify('teacher', 'initial-pass'))
        self.assertTrue(self.store.verify('teacher', 'new-password'))
        self.assertFalse(self.store.verify('nobody', 'whatever'))

    def test_weak_password_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.add_user('teacher', 'short')
        with self.assertRaises(ValueError):
            self.store.add_user('bad name!', 'long-enough-password')

    def test_generated_password_is_readable_and_long_enough(self) -> None:
        password = generate_password()
        self.assertGreaterEqual(len(password), 8)
        self.assertFalse(set('0O1lI') & set(password))


class SimulatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.config = make_config(Path(self._tmp.name))
        self.storage = Storage(self.config['storage']['db_path'])
        settings = default_runtime_settings(self.config)
        self.gateway = DeviceGateway(self.storage, EventBus(), lambda: dict(settings))
        self.simulator = Simulator(
            self.gateway,
            self.config['simulator']['devices'],
            interval=5.0,
            seed=2026,
            offline_demo=True,
        )

    def tearDown(self) -> None:
        self.storage.close()
        self._tmp.cleanup()

    def test_tick_reports_every_metric_for_every_device(self) -> None:
        readings = self.simulator.tick(now=time.time())
        self.assertEqual(len(readings), len(self.config['simulator']['devices']))
        for reading in readings:
            for field in ('temperature', 'humidity', 'co2', 'pm25', 'noise', 'light', 'battery', 'rssi', 'seq'):
                self.assertIn(field, reading)
                self.assertIsNotNone(reading[field], f'{reading["device_id"]} 缺少 {field}')
        node = self.storage.get_node(readings[0]['device_id'])
        self.assertIsNotNone(node['last_pm25'])
        self.assertIsNotNone(node['last_noise'])

    def test_simulated_scenes_cover_different_room_types(self) -> None:
        rooms = {device.get('profile') for device in self.config['simulator']['devices']}
        self.assertGreaterEqual(len(rooms), 4, f'场景过于单一：{rooms}')

    def test_offline_window_skips_reporting(self) -> None:
        current = time.time()
        self.simulator._states[0]['offline_until'] = current + 60
        readings = self.simulator.tick(now=current)
        skipped = self.simulator._states[0]['device_id']
        self.assertNotIn(skipped, [reading['device_id'] for reading in readings])

    def test_every_episode_can_run(self) -> None:
        from roomsense.core.simulator import EPISODES

        current = time.time()
        for name in EPISODES:
            with self.subTest(episode=name):
                state = self.simulator._states[0]
                state['episode'] = name
                state['episode_until'] = current + 60
                state['next_episode'] = current + 600
                self.simulator._advance(state, current)
                reading = self.simulator._reading(state, current)
                self.assertIsNotNone(reading['co2'])
                self.assertIsNotNone(reading['pm25'])
                self.assertIsNotNone(reading['noise'])

    def test_tick_isolates_broken_device(self) -> None:
        '''单个节点出错不应拖垮整轮模拟。'''
        current = time.time()
        broken = self.simulator._states[0]
        broken['baseline'] = None  # 触发 _advance 内部异常
        readings = self.simulator.tick(now=current)
        self.assertEqual(len(readings), len(self.simulator._states) - 1)
        errors = self.storage.list_logs(levels=['ERROR'], limit=5)
        self.assertTrue(any('推进失败' in entry['message'] for entry in errors), errors)


class ConfigTest(unittest.TestCase):
    def test_secret_file_is_created_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'nested' / 'device_key.txt'
            first = read_or_create_secret(path)
            second = read_or_create_secret(path)
            self.assertTrue(first)
            self.assertEqual(first, second)

    def test_environment_override_is_applied(self) -> None:
        import os

        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / 'config.json'
            config_path.write_text(json.dumps({'server': {'port': 1234}}), encoding='utf-8')
            os.environ['ROOMSENSE_PORT'] = '4321'
            os.environ['ROOMSENSE_ADMIN_USER'] = 'teacher'
            try:
                config = load_config(config_path)
            finally:
                os.environ.pop('ROOMSENSE_PORT', None)
                os.environ.pop('ROOMSENSE_ADMIN_USER', None)
            self.assertEqual(config['server']['port'], 4321)
            self.assertEqual(config['server']['admin_user'], 'teacher')
            self.assertEqual(config['storage']['retention_days'], 30)


class HttpApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        config = make_config(Path(cls._tmp.name), simulate=True)
        config['server']['admin_user'] = 'admin'
        config['server']['admin_password'] = 'testing-password'
        config['server']['cors_origins'] = ['http://panel.example']
        config['simulator']['interval'] = 60
        cls.app = App(config)
        cls.server = PanelHTTPServer(('127.0.0.1', 0), cls.app)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.app.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.app.stop()
        cls._tmp.cleanup()

    def call(
        self,
        path: str,
        method: str = 'GET',
        body: dict | None = None,
        headers: dict | None = None,
        with_headers: bool = False,
    ):
        data = json.dumps(body).encode('utf-8') if body is not None else None
        request = urllib.request.Request(
            f'http://127.0.0.1:{self.port}{path}', data=data, method=method, headers=headers or {}
        )
        if data:
            request.add_header('Content-Type', 'application/json')
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode('utf-8')) if response.length != 0 else {}
                if with_headers:
                    return response.status, payload, dict(response.headers)
                return response.status, payload
        except urllib.error.HTTPError as error:
            payload = json.loads(error.read().decode('utf-8'))
            return (error.code, payload, dict(error.headers)) if with_headers else (error.code, payload)

    def login(self, username: str = 'admin', password: str = 'testing-password') -> dict:
        status, payload = self.call('/api/login', 'POST', {'username': username, 'password': password})
        self.assertEqual(status, 200)
        return {'X-Auth-Token': payload['session']}

    def test_health_is_public_and_overview_is_not(self) -> None:
        self.assertEqual(self.call('/api/health')[0], 200)
        self.assertEqual(self.call('/api/overview')[0], 401)

    def test_login_requires_correct_credentials(self) -> None:
        self.assertEqual(self.call('/api/login', 'POST', {'username': 'admin', 'password': 'nope'})[0], 401)
        self.assertEqual(self.call('/api/login', 'POST', {'username': 'admin'})[0], 400)
        headers = self.login()
        status, payload = self.call('/api/session', 'POST', {}, headers)
        self.assertEqual(status, 200)
        self.assertEqual(payload['username'], 'admin')

    def test_password_change_requires_current_password(self) -> None:
        headers = self.login()
        status, payload = self.call(
            '/api/account/password', 'POST', {'current_password': 'wrong', 'new_password': 'brand-new-pass'}, headers
        )
        self.assertEqual(status, 403)
        self.assertIn('当前密码', payload['error'])
        status, _ = self.call(
            '/api/account/password',
            'POST',
            {'current_password': 'testing-password', 'new_password': 'short'},
            headers,
        )
        self.assertEqual(status, 400)

    def test_ingest_requires_device_key(self) -> None:
        status, payload = self.call('/api/ingest', 'POST', {'device_id': 'node-a', 'temperature': 22.0})
        self.assertEqual(status, 401)
        self.assertIn('设备密钥', payload['error'])

    def test_ingest_opens_alert_visible_to_panel(self) -> None:
        headers = self.login()
        device_key = Path(self.app.config['device']['key_path']).read_text(encoding='utf-8').strip()
        status, payload = self.call(
            '/api/ingest',
            'POST',
            {'device_id': 'http-node', 'name': 'HTTP 节点', 'temperature': 33.5, 'humidity': 50.0},
            {'X-Device-Key': device_key},
        )
        self.assertEqual(status, 200)
        self.assertEqual([item['kind'] for item in payload['opened']], ['temperature_high'])
        self.assertEqual(payload['opened'][0]['severity'], 'critical')

        status, alerts = self.call('/api/alerts?state=active', headers=headers)
        self.assertEqual(status, 200)
        self.assertTrue(any(item['device_id'] == 'http-node' for item in alerts['alerts']))

        status, overview = self.call('/api/overview', headers=headers)
        self.assertEqual(status, 200)
        self.assertTrue(any(node['device_id'] == 'http-node' for node in overview['nodes']))

    def test_device_key_is_accepted_as_bearer_token(self) -> None:
        device_key = Path(self.app.config['device']['key_path']).read_text(encoding='utf-8').strip()
        status, _ = self.call(
            '/api/ingest',
            'POST',
            {'device_id': 'bearer-node', 'temperature': 22.0},
            {'Authorization': f'Bearer {device_key}'},
        )
        self.assertEqual(status, 200)

    def test_readings_endpoint_returns_series(self) -> None:
        headers = self.login()
        status, payload = self.call('/api/readings?range=1h', headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(payload['range'], '1h')
        self.assertIn('series', payload)

    def test_stream_requires_session_and_pushes_reading(self) -> None:
        '''SSE 流需要会话，且上报后能收到 reading 事件。'''
        self.assertEqual(self.call('/api/stream')[0], 401)
        status, payload = self.call(
            '/api/login', 'POST', {'username': 'admin', 'password': 'testing-password'}
        )
        self.assertEqual(status, 200)
        session = payload['session']
        url = f'http://127.0.0.1:{self.port}/api/stream?token={session}'
        with urllib.request.urlopen(url, timeout=10) as response:
            self.assertEqual(response.status, 200)
            self.assertIn('text/event-stream', response.headers.get('Content-Type', ''))
            self.assertEqual(response.readline().decode('utf-8').strip(), 'event: hello')
            response.readline()  # data
            response.readline()  # 空行

            device_key = Path(self.app.config['device']['key_path']).read_text(encoding='utf-8').strip()
            self.call(
                '/api/ingest',
                'POST',
                {'device_id': 'stream-node', 'temperature': 23.4, 'humidity': 48.0},
                {'X-Device-Key': device_key},
            )

            events: list[str] = []
            deadline = time.time() + 10
            while time.time() < deadline:
                line = response.readline().decode('utf-8').strip()
                if not line:
                    continue
                events.append(line)
                if line == 'event: reading':
                    break
            self.assertIn('event: reading', events, f'未收到 reading 事件：{events}')

    def test_delete_node_removes_it_and_optionally_purges_history(self) -> None:
        self.assertEqual(self.call('/api/nodes/anything', 'DELETE')[0], 401)
        headers = self.login()
        device_key = Path(self.app.config['device']['key_path']).read_text(encoding='utf-8').strip()
        for device_id in ('retired-node', 'purged-node'):
            status, _ = self.call(
                '/api/ingest',
                'POST',
                {'device_id': device_id, 'name': device_id, 'temperature': 22.0},
                {'X-Device-Key': device_key},
            )
            self.assertEqual(status, 200)

        status, payload = self.call('/api/nodes/retired-node', 'DELETE', None, headers)
        self.assertEqual(status, 200)
        self.assertFalse(payload['purged'])
        self.assertTrue(payload['message'])
        status, nodes = self.call('/api/nodes', headers=headers)
        self.assertNotIn('retired-node', [node['device_id'] for node in nodes['nodes']])
        status, _ = self.call('/api/nodes/retired-node', 'DELETE', None, headers)
        self.assertEqual(status, 404, '重复删除应返回 404')

        status, payload = self.call('/api/nodes/purged-node?purge=1', 'DELETE', None, headers)
        self.assertEqual(status, 200)
        self.assertTrue(payload['purged'])
        self.assertGreaterEqual(payload['deleted_readings'], 1)

    def test_settings_put_validates_input(self) -> None:
        headers = self.login()
        status, payload = self.call(
            '/api/settings', 'PUT', {'offline_timeout': 45, 'retention_days': 7}, headers
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload['settings']['offline_timeout'], 45)
        self.assertTrue(payload['device']['key'])
        status, payload = self.call(
            '/api/settings', 'PUT', {'alerts': {'temperature': {'low': 50.0}}}, headers
        )
        self.assertEqual(status, 400)
        self.assertIn('下限必须小于上限', payload['error'])

    def test_cors_preflight_only_allows_configured_origin(self) -> None:
        status, _, headers = self.call(
            '/api/overview', 'OPTIONS', headers={'Origin': 'http://panel.example'}, with_headers=True
        )
        self.assertEqual(status, 204)
        self.assertEqual(headers.get('Access-Control-Allow-Origin'), 'http://panel.example')
        status, _, headers = self.call(
            '/api/overview', 'OPTIONS', headers={'Origin': 'http://evil.example'}, with_headers=True
        )
        self.assertEqual(status, 403)
        self.assertIsNone(headers.get('Access-Control-Allow-Origin'))


if __name__ == '__main__':
    unittest.main()
