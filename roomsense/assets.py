# -*- encoding: utf-8 -*-
r'''
    ____                       _____
   / __ \____  ____  ____ ___ / ___/___  ____  ________
  / /_/ / __ \/ __ \/ __ `__ \\__ \/ _ \/ __ \/ ___/ _ \
 / _, _/ /_/ / /_/ / / / / / /__/ /  __/ / / (__  )  __/
/_/ |_|\____/\____/_/ /_/ /_/____/\___/_/ /_/____/\___/

@File      :   roomsense/assets.py
@Author    :   RmCat
@Contact   :   RemiliaCatNero@gmail.com
@License   :   AGPL
@Copyright :   (C) 2026, RoomSense-Team
@Desc      :   打包运行期把只读资源释放到 exe 旁边，并按版本刷新。
'''

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .config import BASE_DIR, BUNDLE_DIR, DEFAULT_CONFIG, is_frozen

RELEASED_ASSETS: tuple[str, ...] = ('web', 'doc', 'README.md', 'LICENSE')

CONFIG_RELATIVE = Path('conf') / 'config.json'
DATA_RELATIVE = Path('data')
MARKER_RELATIVE = DATA_RELATIVE / 'assets.json'


@dataclass
class ReleaseReport:
    version: str
    target: Path
    created: int = 0
    '''新写出的文件数。'''

    updated: int = 0
    '''换版本时刷新的文件数（用户没改过的）。'''

    kept: int = 0
    '''因为被用户改过（或写不进去）而保留下来的文件数。'''

    @property
    def changed(self) -> bool:
        return bool(self.created or self.updated)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _bundle_files(bundle: Path, name: str) -> Iterable[tuple[str, Path]]:
    source = bundle / name
    if source.is_file():
        yield name, source
        return
    if not source.is_dir():
        return
    for path in sorted(source.rglob('*')):
        if path.is_file():
            yield path.relative_to(bundle).as_posix(), path


def _read_marker(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    installed = payload.get('installed')
    payload['installed'] = installed if isinstance(installed, dict) else {}
    return payload


def _write_marker(path: Path, version: str, installed: dict[str, str]) -> None:
    payload = {
        'version': version,
        'updated_at': int(time.time()),
        'installed': dict(sorted(installed.items())),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def _write_default_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=4) + '\n', encoding='utf-8')


def ensure_assets(
    version: str,
    *,
    base_dir: str | os.PathLike[str] | None = None,
    bundle_dir: str | os.PathLike[str] | None = None,
    frozen: bool | None = None,
) -> ReleaseReport | None:
    '''在运行目录释放或更新打包资源；源码运行返回 None。'''
    packaged = is_frozen() if frozen is None else bool(frozen)
    if not packaged:
        return None
    base = Path(base_dir if base_dir is not None else BASE_DIR).resolve()
    bundle = Path(bundle_dir if bundle_dir is not None else BUNDLE_DIR).resolve()
    if base == bundle:
        return None

    report = ReleaseReport(version=version, target=base)
    marker_path = base / MARKER_RELATIVE
    marker = _read_marker(marker_path)
    installed: dict[str, str] = {
        str(key): str(value) for key, value in (marker.get('installed') or {}).items()
    }
    first_run = not marker
    version_changed = first_run or str(marker.get('version', '')) != str(version)

    (base / DATA_RELATIVE).mkdir(parents=True, exist_ok=True)
    config_path = base / CONFIG_RELATIVE
    if not config_path.is_file():
        _write_default_config(config_path)
        report.created += 1

    for name in RELEASED_ASSETS:
        for relative, source in _bundle_files(bundle, name):
            target = base / relative
            try:
                if not target.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(source.read_bytes())
                    installed[relative] = _sha256(target)
                    report.created += 1
                    continue
                if not version_changed:
                    continue
                current = _sha256(target)
                bundled = _sha256(source)
                if current == bundled:
                    installed[relative] = bundled
                    continue
                known = installed.get(relative)
                if known is not None and current == known:
                    target.write_bytes(source.read_bytes())
                    installed[relative] = bundled
                    report.updated += 1
                else:
                    report.kept += 1
            except OSError:
                # 只读介质或权限不足：保留包内副本继续运行。
                report.kept += 1

    if report.changed or version_changed or not marker_path.is_file():
        try:
            _write_marker(marker_path, version, installed)
        except OSError:
            pass
    return report
