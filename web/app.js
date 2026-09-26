'use strict';

(() => {

  /* RoomSenseBoard 面板逻辑。

     数据来自 /api/*，实时事件来自 /api/stream。 */

  const $ = (id) => document.getElementById(id);
  const sessionStorageKey = 'roomsense.panel.session';
  const pageStorageKey = 'roomsense.panel.page';
  const API_BASE = String((window.ROOMSENSE_CONFIG && window.ROOMSENSE_CONFIG.apiBase) || '')
    .trim()
    .replace(/\/+$/, '');
  const apiUrl = (path) => `${API_BASE}${path}`;
  const LOG_LEVELS = ['TRACE', 'DEBUG', 'NOTE', 'INFO', 'WARN', 'ERROR', 'FATAL'];

  const METRICS = {
    temperature: { label: '温度', unit: '°C', digits: 1, cssVar: '--temp' },
    humidity: { label: '湿度', unit: '%RH', digits: 1, cssVar: '--humidity' },
    co2: { label: 'CO₂', unit: ' ppm', digits: 0, cssVar: '--co2' },
    pm25: { label: 'PM2.5', unit: ' µg/m³', digits: 0, cssVar: '--pm25' },
    noise: { label: '噪声', unit: ' dB', digits: 0, cssVar: '--noise' },
    light: { label: '光照', unit: ' lx', digits: 0, cssVar: '--light' },
  };
  const METRIC_ORDER = ['temperature', 'humidity', 'co2', 'pm25', 'noise', 'light'];
  const LIVE_METRIC_LIMIT = 2;
  const STATE_LABELS = { open: '未处理', acknowledged: '已确认', resolved: '已恢复' };
  const SEVERITY_LABELS = { warning: '警告', critical: '严重' };
  const SOURCE_LABELS = {
    http: '设备',
    simulator: '模拟器',
    node: '节点',
    alert: '告警',
    auth: '认证',
    settings: '设置',
    server: '服务',
    maintenance: '维护',
  };
  const titles = {
    dashboard: '仪表盘',
    nodes: '节点',
    alerts: '告警',
    history: '历史数据',
    logs: '日志',
    settings: '设置',
  };

  const state = {
    session: '',
    username: '',
    account: null,
    page: 'dashboard',
    clockOffset: 0,
    server: null,
    nodes: [],
    alerts: [],
    alertCounts: { open: 0, acknowledged: 0, resolved: 0, active: 0 },
    liveNode: '',
    liveMetrics: ['temperature', 'humidity'],
    liveSeries: new Map(),
    history: { series: [], metric: 'temperature', range: '24h' },
    settings: null,
    device: null,
    nodeDetail: null,
    logs: [],
    logLevels: new Set(['INFO', 'WARN', 'ERROR', 'FATAL']),
    stream: null,
    streamErrors: 0,
    polls: null,
    noticeTimer: null,
    alertsReload: null,
    charts: {},
  };

  /* ------------------------------------------------------------------ 基础工具 */

  function element(tag, text, attrs = {}) {
    const node = document.createElement(tag);
    if (text !== null && text !== undefined) node.textContent = text;
    for (const [key, value] of Object.entries(attrs)) {
      if (value === true) node.setAttribute(key, '');
      else if (value !== false && value !== null && value !== undefined) node.setAttribute(key, value);
    }
    return node;
  }

  function button(text, action, className = '') {
    const node = element('button', text, { type: 'button', class: className });
    node.addEventListener('click', () => Promise.resolve().then(action).catch(notifyError));
    return node;
  }

  function metricColor(name) {
    const value = getComputedStyle(document.documentElement).getPropertyValue(METRICS[name].cssVar);
    return (value || '#0f8fca').trim();
  }

  function cachedSession(value) {
    try {
      if (value === undefined) return localStorage.getItem(sessionStorageKey) || '';
      if (value) localStorage.setItem(sessionStorageKey, value);
      else localStorage.removeItem(sessionStorageKey);
    } catch {
      // 浏览器禁用本地存储时仍允许手动登录。
    }
    return '';
  }

  function now() {
    return Date.now() / 1000 + state.clockOffset;
  }

  function syncClock(serverTime) {
    if (Number.isFinite(serverTime)) state.clockOffset = serverTime - Date.now() / 1000;
  }

  function formatValue(value, digits = 1) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '--';
    return Number(value).toFixed(digits);
  }

  function formatAge(seconds) {
    const value = Math.max(0, Math.round(seconds));
    if (value < 60) return `${value} 秒`;
    if (value < 3600) return `${Math.floor(value / 60)} 分钟`;
    if (value < 86400) return `${Math.floor(value / 3600)} 小时 ${Math.floor((value % 3600) / 60)} 分`;
    return `${Math.floor(value / 86400)} 天 ${Math.floor((value % 86400) / 3600)} 小时`;
  }

  function formatTime(ts, withDate = false) {
    if (!ts) return '--';
    const date = new Date(ts * 1000);
    const pad = (value) => String(value).padStart(2, '0');
    const time = `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
    return withDate ? `${date.getMonth() + 1}-${pad(date.getDate())} ${time}` : time;
  }

  function formatBytes(bytes) {
    const value = Number(bytes) || 0;
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
    return `${(value / 1024 / 1024).toFixed(2)} MB`;
  }

  function formatUptime(seconds) {
    const value = Math.max(0, Math.round(seconds || 0));
    const days = Math.floor(value / 86400);
    const hours = Math.floor((value % 86400) / 3600);
    const minutes = Math.floor((value % 3600) / 60);
    if (days) return `${days} 天 ${hours} 小时 ${minutes} 分`;
    if (hours) return `${hours} 小时 ${minutes} 分`;
    return `${minutes} 分 ${value % 60} 秒`;
  }

  /* ------------------------------------------------------------------ 提示条 */

  function hideNotice() {
    clearTimeout(state.noticeTimer);
    state.noticeTimer = null;
    $('notice').hidden = true;
    $('notice-message').textContent = '';
  }

  function notify(message, timeout = 5000) {
    hideNotice();
    $('notice-message').textContent = message;
    $('notice').hidden = false;
    state.noticeTimer = setTimeout(hideNotice, timeout);
  }

  function notifyError(error) {
    if (error && error.authHandled) return;
    notify(error?.message || String(error), 8000);
  }

  async function copyText(text, label = '内容') {
    // 非安全上下文（如局域网 HTTP）下 Clipboard API 不可用，回退到 execCommand。
    try {
      await navigator.clipboard.writeText(text);
      notify(`${label}已复制到剪贴板`);
      return;
    } catch {
      const area = element('textarea', text, { style: 'position:fixed;opacity:0' });
      document.body.append(area);
      area.select();
      document.execCommand('copy');
      area.remove();
      notify(`${label}已复制到剪贴板`);
    }
  }

  /* ------------------------------------------------------------------ API */

  async function api(path, options = {}) {
    const { anonymous = false, ...requestOptions } = options;
    const headers = { ...(requestOptions.headers || {}) };
    if (!anonymous && state.session) headers['X-Auth-Token'] = state.session;
    if (requestOptions.body !== undefined && typeof requestOptions.body !== 'string') {
      headers['Content-Type'] = 'application/json';
      requestOptions.body = JSON.stringify(requestOptions.body);
    }
    let response;
    try {
      response = await fetch(apiUrl(path), { ...requestOptions, headers });
    } catch (error) {
      throw new Error('无法连接后端服务，请确认 main.py 仍在运行');
    }
    const data = await response.json().catch(() => ({ error: `请求失败（${response.status}）` }));
    if (response.status === 401 || response.status === 403) {
      const error = new Error(data.error || '登录已失效');
      error.authHandled = true;
      if (!anonymous) resetLogin(error.message);
      throw error;
    }
    if (!response.ok || data.ok === false) {
      throw new Error(data.error || `请求失败（${response.status}）`);
    }
    syncClock(data.server_time);
    return data;
  }

  /* ------------------------------------------------------------------ 登录 */

  async function login(event) {
    event?.preventDefault();
    const username = $('username').value.trim();
    const password = $('password').value;
    const submit = $('login-form').querySelector('button[type=submit]');
    $('login-error').textContent = '';
    if (!username || !password) {
      $('login-error').textContent = '请输入账号与密码';
      return;
    }
    submit.disabled = true;
    try {
      const result = await api('/api/login', {
        method: 'POST',
        body: { username, password },
        anonymous: true,
      });
      state.session = result.session;
      state.username = result.username || username;
      cachedSession(state.session);
      $('password').value = '';
      await enterShell(result);
    } catch (error) {
      $('login-error').textContent = error.message;
      $('loading').hidden = true;
      $('login').hidden = false;
    } finally {
      submit.disabled = false;
    }
  }

  async function logout() {
    const pending = api('/api/logout', { method: 'POST' }).catch(() => {});
    resetLogin();
    await pending;
  }

  function resetLogin(message = '') {
    cachedSession('');
    stopStream();
    clearInterval(state.polls);
    state.polls = null;
    state.session = '';
    state.username = '';
    state.account = null;
    state.nodes = [];
    state.alerts = [];
    state.logs = [];
    state.liveSeries.clear();
    destroyCharts();
    $('shell').hidden = true;
    $('login').hidden = false;
    $('loading').hidden = true;
    $('login-error').textContent = message;
    $('password').value = '';
    for (const dialog of document.querySelectorAll('dialog')) dialog.close();
    hideNotice();
  }

  function destroyCharts() {
    for (const chart of Object.values(state.charts)) {
      if (chart && typeof chart.destroy === 'function') chart.destroy();
    }
    state.charts = {};
  }

  async function enterShell(loginResult = {}) {
    if (loginResult.simulator !== undefined) {
      state.server = { ...(state.server || {}), simulator: loginResult.simulator };
    }
    $('loading').hidden = true;
    $('login').hidden = true;
    $('shell').hidden = false;
    // 先取一次总览，保证侧栏统计与节点下拉在任何页面都可用。
    await Promise.all([
      loadSettings().catch(() => {}),
      loadOverview().catch(() => {}),
      loadAccount().catch(() => {}),
    ]);
    await navigate(restorePage());
    startStream();
    startPolling();
  }

  async function boot() {
    const cached = cachedSession();
    if (!cached) {
      $('loading').hidden = true;
      $('login').hidden = false;
      return;
    }
    state.session = cached;
    try {
      const result = await api('/api/session', { method: 'POST' });
      state.username = result.username || '';
      await enterShell();
    } catch {
      resetLogin('登录已失效，请重新登录');
    }
  }

  /* ------------------------------------------------------------------ 导航 */

  function rememberPage() {
    try {
      sessionStorage.setItem(pageStorageKey, state.page);
    } catch {
      // 忽略存储不可用。
    }
  }

  function restorePage() {
    try {
      const saved = sessionStorage.getItem(pageStorageKey);
      if (saved && titles[saved]) return saved;
    } catch {
      // 忽略存储不可用。
    }
    return 'dashboard';
  }

  async function navigate(page) {
    state.page = page;
    document.querySelectorAll('.page').forEach((node) => {
      node.hidden = node.id !== page;
    });
    document.querySelectorAll('[data-page]').forEach((node) => {
      node.classList.toggle('active', node.dataset.page === page);
    });
    $('page-title').textContent = titles[page] || 'RoomSenseBoard';
    rememberPage();
    if (page === 'dashboard') await loadOverview();
    if (page === 'nodes') await loadNodes();
    if (page === 'alerts') await loadAlerts();
    if (page === 'history') await loadHistory();
    if (page === 'logs') await loadLogs();
    if (page === 'settings') await Promise.all([loadSettings(), loadAccount()]);
  }

  function startPolling() {
    clearInterval(state.polls);
    state.polls = setInterval(() => {
      if (state.page === 'dashboard') loadOverview().catch(notifyError);
      else api('/api/session', { method: 'POST' }).catch(() => {});
    }, 30000);
  }

  /* --------------------------------------------------------------- 实时流 */

  function startStream() {
    stopStream();
    setConnection('正在连接实时数据…', '');
    const source = new EventSource(apiUrl(`/api/stream?token=${encodeURIComponent(state.session)}`));
    state.stream = source;
    const consume = (type) => (event) => {
      try {
        handleStreamEvent(type, JSON.parse(event.data));
      } catch (error) {
        console.warn('无法解析实时事件', error);
      }
    };
    ['hello', 'reading', 'alert', 'node', 'node_deleted', 'log', 'settings', 'alerts_ack', 'device_key'].forEach((type) => {
      source.addEventListener(type, consume(type));
    });
    source.onopen = () => {
      state.streamErrors = 0;
      setConnection('实时连接正常', 'live');
    };
    source.onerror = () => {
      state.streamErrors += 1;
      setConnection('实时连接中断，正在重试…', 'down');
      // 连续失败时校验会话：服务重启或会话过期要回登录页，而不是无限重连。
      if (state.streamErrors >= 2) verifySessionAfterStreamError();
    };
  }

  let sessionCheckPending = false;

  async function verifySessionAfterStreamError() {
    if (sessionCheckPending) return;
    sessionCheckPending = true;
    try {
      const alive = await api('/api/session', { method: 'POST' });
      if (alive) state.streamErrors = 0;
    } catch {
      // 401 已由 api() 统一处理为退出登录；其他错误保持“重试中”状态。
    } finally {
      sessionCheckPending = false;
    }
  }

  function stopStream() {
    if (state.stream) {
      state.stream.close();
      state.stream = null;
    }
    setConnection('正在连接…', '');
  }

  function setConnection(text, className) {
    const node = $('connection');
    node.textContent = text;
    node.className = className || '';
  }

  function handleStreamEvent(type, payload) {
    if (type === 'hello') {
      state.server = { ...(state.server || {}), ...payload };
      renderRuntime();
      setConnection('实时连接正常', 'live');
      return;
    }
    if (type === 'reading') {
      applyReading(payload);
      return;
    }
    if (type === 'alert') {
      upsertAlert(payload);
      scheduleAlertsReload();
      return;
    }
    if (type === 'node') {
      upsertNode(payload);
      return;
    }
    if (type === 'node_deleted') {
      removeNodeLocally(payload.device_id);
      return;
    }
    if (type === 'log') {
      appendLog(payload);
      return;
    }
    if (type === 'settings') {
      loadSettings().catch(() => {});
      return;
    }
    if (type === 'alerts_ack' || type === 'device_key') {
      if (state.page === 'alerts') loadAlerts().catch(notifyError);
      else loadOverview().catch(() => {});
    }
  }

  function applyReading(reading) {
    const list = state.liveSeries.get(reading.device_id) || [];
    const point = {
      ts: reading.ts,
      temperature: reading.temperature,
      humidity: reading.humidity,
      co2: reading.co2,
      pm25: reading.pm25,
      noise: reading.noise,
      light: reading.light,
    };
    const last = list[list.length - 1];
    if (last && last.ts === point.ts) list[list.length - 1] = point;
    else list.push(point);
    const cutoff = now() - 1800;
    while (list.length && list[0].ts < cutoff) list.shift();
    if (list.length > 1200) list.splice(0, list.length - 1200);
    state.liveSeries.set(reading.device_id, list);

    if (reading.device_id === state.liveNode) {
      renderLiveChart();
      $('live-updated').textContent = `最新数据 ${formatTime(reading.ts, true)}`;
    }

    const node = state.nodes.find((item) => item.device_id === reading.device_id);
    if (node) {
      node.last_seen = Math.max(node.last_seen || 0, reading.ts);
      node.online = true;
      if (reading.temperature !== null && reading.temperature !== undefined) node.last_temperature = reading.temperature;
      if (reading.humidity !== null && reading.humidity !== undefined) node.last_humidity = reading.humidity;
      if (reading.co2 !== null && reading.co2 !== undefined) node.last_co2 = reading.co2;
      if (reading.pm25 !== null && reading.pm25 !== undefined) node.last_pm25 = reading.pm25;
      if (reading.noise !== null && reading.noise !== undefined) node.last_noise = reading.noise;
      if (reading.light !== null && reading.light !== undefined) node.last_light = reading.light;
      if (reading.rssi !== null && reading.rssi !== undefined) node.last_rssi = reading.rssi;
      if (reading.battery !== null && reading.battery !== undefined) node.last_battery = reading.battery;
    } else {
      loadNodes().catch(() => {});
    }
    if (state.page === 'dashboard') {
      renderCards();
      renderDashboardNodes();
    }
    if (state.page === 'nodes') renderNodeTable();
  }

  function upsertNode(node) {
    const index = state.nodes.findIndex((item) => item.device_id === node.device_id);
    const merged = { ...(index >= 0 ? state.nodes[index] : {}), ...node };
    if (merged.last_seen) merged.age = Math.max(0, Math.round(now() - merged.last_seen));
    if (merged.online === undefined) merged.online = (merged.age || 0) < (state.settings?.offline_timeout || 90);
    if (index >= 0) state.nodes[index] = merged;
    else state.nodes.push(merged);
    renderNodeOptions();
    renderCards();
    renderDashboardNodes();
    if (state.page === 'nodes') renderNodeTable();
  }

  function removeNodeLocally(deviceId) {
    if (!deviceId) return;
    state.nodes = state.nodes.filter((node) => node.device_id !== deviceId);
    state.liveSeries.delete(deviceId);
    if (state.liveNode === deviceId) state.liveNode = state.nodes[0]?.device_id || '';
    renderNodeOptions();
    renderCards();
    renderDashboardNodes();
    renderLiveChart();
    if (state.page === 'nodes') renderNodeTable();
  }

  function upsertAlert(alert) {
    const index = state.alerts.findIndex((item) => item.id === alert.id);
    if (index >= 0) state.alerts[index] = { ...state.alerts[index], ...alert };
    else state.alerts.unshift(alert);
    renderRecentAlerts();
    if (state.page === 'alerts') renderAlertCards();
  }

  function scheduleAlertsReload() {
    clearTimeout(state.alertsReload);
    state.alertsReload = setTimeout(() => {
      loadAlerts().catch(() => {});
      loadOverview().catch(() => {});
      renderRecentAlerts();
    }, 800);
  }

  /* ------------------------------------------------------------------ 概览 */

  async function loadOverview() {
    const data = await api('/api/overview');
    state.server = { ...(state.server || {}), ...(data.server || {}) };
    state.nodes = data.nodes || [];
    state.alertCounts = data.alert_counts || state.alertCounts;
    if (!state.alerts.length || state.page === 'dashboard') {
      state.alerts = data.alerts || [];
    }
  renderNodeOptions();
  if (state.liveNode && !state.liveSeries.has(state.liveNode)) {
    await loadLiveSeries(state.liveNode);
  }
    renderCards();
    renderDashboardNodes();
    renderRecentAlerts();
    renderRuntime();
    $('nodes-updated').textContent = `更新于 ${formatTime(now())}`;
  }

  async function loadLiveSeries(deviceId) {
    const data = await api(`/api/readings?range=30m&device_id=${encodeURIComponent(deviceId)}`);
    const series = data.series?.[0];
    state.liveSeries.set(
      deviceId,
    (series?.points || []).map((point) => ({
      ts: point.ts,
      temperature: point.temperature,
      humidity: point.humidity,
      co2: point.co2,
      pm25: point.pm25,
      noise: point.noise,
      light: point.light,
    })),
    );
    renderLiveChart();
  }

  function renderCards() {
    const online = state.nodes.filter((node) => node.online).length;
    const temperatures = state.nodes.map((node) => node.last_temperature).filter((value) => Number.isFinite(value));
    const humidities = state.nodes.map((node) => node.last_humidity).filter((value) => Number.isFinite(value));
    const average = (values) => (values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null);
    const open = state.alertCounts.open || 0;
    const critical = state.alerts.filter((alert) => alert.severity === 'critical' && alert.state !== 'resolved').length;
    const cards = [
      {
        label: '在线节点',
        value: `${online}/${state.nodes.length}`,
        note: state.nodes.length ? '设备连接状态' : '等待节点接入',
        className: online === state.nodes.length && online > 0 ? 'ok-text' : state.nodes.length ? 'warn-text' : '',
      },
      {
        label: '平均温度',
        value: average(temperatures),
        unit: '°C',
        digits: 1,
        note: '所有节点当前值',
        className: 'metric-temp',
      },
      {
        label: '平均湿度',
        value: average(humidities),
        unit: '%RH',
        digits: 1,
        note: '所有节点当前值',
        className: 'metric-humidity',
      },
      {
        label: '未处理告警',
        value: String(open),
        note: critical ? `其中 ${critical} 条严重` : '待确认数量',
        className: open ? 'danger-text' : 'ok-text',
      },
    ];
    const container = $('status-cards');
    container.replaceChildren();
    for (const card of cards) {
      const box = element('div', null, { class: 'panel card' });
      box.append(element('span', card.label));
      const strong = element('strong', null, { class: card.className || '' });
      if (card.value === null || card.value === undefined || card.value === '') {
        strong.append(document.createTextNode('--'));
      } else if (card.unit) {
        strong.append(document.createTextNode(formatValue(card.value, card.digits ?? 1)));
        strong.append(element('small', card.unit));
      } else {
        strong.append(document.createTextNode(String(card.value)));
      }
      box.append(strong);
      if (card.note) box.append(element('span', card.note));
      container.append(box);
    }
    updateAlertBadge();
  }

  function updateAlertBadge() {
    const badge = $('nav-alert-badge');
    const open = state.alertCounts.open || 0;
    badge.textContent = open > 99 ? '99+' : String(open);
    badge.hidden = open === 0;
    $('sidebar-online').textContent = `${state.nodes.filter((node) => node.online).length}/${state.nodes.length}`;
    $('sidebar-alerts').textContent = String(open);
  }

  function renderDashboardNodes() {
    const body = $('dashboard-node-rows');
    body.replaceChildren();
    if (!state.nodes.length) {
      const row = element('tr');
      row.append(element('td', '暂无节点数据，启动模拟器或让设备上报后显示', { class: 'empty', colspan: '7' }));
      body.append(row);
      return;
    }
    for (const node of state.nodes) {
      const row = element('tr');
      row.append(element('td', node.name || node.device_id));
      row.append(element('td', node.room || '--'));
      const status = element('td');
      status.append(element('span', null, { class: `dot ${node.online ? 'online' : 'offline'}` }));
      status.append(element('span', node.online ? '在线' : `离线 ${formatAge(now() - node.last_seen)}`, { class: 'status-text' }));
      row.append(status);
    row.append(element('td', formatValue(node.last_temperature, 1), { class: 'metric-temp num' }));
    row.append(element('td', formatValue(node.last_humidity, 1), { class: 'metric-humidity num' }));
    row.append(
      element('td', node.last_co2 === null || node.last_co2 === undefined ? '--' : formatValue(node.last_co2, 0), {
        class: 'metric-co2 num',
      }),
    );
    row.append(element('td', `${formatAge(now() - node.last_seen)}前`, { class: 'num' }));
      row.append(
        (() => {
          const cell = element('td');
          cell.append(button('详情', () => openNodeDialog(node.device_id)));
          return cell;
        })(),
      );
      body.append(row);
    }
  }

  function renderRecentAlerts() {
    const list = $('recent-alerts');
    list.replaceChildren();
    const recent = state.alerts.filter((alert) => alert.state !== 'resolved').slice(0, 6);
    if (!recent.length) {
      list.append(element('li', '当前没有活跃告警，环境状态正常。', { class: 'empty' }));
      return;
    }
    for (const alert of recent) list.append(alertRow(alert));
  }

  function alertRow(alert) {
    const item = element('li');
    item.append(element('span', SEVERITY_LABELS[alert.severity] || alert.severity, { class: `severity ${alert.severity}` }));
    const body = element('div', null, { class: 'alert-body' });
    body.append(element('div', alert.message, { class: 'alert-message' }));
    body.append(
      element('span', `${alert.device_name || alert.device_id} · ${formatTime(alert.opened_at, true)} · ${STATE_LABELS[alert.state] || alert.state}`, {
        class: 'alert-meta',
      }),
    );
    item.append(body);
    return item;
  }

  function renderRuntime() {
    const server = state.server || {};
    const stats = server.stats || {};
    const grid = $('runtime-grid');
    grid.replaceChildren();
    const rows = [
      ['服务版本', server.version ? `v${server.version}` : '--'],
      ['运行时长', formatUptime(server.uptime)],
      ['节点总数', stats.nodes ?? state.nodes.length],
      ['累计读数', stats.readings ?? '--'],
      ['近一小时入库', stats.readings_last_hour ?? '--'],
      ['数据文件', formatBytes(stats.db_bytes)],
      ['实时连接数', server.streams ?? '--'],
      ['数据模拟器', server.simulator ? '已开启' : '已关闭'],
    ];
    for (const [label, value] of rows) {
      const row = element('div');
      row.append(element('span', label), element('strong', String(value)));
      grid.append(row);
    }
    $('runtime-note').textContent = server.simulator
      ? '当前数据由内置模拟器生成，接入真实节点后可在设置中关闭。'
      : '当前仅接收真实设备上报。';
  }

  /* --------------------------------------------------------------- 实时图表 */

  function renderNodeOptions() {
    const select = $('live-node');
    const previous = state.liveNode || select.value;
    select.replaceChildren();
    if (!state.nodes.length) {
      select.append(element('option', '暂无节点', { value: '' }));
      select.disabled = true;
      refreshDropdown(select);
      return;
    }
    select.disabled = false;
    for (const node of state.nodes) {
      select.append(element('option', node.name || node.device_id, { value: node.device_id }));
    }
    if (previous && state.nodes.some((node) => node.device_id === previous)) select.value = previous;
    state.liveNode = select.value;

    const historySelect = $('history-node');
    const historyPrevious = historySelect.value;
    historySelect.replaceChildren();
    historySelect.append(element('option', '全部节点', { value: '' }));
    for (const node of state.nodes) {
      historySelect.append(element('option', node.name || node.device_id, { value: node.device_id }));
    }
    if (historyPrevious && state.nodes.some((node) => node.device_id === historyPrevious)) {
      historySelect.value = historyPrevious;
    }
    refreshDropdown(select);
    refreshDropdown(historySelect);
  }

  function refreshDropdown(select) {
    if (window.RoomSenseUI && window.RoomSenseUI.dropdown) {
      window.RoomSenseUI.dropdown.refresh(select);
    }
  }

  function renderMetricChips() {
    const container = $('live-metrics');
    if (!container) return;
    container.replaceChildren();
    for (const key of METRIC_ORDER) {
      const active = state.liveMetrics.includes(key);
      const chip = element('button', null, {
        type: 'button',
        class: 'metric-chip',
        'data-metric': key,
        'aria-pressed': active ? 'true' : 'false',
        title: `${METRICS[key].label}（${METRICS[key].unit.trim()}）`,
      });
      chip.append(element('span', null, { class: 'swatch', style: `background:${metricColor(key)}` }));
      chip.append(document.createTextNode(METRICS[key].label));
      chip.addEventListener('click', () => toggleLiveMetric(key));
      container.append(chip);
    }
  }

  function toggleLiveMetric(key) {
    if (state.liveMetrics.includes(key)) {
      if (state.liveMetrics.length === 1) {
        notify('至少保留一个监控指标');
        return;
      }
      state.liveMetrics = state.liveMetrics.filter((item) => item !== key);
    } else {
      if (state.liveMetrics.length >= LIVE_METRIC_LIMIT) {
        notify(`最多同时对比 ${LIVE_METRIC_LIMIT} 个指标，请先取消一个`);
        return;
      }
      state.liveMetrics = [...state.liveMetrics, key];
    }
    renderMetricChips();
    renderLiveChart();
  }

  function renderLiveChart() {
    if (!state.charts.live) {
      state.charts.live = new LineChart($('live-chart'), { emptyText: '等待节点上报数据…' });
    }
    const points = state.liveSeries.get(state.liveNode) || [];
    // 第一个指标走左轴、第二个走右轴，因此任意两个不同量纲的指标都能同图对比。
    const series = state.liveMetrics.map((key, index) => ({
      key,
      label: METRICS[key].label,
      color: metricColor(key),
      unit: METRICS[key].unit,
      digits: METRICS[key].digits,
      axis: index === 0 ? 0 : 1,
      fill: index === 0,
      points: points.filter((point) => Number.isFinite(point[key])).map((point) => [point.ts, point[key]]),
    }));
    state.charts.live.setSeries(series);
    const legend = $('live-legend');
    legend.replaceChildren();
    series.forEach((item, index) => {
      const entry = element('span');
      entry.append(element('span', null, { class: 'swatch', style: `background:${item.color}` }));
      entry.append(document.createTextNode(`${item.label}（${item.unit.trim()}，${index === 0 ? '左轴' : '右轴'}）`));
      legend.append(entry);
    });
    if (series.length === 0) legend.append(element('span', '未选择指标'));
    const lastPoint = points[points.length - 1];
    $('live-updated').textContent = lastPoint ? `最新数据 ${formatTime(lastPoint.ts, true)}` : '暂无数据';
  }

  /* ------------------------------------------------------------------ 节点页 */

  async function loadNodes() {
    const data = await api('/api/nodes');
    state.nodes = data.nodes || [];
    renderNodeOptions();
    renderNodeTable();
    renderCards();
    renderDashboardNodes();
    $('nodes-note').textContent = `共 ${state.nodes.length} 个节点`;
  }

  function filteredNodes() {
    const keyword = $('node-search').value.trim().toLowerCase();
    if (!keyword) return state.nodes;
    return state.nodes.filter((node) =>
      [node.device_id, node.name, node.room].some((value) => String(value || '').toLowerCase().includes(keyword)),
    );
  }

  function renderNodeTable() {
    const body = $('node-rows');
    body.replaceChildren();
    const nodes = filteredNodes();
    if (!nodes.length) {
      const row = element('tr');
      row.append(element('td', '没有匹配的节点', { class: 'empty', colspan: '13' }));
      body.append(row);
      return;
    }
    for (const node of nodes) {
      const row = element('tr');
      const nameCell = element('td');
      nameCell.append(element('div', node.name || node.device_id));
      nameCell.append(element('span', node.device_id, { class: 'hint' }));
      row.append(nameCell);
      row.append(element('td', node.room || '--'));
      const status = element('td');
      status.append(element('span', null, { class: `dot ${node.online ? 'online' : 'offline'}` }));
      status.append(element('span', node.online ? '在线' : '离线', { class: 'status-text' }));
      row.append(status);
    row.append(element('td', formatValue(node.last_temperature, 1), { class: 'metric-temp num' }));
    row.append(element('td', formatValue(node.last_humidity, 1), { class: 'metric-humidity num' }));
    row.append(
      element('td', node.last_co2 === null || node.last_co2 === undefined ? '--' : formatValue(node.last_co2, 0), {
        class: 'metric-co2 num',
      }),
    );
    row.append(
      element('td', node.last_pm25 === null || node.last_pm25 === undefined ? '--' : formatValue(node.last_pm25, 0), {
        class: 'metric-pm25 num',
      }),
    );
    row.append(
      element('td', node.last_noise === null || node.last_noise === undefined ? '--' : formatValue(node.last_noise, 0), {
        class: 'metric-noise num',
      }),
    );
    row.append(
      element(
        'td',
        node.last_battery === null || node.last_battery === undefined ? '--' : `${formatValue(node.last_battery, 2)} V`,
        { class: 'num' },
      ),
    );
    row.append(
      element(
        'td',
        node.last_rssi === null || node.last_rssi === undefined ? '--' : `${node.last_rssi} dBm`,
        { class: 'num' },
      ),
    );
    row.append(element('td', String(node.reading_count ?? 0), { class: 'num' }));
    row.append(
      element('td', `${formatTime(node.last_seen, true)}（${formatAge(now() - node.last_seen)}前）`, { class: 'num' }),
    );
      const actions = element('td');
      actions.append(button('详情', () => openNodeDialog(node.device_id)));
      row.append(actions);
      body.append(row);
    }
  }

  async function openNodeDialog(deviceId) {
    const dialog = $('node-dialog');
    state.nodeDetail = null;
    $('node-title').textContent = '节点详情';
    $('node-summary').replaceChildren();
    $('node-alerts').replaceChildren();
    $('node-note').textContent = '正在加载…';
    dialog.showModal();
    dialog.focus(); // 避免焦点自动落到右上角关闭按钮上出现焦点环
    try {
      const data = await api(`/api/nodes/${encodeURIComponent(deviceId)}`);
      const node = data.node;
      state.nodeDetail = data;
      $('node-title').textContent = `${node.name || node.device_id}`;
      $('node-name').value = node.name || '';
      $('node-room').value = node.room || '';
      $('node-form').dataset.deviceId = node.device_id;
      const summary = $('node-summary');
      const rows = [
        ['设备 ID', node.device_id],
        ['位置', node.room || '--'],
        ['状态', node.online ? `在线（${formatAge(now() - node.last_seen)}前上报）` : `离线（${formatAge(now() - node.last_seen)}未上报）`],
        ['温度', `${formatValue(node.last_temperature, 1)} °C`],
        ['湿度', `${formatValue(node.last_humidity, 1)} %RH`],
        ['CO₂', node.last_co2 === null || node.last_co2 === undefined ? '--' : `${formatValue(node.last_co2, 0)} ppm`],
        ['PM2.5', node.last_pm25 === null || node.last_pm25 === undefined ? '--' : `${formatValue(node.last_pm25, 0)} µg/m³`],
        ['噪声', node.last_noise === null || node.last_noise === undefined ? '--' : `${formatValue(node.last_noise, 0)} dB`],
        ['光照', node.last_light === null || node.last_light === undefined ? '--' : `${formatValue(node.last_light, 0)} lx`],
        ['电量', node.last_battery === null || node.last_battery === undefined ? '--' : `${formatValue(node.last_battery, 2)} V`],
        ['信号', node.last_rssi === null || node.last_rssi === undefined ? '--' : `${node.last_rssi} dBm`],
        ['累计上报', `${node.reading_count ?? 0} 条`],
        ['首次接入', formatTime(node.first_seen, true)],
      ];
      for (const [label, value] of rows) {
        const row = element('div');
        row.append(element('span', label), element('strong', String(value)));
        summary.append(row);
      }
      const points = (data.readings || []).map((item) => ({ ...item }));
      if (!state.charts.node) state.charts.node = new LineChart($('node-chart'), { emptyText: '最近 30 分钟暂无数据' });
      state.charts.node.setSeries([
        {
          label: '温度',
          color: metricColor('temperature'),
          unit: ' °C',
          axis: 0,
          fill: true,
          points: points.filter((point) => Number.isFinite(point.temperature)).map((point) => [point.ts, point.temperature]),
        },
        {
          label: '湿度',
          color: metricColor('humidity'),
          unit: ' %RH',
          axis: 1,
          points: points.filter((point) => Number.isFinite(point.humidity)).map((point) => [point.ts, point.humidity]),
        },
      ]);
      const list = $('node-alerts');
      if (!(data.alerts || []).length) list.append(element('li', '该节点暂无告警记录', { class: 'empty' }));
      else for (const alert of data.alerts.slice(0, 6)) list.append(alertRow(alert));
      $('node-note').textContent = '';
    } catch (error) {
      $('node-note').textContent = error.message;
    }
  }

  function openDeleteDialog() {
    const detail = state.nodeDetail;
    if (!detail) {
      notify('节点信息尚未加载完成');
      return;
    }
    const node = detail.node;
    $('delete-summary').textContent =
      `确定删除「${node.name || node.device_id}」（${node.device_id}）吗？` +
      '删除后它会从节点列表与仪表盘消失，若之后再次上报将作为新节点接入。';
    $('delete-purge').checked = true;
    updateDeleteNote();
    const dialog = $('delete-dialog');
    dialog.showModal();
    dialog.focus();
  }

  function updateDeleteNote() {
    const totals = (state.nodeDetail && state.nodeDetail.totals) || { readings: 0, alerts: 0 };
    $('delete-note').textContent = $('delete-purge').checked
      ? `将同时删除 ${totals.readings} 条历史读数与 ${totals.alerts} 条告警记录，删除后无法在历史数据页查询。`
      : `保留 ${totals.readings} 条历史读数（历史数据页仍可查询），该节点的活跃告警会被关闭。`;
  }

  async function confirmDeleteNode() {
    const detail = state.nodeDetail;
    if (!detail) return;
    const deviceId = detail.node.device_id;
    const purge = $('delete-purge').checked;
    const button = $('delete-confirm');
    button.disabled = true;
    try {
      const result = await api(`/api/nodes/${encodeURIComponent(deviceId)}?purge=${purge ? 1 : 0}`, {
        method: 'DELETE',
      });
      $('delete-dialog').close();
      $('node-dialog').close();
      removeNodeLocally(deviceId);
      notify(result.message || `节点 ${deviceId} 已删除`, 9000);
      await Promise.all([loadOverview().catch(() => {}), loadNodes().catch(() => {})]);
    } catch (error) {
      $('delete-note').textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }

  async function saveNode(event) {
    event.preventDefault();
    const deviceId = $('node-form').dataset.deviceId;
    if (!deviceId) return;
    $('node-note').textContent = '正在保存…';
    try {
      const data = await api(`/api/nodes/${encodeURIComponent(deviceId)}`, {
        method: 'PUT',
        body: { name: $('node-name').value.trim(), room: $('node-room').value.trim() },
      });
      upsertNode(data.node);
      $('node-title').textContent = data.node.name;
      $('node-note').textContent = '已保存';
      notify(`节点 ${data.node.name} 信息已更新`);
    } catch (error) {
      $('node-note').textContent = error.message;
    }
  }

  /* ------------------------------------------------------------------ 告警页 */

  async function loadAlerts() {
    const filter = $('alert-filter').value || 'active';
    const data = await api(`/api/alerts?state=${encodeURIComponent(filter)}&limit=300`);
    state.alerts = data.alerts || [];
    state.alertCounts = data.counts || state.alertCounts;
    renderAlertCards();
    renderRecentAlerts();
    renderCards();
    const counts = state.alertCounts;
    $('alert-count').textContent = `未处理 ${counts.open || 0} · 已确认 ${counts.acknowledged || 0} · 已恢复 ${counts.resolved || 0}`;
  }

  function renderAlertCards() {
    const container = $('alert-cards');
    container.replaceChildren();
    if (!state.alerts.length) {
      const panel = element('div', '该筛选条件下暂无告警记录。', { class: 'panel hint' });
      container.append(panel);
      return;
    }
    for (const alert of state.alerts) {
      const card = element('div', null, { class: 'panel alert-card' });
      card.append(element('span', SEVERITY_LABELS[alert.severity] || alert.severity, { class: `severity ${alert.severity}` }));
      const body = element('div', null, { class: 'alert-body' });
      body.append(element('h3', alert.message));
      body.append(
        element(
          'div',
          `${alert.device_name || alert.device_id}${alert.room ? ` · ${alert.room}` : ''} · 触发于 ${formatTime(alert.opened_at, true)}` +
            (alert.resolved_at ? ` · 恢复于 ${formatTime(alert.resolved_at, true)}` : ''),
          { class: 'hint' },
        ),
      );
      body.append(element('span', kindLabel(alert.kind), { class: 'hint' }));
      card.append(body);
      if (alert.state === 'open') {
        card.append(button('确认', () => ackAlert(alert.id), 'primary'));
      }
      card.append(element('span', STATE_LABELS[alert.state] || alert.state, { class: `state ${alert.state}` }));
      container.append(card);
    }
  }

  function kindLabel(kind) {
    return (
      {
        temperature_high: '温度过高',
        temperature_low: '温度过低',
        humidity_high: '湿度过高',
        humidity_low: '湿度过低',
        co2_high: 'CO₂ 浓度过高',
        device_offline: '节点离线',
      }[kind] || kind
    );
  }

  async function ackAlert(id) {
    await api(`/api/alerts/${id}/ack`, { method: 'POST' });
    await Promise.all([loadAlerts(), loadOverview().catch(() => {})]);
    notify('告警已确认');
  }

  async function ackAll() {
    const result = await api('/api/alerts/ack', { method: 'POST' });
    state.alertCounts = result.counts || state.alertCounts;
    await Promise.all([loadAlerts().catch(() => {}), loadOverview().catch(() => {})]);
    notify(result.count ? `已确认 ${result.count} 条告警` : '没有未处理的告警');
  }

  /* ------------------------------------------------------------------ 历史页 */

  async function loadHistory() {
    const range = $('history-range').value || '24h';
    const deviceId = $('history-node').value || '';
    const metric = $('history-metric').value || 'temperature';
    state.history.metric = metric;
    state.history.range = range;
    const params = new URLSearchParams({ range });
    if (deviceId) params.set('device_id', deviceId);
    const data = await api(`/api/readings?${params.toString()}`);
    state.history.series = data.series || [];
    $('history-range-note').textContent = `${rangeLabel(data.range)}，聚合间隔 ${bucketLabel(data.bucket)}`;
    renderHistoryStats();
    renderHistoryChart();
    $('history-note').textContent = `${state.history.series.length} 个节点 · ${state.history.series.reduce((sum, item) => sum + item.points.length, 0)} 个数据点`;
  }

  function rangeLabel(range) {
    return (
      {
        '30m': '最近 30 分钟',
        '1h': '最近 1 小时',
        '6h': '最近 6 小时',
        '24h': '最近 24 小时',
        '7d': '最近 7 天',
        '30d': '最近 30 天',
      }[range] || range
    );
  }

  function bucketLabel(bucket) {
    if (bucket < 60) return `${bucket} 秒`;
    if (bucket < 3600) return `${Math.round(bucket / 60)} 分钟`;
    if (bucket < 86400) return `${Math.round(bucket / 3600)} 小时`;
    return `${Math.round(bucket / 86400)} 天`;
  }

  function metricValues(metric) {
    const values = [];
    for (const item of state.history.series) {
      for (const point of item.points) {
        if (Number.isFinite(point[metric])) values.push(point[metric]);
      }
    }
    return values;
  }

  function renderHistoryStats() {
    const metric = METRICS[state.history.metric];
    const values = metricValues(state.history.metric);
    const container = $('history-stats');
    container.replaceChildren();
    const latestPerNode = state.history.series
      .map((item) => [...item.points].reverse().find((point) => Number.isFinite(point[state.history.metric])))
      .filter(Boolean);
    const latestAverage = latestPerNode.length
      ? latestPerNode.reduce((sum, point) => sum + point[state.history.metric], 0) / latestPerNode.length
      : null;
    const cards = [
      ['最小值', values.length ? Math.min(...values) : null],
      ['平均值', values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null],
      ['最大值', values.length ? Math.max(...values) : null],
      ['最新值', latestAverage],
    ];
    for (const [label, value] of cards) {
      const box = element('div', null, { class: 'panel card' });
      box.append(element('span', `${metric.label}${label}`));
      const strong = element('strong', null, { class: `metric-${state.history.metric}` });
      strong.append(document.createTextNode(value === null ? '--' : formatValue(value, metric.digits)));
      strong.append(element('small', metric.unit));
      box.append(strong);
      container.append(box);
    }
  }

  function renderHistoryChart() {
    if (!state.charts.history) {
      state.charts.history = new LineChart($('history-chart'), { emptyText: '所选范围内暂无数据' });
    }
    const metric = state.history.metric;
    const meta = METRICS[metric];
    const palette = ['#00a0ea', '#e0762f', '#7a5af8', '#168446', '#b82c35', '#af6b0e'];
    const series = state.history.series.map((item, index) => ({
      label: item.name || item.device_id,
      color: palette[index % palette.length],
      unit: ` ${meta.unit.trim()}`,
      digits: meta.digits,
      axis: 0,
      fill: state.history.series.length === 1,
      points: item.points
        .filter((point) => Number.isFinite(point[metric]))
        .map((point) => [point.ts, point[metric]]),
    }));
    state.charts.history.setSeries(series);
    $('history-title').textContent = `${meta.label}历史曲线`;
    const legend = $('history-legend');
    legend.replaceChildren();
    for (const item of series) {
      const entry = element('span');
      entry.append(element('span', null, { class: 'swatch', style: `background:${item.color}` }));
      entry.append(document.createTextNode(item.label));
      legend.append(entry);
    }
  }

  function exportHistoryCsv() {
    if (!state.history.series.length) {
      notify('当前没有可导出的数据');
      return;
    }
    const metric = state.history.metric;
    const meta = METRICS[metric];
    const rows = [['节点', '设备ID', '时间', `${meta.label}(${meta.unit.trim()})`]];
    for (const item of state.history.series) {
      for (const point of item.points) {
        if (!Number.isFinite(point[metric])) continue;
        rows.push([item.name || item.device_id, item.device_id, new Date(point.ts * 1000).toISOString(), point[metric]]);
      }
    }
    const csv = rows
      .map((row) => row.map((value) => `"${String(value).replace(/"/g, '""')}"`).join(','))
      .join('\r\n');
    const blob = new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8' });
    const link = element('a', null, {
      href: URL.createObjectURL(blob),
      download: `roomsense-${state.history.range}-${metric}.csv`,
    });
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(link.href);
  }

  /* ------------------------------------------------------------------ 日志页 */

  function selectedLogLevels() {
    const all = $('log-all').checked;
    if (all) return [...LOG_LEVELS];
    return LOG_LEVELS.filter((level) => {
      const input = document.querySelector(`#log-level-options input[value="${level}"]`);
      return input && input.checked;
    });
  }

  function updateLogFilter() {
    const levels = selectedLogLevels();
    state.logLevels = new Set(levels);
    $('log-level').textContent = levels.length === LOG_LEVELS.length ? '全部' : levels.join('、') || '未选择';
  }

  async function loadLogs() {
    updateLogFilter();
    const levels = [...state.logLevels];
    if (!levels.length) {
      state.logs = [];
      renderLogs();
      return;
    }
    const data = await api(`/api/logs?levels=${encodeURIComponent(levels.join(','))}&limit=500`);
    state.logs = data.logs || [];
    renderLogs();
  }

  function renderLogs() {
    const output = $('log-output');
    output.replaceChildren();
    for (const entry of state.logs) output.append(logLine(entry));
    $('log-count').textContent = `显示 ${state.logs.length} 条`;
    if ($('log-scroll').checked) output.scrollTop = output.scrollHeight;
  }

  function logLine(entry) {
    const line = element('div', null, { class: `log-line level-${entry.level}` });
    const source = SOURCE_LABELS[entry.source] || entry.source;
    line.append(element('span', `${formatTime(entry.ts, true)} [${entry.level}] `));
    line.append(element('span', `${source} `, { class: 'log-source' }));
    line.append(document.createTextNode(entry.message));
    return line;
  }

  function appendLog(entry) {
    if (!state.logLevels.has(entry.level)) return;
    state.logs.push(entry);
    if (state.logs.length > 500) state.logs.splice(0, state.logs.length - 500);
    if (state.page !== 'logs') return;
    const output = $('log-output');
    output.append(logLine(entry));
    while (output.childElementCount > 500) output.firstElementChild.remove();
    $('log-count').textContent = `显示 ${output.childElementCount} 条`;
    if ($('log-scroll').checked) output.scrollTop = output.scrollHeight;
  }

  /* ------------------------------------------------------------------ 设置页 */

  async function loadSettings() {
    const data = await api('/api/settings');
    state.settings = data.settings;
    state.device = data.device;
    const rules = data.settings.alerts || {};
    const temperature = rules.temperature || {};
    const humidity = rules.humidity || {};
    const co2 = rules.co2 || {};
    $('set-temp-low').value = temperature.low ?? '';
    $('set-temp-high').value = temperature.high ?? '';
    $('set-temp-critical-low').value = temperature.critical_low ?? '';
    $('set-temp-critical-high').value = temperature.critical_high ?? '';
    $('set-humidity-low').value = humidity.low ?? '';
    $('set-humidity-high').value = humidity.high ?? '';
    $('set-humidity-critical-low').value = humidity.critical_low ?? '';
    $('set-humidity-critical-high').value = humidity.critical_high ?? '';
    $('set-co2-high').value = co2.high ?? '';
    $('set-co2-critical-high').value = co2.critical_high ?? '';
    $('set-offline').value = data.settings.offline_timeout ?? 90;
    $('set-retention').value = data.settings.retention_days ?? 30;
    const device = data.device || {};
    const info = $('device-info');
    info.replaceChildren();
    const rows = [
      ['上报地址', device.ingest_url || '--'],
      ['节点密钥', device.key || '--'],
      ['离线判定', `${device.offline_timeout ?? '--'} 秒`],
      ['单批上限', `${device.max_batch ?? '--'} 条`],
    ];
    for (const [label, value] of rows) {
      const row = element('div');
      row.append(element('span', label), element('strong', String(value)));
      info.append(row);
    }
    $('ingest-example').textContent = [
      'POST /api/ingest',
      'Content-Type: application/json',
      `X-Device-Key: ${device.key || '<节点密钥>'}`,
      '',
      JSON.stringify(
        {
          device_id: 'meeting-a',
          name: 'A 会议室',
          room: '行政楼 1F',
          ts: Math.floor(now()),
          temperature: 24.6,
          humidity: 52.3,
          co2: 780,
          pm25: 14,
          noise: 48,
          battery: 4.02,
          rssi: -58,
          seq: 1024,
        },
        null,
        2,
      ),
    ].join('\n');
    const about = $('about-info');
    about.replaceChildren();
    const server = data.server || {};
    const aboutRows = [
      ['服务名称', server.name || 'RoomSenseBoard'],
      ['版本', server.version ? `v${server.version}` : '--'],
      ['运行时长', formatUptime(server.uptime)],
      ['监听地址', `${server.host || '--'}:${server.port || '--'}`],
      ['数据模拟器', server.simulator ? '已开启' : '已关闭'],
    ];
    for (const [label, value] of aboutRows) {
      const row = element('div');
      row.append(element('span', label), element('strong', String(value)));
      about.append(row);
    }
    renderAccount();
  }

  async function loadAccount() {
    const data = await api('/api/account');
    state.account = data;
    state.username = data.account?.username || state.username;
    renderAccount();
  }

  function renderAccount() {
    const info = $('account-info');
    if (!info) return;
    info.replaceChildren();
    const account = state.account?.account || {};
    const rows = [
      ['当前账号', account.username || state.username || '--'],
      ['创建时间', account.created_at ? formatTime(account.created_at, true) : '--'],
      ['最近修改', account.updated_at ? formatTime(account.updated_at, true) : '--'],
      ['账号数量', String((state.account?.users || []).length || 1)],
      ['在线会话', String(state.account?.sessions ?? '--')],
    ];
    for (const [label, value] of rows) {
      const row = element('div');
      row.append(element('span', label), element('strong', String(value)));
      info.append(row);
    }
  }

  async function changePassword(event) {
    event.preventDefault();
    const current = $('current-password').value;
    const next = $('new-password').value;
    const confirm = $('confirm-password').value;
    const note = $('password-note');
    if (!current || !next) {
      note.textContent = '请填写当前密码与新密码';
      return;
    }
    if (next !== confirm) {
      note.textContent = '两次输入的新密码不一致';
      return;
    }
    note.textContent = '正在修改…';
    try {
      const data = await api('/api/account/password', {
        method: 'POST',
        body: { current_password: current, new_password: next },
      });
      $('current-password').value = '';
      $('new-password').value = '';
      $('confirm-password').value = '';
      note.textContent =
        data.revoked_sessions > 0 ? `密码已更新，其他 ${data.revoked_sessions} 个会话已退出` : '密码已更新';
      notify('密码修改成功', 8000);
      loadAccount().catch(() => {});
    } catch (error) {
      note.textContent = error.message;
    }
  }

  async function saveSettings(event) {
    event.preventDefault();
    const fields = [
      'set-temp-low',
      'set-temp-high',
      'set-temp-critical-low',
      'set-temp-critical-high',
      'set-humidity-low',
      'set-humidity-high',
      'set-humidity-critical-low',
      'set-humidity-critical-high',
      'set-co2-high',
      'set-co2-critical-high',
      'set-offline',
      'set-retention',
    ];
    const empty = fields.find((id) => $(id).value.trim() === '' || !Number.isFinite(Number($(id).value)));
    if (empty) {
      $('settings-note').textContent = '请填写全部阈值后再保存';
      return;
    }
    const number = (id) => Number($(id).value);
    const payload = {
      offline_timeout: number('set-offline'),
      retention_days: number('set-retention'),
      alerts: {
        temperature: {
          low: number('set-temp-low'),
          high: number('set-temp-high'),
          critical_low: number('set-temp-critical-low'),
          critical_high: number('set-temp-critical-high'),
        },
        humidity: {
          low: number('set-humidity-low'),
          high: number('set-humidity-high'),
          critical_low: number('set-humidity-critical-low'),
          critical_high: number('set-humidity-critical-high'),
        },
        co2: { high: number('set-co2-high'), critical_high: number('set-co2-critical-high') },
      },
    };
    $('settings-note').textContent = '正在保存…';
    try {
      const data = await api('/api/settings', { method: 'PUT', body: payload });
      state.settings = data.settings;
      $('settings-note').textContent = '已保存并立即生效';
      notify('告警阈值与运行参数已更新');
    } catch (error) {
      $('settings-note').textContent = error.message;
    }
  }

  async function regenerateDeviceKey() {
    if (!window.confirm('重新生成后，旧密钥会立即失效，需要同步更新所有节点固件。确定继续？')) return;
    const data = await api('/api/device-key/regenerate', { method: 'POST' });
    notify(`新的节点密钥：${data.device_key}`, 20000);
    loadSettings().catch(notifyError);
  }

  /* ------------------------------------------------------------------ 事件绑定 */

  function bindEvents() {
    renderMetricChips();
    $('login-form').addEventListener('submit', (event) => login(event));
    $('logout').addEventListener('click', () => logout());
    $('notice-close').addEventListener('click', hideNotice);
    document.querySelectorAll('[data-page]').forEach((node) => {
      node.addEventListener('click', () => navigate(node.dataset.page).catch(notifyError));
    });
    document.querySelectorAll('[data-close]').forEach((node) => {
      node.addEventListener('click', () => $(node.dataset.close).close());
    });

    $('live-node').addEventListener('change', async (event) => {
      state.liveNode = event.target.value;
      if (!state.liveSeries.has(state.liveNode)) await loadLiveSeries(state.liveNode);
      else renderLiveChart();
    });
    $('dashboard-refresh').addEventListener('click', () => loadOverview().catch(notifyError));
    $('refresh-all').addEventListener('click', () => {
      Promise.all([loadOverview(), loadSettings()])
        .then(() => notify('数据已刷新'))
        .catch(notifyError);
    });
    $('dashboard-alerts').addEventListener('click', () => navigate('alerts').catch(notifyError));
    $('ack-all').addEventListener('click', () => ackAll().catch(notifyError));

    $('node-search').addEventListener('input', renderNodeTable);
    $('refresh-nodes').addEventListener('click', () => loadNodes().catch(notifyError));
    $('node-form').addEventListener('submit', (event) => saveNode(event).catch(notifyError));
    $('node-delete').addEventListener('click', openDeleteDialog);
    $('delete-purge').addEventListener('change', updateDeleteNote);
    $('delete-confirm').addEventListener('click', () => confirmDeleteNode().catch(notifyError));

    $('alert-filter').addEventListener('change', () => loadAlerts().catch(notifyError));
    $('refresh-alerts').addEventListener('click', () => loadAlerts().catch(notifyError));
    $('ack-all-alerts').addEventListener('click', () => ackAll().catch(notifyError));

    $('history-refresh').addEventListener('click', () => loadHistory().catch(notifyError));
    $('history-range').addEventListener('change', () => loadHistory().catch(notifyError));
    $('history-node').addEventListener('change', () => loadHistory().catch(notifyError));
    $('history-metric').addEventListener('change', () => {
      state.history.metric = $('history-metric').value;
      renderHistoryStats();
      renderHistoryChart();
    });
    $('history-export').addEventListener('click', exportHistoryCsv);

    $('log-filter').addEventListener('change', (event) => {
      if (event.target && event.target.id === 'log-all') {
        const checked = event.target.checked;
        document.querySelectorAll('#log-level-options input[value]').forEach((input) => {
          input.checked = checked;
        });
      } else {
        const boxes = document.querySelectorAll('#log-level-options input[value]');
        $('log-all').checked = [...boxes].every((input) => input.checked);
      }
      loadLogs().catch(notifyError);
    });
    $('settings-form').addEventListener('submit', (event) => saveSettings(event).catch(notifyError));
    $('settings-reload').addEventListener('click', () => loadSettings().catch(notifyError));
    $('copy-ingest').addEventListener('click', () => copyText(state.device?.ingest_url || '', '上报地址'));
    $('copy-device-key').addEventListener('click', () => copyText(state.device?.key || '', '节点密钥'));
    $('regen-device-key').addEventListener('click', () => regenerateDeviceKey().catch(notifyError));
    $('password-form').addEventListener('submit', (event) => changePassword(event).catch(notifyError));

    window.addEventListener('roomsense:theme', () => {
      renderMetricChips();
      renderLiveChart();
      if (state.charts.history) renderHistoryChart();
    });
    window.addEventListener('storage', (event) => {
      if (event.key === sessionStorageKey && !event.newValue && state.session) {
        resetLogin('已在其他标签页退出登录');
      }
    });
    window.addEventListener('beforeunload', () => {
      if (state.stream) state.stream.close();
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    bindEvents();
    boot().catch((error) => {
      $('loading').hidden = true;
      $('login').hidden = false;
      $('login-error').textContent = error.message;
    });
  });
})();
