# RoomSenseBoard

室内环境感知系统，由四部分组成，本仓库为前三部分：

| 组成 | 职责 | 代码位置 |
| --- | --- | --- |
| 监控面板前端 | 纯静态页面，绘制实时曲线、节点、告警、历史与日志 | `web/` |
| 管理系统后端 | 账号密码登录、会话、节点信息、告警处理、历史查询、运行参数 | `roomsense/api/panel.py`、`roomsense/core/auth.py` |
| 数据收发后端 | 感知节点接入：密钥鉴权、上报校验、入库、阈值告警、实时广播 | `roomsense/api/ingest.py`、`roomsense/core/gateway.py` |
| 环境感知节点 | 环境感知、数据量化、异常侦测、智能执行 | 参见 [RoomSenseNode]() |

前端只依赖 HTTP / SSE 接口，可独立部署到任意静态服务器；节点协议与面板账号体系互不影响。

## 快速开始

### 源码部署

```powershell
python main.py                 # 默认 http://127.0.0.1:20500，带 5 个模拟节点
python main.py --no-simulator  # 只接收真实设备上报
python main.py --open          # 启动后自动打开浏览器
```

首次启动会生成管理账号并**在终端打印一次初始密码**：

```text
RoomSenseBoard 已启动
  监控面板：http://127.0.0.1:20500/
  管理账号：admin（账号文件 conf/accounts.json）
  初始密码：xxxxxxxxxxxxxx
  数据上传：http://127.0.0.1:20500/api/ingest（节点密钥 data/device_key.txt）
```

登录面板后在“设置 → 账号安全”修改密码；忘记密码时执行
`python main.py --reset-password admin` 会重置为新的随机密码并打印。

节点侧使用 `data/device_key.txt` 中的密钥上报（协议见 [doc/protocol.md](doc/protocol.md)）。
无需安装任何第三方依赖，Python 3.10+ 即可运行（开发环境为 3.11）。

## 界面

| 页面 | 内容 |
| --- | --- |
| 仪表盘 | 在线节点数、平均温湿度、未处理告警、实时指标曲线（最多同时对比 2 个指标）、节点状态表、最近告警、运行状态 |
| 节点 | 全部节点的实时数值（温湿度 / CO₂ / PM2.5 / 噪声 / 电量 / 信号）、累计上报次数；点击“详情”查看单节点曲线、改名/改位置，或删除节点（可选是否连带清理历史数据） |
| 告警 | 按状态筛选（活跃 / 未处理 / 已确认 / 已恢复 / 全部），逐条或一键确认 |
| 历史数据 | 按节点与指标查询 30 分钟至 30 天的聚合曲线，含最小/平均/最大/最新值，可导出 CSV |
| 日志 | 按级别（TRACE…FATAL）筛选，实时滚动，记录上报、告警、认证与配置变更 |
| 设置 | 告警阈值、离线判定、数据保留天数、节点接入信息、节点密钥轮换、账号密码修改 |

外观支持浅色 / 深色 / 跟随系统，选择保存在浏览器本地；窄屏下自动切换为顶部导航。

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
│   │   ├── alerts.py      # 阈值判定与回差
│   │   ├── storage.py     # SQLite 全部 SQL：节点/读数/告警/日志/设置
│   │   ├── auth.py        # 账号文件、PBKDF2 口令校验、会话、登录限速
│   │   ├── bus.py         # 事件总线（有界队列，慢客户端不拖垮采集）
│   │   └── simulator.py   # 无硬件阶段的数据模拟器
│   ├── config.py          # 默认配置、深合并、节点密钥文件
│   └── app.py             # 组件装配、周期任务
├── web/                   # 监控面板前端
│   ├── ui/                # 自绘控件层：dropdown.js（JS）+ controls.css（自绘控件样式）
│   ├── index.html         # 页面结构
│   ├── style.css          # 主题变量与页面布局
│   ├── app.js             # 页面逻辑
│   ├── chart.js           # Canvas 折线图
│   ├── theme.js           # 主题切换
│   ├── config.js          # 前后端分离时的接口地址
│   └── logo.svg           # 标识占位符
├── conf/config.json       # 端口、CORS 白名单、阈值、模拟节点等配置
├── doc/protocol.md        # 设备上报协议与面板 API
└── tests/test_core.py     # 41 个 unittest 用例
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
        "session_hours": 12,
        "cors_origins": ["http://192.168.1.20:5173"]
    },
    "device": { "offline_timeout": 120 },
    "simulator": { "enable": false }
}
```

环境变量优先级最高：`ROOMSENSE_HOST`、`ROOMSENSE_PORT`、`ROOMSENSE_SIMULATE=0/1`
，以及仅用于首次创建账号的 `ROOMSENSE_ADMIN_USER`、`ROOMSENSE_ADMIN_PASSWORD`。
监听非本机地址前请确认账号密码强度，并且不要直接暴露到公网。

阈值、离线判定与保留天数也可以在面板“设置”页修改，保存后立即生效并写入数据库。

## 测试

```powershell
python -m unittest discover -s tests -v
```

覆盖内容：账号口令派生与校验、初始密码生成、改密与登录限速依赖、CORS 白名单、
SSE 实时流连通与事件推送、告警触发/回差/严重级别、同秒重复上报合并、历史补传不覆盖
实时状态、时间桶聚合（含 PM2.5/噪声/光照）、批量上报、离线告警与恢复、设置校验、
设备密钥鉴权与上报链路。

## 当前状态与后续计划

- [x] 数据收发后端：上报校验、入库、阈值告警、离线检测、模拟器
- [x] 管理系统后端：账号密码登录、会话、节点/告警/历史/日志/设置接口
- [x] 监控面板前端：仪表盘、节点、告警、历史、日志、设置六个页面
- [ ] RA4M2 固件（FSP + WiFi 模块 AT 指令）按 `doc/protocol.md` 对接
- [ ] 可选 MQTT 接入（网关侧订阅后转发到同一 `DeviceGateway`）
- [ ] 多用户与角色权限、异常事件邮件/企业微信通知

## 已知限制
- 单进程部署（单栋楼宇规模足够）
- 每个 SSE 连接占用一个工作线程；默认明文 HTTP，
- 仅适合局域网，公网部署需加反向代理与 SSL 校验。

## 特别声明
> 本项目的面板布局、配色与交互参考自 [OlivOS 青果核心交互栈](https://github.com/OlivOS-Team/OlivOS)
> 的 WebUI（侧栏 + 页面 + 主题切换 + 登录页），实现与业务逻辑为本项目独立编写。
> 依照 `AGPLv3` 规定条款，本项目继承该许可证
