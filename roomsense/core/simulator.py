# -*- encoding: utf-8 -*-
r'''
    ____                       _____
   / __ \____  ____  ____ ___ / ___/___  ____  ________
  / /_/ / __ \/ __ \/ __ `__ \\__ \/ _ \/ __ \/ ___/ _ \
 / _, _/ /_/ / /_/ / / / / / /__/ /  __/ / / (__  )  __/
/_/ |_|\____/\____/_/ /_/ /_/____/\___/_/ /_/____/\___/

@File      :   roomsense/core/simulator.py
@Author    :   RmCat
@Contact   :   RemiliaCatNero@gmail.com
@License   :   AGPL
@Copyright :   (C) 2026, RoomSense-Team
@Desc      :   数据模拟器：按房间场景生成读数，走与真实设备相同的上报链路。
'''

from __future__ import annotations

import math
import random
import threading
import time
from typing import Any

from .gateway import DeviceGateway

PROFILES: dict[str, dict[str, float]] = {
    'classroom': {'temperature': 23.4, 'humidity': 52.0, 'co2': 560.0, 'pm25': 18.0, 'noise': 52.0, 'light': 320.0},
    'office': {'temperature': 24.2, 'humidity': 48.0, 'co2': 620.0, 'pm25': 15.0, 'noise': 45.0, 'light': 340.0},
    'meeting': {'temperature': 23.8, 'humidity': 50.0, 'co2': 700.0, 'pm25': 16.0, 'noise': 46.0, 'light': 300.0},
    'lab': {'temperature': 22.6, 'humidity': 44.0, 'co2': 520.0, 'pm25': 12.0, 'noise': 50.0, 'light': 380.0},
    'server': {'temperature': 25.4, 'humidity': 38.0, 'co2': 460.0, 'pm25': 10.0, 'noise': 62.0, 'light': 120.0},
}

EPISODES: dict[str, dict[str, Any]] = {
    'crowd': {
        'co2_range': (1350.0, 1850.0),
        'temperature': 1.6,
        'humidity': 5.0,
        'noise': 20.0,
        'duration': (100, 200),
    },
    'window': {'temperature': -6.5, 'humidity': -12.0, 'pm25': -8.0, 'noise': 6.0, 'duration': (110, 210)},
    'equipment': {'temperature': 7.5, 'co2_offset': 150.0, 'noise': 10.0, 'duration': (70, 150)},
    'dry': {'humidity': -24.0, 'duration': (120, 220)},
    'dust': {'pm25': 90.0, 'humidity': -6.0, 'noise': 4.0, 'duration': (120, 240)},
}


class Simulator(threading.Thread):
    def __init__(
        self,
        gateway: DeviceGateway,
        devices: list[dict[str, Any]],
        interval: float = 5.0,
        seed: int | None = None,
        offline_demo: bool = True,
    ) -> None:
        super().__init__(name='roomsense-simulator', daemon=True)
        self.gateway = gateway
        self.interval = max(1.0, float(interval))
        self.random = random.Random(seed)
        self.offline_demo = offline_demo
        self._stop_event = threading.Event()
        self._states: list[dict[str, Any]] = []
        for index, device in enumerate(devices or []):
            if not isinstance(device, dict) or not device.get('device_id'):
                continue
            self._states.append(self._init_state(device, index))

    @property
    def device_count(self) -> int:
        return len(self._states)

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:  # pragma: no cover - 线程循环，由集成测试间接覆盖
        self._stop_event.wait(0.5)
        while not self._stop_event.is_set():
            started = time.time()
            try:
                self.tick(now=started)
            except Exception as error:  # 单个节点异常不应让模拟器退出
                self.gateway.storage.add_log('ERROR', 'simulator', f'模拟器 tick 失败：{error}')
            elapsed = time.time() - started
            self._stop_event.wait(max(0.2, self.interval - elapsed))

    def tick(self, now: float | None = None) -> list[dict[str, Any]]:
        '''推进一次模拟，返回本步提交的读数。'''
        current = now if now is not None else time.time()
        submitted: list[dict[str, Any]] = []
        for state in self._states:
            try:
                reading = self._step(state, current)
            except Exception as error:
                self._log_step_error(state, error)
                continue
            if reading is not None:
                submitted.append(reading)
        return submitted

    def _step(self, state: dict[str, Any], current: float) -> dict[str, Any] | None:
        '''推进单个节点；处于离线演示窗口时返回 None。'''
        if state['offline_until'] > current:
            return None
        if self.offline_demo and state['offline_plan'] and state['next_offline'] <= current:
            state['offline_until'] = current + self.random.uniform(100.0, 140.0)
            state['next_offline'] = state['offline_until'] + self.random.uniform(360.0, 540.0)
            self.gateway.storage.add_log(
                'NOTE', 'simulator', f"模拟节点 {state['device_id']} 暂停上报（用于演示离线告警）"
            )
            return None
        self._advance(state, current)
        reading = self._reading(state, current)
        self.gateway.submit(reading, source='simulator')
        return reading

    def _log_step_error(self, state: dict[str, Any], error: Exception) -> None:
        try:
            self.gateway.storage.add_log(
                'ERROR',
                'simulator',
                f"模拟节点 {state.get('device_id', '?')} 推进失败：{type(error).__name__}: {error}",
            )
        except Exception:  # pragma: no cover - 日志写入失败不再向上抛
            pass

    def _init_state(self, device: dict[str, Any], index: int) -> dict[str, Any]:
        default_profile = 'lab' if 'lab' in str(device['device_id']).lower() else 'classroom'
        profile = str(device.get('profile') or default_profile)
        baseline = PROFILES.get(profile) or PROFILES['classroom']
        return {
            'device_id': device['device_id'],
            'name': device.get('name') or device['device_id'],
            'room': device.get('room') or '',
            'profile': profile,
            'temperature': baseline['temperature'] + self.random.uniform(-0.4, 0.4),
            'humidity': baseline['humidity'] + self.random.uniform(-3.0, 3.0),
            'co2': baseline['co2'] + self.random.uniform(-40.0, 40.0),
            'pm25': baseline['pm25'] + self.random.uniform(-3.0, 3.0),
            'noise': baseline['noise'] + self.random.uniform(-2.0, 2.0),
            'light': baseline['light'],
            'battery': 4.02 - index * 0.06 + self.random.uniform(-0.02, 0.02),
            'rssi': -52 + index * 3,
            'baseline': baseline,
            'episode': None,
            'episode_until': 0.0,
            'next_episode': time.time() + self.random.uniform(45.0, 150.0),
            'offline_plan': bool(device.get('offline_demo', index == 2)),
            'offline_until': 0.0,
            'next_offline': time.time() + self.random.uniform(240.0, 360.0),
            'phase': self.random.uniform(0.0, math.tau),
        }

    def _advance(self, state: dict[str, Any], current: float) -> None:
        if state['episode'] and current >= state['episode_until']:
            self.gateway.storage.add_log(
                'NOTE', 'simulator', f"模拟节点 {state['device_id']} 结束事件 {state['episode']}"
            )
            state['episode'] = None
        if state['episode'] is None and current >= state['next_episode']:
            episode = self.random.choice(list(EPISODES))
            config = EPISODES[episode]
            state['episode'] = episode
            state['episode_until'] = current + self.random.uniform(*config['duration'])
            state['next_episode'] = state['episode_until'] + self.random.uniform(120.0, 300.0)

        episode = EPISODES.get(state['episode'] or '', {})
        baseline = state['baseline']
        local = time.localtime(current)
        hour = local.tm_hour + local.tm_min / 60.0
        daily = math.sin((hour - 9.0) / 24.0 * math.tau) * 0.9

        target_temperature = baseline['temperature'] + daily + float(episode.get('temperature', 0.0))
        target_humidity = (
            baseline['humidity']
            - daily * 2.0
            + float(episode.get('humidity', 0.0))
            + math.sin(current / 1800.0 + state['phase']) * 1.2
        )
        target_co2 = baseline['co2'] + math.sin((hour - 7.0) / 24.0 * math.tau) * 90.0
        if episode.get('co2_range'):
            low, high = episode['co2_range']
            target_co2 = self.random.uniform(low, high)
        elif episode.get('co2_offset'):
            target_co2 += float(episode['co2_offset'])
        target_pm25 = baseline['pm25'] + math.sin(current / 3600.0 + state['phase']) * 2.5
        if episode.get('pm25'):
            target_pm25 += float(episode['pm25'])
        target_noise = baseline['noise'] + math.sin(current / 900.0 + state['phase']) * 2.5
        if episode.get('noise'):
            target_noise += float(episode['noise'])

        state['temperature'] += (target_temperature - state['temperature']) * 0.22
        state['temperature'] += self.random.uniform(-0.06, 0.06)
        state['humidity'] += (target_humidity - state['humidity']) * 0.18
        state['humidity'] += self.random.uniform(-0.35, 0.35)
        state['co2'] += (target_co2 - state['co2']) * 0.25
        state['co2'] += self.random.uniform(-12.0, 12.0)
        state['pm25'] += (target_pm25 - state['pm25']) * 0.12
        state['pm25'] += self.random.uniform(-0.8, 0.8)
        state['noise'] += (target_noise - state['noise']) * 0.25
        state['noise'] += self.random.uniform(-1.2, 1.2)
        state['light'] = baseline['light'] + 220.0 * math.sin((hour - 6.0) / 12.0 * math.pi)
        state['light'] += self.random.uniform(-15.0, 15.0)
        state['battery'] = max(3.3, state['battery'] - self.interval / 86400.0 * 0.9)
        state['rssi'] = int(max(-80, min(-40, state['rssi'] + self.random.uniform(-2.0, 2.0))))

        state['temperature'] = round(max(-10.0, min(50.0, state['temperature'])), 2)
        state['humidity'] = round(max(5.0, min(99.0, state['humidity'])), 1)
        state['co2'] = round(max(400.0, min(4000.0, state['co2'])), 0)
        state['pm25'] = round(max(1.0, min(500.0, state['pm25'])), 1)
        state['noise'] = round(max(25.0, min(110.0, state['noise'])), 1)
        state['light'] = round(max(0.0, min(20000.0, state['light'])), 1)

    def _reading(self, state: dict[str, Any], current: float) -> dict[str, Any]:
        return {
            'device_id': state['device_id'],
            'name': state['name'],
            'room': state['room'],
            'ts': int(current),
            'temperature': state['temperature'],
            'humidity': state['humidity'],
            'co2': state['co2'],
            'pm25': state['pm25'],
            'noise': state['noise'],
            'light': state['light'],
            'battery': round(state['battery'], 3),
            'rssi': state['rssi'],
            'seq': int(current) % 2147483647,
        }
