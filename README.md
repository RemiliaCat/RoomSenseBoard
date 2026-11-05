# RoomSenseBoard

室内环境感知系统，由四部分组成，本仓库为前三部分：

| 组成 | 职责 | 代码位置 |
| --- | --- | --- |
| 监控面板前端 | 纯静态页面，绘制实时曲线、节点、告警、历史与日志 | `web/` |
| 管理系统后端 | 账号密码登录、会话、节点信息、告警处理、历史查询、运行参数 | `roomsense/api/panel.py`、`roomsense/core/auth.py` |
| 数据收发后端 | 感知节点接入：密钥鉴权、上报校验、入库、阈值告警、实时广播 | `roomsense/api/ingest.py`、`roomsense/core/gateway.py` |
| 环境感知节点 | 环境感知、数据量化、异常侦测、智能执行 | 参见 [RoomSenseNode]() |

当前版本 **0.3.1**：多指标告警、滑动时间窗、可处理的告警（备注 / 静默 / 关闭）、
多指标实时趋势、移动端适配、控制台日志同步与 PWA 支持。0.3.1 修复了监听
`127.0.0.1` 时自动打开被公网地址带偏、导致本机反而打不开的问题。

前端只依赖 HTTP / SSE 接口，可独立部署到任意静态服务器；节点协议与面板账号体系互不影响。

## 快速开始

### 源码部署

```powershell
python main.py              # 默认 http://127.0.0.1:20500，只接收真实设备上报
python main.py --host 0.0.0.0  # 让同网段或公网访问面板（源码运行时默认只监听本机）
python main.py --simulator  # 额外启动 5 个内置模拟节点，用于演示与联调
python main.py --open       # 启动后自动打开浏览器
```

生产环境默认**不带模拟器**（加 `--simulator` 或环境变量 `ROOMSENSE_SIMULATE=1` 才会打开），
打包后的 exe 同样如此。

源码运行默认只监听 `127.0.0.1`；**打包后的 exe 默认监听 `0.0.0.0`**，双击即可让同网段或
公网访问（还需要 Windows 防火墙与云安全组放行端口）。`--host` 与 `conf/config.json` 的
`server.host` 都可以覆盖这个默认值。

双击 exe 或加 `--open` 时，浏览器只打开**本机可访问地址**（监听所有网卡或回环时用
`http://127.0.0.1:端口/`，绑定具体网卡时用该网卡地址），不会跳到探测到的公网 IP；
公网地址只用于展示、节点上报和外部访问。云服务器的公网 IP 常是 NAT 映射，从服务器自身
访问公网 IP 可能无法回环，因此本机请始终用上面的本机地址。

首次启动会生成管理账号并**在终端打印一次初始密码**：

```text
RoomSenseBoard 已启动
  监控面板：http://127.0.0.1:20500/
  管理账号：admin（账号文件 conf/accounts.json）
  初始密码：xxxxxxxxxxxxxx
  数据上传：http://127.0.0.1:20500/api/ingest（节点密钥 data/device_key.txt）
  数据模拟：关闭
```

登录面板后在“设置 → 账号安全”修改密码；忘记密码时执行
`python main.py --reset-password admin`（打包后是 `RoomSenseBoard.exe --reset-password admin`）
会重置为新的随机密码并打印，同时打印账号文件路径——重置只影响**与该 exe 同目录**的
`conf/accounts.json`，另一份拷贝（换过目录、或用别人发来的 exe）的面板仍用自己那份账号文件。

节点侧使用 `data/device_key.txt` 中的密钥上报（协议见 [doc/protocol.md](doc/protocol.md)）。
无需安装任何第三方依赖，Python 3.10+ 即可运行（开发环境为 3.11）。

### 打包发布

用 [PyInstaller](https://pyinstaller.org/) 把解释器、标准库与 `web/` 一起装进**一个 exe**。
PyInstaller 只做收集与打包、不编译 C 代码，本项目又是纯标准库应用，一次构建通常十几秒。

```powershell
uv sync                               # 安装开发依赖（PyInstaller，运行期仍然零依赖）
.\package\build.ps1                 # 单文件 exe → dist\RoomSenseBoard\RoomSenseBoard.exe
.\package\build.ps1 -Zip            # 额外生成 dist\RoomSenseBoard-0.3.1-onefile-win64.zip
.\package\build.ps1 -Mode onedir    # 目录模式，适合本机反复替换 web/ 调试
.\package\build.ps1 -SkipTests      # 跳过打包前的单元测试
.\package\build.ps1 -DistPath build\release  # dist 被正在运行的实例占用时，换一个输出目录
```

也可以直接双击 `package\build.cmd`，等同默认参数。

分发包里只有 `RoomSenseBoard.exe` 与 `LICENSE`；**双击 exe 就能运行**，首次启动会在 exe 旁边
自动释放全部资源，并自动打开浏览器：

```text
RoomSenseBoard\                    ← 首次运行后长这样
├── RoomSenseBoard.exe             分发包里只有它（和 LICENSE）
├── conf\config.json               默认配置（不含模拟节点），之后由你维护
├── data\                          roomsense.db、节点密钥与资源释放记录
├── web\                           面板页面，可以自行替换
├── doc\protocol.md
├── README.md
└── LICENSE
```

之后**升级就是替换这一个 exe**：程序会比对 `data\assets.json` 里记录的版本与文件哈希，
只刷新"上次由程序写入、之后没被改过"的文件；`conf\`、`data\` 以及你改过的页面原样保留。

脚本按顺序做这些事：检查 Python 与 PyInstaller（缺失时自动 `uv sync` 或 `pip install`）→ 把
`package/version_info.txt` 的版本号同步成 `roomsense/__init__.py` 里的 `__version__` →
跑单元测试 → 清理 `build\` 与 `dist\` → 调用 PyInstaller → 发布目录只留 exe 与 LICENSE →
冒烟测试（先 `--help`，再在临时目录里真跑一遍：确认资源已释放、默认配置未启用模拟器、面板可
访问）。需要时用 `-SkipTests`、`-SkipSmokeTest` 单独跳过对应环节。

| 模式 | 产物 | 适合 |
| --- | --- | --- |
| `onefile`（默认） | `dist\RoomSenseBoard\RoomSenseBoard.exe`，约 9 MB | 分发，替换 exe 升级 |
| `onedir` | `dist\RoomSenseBoard\`（exe 加 `_internal\`） | 本机调试、频繁改 `web\` |

运行期路径规则见 `roomsense/config.py`：**可写**的 `conf/`、`data/` 按 exe 所在目录解析（不是
当前工作目录），整个目录拷到任何位置都能直接跑；**只读**的 `web/` 由 `roomsense/assets.py` 从
exe 里释放到同目录，之后以磁盘上的那份为准。

#### 对外访问地址

`server.host` 是**监听地址**，`server.public_url` 是**别人访问你时用的地址**，两者不是一回事：
`0.0.0.0` 只适合监听，不能写进浏览器或节点固件；`127.0.0.1` 只有服务器本机能用。

程序按下面的顺序给出"上报地址"：

1. `server.public_url`（`conf/config.json`）或面板"设置 → 对外访问地址"（存数据库、立即生效）；
   部署在域名或反向代理后面时填它，例如 `https://panel.example.com`。设置页的
   **"使用当前访问地址"** 会直接取浏览器地址栏，适合公网 IP / 域名已经能打开面板的情况；
2. 监听的是具体地址（如 `192.168.1.20`）时，直接用监听地址；
3. 监听 `0.0.0.0` 时先探测公网出口：腾讯云 / 阿里云元数据优先，通用回显兜底；
   元数据确认的公网 IP 排第一，通用回显只作为候选列出；
4. 否则探测本机局域网 IPv4：默认路由所在网卡排第一，其余网卡作为候选一并列出；
5. 都没有时回退 `127.0.0.1`。

启动横幅、"设置 → 设备接入"里的上报地址与"关于"里的对外地址用的是同一份结果。
`0.0.0.0` / `::` / `*` 作为对外地址会被拒绝，并提示填写公网 IP、域名或局域网 IP。

公网部署的端口放行、开机自启与管理员快捷方式见 [doc/deploy-public.md](doc/deploy-public.md)。

## 界面

| 页面 | 内容 |
| --- | --- |
| 仪表盘 | 在线节点、平均温湿度、未处理告警的环形卡片；环境概览（CO₂ / PM2.5 / 噪声 / 光照）；实时趋势支持同时显示同一节点的全部指标（每个指标独立纵轴）；节点状态表、最近告警、运行状态 |
| 节点 | 全部节点的实时数值（温湿度 / CO₂ / PM2.5 / 噪声 / 电量 / 信号）、累计上报次数；点击“详情”查看单节点曲线、改名/改位置，或删除节点（可选是否连带清理历史数据） |
| 告警 | 按状态筛选（活跃 / 未处理 / 已确认 / 已静默 / 已恢复 / 全部）；处理告警时可写备注、确认、静默一段时间或手动关闭本次告警 |
| 历史数据 | 按节点与指标查询 30 分钟至 30 天的聚合曲线，固定显示所选时间范围，含最小/平均/最大/最新值，可导出 CSV |
| 日志 | 按级别（TRACE…FATAL）筛选，实时滚动，记录上报、告警、认证与配置变更；服务端同步打印到终端 |
| 设置 | 温湿度、CO₂、PM2.5、噪声、光照、电量的告警阈值，离线判定、数据保留天数、实时趋势窗口、节点接入信息、节点密钥轮换、账号密码修改 |

外观支持浅色 / 深色 / 跟随系统，选择保存在浏览器本地。窄屏下自动切换为顶部横向导航、
表格卡片化、全屏弹窗与触摸可用的图表，面板还可安装为 PWA（见下文）。

### 实时趋势与时间轴

实时趋势以“先增长、后滑动”的方式绘制：节点刚开始上报时，横轴从首条数据开始逐秒撑开，
曲线始终从左侧起点向右生长；累计满 N 分钟后，窗口不再变长，开始整体向右滑动，横轴保持
最近 N 分钟。N 在“设置 → 运行参数 → 实时趋势窗口”里配置，默认 **25 分钟**，范围 5–120
分钟；保存后仪表盘、单节点详情与历史查询立即使用新的窗口 / 时间域。

每个指标各自一张小图、独立纵轴，可以在指标条上任意多选（至少保留 1 个），因此同一节点的
温度、湿度、CO₂、PM2.5、噪声、光照与电量可以同时查看，不会出现量纲互相挤压。图中虚线是
该指标的告警阈值。

### 告警处理

告警实例有四种状态：`未处理`、`已确认`、`已静默`、`已恢复`。

| 操作 | 效果 |
| --- | --- |
| 确认 | 记录处理人与备注，移出“未处理”角标，但仍保留在活跃告警中 |
| 静默 | 指定 5–1440 分钟内不再计入未处理；到期自动回到未处理（仍然接收数值刷新） |
| 关闭本次告警 | 结束本次告警并记录处理人/备注；如果越限仍然持续，下一条读数会重新开一条新告警 |
| 自动恢复 | 指标回到阈值内（含回差）自动转为已恢复，不需要人工介入 |

确认、静默与关闭都会写入日志；重复确认不会覆盖第一次的处理人和备注，只补充备注。

### 控制台日志

服务端日志在写入数据库的同时同步打印到运行终端，默认级别 `INFO`；`--verbose` 会打印
每个 HTTP 请求并把控制台级别降到 `DEBUG`。也可以在 `conf/config.json` 的 `logging.console_level`
或环境变量 `ROOMSENSE_LOG_LEVEL` 中设置（`TRACE`、`DEBUG`、`NOTE`、`INFO`、`WARN`、`ERROR`、
`FATAL`），设为 `OFF` 可关闭终端输出。

### PWA

面板提供 `manifest.webmanifest` 与 Service Worker：通过 HTTPS（或本机 `http://127.0.0.1`）
打开时可以“添加到主屏幕 / 安装应用”，已缓存的页面外壳在断网时仍能打开。`/api/*` 请求
始终走网络，不做离线缓存；实时数据需要后端在线。

Service Worker 的缓存名在 `web/sw.js` 的 `CACHE` 常量里，发版时需要跟随
`roomsense/__init__.py` 的版本号一起更新，否则浏览器可能继续使用旧的静态资源。
普通局域网 HTTP 访问受浏览器安全上下文限制，无法注册 Service Worker，需要反向代理
提供 HTTPS（见 [doc/deploy-public.md](doc/deploy-public.md)）。

### 自绘控件约定（`web/ui/`）

只在“控件外观或弹出层由浏览器/系统绘制、CSS 无法统一到面板主题”时才自绘：

| 控件 | 实现 | 说明 |
| --- | --- | --- |
| 下拉选择器 | `ui/dropdown.js` + `ui/controls.css` | JS 接管后才隐藏原生 `select`，未执行脚本时降级为原生下拉；键盘支持 ↑↓/Home/End/Enter/Esc/字母定位，ARIA 为 combobox + listbox |
| 复选框 | `ui/controls.css`（纯 CSS） | 保留原生 `input` 的语义、键盘与表单行为，只替换勾选外观 |
| 折叠面板标记 | `ui/controls.css`（纯 CSS） | 隐藏浏览器默认三角，自绘可跟随主题的雪佛龙 |

新增自绘控件的约定：一个控件一个文件、统一挂到 `window.RoomSenseUI.*`、样式集中在
`ui/controls.css`、必须保留键盘操作与 ARIA，并且不改变原生控件的取值与 `change` 事件语义。

## 架构

```text
RA4M2 节点（温湿度 / CO₂ / PM2.5 / 噪声 / 光照）
   │  WiFi + HTTP POST /api/ingest（X-Device-Key 鉴权）—— 数据收发后端
   ▼
DeviceGateway ── 校验 ──► SQLite（readings / alerts / logs / nodes）
   │                        ▲
   ├── 阈值判定（回差防抖动）─┘
   └── EventBus ──► SSE /api/stream ──► 监控面板前端（web/）
                         ▲
                       REST /api/*（账号会话）—— 管理系统后端
```

为什么用 SSE 而不是 WebSocket：本项目只需要服务端单向推送，SSE 由浏览器原生支持断线重连，
服务端实现也更简单；确认告警、修改设置等指令走普通 REST。

```text
RoomSenseBoard/
├── main.py                # 启动入口（端口占用时自动顺延）
├── roomsense/
│   ├── api/
│   │   ├── ingest.py      # 数据收发后端：设备上报入口
│   │   ├── panel.py       # 管理系统后端：静态资源 + 管理 REST + SSE + CORS
│   │   └── errors.py      # HTTP 层共用错误类型
│   ├── core/
│   │   ├── gateway.py     # 上报校验、入库、告警联动、离线检测
│   │   ├── alerts.py      # 多指标阈值判定与回差
│   │   ├── storage.py     # SQLite 全部 SQL：节点/读数/告警/日志/设置
│   │   ├── auth.py        # 账号文件（口令明文存储）、会话、登录限速
│   │   ├── bus.py         # 事件总线（有界队列，慢客户端不拖垮采集）
│   │   └── simulator.py   # 无硬件阶段的数据模拟器
│   ├── config.py          # 默认配置、深合并、节点密钥文件
│   ├── assets.py          # 打包运行期：把 web/、conf/ 等资源释放到 exe 旁边
│   ├── netinfo.py         # 对外访问地址：public_url / 本机网卡 / 回环
│   └── app.py             # 组件装配、周期任务
├── web/                   # 监控面板前端
│   ├── ui/                # 自绘控件层：dropdown.js（JS）+ controls.css（自绘控件样式）
│   ├── index.html         # 页面结构
│   ├── style.css          # 主题变量、布局、环形仪表与响应式样式
│   ├── app.js             # 页面逻辑：多指标小图、告警处理、PWA 注册
│   ├── chart.js           # Canvas 折线图：固定时间域、滑动窗口、触摸悬停
│   ├── theme.js           # 主题切换
│   ├── config.js          # 前后端分离时的接口地址
│   ├── manifest.webmanifest # PWA 清单
│   ├── sw.js              # PWA Service Worker（面板外壳缓存）
│   ├── icon.png           # 应用图标主图（1024 px）
│   ├── icon.ico           # 多尺寸应用图标（16–256 px）
│   ├── icon-180.png       # iOS 主屏幕图标
│   ├── icon-192.png       # PWA 图标
│   ├── icon-512.png       # PWA 图标
│   ├── icon-maskable-512.png # PWA 自适应图标
│   ├── favicon.ico        # 浏览器页签图标（16–64 px）
│   └── logo.png           # 面板品牌位使用的 128 px 图标
├── conf/config.json       # 端口、CORS 白名单、阈值、模拟节点等配置
├── doc/protocol.md        # 设备上报协议与面板 API
├── doc/deploy-public.md   # 公网部署、端口放行、开机自启与管理员入口
├── package/             # 打包发布：spec、版本资源与 build.ps1 / build.cmd（产物在 build/、dist/）
└── tests/test_core.py     # 78 个 unittest 用例
```

## 前后端分离部署

默认由 `main.py` 同时提供页面与接口（同源）。把 `web/` 放到别的静态服务器（Nginx、
Vite 开发服务器等）时：

1. 修改 `web/config.js`，把接口指向后端地址：

```javascript
window.ROOMSENSE_CONFIG = { apiBase: 'http://192.168.1.20:20500' };
```

2. 在 `conf/config.json` 的 `server.cors_origins` 中登记面板来源（只回显白名单内的来源）：

```json
{ "server": { "cors_origins": ["http://192.168.1.20:5173"] } }
```

登录接口返回的会话令牌通过 `X-Auth-Token` 请求头发送，SSE 通过查询参数
`/api/stream?token=` 携带，因此跨域部署不需要 Cookie。

## 配置

`conf/config.json` 为增量配置文件：

```json
{
    "server": {
        "host": "0.0.0.0",
        "port": 20500,
        "public_url": "https://panel.example.com",
        "session_hours": 12,
        "cors_origins": ["http://192.168.1.20:5173"]
    },
    "storage": {
        "retention_days": 30,
        "live_window_minutes": 25
    },
    "logging": { "console_level": "INFO" },
    "device": { "offline_timeout": 120 },
    "alerts": {
        "pm25": { "high": 75, "critical_high": 150, "hysteresis": 10 },
        "noise": { "high": 70, "critical_high": 85, "hysteresis": 5 }
    },
    "simulator": { "enable": false }
}
```

环境变量优先级最高：`ROOMSENSE_HOST`、`ROOMSENSE_PORT`、`ROOMSENSE_SIMULATE=0/1`、
`ROOMSENSE_LOG_LEVEL`，以及仅用于首次创建账号的 `ROOMSENSE_ADMIN_USER`、
`ROOMSENSE_ADMIN_PASSWORD`。

打包后的 exe 默认监听 `0.0.0.0`，源码运行默认只监听 `127.0.0.1`。把面板开放到公网前，
请先确认账号密码强度、Windows 防火墙与云安全组只放行必要端口，并优先通过反向代理加 HTTPS；
明文 HTTP 下登录密码和会话令牌在公网上是可被嗅探的。

`public_url` 留空时按监听地址、公网出口与本机网卡推断；面板“设置 → 对外访问地址”里的值
存在数据库里，会覆盖配置文件中的默认值（改配置文件后想重新生效，把设置页清空保存即可）。
`0.0.0.0` / `::` 不是可访问地址，填进去会被拒绝。

温度、湿度、CO₂、PM2.5、噪声、光照与电量的阈值，以及离线判定、保留天数、实时趋势窗口和
对外访问地址，都可以在面板“设置”页修改，保存后立即生效并写入数据库。回差（`hysteresis`）
只在配置文件里维护；面板保存阈值时不会改动它。

## 测试

```powershell
python -m unittest discover -s tests -v
```

覆盖内容：账号口令存储与校验（明文，旧摘要格式登录后自动迁移）、初始密码生成、改密与登录限速依赖、CORS 白名单、
SSE 实时流连通与事件推送、多指标告警触发/回差/严重级别、告警确认/静默/到期恢复/手动关闭与处理人备注、
实时窗口设置与 `/api/live` 原始读数、同秒重复上报合并、历史补传不覆盖
实时状态、时间桶聚合（含 PM2.5/噪声/光照）、批量上报、离线告警与恢复、设置校验、
设备密钥鉴权与上报链路、控制台日志级别过滤、对外地址解析（配置优先/公网出口探测/网卡探测/回环回退）、
`0.0.0.0` 等监听地址被拒绝、PWA 清单与 Service Worker 路由、打包资源的首次释放与换版本时的
"刷新未改文件、保留已改文件"。

## 已知限制
- 单进程部署（单栋楼宇规模足够）
- 账号口令按项目约定明文保存在 `conf/accounts.json`，便于查看与找回；能读到该文件的人
  即可拿到全部口令，请保持文件 0600 权限、不要提交或发给别人
- 每个 SSE 连接占用一个工作线程；默认明文 HTTP。局域网可直接使用，公网部署请加反向代理 /
  HTTPS 或 VPN 接入（见 [doc/deploy-public.md](doc/deploy-public.md)）。
- PWA 安装与 Service Worker 需要安全上下文：HTTPS 或本机 `http://127.0.0.1`；普通
  局域网 HTTP 地址只能使用网页版。
- 前端实时数据靠 SSE + 1 秒时间轴刷新，旧浏览器（不支持 `ResizeObserver` / `dialog`）需要升级。

## 特别声明
> 本项目的面板布局、配色与交互参考自 [OlivOS 青果核心交互栈](https://github.com/OlivOS-Team/OlivOS)
> 的 WebUI（侧栏 + 页面 + 主题切换 + 登录页），实现与业务逻辑为本项目独立编写。
> 依照 `AGPLv3` 规定条款，本项目继承该许可证
