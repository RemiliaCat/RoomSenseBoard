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
@Desc      :   阈值判定：按指标规则生成需要新开或解除的告警。
'''

from __future__ import annotations

from typing import Any

# 指标 → 展示信息；规则本身由 settings.alerts 提供，含 low / high / critical_* / hysteresis。
METRICS: dict[str, dict[str, Any]] = {
    'temperature': {'label': '温度', 'unit': '°C', 'digits': 1},
    'humidity': {'label': '湿度', 'unit': '%RH', 'digits': 1},
    'co2': {'label': 'CO₂', 'unit': 'ppm', 'digits': 0},
    'pm25': {'label': 'PM2.5', 'unit': 'µg/m³', 'digits': 0},
    'noise': {'label': '噪声', 'unit': 'dB', 'digits': 0},
    'light': {'label': '光照', 'unit': 'lx', 'digits': 0},
    'battery': {'label': '电量', 'unit': 'V', 'digits': 2},
}


def _format(value: float, unit: str, digits: int) -> str:
    return f'{value:.{digits}f} {unit}'


def evaluate_reading(
    reading: dict[str, Any],
    settings: dict[str, Any],
    active_kinds: set[str],
) -> list[dict[str, Any]]:
    '''按一条读数给出需要新开或解除的告警；未上报或未配置的指标跳过。'''
    rules = settings.get('alerts') or {}
    results: list[dict[str, Any]] = []
    for field, meta in METRICS.items():
        value = reading.get(field)
        rule = rules.get(field)
        if value is None or not isinstance(rule, dict) or not rule:
            continue
        results.extend(_evaluate_metric(field, meta, float(value), rule, active_kinds))
    return results


def _evaluate_metric(
    field: str,
    meta: dict[str, Any],
    value: float,
    rule: dict[str, Any],
    active_kinds: set[str],
) -> list[dict[str, Any]]:
    label = meta['label']
    unit = meta['unit']
    digits = int(meta['digits'])
    results: list[dict[str, Any]] = []

    high_kind = f'{field}_high'
    if rule.get('high') is not None:
        limit = float(rule['high'])
        critical = float(rule.get('critical_high', limit))
        hysteresis = float(rule.get('hysteresis', 0.0))
        active = value > (limit - hysteresis) if high_kind in active_kinds else value > limit
        if active:
            severity = 'critical' if value >= critical else 'warning'
            threshold = critical if severity == 'critical' else limit
            message = f'{label} {_format(value, unit, digits)} 超过上限 {_format(limit, unit, digits)}'
            results.append(_result('open', high_kind, severity, value, threshold, message))
        elif high_kind in active_kinds:
            message = f'{label}已回落（当前 {_format(value, unit, digits)}）'
            results.append(_result('resolve', high_kind, 'warning', value, limit, message))

    low_kind = f'{field}_low'
    if rule.get('low') is not None:
        limit = float(rule['low'])
        critical = float(rule.get('critical_low', limit))
        hysteresis = float(rule.get('hysteresis', 0.0))
        active = value < (limit + hysteresis) if low_kind in active_kinds else value < limit
        if active:
            severity = 'critical' if value <= critical else 'warning'
            threshold = critical if severity == 'critical' else limit
            message = f'{label} {_format(value, unit, digits)} 低于下限 {_format(limit, unit, digits)}'
            results.append(_result('open', low_kind, severity, value, threshold, message))
        elif low_kind in active_kinds:
            message = f'{label}已回升（当前 {_format(value, unit, digits)}）'
            results.append(_result('resolve', low_kind, 'warning', value, limit, message))

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
