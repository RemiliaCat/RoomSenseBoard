# 公网部署与管理员入口

本文假设 RoomSenseBoard.exe 运行在一台带公网 IP 的 Windows 服务器上（例如腾讯云
`134.175.137.13`），管理员从自己的电脑或手机浏览器访问面板。目标：**管理员只需要一个
桌面图标，不接触命令行**。

## 先分清两个地址

| 配置 | 含义 | 典型值 |
| --- | --- | --- |
| `server.host` | 监听地址：程序在哪个网卡上等连接 | `0.0.0.0`（所有网卡）、`127.0.0.1`（仅本机） |
| `server.public_url` | 对外地址：别人访问你时用的地址，也是写进节点固件的上报地址 | `http://134.175.137.13:20500` |

`0.0.0.0` 只能用于监听，不能填进浏览器，也不能写进节点固件；`127.0.0.1` 只有服务器
本机能访问。面板“设置 → 对外访问地址”填的是后者。

从 0.2.1 开始：

- 打包后的 exe 默认监听 `0.0.0.0`，双击即可让其他电脑访问；
- 自动探测公网出口（腾讯云/阿里云元数据优先，通用回显兜底），探测到就用于展示；
- 设置页有“使用当前访问地址”，直接把浏览器地址栏填进去；
- `0.0.0.0`、`::`、`*` 作为对外地址会返回 400，并提示填公网 IP、域名或局域网 IP。

## 一次性配置

### 1. 放到固定目录

不要放在桌面或用户目录下，避免多用户、桌面清理、权限变化导致路径失效：

```text
C:\RoomSenseBoard\
├── RoomSenseBoard.exe
├── conf\accounts.json
├── conf\config.json
└── data\roomsense.db
```

首次运行会自动释放 `conf\`、`data\`、`web\`、`doc\`。升级时只替换 exe，`conf\` 与
`data\` 会保留。

从 0.2.0 或更早版本升级时要注意：已有的 `conf\config.json` **不会被覆盖**，如果里面的
`server.host` 还是 `127.0.0.1`，双击 exe 仍然只监听本机。把这一行改成 `0.0.0.0`，或者
在任务计划/快捷方式里显式加 `--host 0.0.0.0`。

### 2. 设置对外地址

用浏览器打开 `http://127.0.0.1:20500/`，登录后进入“设置 → 设备接入”：

1. 在“对外访问地址”填 `http://134.175.137.13:20500`；
2. 或者点“使用当前访问地址”（前提是你已经通过公网地址打开了面板）；
3. 保存后，“上报地址”会变成 `http://134.175.137.13:20500/api/ingest`。

这个值存在 `data\roomsense.db` 里，优先级高于 `conf\config.json` 的 `server.public_url`。
换机器或清空数据库时，记得在 `conf\config.json` 里也写一份。

### 3. 放行端口

Windows 防火墙（管理员 PowerShell，执行一次）：

```powershell
New-NetFirewallRule -DisplayName 'RoomSenseBoard TCP 20500' `
  -Direction Inbound -Action Allow -Protocol TCP -LocalPort 20500 -Profile Any
```

首次双击 exe 时 Windows 也可能弹出“是否允许此应用通信”的提示，勾选“专用网络”和
“公用网络”并允许即可；如果没弹或点了取消，用上面的命令补一条端口规则。

云服务器安全组（腾讯云控制台 → 云服务器 → 安全组 → 入站规则）添加：

| 来源 | 协议端口 | 策略 |
| --- | --- | --- |
| `0.0.0.0/0`（或管理员固定 IP 段） | TCP:20500 | 允许 |

如果只允许公司网络访问，把来源改成公司出口 IP 段；管理员换网络时会进不来，需提前说明。

### 4. 开机自启（管理员不用碰命令行）

把下面保存成 `install-autostart.ps1`，在服务器上**以管理员身份**运行一次：

```powershell
$exe = 'C:\RoomSenseBoard\RoomSenseBoard.exe'
$work = Split-Path -Parent $exe
$action = New-ScheduledTaskAction -Execute $exe -Argument '--host 0.0.0.0' -WorkingDirectory $work
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName 'RoomSenseBoard' -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force
Start-ScheduledTask -TaskName 'RoomSenseBoard'
```

之后服务器重启、程序崩溃，任务计划程序都会把它拉起来；管理员不需要登录服务器启动 exe。

验证：

```powershell
Get-ScheduledTask -TaskName RoomSenseBoard | Select-Object State
Get-NetTCPConnection -LocalPort 20500 -State Listen
```

第二条应显示 `0.0.0.0:20500` 或 `[::]:20500`。

### 5. 给管理员一个桌面图标

在管理员自己的电脑上，用 PowerShell 生成一个网址快捷方式：

```powershell
$desktop = [Environment]::GetFolderPath('Desktop')
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut((Join-Path $desktop '环境监测面板.url'))
$sc.TargetPath = 'http://134.175.137.13:20500/'
$sc.IconLocation = 'C:\RoomSenseBoard\web\icon.ico,0'
$sc.Save()
```

双击就会用默认浏览器打开；不需要记 IP，也不需要命令行。也可以直接把网址拖进浏览器
收藏夹，改名为“环境监测面板”。

## 验证

1. 在服务器上：`Invoke-WebRequest http://127.0.0.1:20500/api/health` 返回 JSON；
2. 在管理员电脑上打开 `http://134.175.137.13:20500/`，能出现登录页；
3. 用手机**关掉 Wi-Fi、走蜂窝网络**再打开一次，确认不是只通内网；
4. 登录后“设置 → 设备接入”里看到的“上报地址”应是公网地址，而不是 `10.x.x.x`；
5. 如果通过 HTTPS 域名访问，可以在手机浏览器菜单里“添加到主屏幕 / 安装应用”；
   普通的 `http://IP:20500` 不满足 Service Worker 的安全上下文要求，不会出现安装入口。

## 安全提醒

面板默认是明文 HTTP，登录密码和会话令牌在公网上可被嗅探；项目又按约定把口令明文保存在
`conf\accounts.json`。公网部署至少要做这几件事：

- 首次登录后立即改掉初始密码，使用足够长的随机密码；
- 不要把 `conf\accounts.json` 发给别人，服务器只给必要的人登录；
- 关注“日志”页的登录失败记录，必要时在安全组限制来源 IP；
- 有条件时加一层 HTTPS 或零信任接入，优先级如下。

### 方案 A：域名 + Caddy 反向代理（推荐，需域名）

1. 域名解析到服务器公网 IP；
2. 服务器上放一个 `caddy.exe` 和 `Caddyfile`：

```text
panel.example.com {
    reverse_proxy 127.0.0.1:20500
}
```

3. 面板的 `conf/config.json` 改回 `"host": "127.0.0.1"`，`public_url` 填
   `https://panel.example.com`；
4. 安全组只放行 80/443，不再对公网开放 20500。

Caddy 会自动申请证书。中国大陆服务器的域名走 80/443 通常需要备案，请先确认。

### 方案 B：Tailscale / ZeroTier（不暴露端口）

服务器和管理员电脑都安装 Tailscale 并登录同一网络，然后：

```powershell
tailscale serve --bg 443 http://127.0.0.1:20500
```

管理员打开 Tailscale 提供的 `https://<机器名>.<tailnet>.ts.net/` 即可。面板只监听
`127.0.0.1`，公网扫描器完全看不到它。代价是管理员要装一次客户端。

### 方案 C：Cloudflare Tunnel + Access

服务器上运行 `cloudflared`，把 `panel.example.com` 指到 `http://127.0.0.1:20500`，
在 Cloudflare 侧加 Access 策略（邮箱验证码）。不开放入站端口，自带 HTTPS 和登录保护；
需要域名和 Cloudflare 账号，国内访问速度取决于线路。

### 最低限度

如果暂时只能 `http://IP:20500`，至少保证：强密码、登录限速开启（默认已开）、安全组
来源尽量收窄、定期看日志。不要把默认密码或弱密码留在公网面板上。

## 常见问题

**双击 exe 自动打开的是公网 IP，但打不开**

- 0.3.0 及更早版本在“监听 127.0.0.1 / 指定网卡 + 云厂商元数据确认公网 IP”时，会把公网 IP
  当成首选展示地址，自动打开也跟着跳公网 IP；云服务器的公网 IP 常是 NAT 映射，从服务器
  自身访问可能无法回环；
- 0.3.1 起自动打开固定使用本机可访问地址：监听所有网卡 / 回环时是
  `http://127.0.0.1:端口/`，绑定具体网卡时是网卡地址；公网地址只用于展示和节点上报；
- 在服务器上请直接打开 `http://127.0.0.1:20500/`；外部访问才用公网 IP。

**面板打不开，但服务器本机能开**

- `conf/config.json` 的 `server.host` 还是 `127.0.0.1`；改成 `0.0.0.0` 或启动时加
  `--host 0.0.0.0`；
- Windows 防火墙没放行 20500；
- 云安全组没放行 20500；
- 用了域名但 DNS 没解析到这台机器。

**“上报地址”显示 `10.x.x.x` 或 `192.168.x.x`**

- 这是内网网卡地址，公网节点不可达；在“设置 → 对外访问地址”填公网 IP/域名，或点
  “使用当前访问地址”，保存即可；
- 如果填的是 `0.0.0.0`，0.2.1 起会直接报错，不会再静默接受。

**控制台不断打印 `ConnectionResetError`**

- 0.2.0 及更早版本会把公网扫描器的断开打印成 traceback；0.2.1 起已静默常见断开，
  其他异常仍会记录。升级 exe 即可，不影响数据。

**忘记管理员密码**

- 在服务器上执行 `RoomSenseBoard.exe --reset-password admin`，会打印新密码；
- 或直接查看 `conf\accounts.json`（项目约定明文保存），但注意这等于拿到全部口令。

**端口 20500 被占用**

- 程序会自动顺延到下一个端口，启动横幅会显示实际端口；防火墙/安全组要同步改。
