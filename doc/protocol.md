# 设备接入与接口协议

本文档是 RA4M2 节点与后端之间的接口约定，也是监控面板所用 REST / SSE 接口的参考。
改动本文件等同于改动对外契约，请同步更新 `tests/test_core.py`。

后端分为两个平面，鉴权方式彼此独立：

| 平面 | 入口 | 鉴权 |
| --- | --- | --- |
| 数据收发后端 | `POST /api/ingest` | 节点密钥 `X-Device-Key` |
| 管理系统后端 | `/api/*` 其余接口 | 账号密码登录后获得的会话令牌 `X-Auth-Token` |

## 1. 节点上报

```http
POST /api/ingest HTTP/1.1
Host: 192.168.1.20:20500
Content-Type: application/json
X-Device-Key: <data/device_key.txt 中的内容>
```

也支持把密钥放在 `Authorization: Bearer <节点密钥>` 头中，便于复用现成的 HTTP 客户端。

单条上报：

```json
{
  "device_id": "meeting-a",
  "name": "A 会议室",
  "room": "行政楼 1F",
  "ts": 1790414162,
  "temperature": 24.6,
  "humidity": 52.3,
  "co2": 780,
  "pm25": 14,
  "noise": 48,
  "light": 320,
  "battery": 4.02,
  "rssi": -58,
  "seq": 1024
}
```

批量上报（节点离线缓存后补传，单批最多 `device.max_batch` 条，默认 256）：

```json
{
  "readings": [
    { "device_id": "meeting-a", "ts": 1790414162, "temperature": 24.6, "humidity": 52.3 },
    { "device_id": "meeting-a", "ts": 1790414167, "temperature": 24.7, "humidity": 52.1 }
  ]
}
```

### 字段

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| device_id | 是 | 1-64 位字母、数字或 `_ . : -`，建议 `房间标识-编号`，如 `meeting-a`、`office-201` |
| ts | 否 | Unix 秒。缺省用服务端时间；超前 5 分钟按服务端时间处理，回溯超过 24 小时拒绝 |
| temperature | 五项至少一个 | 摄氏度，-40 ~ 125 |
| humidity | 五项至少一个 | 相对湿度 %，0 ~ 100 |
| co2 | 五项至少一个 | ppm，0 ~ 20000（未接传感器时不要上报该字段） |
| pm25 | 五项至少一个 | PM2.5，µg/m³，0 ~ 1000 |
| noise | 五项至少一个 | 噪声，dB，0 ~ 140 |
| name / room | 否 | 首次上报时写入，之后可在面板修改；留空则用 device_id |
| light | 否 | lux，0 ~ 200000 |
| battery | 否 | 电池电压 V，0 ~ 15 |
| rssi | 否 | 信号强度 dBm，-150 ~ 0 |
| seq | 否 | 设备侧序号，便于排查丢包与重复 |

### 响应

```json
{
  "ok": true,
  "accepted": 1,
  "stored": 1,
  "opened": [
    {
      "id": 12,
      "kind": "temperature_high",
      "severity": "warning",
      "message": "温度 30.2 °C 超过上限 28.0 °C",
      "state": "open"
    }
  ],
  "resolved": [],
  "server_time": 1790414163
}
```

| 状态码 | 含义 |
| --- | --- |
| 200 | 已受理（同一 `device_id + ts` 重复上报会覆盖该点，不重复计数） |
| 400 | 字段缺失或超范围，`error` 说明原因；固件应记录日志并停止重发同一条坏数据 |
| 401 | 节点密钥错误，检查 `data/device_key.txt` 是否与固件一致 |
| 405 | 使用了非 POST 方法 |
| 413 | 请求体超过 1 MiB |

### 上报节奏建议

- 常规：5 ~ 30 秒一条，`seq` 自增；
- 无异常时只报 `temperature`、`humidity`（以及已接的 `co2`），可选字段按需携带；
- 发现异常（越限）时立即补报一条，缩短告警延迟；
- 断网时在 RAM/Flash 中缓存最近若干条，恢复后按批量接口补传，`ts` 使用采集时刻；
- 采用 HTTP `Connection: close` 或长连接均可，固件侧建议每 30 ~ 60 秒复用一次连接以省电。

### RA4M2 固件要点

1. RA4M2 本身不含 WiFi，常用 ESP32 / ESP8266 模组经 UART AT 指令联网；把 JSON 拆成
   AT 发送窗口大小的分片，注意转义引号与花括号；
2. 时间基准可用模组 SNTP 或后端返回的 `server_time` 校准，`ts` 误差只影响曲线横轴；
3. 组包示例（C 语言，缓冲区长度按实际 `snprintf` 实现调整）：

```c
char payload[256];
snprintf(payload, sizeof(payload),
         "{\"device_id\":\"%s\",\"ts\":%lu,\"temperature\":%.1f,\"humidity\":%.1f,\"co2\":%d,"
         "\"battery\":%.2f,\"rssi\":%d,\"seq\":%lu}",
         DEVICE_ID, (unsigned long)timestamp, temperature, humidity, co2, battery, rssi, (unsigned long)seq);
/* 计算 Content-Length 后发送：POST /api/ingest + X-Device-Key 头 */
```

4. 调试顺序建议：先 `GET /api/health` 验证链路，再发送一条固定 JSON 确认 200，
   最后接入真实传感器读数。

### 可选：MQTT 接入

若现场更适合 MQTT（例如节点走 4G 模组或需要 QoS），保持后端不变，增加一个订阅
`roomsense/<device_id>/reading` 的网桥，把消息体交给同一个 `DeviceGateway.submit()`：

```python
# 需要额外安装 paho-mqtt；核心逻辑复用，无需改动存储与告警
import json
import paho.mqtt.client as mqtt


def on_message(client, userdata, message):
    gateway.submit(json.loads(message.payload), source='mqtt')


client = mqtt.Client()
client.on_message = on_message
client.connect('127.0.0.1', 1883)
client.subscribe('roomsense/+/reading')
client.loop_start()
```

## 2. 管理系统 API

除 `/api/health`、`/api/login`、`/api/ingest` 外，其余接口都需要登录会话
（登录返回的 `session`，通过请求头 `X-Auth-Token` 携带，SSE 通过 `?token=` 携带）。

| 方法与路径 | 说明 |
| --- | --- |
| `POST /api/login` | 账号密码登录，返回 `{session, username, expires_hours, server_time, version}` |
| `POST /api/logout` | 注销当前会话 |
| `GET /api/session` | 校验并续期当前会话 |
| `GET /api/account` | 当前账号信息、账号数量与在线会话数 |
| `POST /api/account/password` | 修改密码：`{current_password, new_password}`，其他会话立即失效 |
| `GET /api/health` | 服务状态、运行时长、节点与读数统计（无需鉴权，用于探活） |
| `GET /api/overview` | 仪表盘聚合数据：统计、节点、活跃告警、全部指标平均值、严重级别计数、运行状态 |
| `GET /api/nodes` | 节点列表（含在线状态与最近快照） |
| `GET /api/nodes/<id>` | 单节点详情：实时窗口内的原始读数 + 最近告警 |
| `PUT /api/nodes/<id>` | 修改节点 `name` / `room` |
| `DELETE /api/nodes/<id>?purge=0\|1` | 删除节点；`purge=0` 保留历史读数并关闭其活跃告警，`purge=1` 连同该节点读数与告警一起删除 |
| `GET /api/live?device_id=&minutes=` | 实时趋势原始读数；`minutes` 缺省取设置里的实时窗口（默认 25，范围 5–120） |
| `GET /api/readings?range=24h&device_id=&bucket=` | 历史聚合曲线，range 取 30m / 1h / 6h / 24h / 7d / 30d |
| `GET /api/alerts?state=active&limit=300` | 告警列表，state 取 active（未处理 + 已确认 + 已静默）/ open / acknowledged / muted / resolved / all |
| `POST /api/alerts/<id>/ack` | 确认单条告警，可选 `{"note": "..."}` |
| `POST /api/alerts/<id>/mute` | 静默单条告警：`{"minutes": 60, "note": "..."}`，范围 5–1440 分钟 |
| `POST /api/alerts/<id>/resolve` | 手动关闭本次告警，可选 `{"note": "..."}` |
| `POST /api/alerts/ack` | 确认全部未处理告警 |
| `GET /api/logs?levels=INFO,WARN&limit=500&since_id=` | 服务日志 |
| `GET /api/settings` / `PUT /api/settings` | 阈值与运行参数 |
| `POST /api/device-key/regenerate` | 重新生成节点密钥（旧密钥立即失效） |
| `GET /api/stream?token=` | SSE 实时流 |
| `GET /manifest.webmanifest` | PWA 清单 |
| `GET /sw.js` | PWA Service Worker（响应头允许根作用域） |

登录失败按来源限速（5 分钟内 10 次），账号不存在与密码错误返回相同提示。

告警处理返回完整的告警对象。已恢复的告警再次处理会返回 `409`；确认 / 静默 / 关闭都会记录
操作人（当前登录账号）与备注，备注最长 200 字。手动关闭只结束本次告警，如果越限持续，
下一条读数仍会重新开一条新告警。

### 多指标告警

`settings.alerts` 中每个指标独立配置，缺省值见 `roomsense/config.py`：

| 指标 | 触发方向 | 默认警告 | 默认严重 | 回差 |
| --- | --- | --- | --- | --- |
| temperature | 上限 / 下限 | 28 / 18 °C | 32 / 12 °C | 0.5 |
| humidity | 上限 / 下限 | 70 / 30 %RH | 85 / 20 %RH | 2 |
| co2 | 上限 | 1000 ppm | 1500 ppm | 50 |
| pm25 | 上限 | 75 µg/m³ | 150 µg/m³ | 10 |
| noise | 上限 | 70 dB | 85 dB | 5 |
| light | 下限 | 80 lx | 30 lx | 15 |
| battery | 下限 | 3.5 V | 3.3 V | 0.05 |

只有上报了对应字段的读数才会参与判定；没有接传感器时不会误报。

### 运行时设置

`PUT /api/settings` 支持部分更新，未出现的字段保持不变：

```json
{
  "offline_timeout": 90,
  "retention_days": 30,
  "live_window_minutes": 25,
  "public_url": "https://panel.example.com",
  "alerts": {
    "temperature": {"low": 18, "high": 28, "critical_low": 12, "critical_high": 32},
    "humidity": {"low": 30, "high": 70, "critical_low": 20, "critical_high": 85},
    "co2": {"high": 1000, "critical_high": 1500},
    "pm25": {"high": 75, "critical_high": 150},
    "noise": {"high": 70, "critical_high": 85},
    "light": {"low": 80, "critical_low": 30},
    "battery": {"low": 3.5, "critical_low": 3.3}
  }
}
```

`live_window_minutes` 范围 5–120；`offline_timeout` 15–3600 秒；`retention_days` 1–365 天。
阈值超出物理范围、下限不小于上限或严重阈值越过普通阈值时返回 400。

### SSE 事件

| 事件 | 载荷 |
| --- | --- |
| `hello` | 服务状态（版本、运行时长、模拟器开关、统计） |
| `reading` | 一条新读数：`device_id / ts / temperature / humidity / co2 / pm25 / noise / light / battery / rssi` |
| `alert` | 新建、刷新、静默、恢复的告警对象（`state` 为 `open` / `acknowledged` / `muted` / `resolved`，含 `acknowledged_by`、`acknowledged_at`、`muted_until`、`resolved_by`、`note`） |
| `node` | 节点上线、离线或信息变更 |
| `node_deleted` | 节点被删除（含 `purged`、删除/保留的记录数），前端据此移除该节点 |
| `log` | 一条日志：`ts / level / source / message` |
| `settings` | 设置被修改后的完整设置对象 |
| `device_key` | 节点密钥被轮换 |

连接断开时浏览器会自动重连；服务端每 15 秒发送一次注释心跳。告警静默到期由服务端维护任务
自动把状态改回 `open`，并通过 `alert` 事件通知所有面板。

### 跨域（前后端分离部署）

面板静态资源部署在其他来源时，需在 `conf/config.json` 登记白名单：

```json
{ "server": { "cors_origins": ["http://192.168.1.20:5173"] } }
```

后端只对白名单内的 `Origin` 回显 `Access-Control-Allow-Origin`，并允许
`X-Auth-Token`、`X-Device-Key`、`Authorization` 请求头；未登记来源的预检请求返回 403。

## 3. 账号与安全边界

- 面板使用账号密码登录：`conf/accounts.json` **按项目约定保存明文口令**，便于查看与找回；
  能读到该文件的人即可拿到全部口令，请保持文件 0600 权限、不要提交或发给别人；
  历史版本的 PBKDF2 摘要仍可登录，登录成功后自动迁移为明文；
- 首次启动生成随机初始密码并打印一次到终端；`python main.py --reset-password [账号]`
  可随时重置为新的随机密码；
- 登录后使用内存会话令牌（默认 12 小时有效，服务重启即失效），刷新页面自动恢复登录；
- 节点密钥与面板账号完全独立，可在“设置 → 设备接入”轮换；两者都不要写进前端代码、
  URL 或日志；
- 当前默认明文 HTTP，仅适合局域网；公网部署需在前端加 HTTPS 反向代理，并把
  `server.host` 保持为内网地址。PWA 安装与 Service Worker 同样要求 HTTPS 或 localhost。
