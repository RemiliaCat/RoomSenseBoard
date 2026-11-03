# -*- encoding: utf-8 -*-
r'''
    ____                       _____
   / __ \____  ____  ____ ___ / ___/___  ____  ________
  / /_/ / __ \/ __ \/ __ `__ \\__ \/ _ \/ __ \/ ___/ _ \
 / _, _/ /_/ / /_/ / / / / / /__/ /  __/ / / (__  )  __/
/_/ |_|\____/\____/_/ /_/ /_/____/\___/_/ /_/____/\___/

@File      :   main.py
@Author    :   RmCat
@Contact   :   RemiliaCatNero@gmail.com
@License   :   AGPL
@Copyright :   (C) 2026, RoomSense-Team
@Desc      :   服务入口：解析命令行、释放资源、绑定端口并拉起 App。
'''

from __future__ import annotations

import argparse
import errno
import sys
import webbrowser
from pathlib import Path

from roomsense import __version__
from roomsense.api.panel import PanelHTTPServer
from roomsense.app import App
from roomsense.assets import ensure_assets
from roomsense.config import is_frozen, load_config
from roomsense.netinfo import WILDCARD_HOSTS, join_url, resolve_endpoint

PORT_RETRY = 10

SOURCE_TEXT = {
    'configured': '来自配置或设置页',
    'listen': '来自监听地址',
    'lan': '按本机网卡推断',
    'loopback': '本机回环地址',
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='RoomSenseBoard 室内环境感知面板')
    parser.add_argument('--config', default='./conf/config.json', help='配置文件路径（默认 ./conf/config.json）')
    parser.add_argument('--host', default=None, help='监听地址，覆盖配置文件')
    parser.add_argument('--port', type=int, default=None, help='监听端口，覆盖配置文件')
    parser.add_argument('--simulator', action='store_true', help='启动内置数据模拟器（默认关闭）')
    parser.add_argument(
        '--no-simulator',
        action='store_true',
        help='强制关闭数据模拟器（默认即关闭，保留该参数以兼容旧命令行）',
    )
    parser.add_argument('--verbose', action='store_true', help='在终端输出每个 HTTP 请求日志')
    parser.add_argument('--open', action='store_true', help='启动后自动打开浏览器')
    parser.add_argument(
        '--reset-password',
        nargs='?',
        const='admin',
        default=None,
        metavar='USER',
        help='把指定账号（默认 admin）的密码重置为随机值后退出',
    )
    return parser.parse_args(argv)


def release_runtime_assets() -> None:
    try:
        report = ensure_assets(__version__)
    except OSError as error:
        print(f'警告：无法在程序目录释放资源（{error}），本次直接使用程序内置资源')
        return
    if report is None:
        return
    if report.created:
        print(f'首次运行：已把 web/、conf/config.json、data/ 等资源释放到 {report.target}')
    elif report.updated:
        print(f'资源已更新到 v{report.version}（你改过的文件已保留）')


def bind_server(app: App, host: str, port: int) -> PanelHTTPServer:
    last_error: OSError | None = None
    for offset in range(PORT_RETRY + 1):
        candidate = port + offset
        if candidate > 65535:
            break
        try:
            return PanelHTTPServer((host, candidate), app)
        except OSError as error:
            if error.errno not in (errno.EADDRINUSE, errno.EACCES):
                raise
            last_error = error
            if port == 0:
                break
            print(f'端口 {candidate} 不可用，尝试 {candidate + 1} …')
    raise SystemExit(f'无法监听 {host}:{port}：{last_error}')


def build_app(args: argparse.Namespace) -> App:
    config = load_config(args.config)
    if args.host:
        config['server']['host'] = args.host
    if args.port is not None:
        config['server']['port'] = args.port
    if args.simulator:
        config['simulator']['enable'] = True
    if args.no_simulator:
        config['simulator']['enable'] = False
    if args.verbose:
        config['server']['verbose'] = True
    return App(config)


def _listen_display(host: str, port: int) -> str:
    return f'[{host}]:{port}' if ':' in host else f'{host}:{port}'


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    args = parse_args(argv)

    release_runtime_assets()

    app = build_app(args)

    if args.reset_password:
        try:
            username, password = app.reset_password(args.reset_password)
        except ValueError as error:
            print(error)
            app.storage.close()
            return 1
        print(f'账号 {username} 的新密码：{password}')
        print('请立即登录面板并在“设置 → 账号安全”中修改。')
        sys.stdout.flush()
        app.storage.close()
        return 0

    try:
        server = bind_server(app, app.config['server']['host'], int(app.config['server']['port']))
    except SystemExit as error:
        print(error)
        app.storage.close()
        return 1

    host, port = server.server_address[0], server.server_address[1]
    endpoint = resolve_endpoint(
        str(app.runtime_settings().get('public_url') or ''), str(host), int(port)
    )
    panel_url = join_url(endpoint.base_url, '/')
    ingest_url = join_url(endpoint.base_url, '/api/ingest')
    accounts_path = Path(app.config['server']['accounts_path'])
    device_key_path = Path(app.config['device']['key_path'])
    static_index = Path(app.config['web']['static_dir']) / 'index.html'

    app.start()
    print('RoomSenseBoard 已启动')
    if host in WILDCARD_HOSTS:
        print(f'  监听地址：{_listen_display(str(host), int(port))}')
    if endpoint.source == 'configured':
        print(f'  对外地址：{endpoint.base_url}（{SOURCE_TEXT[endpoint.source]}）')
    print(f'  监控面板：{panel_url}')
    if len(endpoint.candidates) > 1:
        others = '、'.join(join_url(item, '/') for item in endpoint.candidates[1:4])
        print(f'  其它地址：{others}')
    if not static_index.is_file():
        print(f'  警告：缺少前端页面 {static_index}，面板将无法打开')
    print(f'  管理账号：{app.admin_user}（账号文件 {accounts_path}）')
    if app.initial_password:
        print(f'  初始密码：{app.initial_password}')
        print('           仅在首次创建账号时显示，请登录后立即在“设置 → 账号安全”中修改。')
    else:
        print('  账号密码：沿用 conf/accounts.json，忘记时可执行 RoomSenseBoard.exe --reset-password')
    print(f'  数据上传：{ingest_url}（节点密钥 {device_key_path}）')
    print(f"  数据模拟：{'开启' if app.simulator else '关闭'}")
    print('  按 Ctrl+C 停止服务')
    sys.stdout.flush()

    # 双击 exe 启动时自动打开浏览器。
    if args.open or (is_frozen() and not arguments):
        webbrowser.open(panel_url)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print('\n正在停止 …')
    finally:
        server.shutdown()
        server.server_close()
        app.stop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
