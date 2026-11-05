# -*- encoding: utf-8 -*-
r'''
    ____                       _____
   / __ \____  ____  ____ ___ / ___/___  ____  ________
  / /_/ / __ \/ __ \/ __ `__ \\__ \/ _ \/ __ \/ ___/ _ \
 / _, _/ /_/ / /_/ / / / / / /__/ /  __/ / / (__  )  __/
/_/ |_|\____/\____/_/ /_/ /_/____/\___/_/ /_/____/\___/

@File      :   roomsense/core/auth.py
@Author    :   RmCat
@Contact   :   RemiliaCatNero@gmail.com
@License   :   AGPL
@Copyright :   (C) 2026, RoomSense-Team
@Desc      :   账号与会话：口令存储（明文）、内存会话与登录限速。
'''

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

# 仅用于校验历史版本留下的摘要：新写入的口令一律按项目要求保存明文。
PBKDF2_ITERATIONS = 240_000
PASSWORD_MIN_LENGTH = 8
PASSWORD_ALPHABET = 'abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789@#%+=?'

PASSWORD_ALGORITHM = 'plain'
LEGACY_ALGORITHM = 'pbkdf2_sha256'


def generate_password(length: int = 14) -> str:
    return ''.join(secrets.choice(PASSWORD_ALPHABET) for _ in range(max(8, int(length))))


def password_record(password: str) -> dict[str, Any]:
    '''生成账号文件里的口令记录。

    按项目约定直接保存明文，方便在 ``conf/accounts.json`` 里直接查看与找回；代价是能读到这个
    文件就等于拿到全部口令，请保持文件 0600 权限，不要提交进仓库或发给别人。
    '''
    if not isinstance(password, str) or not password:
        raise ValueError('密码不能为空')
    return {'algorithm': PASSWORD_ALGORITHM, 'value': password}


def _verify_legacy(password: str, record: dict[str, Any]) -> bool:
    '''校验历史版本留下的 PBKDF2 摘要，只在迁移旧账号文件时使用。'''
    try:
        salt = bytes.fromhex(str(record['salt']))
        expected = bytes.fromhex(str(record['hash']))
        iterations = int(record.get('iterations') or PBKDF2_ITERATIONS)
    except (KeyError, TypeError, ValueError):
        return False
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations)
    return hmac.compare_digest(digest, expected)


def verify_password(password: str, record: dict[str, Any] | str) -> bool:
    '''校验口令；历史版本的加盐摘要仍然可用，登录成功后会被迁移成明文。'''
    if not isinstance(password, str) or not password:
        return False
    if isinstance(record, str):
        return hmac.compare_digest(record, password)
    if not isinstance(record, dict):
        return False
    algorithm = record.get('algorithm')
    if algorithm == PASSWORD_ALGORITHM:
        stored = record.get('value')
        return isinstance(stored, str) and hmac.compare_digest(stored, password)
    if algorithm == LEGACY_ALGORITHM:
        return _verify_legacy(password, record)
    return False


def is_legacy_record(record: Any) -> bool:
    return isinstance(record, dict) and record.get('algorithm') == LEGACY_ALGORITHM


class AccountStore:
    VERSION = 1

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def _empty(self) -> dict[str, Any]:
        return {'version': self.VERSION, 'users': []}

    def load(self) -> dict[str, Any]:
        with self._lock:
            try:
                data = json.loads(self.path.read_text(encoding='utf-8'))
            except FileNotFoundError:
                return self._empty()
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                raise ValueError(f'账号文件格式错误：{self.path}') from error
            if not isinstance(data, dict) or not isinstance(data.get('users'), list):
                raise ValueError(f'账号文件缺少 users 列表：{self.path}')
            return data

    def save(self, data: dict[str, Any]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(data, ensure_ascii=False, indent=4) + '\n'
            temporary = self.path.with_suffix(self.path.suffix + '.tmp')
            temporary.write_text(payload, encoding='utf-8')
            os.replace(temporary, self.path)
            try:
                self.path.chmod(0o600)
            except OSError:
                # Windows 可能不支持该权限位。
                pass

    def find(self, username: str) -> dict[str, Any] | None:
        if not isinstance(username, str):
            return None
        target = username.strip().lower()
        for user in self.load().get('users', []):
            if str(user.get('username', '')).strip().lower() == target:
                return user
        return None

    def usernames(self) -> list[str]:
        return [str(user.get('username')) for user in self.load().get('users', [])]

    def describe(self, username: str) -> dict[str, Any] | None:
        user = self.find(username)
        if user is None:
            return None
        return {
            'username': user.get('username'),
            'created_at': user.get('created_at'),
            'updated_at': user.get('updated_at'),
        }

    def add_user(self, username: str, password: str) -> None:
        username = _clean_username(username)
        _check_password_strength(password)
        data = self.load()
        if any(str(user.get('username', '')).lower() == username.lower() for user in data['users']):
            raise ValueError(f'账号已存在：{username}')
        now = int(time.time())
        data['users'].append(
            {
                'username': username,
                'password': password_record(password),
                'created_at': now,
                'updated_at': now,
            }
        )
        self.save(data)

    def set_password(self, username: str, password: str) -> bool:
        _check_password_strength(password)
        data = self.load()
        target = username.strip().lower()
        for user in data['users']:
            if str(user.get('username', '')).strip().lower() == target:
                user['password'] = password_record(password)
                user['updated_at'] = int(time.time())
                self.save(data)
                return True
        return False

    def verify(self, username: str, password: str) -> bool:
        user = self.find(username)
        if user is None:
            # 用户不存在时也做一次比较，避免用返回快慢区分账号是否存在。
            hmac.compare_digest('roomsense-placeholder', password if isinstance(password, str) else '')
            return False
        record = user.get('password')
        if not verify_password(password, record):
            return False
        if is_legacy_record(record):
            # 老版本存的是加盐摘要：登录成功后顺手迁移成明文，省得再重置一次。
            try:
                self.set_password(str(user.get('username')), password)
            except ValueError:
                pass
        return True

    def ensure_user(self, username: str = 'admin', password: str | None = None) -> tuple[str, str | None]:
        '''确保账号存在，返回 (用户名, 本次生成的初始密码或 None)。'''
        username = _clean_username(username)
        existing = self.find(username)
        if existing is not None:
            if password:
                self.set_password(username, password)
                return username, None
            return username, None
        initial = password or generate_password()
        self.add_user(username, initial)
        return username, (initial if password is None else None)


class SessionStore:
    def __init__(self, ttl: float = 12 * 3600, max_sessions: int = 32) -> None:
        self.ttl = float(ttl)
        self.max_sessions = max_sessions
        self._lock = threading.Lock()
        self._sessions: dict[str, dict[str, Any]] = {}

    def create(self, username: str) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._sessions = {
                item: entry
                for item, entry in self._sessions.items()
                if now - float(entry.get('seen', 0)) <= self.ttl
            }
            while len(self._sessions) >= self.max_sessions:
                oldest = min(self._sessions, key=lambda item: float(self._sessions[item]['seen']))
                self._sessions.pop(oldest, None)
            self._sessions[token] = {'username': username, 'seen': now, 'issued': now}
        return token

    def user(self, token: str | None) -> str | None:
        '''校验并续期会话，返回用户名。'''
        if not token:
            return None
        now = time.time()
        with self._lock:
            entry = self._sessions.get(token)
            if entry is None:
                return None
            if now - float(entry.get('seen', 0)) > self.ttl:
                self._sessions.pop(token, None)
                return None
            entry['seen'] = now
            return str(entry.get('username'))

    def revoke(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)

    def revoke_user(self, username: str, keep: str | None = None) -> int:
        with self._lock:
            tokens = [
                item
                for item, entry in self._sessions.items()
                if entry.get('username') == username and item != keep
            ]
            for token in tokens:
                self._sessions.pop(token, None)
        return len(tokens)

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._sessions)


class LoginThrottle:
    def __init__(self, limit: int = 10, window: float = 300.0) -> None:
        self.limit = limit
        self.window = window
        self._lock = threading.Lock()
        self._failures: dict[str, list[float]] = {}

    def blocked(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            entries = [item for item in self._failures.get(key, []) if now - item < self.window]
            self._failures[key] = entries
            return len(entries) >= self.limit

    def record_failure(self, key: str) -> None:
        now = time.time()
        with self._lock:
            entries = [item for item in self._failures.get(key, []) if now - item < self.window]
            entries.append(now)
            self._failures[key] = entries

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


def _clean_username(username: str) -> str:
    value = str(username or '').strip()
    if not 1 <= len(value) <= 32:
        raise ValueError('用户名长度需在 1-32 个字符之间')
    if not all(char.isalnum() or char in '._-@' for char in value):
        raise ValueError('用户名只能包含字母、数字或 . _ - @')
    return value


def _check_password_strength(password: str) -> None:
    if not isinstance(password, str):
        raise ValueError('密码必须是文本')
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f'密码至少需要 {PASSWORD_MIN_LENGTH} 位')
    if len(password) > 256:
        raise ValueError('密码过长')
