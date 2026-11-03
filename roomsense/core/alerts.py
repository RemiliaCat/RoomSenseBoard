# -*- encoding: utf-8 -*-
r'''
    ____                       _____
   / __ \____  ____  ____ ___ / ___/___  ____  ________
  / /_/ / __ \/ __ \/ __ `__ \\__ \/ _ \/ __ \/ ___/ _ \
 / _, _/ /_/ / /_/ / / / / / /__/ /  __/ / / (__  )  __/
/_/ |_|\____/\____/_/ /_/ /_/____/\___/_/ /_/____/\___/

@File      :   roomsense/core/alerts.py
@Author    :   RmCat
@Contact   :   RemiliaCatNero@gmail.com
@License   :   AGPL
@Copyright :   (C) 2026, RoomSense-Team
@Desc      :   阈值判定：带回差的上下限规则，输出需要新开或解除的告警。
'''

from __future__ import annotations

from typing import Any


def _format(value: float, unit: str, digits: int = 1) -> str:
    return f'{value:.{digits}f} {unit}'.strip()


def evaluate_reading(
    reading: dict[str, Any],
    settings: dict[str, Any],
    active_kinds: set[str],
) -> list[dict[str, Any]]:
    '''按一条读数给出需要新开或解除的告警。'''
    results: list[dict[str, Any]] = []
    rules = settings.get('alerts') or {}

    temperature = reading.get('temperature')
    humidity = reading.get('humidity')
    co2 = reading.get('co2')

    temp_rules = rules.get('temperature') or {}
    if temperature is not None and temp_rules:
        results.extend(
            _evaluate_pair(
                'temperature',
                '温度',
                float(temperature),
                temp_rules,
                active_kinds,
                unit='°C',
            )
        )

    humidity_rules = rules.get('humidity') or {}
    if humidity is not None and humidity_rules:
        results.extend(
            _evaluate_pair(
                'humidity',
                '湿度',
                float(humidity),
                humidity_rules,
                active_kinds,
                unit='%RH',
            )
        )

    co2_rules = rules.get('co2') or {}
    if co2 is not None and co2_rules.get('high') is not None:
        value = float(co2)
        limit = float(co2_rules.get('high', 1000.0))
        critical = float(co2_rules.get('critical_high', limit + 400.0))
        hysteresis = float(co2_rules.get('hysteresis', 50.0))
        kind = 'co2_high'
        active = value > (limit - hysteresis) if kind in active_kinds else value > limit
        if active:
            severity = 'critical' if value >= critical else 'warning'
            message = f'CO₂ {_format(value, "ppm", 0)} 超过上限 {_format(limit, "ppm", 0)}'
            threshold = critical if severity == 'critical' else limit
            results.append(_result('open', kind, severity, value, threshold, message))
        elif kind in active_kinds:
            message = f'CO₂ 已回落（当前 {_format(value, "ppm", 0)}）'
            results.append(_result('resolve', kind, 'warning', value, limit, message))

    return results


def _evaluate_pair(
    prefix: str,
    label: str,
    value: float,
    rule: dict[str, Any],
    active_kinds: set[str],
    unit: str,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    high_kind = f'{prefix}_high'
    low_kind = f'{prefix}_low'

    if rule.get('high') is not None:
        limit = float(rule['high'])
        critical = float(rule.get('critical_high', limit))
        hysteresis = float(rule.get('hysteresis', 0.0))
        active = value > (limit - hysteresis) if high_kind in active_kinds else value > limit
        if active:
            severity = 'critical' if value >= critical else 'warning'
            threshold = critical if severity == 'critical' else limit
            message = f'{label} {_format(value, unit)} 超过上限 {_format(limit, unit)}'
            results.append(_result('open', high_kind, severity, value, threshold, message))
        elif high_kind in active_kinds:
            results.append(
                _result('resolve', high_kind, 'warning', value, limit, f'{label}已回落（当前 {_format(value, unit)}）')
            )

    if rule.get('low') is not None:
        limit = float(rule['low'])
        critical = float(rule.get('critical_low', limit))
        hysteresis = float(rule.get('hysteresis', 0.0))
        active = value < (limit + hysteresis) if low_kind in active_kinds else value < limit
        if active:
            severity = 'critical' if value <= critical else 'warning'
            threshold = critical if severity == 'critical' else limit
            message = f'{label} {_format(value, unit)} 低于下限 {_format(limit, unit)}'
            results.append(_result('open', low_kind, severity, value, threshold, message))
        elif low_kind in active_kinds:
            results.append(
                _result('resolve', low_kind, 'warning', value, limit, f'{label}已回升（当前 {_format(value, unit)}）')
            )

    return results


def _result(
    action: str,
    kind: str,
    severity: str,
    value: float,
    threshold: float,
    message: str,
) -> dict[str, Any]:
    return {
        'action': action,
        'kind': kind,
        'severity': severity,
        'value': round(value, 2),
        'threshold': round(threshold, 2),
        'message': message,
    }
