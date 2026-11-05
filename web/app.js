/*
    ____                       _____
   / __ \____  ____  ____ ___ / ___/___  ____  ________
  / /_/ / __ \/ __ \/ __ `__ \\__ \/ _ \/ __ \/ ___/ _ \
 / _, _/ /_/ / /_/ / / / / / /__/ /  __/ / / (__  )  __/
/_/ |_|\____/\____/_/ /_/ /_/____/\___/_/ /_/____/\___/

@File      :   web/app.js
@Author    :   RmCat
@Contact   :   RemiliaCatNero@gmail.com
@License   :   AGPL
@Copyright :   (C) 2026, RoomSense-Team
@Desc      :   面板前端逻辑：登录、导航、概览、实时图表与各功能页。
*/
'use strict';

(() => {

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
    co2: { label: 'CO₂', unit: 'ppm', digits: 0, cssVar: '--co2' },
    pm25: { label: 'PM2.5', unit: 'µg/m³', digits: 0, cssVar: '--pm25' },
    noise: { label: '噪声', unit: 'dB', digits: 0, cssVar: '--noise' },
    light: { label: '光照', unit: 'lx', digits: 0, cssVar: '--light' },
    battery: { label: '电量', unit: 'V', digits: 2, cssVar: '--battery' },
  };
  const METRIC_ORDER = ['temperature', 'humidity', 'co2', 'pm25', 'noise', 'light', 'battery'];
  const STATE_LABELS = { open: '未处理', acknowledged: '已确认', muted: '已静默', resolved: '已恢复' };
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
    alertCounts: { open: 0, acknowledged: 0, muted: 0, resolved: 0, active: 0 },
    severityCounts: { warning: 0, critical: 0 },
    averages: {},
    liveNode: '',
    liveMetrics: ['temperature', 'humidity'],
    liveWindow: 25,
    liveSeries: new Map(),
    history: { series: [], metric: 'temperature', range: '24h', start: 0, end: 0, bucket: 60 },
    settings: null,
    device: null,
    nodeDetail: null,
    alertAction: null,
    logs: [],
    logLevels: new Set(['INFO', 'WARN', 'ERROR', 'FATAL']),
    stream: null,
    streamErrors: 0,
    polls: null,
    liveTicker: null,
    noticeTimer: null,
    alertsReload: null,
    charts: {},
    liveCharts: new Map(),
  };

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
    // 非安全上下文（局域网 HTTP）下 Clipboard API 不可用，回退到 execCommand。
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
    clearInterval(state.liveTicker);
    state.liveTicker = null;
    state.session = '';
    state.username = '';
    state.account = null;
    state.nodes = [];
    state.alerts = [];
    state.logs = [];
    state.liveSeries.clear();
    destroyCharts();
    $('live-charts').replaceChildren();
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
    for (const entry of state.liveCharts.values()) {
      if (entry.chart && typeof entry.chart.destroy === 'function') entry.chart.destroy();
    }
    state.charts = {};
    state.liveCharts.clear();
  }

  async function enterShell(loginResult = {}) {
    if (loginResult.simulator !== undefined) {
      state.server = { ...(state.server || {}), simulator: loginResult.simulator };
    }
    $('loading').hidden = true;
    $('login').hidden = true;
    $('shell').hidden = false;
    await Promise.all([
      loadSettings().catch(() => {}),
      loadOverview().catch(() => {}),
      loadAccount().catch(() => {}),
    ]);
    await navigate(restorePage());
    startStream();
    startPolling();
    startLiveTicker();
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

  function rememberPage() {
    try {
      sessionStorage.setItem(pageStorageKey, state.page);
    } catch {
    }
  }

  function restorePage() {
    try {
      const saved = sessionStorage.getItem(pageStorageKey);
      if (saved && titles[saved]) return saved;
    } catch {
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

  function updateWindowLabel() {
    const node = $('live-window');
    if (!node) return;
    node.textContent = `窗口 ${state.liveWindow} 分钟`;
    node.title = `曲线从首条数据开始逐秒撑开，累计满 ${state.liveWindow} 分钟后向右滑动`;
  }

  function growingDomain(points, end, windowSeconds) {
    const first = points.length ? points[0].ts : null;
    const start = Number.isFinite(first)
      ? Math.max(end - windowSeconds, first)
      : end - windowSeconds;
    return [start, Math.max(end, start + 1)];
  }

  function liveDomain() {
    const points = state.liveSeries.get(state.liveNode) || [];
    return growingDomain(points, now(), state.liveWindow * 60);
  }

  function startLiveTicker() {
    clearInterval(state.liveTicker);
    state.liveTicker = setInterval(() => {
      if (state.page !== 'dashboard' || document.hidden) return;
      const [start, end] = liveDomain();
      for (const entry of state.liveCharts.values()) entry.chart.setDomain(start, end);
    }, 1000);
  }

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
      // 连续失败时校验会话：会话过期要回登录页，而不是无限重连。
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
      // 401 已由 api() 统一处理为退出登录。
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
      loadSettings()
        .then(() => {
          renderCards();
          renderEnvironment();
        })
        .catch(() => {});
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
      battery: reading.battery,
    };
    let index = list.length;
    while (index > 0 && list[index - 1].ts > point.ts) index -= 1;
    if (index > 0 && list[index - 1].ts === point.ts) list[index - 1] = point;
    else list.splice(index, 0, point);
    const cutoff = now() - state.liveWindow * 60 - 120;
    while (list.length && list[0].ts < cutoff) list.shift();
    if (list.length > 3000) list.splice(0, list.length - 3000);
    state.liveSeries.set(reading.device_id, list);

    if (reading.device_id === state.liveNode) {
      updateLiveCharts();
      $('live-updated').textContent = `最新数据 ${formatTime(reading.ts, true)}`;
    }

    const node = state.nodes.find((item) => item.device_id === reading.device_id);
    if (node) {
      node.last_seen = Math.max(node.last_seen || 0, reading.ts);
      node.online = true;
      for (const field of ['temperature', 'humidity', 'co2', 'pm25', 'noise', 'light', 'rssi', 'battery']) {
        if (reading[field] !== null && reading[field] !== undefined) node[`last_${field}`] = reading[field];
      }
      if (reading.battery !== undefined) node.last_battery = reading.battery;
    } else {
      loadNodes().catch(() => {});
    }
    if (state.page === 'dashboard') {
      renderCards();
      renderEnvironment();
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
    renderEnvironment();
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
    renderEnvironment();
    renderDashboardNodes();
    updateLiveCharts();
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

  async function loadOverview() {
    const data = await api('/api/overview');
    state.server = { ...(state.server || {}), ...(data.server || {}) };
    state.nodes = data.nodes || [];
    state.alertCounts = data.alert_counts || state.alertCounts;
    state.severityCounts = data.severity_counts || state.severityCounts;
    state.averages = data.averages || {};
    if (data.settings && data.settings.live_window_minutes) {
      applyLiveWindow(data.settings.live_window_minutes);
    } else if (data.live_window_minutes) {
      applyLiveWindow(data.live_window_minutes);
    }
    if (!state.alerts.length || state.page === 'dashboard') {
      state.alerts = data.alerts || [];
    }
    renderNodeOptions();
    if (state.liveNode && !state.liveSeries.has(state.liveNode)) {
      await loadLiveSeries(state.liveNode);
    }
    renderCards();
    renderEnvironment();
    renderDashboardNodes();
    renderRecentAlerts();
    renderRuntime();
    ensureLiveCharts();
    updateLiveCharts();
    $('nodes-updated').textContent = `更新于 ${formatTime(now())}`;
  }

  function applyLiveWindow(minutes) {
    const value = Math.max(5, Math.min(120, Number(minutes) || 25));
    if (value === state.liveWindow) return;
    state.liveWindow = value;
    const cutoff = now() - value * 60 - 120;
    for (const [deviceId, list] of state.liveSeries) {
      const kept = list.filter((point) => point.ts >= cutoff);
      state.liveSeries.set(deviceId, kept);
    }
    if (state.liveNode) loadLiveSeries(state.liveNode).catch(() => {});
  }

  async function loadLiveSeries(deviceId) {
    const data = await api(
      `/api/live?device_id=${encodeURIComponent(deviceId)}&minutes=${state.liveWindow}`,
    );
    state.liveSeries.set(
      deviceId,
      (data.readings || []).map((point) => ({
        ts: point.ts,
        temperature: point.temperature,
        humidity: point.humidity,
        co2: point.co2,
        pm25: point.pm25,
        noise: point.noise,
        light: point.light,
        battery: point.battery,
      })),
    );
    updateLiveCharts();
  }

  const SVG_NS = 'http://www.w3.org/2000/svg';

  function svgNode(tag, attrs = {}) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
    return node;
  }

  function clampRatio(value) {
    if (!Number.isFinite(value)) return 0;
    return Math.max(0, Math.min(1, value));
  }

  function ring({ segments = [], ratio = 0, color = 'var(--accent)', center = '--', unit = '' }) {
    const wrapper = element('div', null, { class: 'ring' });
    const svg = svgNode('svg', { viewBox: '0 0 100 100', 'aria-hidden': 'true' });
    svg.append(svgNode('circle', { class: 'ring-track', cx: 50, cy: 50, r: 42, pathLength: 100 }));
    const parts = (segments.length ? segments : [{ value: clampRatio(ratio) * 100, color }])
      .filter((part) => Number.isFinite(part.value) && part.value > 0)
      .map((part) => ({ value: Math.min(100, part.value), color: part.color || color }));
    let offset = 0;
    for (const part of parts) {
      const circle = svgNode('circle', {
        class: 'ring-seg',
        cx: 50,
        cy: 50,
        r: 42,
        pathLength: 100,
        stroke: part.color,
        'stroke-dasharray': `${part.value} 100`,
        'stroke-dashoffset': -offset,
      });
      svg.append(circle);
      offset += part.value;
    }
    wrapper.append(svg);
    const centerNode = element('div', null, { class: 'ring-center' });
    centerNode.append(element('strong', String(center)));
    if (unit) centerNode.append(element('span', unit));
    wrapper.append(centerNode);
    return wrapper;
  }

  function averageOf(field) {
    const values = state.nodes
      .map((node) => node[`last_${field}`])
      .filter((value) => Number.isFinite(value));
    if (values.length) return values.reduce((sum, value) => sum + value, 0) / values.length;
    const backend = state.averages ? state.averages[field] : null;
    return Number.isFinite(backend) ? backend : null;
  }

  function comfortRatio(value, low, high) {
    if (!Number.isFinite(value)) return 0;
    return clampRatio((value - low) / (high - low));
  }

  function thresholdColor(value, rule, mode) {
    if (!Number.isFinite(value) || !rule) return 'var(--muted)';
    if (mode !== 'low' && rule.critical_high !== undefined && value >= Number(rule.critical_high)) {
      return 'var(--danger)';
    }
    if (mode !== 'high' && rule.critical_low !== undefined && value <= Number(rule.critical_low)) {
      return 'var(--danger)';
    }
    if (mode !== 'low' && rule.high !== undefined && value > Number(rule.high)) return 'var(--warn)';
    if (mode !== 'high' && rule.low !== undefined && value < Number(rule.low)) return 'var(--warn)';
    return 'var(--ok)';
  }

  function gaugeCard({ label, value, unit, ratio, color, note, segments }) {
    const card = element('div', null, { class: 'panel card gauge-card' });
    card.append(ring({ segments, ratio, color, center: value, unit }));
    const body = element('div', null, { class: 'card-body' });
    body.append(element('span', label, { class: 'card-label' }));
    body.append(element('span', note, { class: 'card-note' }));
    card.append(body);
    return card;
  }

  function renderCards() {
    const rules = state.settings?.alerts || {};
    const total = state.nodes.length;
    const online = state.nodes.filter((node) => node.online).length;
    const open = state.alertCounts.open || 0;
    const critical = state.severityCounts.critical || 0;
    const warning = state.severityCounts.warning || 0;
    const temperature = averageOf('temperature');
    const humidity = averageOf('humidity');
    const tempRule = rules.temperature || {};
    const humidityRule = rules.humidity || {};
    const tempColor = thresholdColor(temperature, tempRule, 'both');
    const humidityColor = thresholdColor(humidity, humidityRule, 'both');
    const severityTotal = critical + warning;
    const segmentTotal = severityTotal || 1;

    const cards = [
      gaugeCard({
        label: '在线节点',
        value: `${online}/${total}`,
        unit: '台',
        ratio: total ? online / total : 0,
        color: total && online === total ? 'var(--ok)' : total ? 'var(--warn)' : 'var(--muted)',
        note: total ? (online === total ? '全部在线' : `${total - online} 台离线`) : '等待节点接入',
      }),
      gaugeCard({
        label: '平均温度',
        value: Number.isFinite(temperature) ? formatValue(temperature, 1) : '--',
        unit: '°C',
        ratio: comfortRatio(temperature, Number(tempRule.low ?? 18), Number(tempRule.high ?? 28)),
        color: tempColor === 'var(--ok)' ? 'var(--temp)' : tempColor,
        note: `舒适 ${tempRule.low ?? 18}–${tempRule.high ?? 28} °C`,
      }),
      gaugeCard({
        label: '平均湿度',
        value: Number.isFinite(humidity) ? formatValue(humidity, 1) : '--',
        unit: '%RH',
        ratio: comfortRatio(humidity, Number(humidityRule.low ?? 30), Number(humidityRule.high ?? 70)),
        color: humidityColor === 'var(--ok)' ? 'var(--humidity)' : humidityColor,
        note: `舒适 ${humidityRule.low ?? 30}–${humidityRule.high ?? 70} %RH`,
      }),
      gaugeCard({
        label: '未处理告警',
        value: String(open),
        unit: '条',
        segments: [
          { value: (critical / segmentTotal) * 100, color: 'var(--danger)' },
          { value: (warning / segmentTotal) * 100, color: 'var(--warn)' },
        ],
        color: open ? 'var(--danger)' : 'var(--ok)',
        note: `${critical} 严重 · ${warning} 警告`,
      }),
    ];
    $('status-cards').replaceChildren(...cards);
    updateAlertBadge();
    updateWindowLabel();
  }

  const ENV_METRICS = [
    { field: 'co2', label: 'CO₂', unit: 'ppm', digits: 0, mode: 'high' },
    { field: 'pm25', label: 'PM2.5', unit: 'µg/m³', digits: 0, mode: 'high' },
    { field: 'noise', label: '噪声', unit: 'dB', digits: 0, mode: 'high' },
    { field: 'light', label: '光照', unit: 'lx', digits: 0, mode: 'low' },
  ];

  function renderEnvironment() {
    const container = $('environment-grid');
    if (!container) return;
    const rules = state.settings?.alerts || {};
    const nodes = [];
    for (const meta of ENV_METRICS) {
      const value = averageOf(meta.field);
      const rule = rules[meta.field] || {};
      const color = thresholdColor(value, rule, meta.mode);
      let ratio = 0;
      if (Number.isFinite(value)) {
        if (meta.mode === 'low') {
          const target = Math.max(Number(rule.low ?? 80) * 3, 1);
          ratio = clampRatio(value / target);
        } else {
          const target = Math.max(Number(rule.critical_high ?? rule.high ?? 100), 1);
          ratio = clampRatio(value / target);
        }
      }
      const gauge = element('div', null, { class: 'env-gauge' });
      gauge.append(
        ring({
          ratio,
          color,
          center: Number.isFinite(value) ? formatValue(value, meta.digits) : '--',
          unit: meta.unit,
        }),
      );
      const text = element('div', null, { class: 'env-text' });
      text.append(element('span', meta.label));
      const limit = meta.mode === 'low' ? rule.low : rule.high;
      text.append(
        element(
          'span',
          Number.isFinite(limit) ? `${meta.mode === 'low' ? '下限' : '上限'} ${formatValue(limit, meta.digits)}` : '未配置',
          { class: 'env-note' },
        ),
      );
      gauge.append(text);
      nodes.push(gauge);
    }
    container.replaceChildren(...nodes);
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
      row.append(element('td', '暂无节点数据，启动模拟器或让设备上报后显示', { class: 'empty', colspan: '8' }));
      body.append(row);
      return;
    }
    for (const node of state.nodes) {
      const row = element('tr');
      const nameCell = element('td', null, { 'data-label': '节点' });
      nameCell.append(element('div', node.name || node.device_id));
      nameCell.append(element('span', node.room || node.device_id, { class: 'hint' }));
      row.append(nameCell);

      const status = element('td', null, { 'data-label': '状态' });
      const statusWrap = element('span', null, { class: 'status-inline' });
      statusWrap.append(element('span', null, { class: `dot ${node.online ? 'online' : 'offline'}` }));
      statusWrap.append(
        element('span', node.online ? '在线' : `离线 ${formatAge(now() - node.last_seen)}`, {
          class: 'status-text',
        }),
      );
      status.append(statusWrap);
      row.append(status);
      row.append(element('td', formatValue(node.last_temperature, 1), { class: 'metric-temp num', 'data-label': '温度' }));
      row.append(element('td', formatValue(node.last_humidity, 1), { class: 'metric-humidity num', 'data-label': '湿度' }));
      row.append(
        element('td', node.last_co2 === null || node.last_co2 === undefined ? '--' : formatValue(node.last_co2, 0), {
          class: 'metric-co2 num',
          'data-label': 'CO₂',
        }),
      );
      row.append(
        element('td', node.last_pm25 === null || node.last_pm25 === undefined ? '--' : formatValue(node.last_pm25, 0), {
          class: 'metric-pm25 num',
          'data-label': 'PM2.5',
        }),
      );
      row.append(element('td', `${formatAge(now() - node.last_seen)}前`, { class: 'num', 'data-label': '最后上报' }));
      const actions = element('td', null, { 'data-label': '操作' });
      actions.append(button('详情', () => openNodeDialog(node.device_id)));
      row.append(actions);
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
    const meta = [
      alert.device_name || alert.device_id,
      formatTime(alert.opened_at, true),
      STATE_LABELS[alert.state] || alert.state,
    ];
    if (alert.state === 'muted' && alert.muted_until) meta.push(`至 ${formatTime(alert.muted_until, true)}`);
    body.append(element('span', meta.join(' · '), { class: 'alert-meta' }));
    if (alert.acknowledged_by) {
      body.append(
        element('span', `由 ${alert.acknowledged_by} 处理${alert.note ? `：${alert.note}` : ''}`, {
          class: 'alert-meta',
        }),
      );
    }
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
      ['活跃告警', `${state.alertCounts.active || 0}（未处理 ${state.alertCounts.open || 0}）`],
      ['实时窗口', `${state.liveWindow} 分钟`],
      ['实时连接数', server.streams ?? '--'],
      ['数据文件', formatBytes(stats.db_bytes)],
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

  function renderNodeOptions() {
    const select = $('live-node');
    const previous = state.liveNode || select.value;
    select.replaceChildren();
    if (!state.nodes.length) {
      select.append(element('option', '暂无节点', { value: '' }));
      select.disabled = true;
      state.liveNode = '';
      const historySelect = $('history-node');
      historySelect.replaceChildren(element('option', '全部节点', { value: '' }));
      refreshDropdown(select);
      refreshDropdown(historySelect);
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
        title: `${METRICS[key].label}（${METRICS[key].unit}）`,
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
      state.liveMetrics = METRIC_ORDER.filter((item) => state.liveMetrics.includes(item) || item === key);
    }
    renderMetricChips();
    ensureLiveCharts();
    updateLiveCharts();
  }

  function metricGuides(key) {
    const rule = state.settings?.alerts?.[key] || {};
    const color = metricColor(key);
    const guides = [];
    for (const field of ['high', 'low', 'critical_high', 'critical_low']) {
      const value = Number(rule[field]);
      if (Number.isFinite(value)) guides.push({ value, color });
    }
    return guides;
  }

  function ensureLiveCharts() {
    const container = $('live-charts');
    if (!container) return;
    const wanted = new Set(state.liveMetrics);
    for (const [key, entry] of state.liveCharts) {
      if (wanted.has(key)) continue;
      entry.chart.destroy();
      entry.cell.remove();
      state.liveCharts.delete(key);
    }
    for (const key of METRIC_ORDER) {
      if (!wanted.has(key)) continue;
      let entry = state.liveCharts.get(key);
      if (!entry) {
        const cell = element('figure', null, { class: 'live-cell', 'data-metric': key });
        const caption = element('figcaption');
        const title = element('span', null, { class: 'live-title' });
        title.append(element('span', null, { class: 'swatch', style: `background:${metricColor(key)}` }));
        title.append(document.createTextNode(METRICS[key].label));
        const value = element('span', '--', { class: 'live-value' });
        caption.append(title, value);
        const wrap = element('div', null, { class: 'chart-wrap live-chart-wrap' });
        const canvas = element('canvas', null, {
          role: 'img',
          'aria-label': `${METRICS[key].label}实时曲线`,
        });
        wrap.append(canvas);
        cell.append(caption, wrap);
        entry = {
          cell,
          title,
          value,
          chart: new LineChart(canvas, { emptyText: `等待${METRICS[key].label}上报…` }),
        };
        state.liveCharts.set(key, entry);
      }
      container.append(entry.cell);
    }
    container.classList.toggle('single', state.liveMetrics.length === 1);
  }

  function updateLiveCharts() {
    if (!state.liveCharts.size) ensureLiveCharts();
    const points = state.liveSeries.get(state.liveNode) || [];
    const [start, end] = liveDomain();
    for (const [key, entry] of state.liveCharts) {
      const meta = METRICS[key];
      const seriesPoints = points
        .filter((point) => Number.isFinite(point[key]))
        .map((point) => [point.ts, point[key]]);
      entry.chart.setDomain(start, end);
      entry.chart.setGuides(metricGuides(key));
      entry.chart.setSeries([
        {
          label: meta.label,
          color: metricColor(key),
          unit: ` ${meta.unit}`,
          digits: meta.digits,
          axis: 0,
          fill: true,
          maxGap: 120,
          points: seriesPoints,
        },
      ]);
      const latest = seriesPoints[seriesPoints.length - 1];
      entry.value.replaceChildren();
      entry.value.append(document.createTextNode(latest ? formatValue(latest[1], meta.digits) : '--'));
      if (latest) entry.value.append(element('small', meta.unit));
      const swatch = entry.title.querySelector('.swatch');
      if (swatch) swatch.style.background = metricColor(key);
    }
    const last = points[points.length - 1];
    $('live-updated').textContent = last ? `最新数据 ${formatTime(last.ts, true)}` : '暂无数据';
    updateWindowLabel();
  }

  async function loadNodes() {
    const data = await api('/api/nodes');
    state.nodes = data.nodes || [];
    renderNodeOptions();
    renderNodeTable();
    renderCards();
    renderEnvironment();
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
      const nameCell = element('td', null, { 'data-label': '节点' });
      nameCell.append(element('div', node.name || node.device_id));
      nameCell.append(element('span', node.device_id, { class: 'hint' }));
      row.append(nameCell);
      row.append(element('td', node.room || '--', { 'data-label': '位置' }));
      const status = element('td', null, { 'data-label': '状态' });
      const statusWrap = element('span', null, { class: 'status-inline' });
      statusWrap.append(element('span', null, { class: `dot ${node.online ? 'online' : 'offline'}` }));
      statusWrap.append(element('span', node.online ? '在线' : '离线', { class: 'status-text' }));
      status.append(statusWrap);
      row.append(status);
      row.append(element('td', formatValue(node.last_temperature, 1), { class: 'metric-temp num', 'data-label': '温度' }));
      row.append(element('td', formatValue(node.last_humidity, 1), { class: 'metric-humidity num', 'data-label': '湿度' }));
      row.append(
        element('td', node.last_co2 === null || node.last_co2 === undefined ? '--' : formatValue(node.last_co2, 0), {
          class: 'metric-co2 num',
          'data-label': 'CO₂',
        }),
      );
      row.append(
        element('td', node.last_pm25 === null || node.last_pm25 === undefined ? '--' : formatValue(node.last_pm25, 0), {
          class: 'metric-pm25 num',
          'data-label': 'PM2.5',
        }),
      );
      row.append(
        element('td', node.last_noise === null || node.last_noise === undefined ? '--' : formatValue(node.last_noise, 0), {
          class: 'metric-noise num',
          'data-label': '噪声',
        }),
      );
      row.append(
        element(
          'td',
          node.last_battery === null || node.last_battery === undefined ? '--' : `${formatValue(node.last_battery, 2)} V`,
          { class: 'metric-battery num', 'data-label': '电量' },
        ),
      );
      row.append(
        element(
          'td',
          node.last_rssi === null || node.last_rssi === undefined ? '--' : `${node.last_rssi} dBm`,
          { class: 'num', 'data-label': '信号' },
        ),
      );
      row.append(element('td', String(node.reading_count ?? 0), { class: 'num', 'data-label': '累计上报' }));
      row.append(
        element('td', `${formatTime(node.last_seen, true)}（${formatAge(now() - node.last_seen)}前）`, {
          class: 'num',
          'data-label': '最后上报',
        }),
      );
      const actions = element('td', null, { 'data-label': '操作' });
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
        ['趋势窗口', `${data.window_minutes || state.liveWindow} 分钟`],
      ];
      for (const [label, value] of rows) {
        const row = element('div');
        row.append(element('span', label), element('strong', String(value)));
        summary.append(row);
      }
      const points = (data.readings || []).map((item) => ({ ...item }));
      const windowMinutes = data.window_minutes || state.liveWindow;
      const end = data.server_time || now();
      if (!state.charts.node) {
        state.charts.node = new LineChart($('node-chart'), { emptyText: '该窗口内暂无数据' });
      }
      state.charts.node.setEmptyText(`最近 ${windowMinutes} 分钟暂无数据`);
      const [domainStart, domainEnd] = growingDomain(points, end, windowMinutes * 60);
      state.charts.node.setDomain(domainStart, domainEnd);
      state.charts.node.setSeries([
        {
          label: '温度',
          color: metricColor('temperature'),
          unit: ' °C',
          digits: 1,
          axis: 0,
          fill: true,
          maxGap: 180,
          points: points.filter((point) => Number.isFinite(point.temperature)).map((point) => [point.ts, point.temperature]),
        },
        {
          label: '湿度',
          color: metricColor('humidity'),
          unit: ' %RH',
          digits: 1,
          axis: 1,
          maxGap: 180,
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

  async function loadAlerts() {
    const filter = $('alert-filter').value || 'active';
    const data = await api(`/api/alerts?state=${encodeURIComponent(filter)}&limit=300`);
    state.alerts = data.alerts || [];
    state.alertCounts = data.counts || state.alertCounts;
    state.severityCounts = data.severity_counts || state.severityCounts;
    renderAlertCards();
    renderAlertSummary();
    renderRecentAlerts();
    renderCards();
    const counts = state.alertCounts;
    $('alert-count').textContent = `当前显示 ${state.alerts.length} 条 · 未处理 ${counts.open || 0} · 已确认 ${counts.acknowledged || 0} · 已静默 ${counts.muted || 0} · 已恢复 ${counts.resolved || 0}`;
  }

  function renderAlertSummary() {
    const container = $('alert-summary');
    if (!container) return;
    const counts = state.alertCounts;
    const chips = [
      ['open', '未处理', counts.open || 0],
      ['acknowledged', '已确认', counts.acknowledged || 0],
      ['muted', '已静默', counts.muted || 0],
      ['resolved', '已恢复', counts.resolved || 0],
      ['critical', '严重', state.severityCounts.critical || 0],
      ['warning', '警告', state.severityCounts.warning || 0],
    ];
    container.replaceChildren(
      ...chips.map(([key, label, value]) => {
        const chip = element('span', null, { class: `count-chip ${key}` });
        chip.append(element('strong', String(value)), document.createTextNode(label));
        return chip;
      }),
    );
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
            (alert.resolved_at ? ` · 恢复于 ${formatTime(alert.resolved_at, true)}` : '') +
            (alert.state === 'muted' && alert.muted_until ? ` · 静默至 ${formatTime(alert.muted_until, true)}` : ''),
          { class: 'hint' },
        ),
      );
      body.append(element('span', kindLabel(alert.kind), { class: 'hint' }));
      if (alert.acknowledged_by || alert.resolved_by) {
        const operator = alert.resolved_by || alert.acknowledged_by;
        const action = alert.resolved_by ? '关闭' : '确认';
        const stamp = alert.resolved_at || alert.acknowledged_at;
        body.append(
          element('span', `由 ${operator} ${action}${stamp ? ` · ${formatTime(stamp, true)}` : ''}`, {
            class: 'alert-ack-meta',
          }),
        );
      }
      if (alert.note) body.append(element('span', `备注：${alert.note}`, { class: 'alert-ack-meta' }));
      card.append(body);
      card.append(element('span', STATE_LABELS[alert.state] || alert.state, { class: `state ${alert.state}` }));
      if (alert.state !== 'resolved') {
        const actions = element('div', null, { class: 'alert-actions' });
        actions.append(button('处理', () => openAlertDialog(alert), 'primary'));
        card.append(actions);
      }
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
        pm25_high: 'PM2.5 过高',
        noise_high: '噪声过高',
        light_low: '光照不足',
        battery_low: '节点电量低',
        device_offline: '节点离线',
      }[kind] || kind
    );
  }

  function openAlertDialog(alert) {
    state.alertAction = alert;
    $('alert-action-title').textContent = '处理告警';
    $('alert-action-message').textContent = alert.message;
    const meta = $('alert-action-meta');
    meta.replaceChildren();
    const rows = [
      ['节点', `${alert.device_name || alert.device_id}${alert.room ? `（${alert.room}）` : ''}`],
      ['类型', kindLabel(alert.kind)],
      ['触发时间', formatTime(alert.opened_at, true)],
      ['当前状态', STATE_LABELS[alert.state] || alert.state],
    ];
    if (alert.acknowledged_by) rows.push(['处理人', alert.acknowledged_by]);
    if (alert.note) rows.push(['已有备注', alert.note]);
    for (const [label, value] of rows) {
      const row = element('div');
      row.append(element('span', label), element('strong', String(value)));
      meta.append(row);
    }
    $('alert-note').value = '';
    $('alert-action-note').textContent = '';
    const dialog = $('alert-dialog');
    dialog.showModal();
    dialog.focus();
  }

  async function submitAlertAction(action) {
    const alert = state.alertAction;
    if (!alert) return;
    const note = $('alert-note').value.trim();
    const minutes = Number($('alert-mute-minutes').value) || 60;
    const noteNode = $('alert-action-note');
    const buttons = [...document.querySelectorAll('#alert-dialog .alert-actions button')];
    noteNode.textContent = '正在提交…';
    buttons.forEach((item) => {
      item.disabled = true;
    });
    try {
      await api(`/api/alerts/${alert.id}/${action}`, { method: 'POST', body: { note, minutes } });
      $('alert-dialog').close();
      state.alertAction = null;
      await Promise.all([loadAlerts().catch(() => {}), loadOverview().catch(() => {})]);
      const text = action === 'ack' ? '告警已确认' : action === 'mute' ? `告警已静默 ${minutes} 分钟` : '告警已关闭';
      notify(text);
    } catch (error) {
      noteNode.textContent = error.message;
    } finally {
      buttons.forEach((item) => {
        item.disabled = false;
      });
    }
  }

  async function ackAll() {
    const result = await api('/api/alerts/ack', { method: 'POST' });
    state.alertCounts = result.counts || state.alertCounts;
    await Promise.all([loadAlerts().catch(() => {}), loadOverview().catch(() => {})]);
    notify(result.count ? `已确认 ${result.count} 条告警` : '没有未处理的告警');
  }

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
    state.history.start = data.start || 0;
    state.history.end = data.end || 0;
    state.history.bucket = data.bucket || 60;
    $('history-range-note').textContent =
      `${rangeLabel(data.range)}，${formatTime(data.start, true)} 至 ${formatTime(data.end, true)}，聚合间隔 ${bucketLabel(data.bucket)}`;
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
    const maxGap = Math.max(60, (state.history.bucket || 60) * 3);
    const series = state.history.series.map((item, index) => ({
      label: item.name || item.device_id,
      color: palette[index % palette.length],
      unit: ` ${meta.unit}`,
      digits: meta.digits,
      axis: 0,
      fill: state.history.series.length === 1,
      maxGap,
      points: item.points
        .filter((point) => Number.isFinite(point[metric]))
        .map((point) => [point.ts, point[metric]]),
    }));
    if (state.history.start && state.history.end) {
      state.charts.history.setDomain(state.history.start, state.history.end);
    }
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

  const ADDRESS_SOURCE_TEXT = {
    configured: '来自配置或设置页',
    public: '自动探测公网出口',
    listen: '来自监听地址',
    lan: '按本机网卡推断',
    loopback: '本机回环地址',
  };

  function addressSourceText(source) {
    return ADDRESS_SOURCE_TEXT[source] || '自动推断';
  }

  function publicSourceText(source) {
    if (source === 'tencent') return '腾讯云元数据';
    if (source === 'aliyun') return '阿里云元数据';
    return source ? '公网回显' : '';
  }

  async function loadSettings() {
    const data = await api('/api/settings');
    state.settings = data.settings;
    state.device = data.device;
    const rules = data.settings.alerts || {};
    const temperature = rules.temperature || {};
    const humidity = rules.humidity || {};
    const co2 = rules.co2 || {};
    const pm25 = rules.pm25 || {};
    const noise = rules.noise || {};
    const light = rules.light || {};
    const battery = rules.battery || {};
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
    $('set-pm25-high').value = pm25.high ?? '';
    $('set-pm25-critical-high').value = pm25.critical_high ?? '';
    $('set-noise-high').value = noise.high ?? '';
    $('set-noise-critical-high').value = noise.critical_high ?? '';
    $('set-light-low').value = light.low ?? '';
    $('set-light-critical-low').value = light.critical_low ?? '';
    $('set-battery-low').value = battery.low ?? '';
    $('set-battery-critical-low').value = battery.critical_low ?? '';
    $('set-offline').value = data.settings.offline_timeout ?? 90;
    $('set-retention').value = data.settings.retention_days ?? 30;
    $('set-live-window').value = data.settings.live_window_minutes ?? 25;
    $('set-public-url').value = data.settings.public_url || '';
    applyLiveWindow(data.settings.live_window_minutes ?? 25);
    updateLiveCharts();
    const device = data.device || {};
    const serverInfo = data.server || {};
    const urlNote = $('public-url-note');
    if (serverInfo.public_url_source === 'loopback') {
      urlNote.textContent = '当前只监听本机，其他电脑无法访问面板。';
    } else if (serverInfo.public_url_source === 'lan') {
      urlNote.textContent = '当前按本机网卡推断；如果从公网访问，请点“使用当前访问地址”。';
    } else if (serverInfo.public_url_source === 'public') {
      urlNote.textContent = `已自动探测公网出口 ${serverInfo.detected_public_ip || ''}。`;
    } else {
      urlNote.textContent = '';
    }
    const info = $('device-info');
    info.replaceChildren();
    const rows = [
      ['上报地址', device.ingest_url || '--'],
      ['地址来源', addressSourceText(serverInfo.public_url_source)],
      ['当前访问', serverInfo.request_origin || '--'],
      ['公网出口', serverInfo.detected_public_ip
        ? `${serverInfo.detected_public_ip}（${publicSourceText(serverInfo.detected_public_ip_source)}）`
        : '未探测到'],
      ['节点密钥', device.key || '--'],
      ['离线判定', `${device.offline_timeout ?? '--'} 秒`],
      ['实时窗口', `${data.settings.live_window_minutes ?? 25} 分钟`],
      ['单批上限', `${device.max_batch ?? '--'} 条`],
    ];
    for (const [label, value] of rows) {
      const row = element('div');
      row.append(element('span', label), element('strong', String(value)));
      info.append(row);
    }
    const extras = (device.ingest_urls || []).slice(1);
    $('ingest-addresses').textContent = extras.length ? `其它可用地址：${extras.join('、')}` : '';
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
      ['对外地址', server.public_url || '--'],
      ['PWA', window.isSecureContext ? '可安装到主屏幕' : '需要 HTTPS 才能安装'],
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
      'set-pm25-high',
      'set-pm25-critical-high',
      'set-noise-high',
      'set-noise-critical-high',
      'set-light-low',
      'set-light-critical-low',
      'set-battery-low',
      'set-battery-critical-low',
      'set-offline',
      'set-retention',
      'set-live-window',
    ];
    const empty = fields.find((id) => $(id).value.trim() === '' || !Number.isFinite(Number($(id).value)));
    if (empty) {
      $('settings-note').textContent = '请填写全部阈值与运行参数后再保存';
      return;
    }
    const number = (id) => Number($(id).value);
    const payload = {
      offline_timeout: number('set-offline'),
      retention_days: number('set-retention'),
      live_window_minutes: number('set-live-window'),
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
        pm25: { high: number('set-pm25-high'), critical_high: number('set-pm25-critical-high') },
        noise: { high: number('set-noise-high'), critical_high: number('set-noise-critical-high') },
        light: { low: number('set-light-low'), critical_low: number('set-light-critical-low') },
        battery: { low: number('set-battery-low'), critical_low: number('set-battery-critical-low') },
      },
    };
    $('settings-note').textContent = '正在保存…';
    try {
      const data = await api('/api/settings', { method: 'PUT', body: payload });
      state.settings = data.settings;
      applyLiveWindow(data.settings.live_window_minutes ?? state.liveWindow);
      ensureLiveCharts();
      updateLiveCharts();
      renderCards();
      renderEnvironment();
      $('settings-note').textContent = '已保存并立即生效';
      notify('告警阈值与运行参数已更新');
    } catch (error) {
      $('settings-note').textContent = error.message;
    }
  }

  function useCurrentUrl() {
    const note = $('public-url-note');
    const host = window.location.hostname;
    const loopback = ['127.0.0.1', 'localhost', '::1', '[::1]', '0.0.0.0'].includes(host);
    if (loopback) {
      note.textContent = '你现在是通过本机地址打开的，别人访问不到；请用公网 IP 或域名打开面板后再点。';
      return;
    }
    if (!/^https?:$/.test(window.location.protocol)) {
      note.textContent = '当前页面不是通过 http/https 打开的，无法自动填入。';
      return;
    }
    $('set-public-url').value = window.location.origin;
    note.textContent = '已填入当前访问地址，点“保存对外地址”生效。';
  }

  async function savePublicUrl(event) {
    event.preventDefault();
    const note = $('public-url-note');
    note.textContent = '正在保存…';
    try {
      await api('/api/settings', {
        method: 'PUT',
        body: { public_url: $('set-public-url').value.trim() },
      });
      note.textContent = '已保存并立即生效';
      notify('对外访问地址已更新');
      await loadSettings();
    } catch (error) {
      note.textContent = error.message;
    }
  }

  async function regenerateDeviceKey() {
    if (!window.confirm('重新生成后，旧密钥会立即失效，需要同步更新所有节点固件。确定继续？')) return;
    const data = await api('/api/device-key/regenerate', { method: 'POST' });
    notify(`新的节点密钥：${data.device_key}`, 20000);
    loadSettings().catch(notifyError);
  }

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
      ensureLiveCharts();
      if (!state.liveSeries.has(state.liveNode)) await loadLiveSeries(state.liveNode);
      else updateLiveCharts();
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
    $('alert-do-ack').addEventListener('click', () => submitAlertAction('ack').catch(notifyError));
    $('alert-do-mute').addEventListener('click', () => submitAlertAction('mute').catch(notifyError));
    $('alert-do-resolve').addEventListener('click', () => submitAlertAction('resolve').catch(notifyError));
    $('alert-dialog').addEventListener('close', () => {
      state.alertAction = null;
    });

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
    $('public-url-form').addEventListener('submit', (event) => savePublicUrl(event).catch(notifyError));
    $('use-current-url').addEventListener('click', useCurrentUrl);
    $('copy-ingest').addEventListener('click', () => copyText(state.device?.ingest_url || '', '上报地址'));
    $('copy-device-key').addEventListener('click', () => copyText(state.device?.key || '', '节点密钥'));
    $('regen-device-key').addEventListener('click', () => regenerateDeviceKey().catch(notifyError));
    $('password-form').addEventListener('submit', (event) => changePassword(event).catch(notifyError));

    window.addEventListener('roomsense:theme', () => {
      renderMetricChips();
      ensureLiveCharts();
      updateLiveCharts();
      renderCards();
      renderEnvironment();
      if (state.charts.history) renderHistoryChart();
    });
    window.addEventListener('storage', (event) => {
      if (event.key === sessionStorageKey && !event.newValue && state.session) {
        resetLogin('已在其他标签页退出登录');
      }
    });
    window.addEventListener('beforeunload', () => {
      if (state.stream) state.stream.close();
      clearInterval(state.liveTicker);
    });
    registerServiceWorker();
  }

  function registerServiceWorker() {
    if (!('serviceWorker' in navigator) || !/^https?:$/.test(window.location.protocol)) return;
    navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => {});
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
