'''RoomSenseBoard 服务入口。

用法::

    python main.py                          # 默认读取 conf/config.json，带模拟节点
    python main.py --no-simulator           # 只接收真实设备上报
    python main.py --reset-password admin   # 重置账号密码并退出（不启动服务）
    python main.py --port 20500 --open
'''

from __future__ import annotations

import argparse
import errno
import sys
import webbrowser
from pathlib import Path

from roomsense.api.panel import PanelHTTPServer
from roomsense.app import App
from roomsense.config import load_config

PORT_RETRY = 10


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='RoomSenseBoard 室内环境感知面板')
    parser.add_argument('--config', default='./conf/config.json', help='配置文件路径（默认 ./conf/config.json）')
    parser.add_argument('--host', default=None, help='监听地址，覆盖配置文件')
    parser.add_argument('--port', type=int, default=None, help='监听端口，覆盖配置文件')
    parser.add_argument('--no-simulator', action='store_true', help='关闭内置数据模拟器')
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


def bind_server(app: App, host: str, port: int) -> PanelHTTPServer:
    '''绑定端口；端口被占用时向后顺延。'''
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
    if args.no_simulator:
        config['simulator']['enable'] = False
    if args.verbose:
        config['server']['verbose'] = True
    return App(config)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
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
    display_host = '127.0.0.1' if host in ('0.0.0.0', '::') else host
    base_url = f'http://{display_host}:{port}'
    accounts_path = Path(app.config['server']['accounts_path'])
    device_key_path = Path(app.config['device']['key_path'])

    app.start()
    print('RoomSenseBoard 已启动')
    print(f'  监控面板：{base_url}/')
    print(f'  管理账号：{app.admin_user}（账号文件 {accounts_path}）')
    if app.initial_password:
        print(f'  初始密码：{app.initial_password}')
        print('           仅在首次创建账号时显示，请登录后立即在“设置 → 账号安全”中修改。')
    else:
        print('  账号密码：沿用 conf/accounts.json，忘记时可执行 python main.py --reset-password')
    print(f'  数据上传：{base_url}/api/ingest（节点密钥 {device_key_path}）')
    print(f"  数据模拟：{'开启' if app.simulator else '关闭'}")
    print('  按 Ctrl+C 停止服务')
    sys.stdout.flush()

    if args.open:
        webbrowser.open(base_url)
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
