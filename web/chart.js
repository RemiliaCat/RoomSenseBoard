'use strict';

/* 轻量 Canvas 折线图，无第三方依赖。

   每个序列可以指定 axis: 0（左侧）或 1（右侧），两条轴各自独立缩放，因此温度与
   湿度这类量纲不同的数据可以画在同一张图里。主题切换时自动重绘。 */
class LineChart {
  constructor(canvas, options = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.options = options;
    this.series = [];
    this.emptyText = options.emptyText || '暂无数据';
    this.padding = { top: 14, right: 12, bottom: 28, left: 12 };
    this.hover = null;
    this.frame = null;
    this.destroyed = false;

    this.tooltip = document.createElement('div');
    this.tooltip.className = 'chart-tooltip';
    this.tooltip.hidden = true;
    const parent = canvas.parentElement;
    if (parent) {
      if (getComputedStyle(parent).position === 'static') parent.style.position = 'relative';
      parent.append(this.tooltip);
    }

    this.onMove = (event) => this.handleMove(event);
    this.onLeave = () => {
      this.hover = null;
      this.tooltip.hidden = true;
      this.schedule();
    };
    canvas.addEventListener('mousemove', this.onMove);
    canvas.addEventListener('mouseleave', this.onLeave);
    this.onTheme = () => this.schedule();
    window.addEventListener('roomsense:theme', this.onTheme);

    this.observer = new ResizeObserver(() => this.schedule());
    this.observer.observe(canvas.parentElement || canvas);
    this.schedule();
  }

  setSeries(series) {
    this.series = (series || []).map((item) => ({
      axis: 0,
      digits: 1,
      unit: '',
      ...item,
      points: (item.points || []).filter((point) => Number.isFinite(point[1])),
    }));
    if (this.hover) this.hover = null;
    this.tooltip.hidden = true;
    this.schedule();
  }

  setEmptyText(text) {
    this.emptyText = text;
    this.schedule();
  }

  destroy() {
    this.destroyed = true;
    cancelAnimationFrame(this.frame);
    this.canvas.removeEventListener('mousemove', this.onMove);
    this.canvas.removeEventListener('mouseleave', this.onLeave);
    window.removeEventListener('roomsense:theme', this.onTheme);
    this.observer.disconnect();
    this.tooltip.remove();
  }

  schedule() {
    if (this.destroyed || this.frame) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = null;
      this.render();
    });
  }

  /* --------------------------------------------------------------- 绘制 */

  render() {
    const canvas = this.canvas;
    const rect = canvas.getBoundingClientRect();
    const width = Math.max(120, Math.round(rect.width));
    const height = Math.max(90, Math.round(rect.height));
    const ratio = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
    }
    const ctx = this.ctx;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);

    const style = getComputedStyle(document.documentElement);
    const color = (name, fallback) => (style.getPropertyValue(name) || fallback).trim();
    const lineColor = color('--line', '#dce1e6');
    const mutedColor = color('--muted', '#626b75');
    const panelColor = color('--panel', '#fff');

    const visible = this.series.filter((item) => item.points.length > 0);
    if (!visible.length) {
      ctx.fillStyle = mutedColor;
      ctx.font = '13px system-ui, "Microsoft YaHei", sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(this.emptyText, width / 2, height / 2);
      return;
    }

    const hasRight = visible.some((item) => item.axis === 1);
    const padding = {
      top: this.padding.top,
      right: hasRight ? 52 : 16,
      bottom: this.padding.bottom,
      left: 52,
    };
    const plot = {
      x: padding.left,
      y: padding.top,
      width: Math.max(10, width - padding.left - padding.right),
      height: Math.max(10, height - padding.top - padding.bottom),
    };

    const times = visible.flatMap((item) => item.points.map((point) => point[0]));
    let xMin = Math.min(...times);
    let xMax = Math.max(...times);
    if (xMax - xMin < 60) {
      xMin -= 30;
      xMax += 30;
    }
    const timeSpan = xMax - xMin;
    const xScale = (ts) => plot.x + ((ts - xMin) / timeSpan) * plot.width;

    const scales = {
      0: this.buildScale(visible.filter((item) => item.axis === 0).flatMap((item) => item.points.map((p) => p[1]))),
      1: this.buildScale(visible.filter((item) => item.axis === 1).flatMap((item) => item.points.map((p) => p[1]))),
    };
    const yScale = (axis, value) => {
      const scale = scales[axis];
      const ratio = (value - scale.min) / (scale.max - scale.min);
      return plot.y + plot.height - ratio * plot.height;
    };

    ctx.lineWidth = 1;
    ctx.strokeStyle = lineColor;
    ctx.fillStyle = mutedColor;
    ctx.font = '11px system-ui, "Microsoft YaHei", sans-serif';
    ctx.textBaseline = 'middle';

    // 水平网格与左右两轴的刻度
    const ticks = Math.max(2, Math.min(6, Math.round(plot.height / 46)));
    for (let index = 0; index <= ticks; index += 1) {
      const y = plot.y + (plot.height / ticks) * index;
      ctx.globalAlpha = 0.7;
      ctx.beginPath();
      ctx.moveTo(plot.x, Math.round(y) + 0.5);
      ctx.lineTo(plot.x + plot.width, Math.round(y) + 0.5);
      ctx.stroke();
      ctx.globalAlpha = 1;
      ctx.textAlign = 'right';
      const leftValue = scales[0].max - ((scales[0].max - scales[0].min) / ticks) * index;
      ctx.fillText(formatValue(leftValue), plot.x - 8, y);
      if (hasRight) {
        ctx.textAlign = 'left';
        const rightValue = scales[1].max - ((scales[1].max - scales[1].min) / ticks) * index;
        ctx.fillText(formatValue(rightValue), plot.x + plot.width + 8, y);
      }
    }

    // 垂直网格与时间刻度
    const verticalTicks = Math.max(2, Math.min(6, Math.round(plot.width / 110)));
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    for (let index = 0; index <= verticalTicks; index += 1) {
      const x = plot.x + (plot.width / verticalTicks) * index;
      ctx.globalAlpha = 0.45;
      ctx.beginPath();
      ctx.moveTo(Math.round(x) + 0.5, plot.y);
      ctx.lineTo(Math.round(x) + 0.5, plot.y + plot.height);
      ctx.stroke();
      ctx.globalAlpha = 1;
      const ts = xMin + (timeSpan / verticalTicks) * index;
      ctx.fillText(formatTime(ts, timeSpan), x, plot.y + plot.height + 8);
    }

    // 数据线
    for (const item of visible) {
      ctx.strokeStyle = item.color;
      ctx.lineWidth = 2;
      ctx.lineJoin = 'round';
      ctx.beginPath();
      item.points.forEach((point, index) => {
        const x = xScale(point[0]);
        const y = yScale(item.axis, point[1]);
        if (index === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
      if (item.fill && item.points.length > 1) {
        const gradient = ctx.createLinearGradient(0, plot.y, 0, plot.y + plot.height);
        gradient.addColorStop(0, hexToRgba(item.color, 0.22));
        gradient.addColorStop(1, hexToRgba(item.color, 0.02));
        ctx.lineTo(xScale(item.points[item.points.length - 1][0]), plot.y + plot.height);
        ctx.lineTo(xScale(item.points[0][0]), plot.y + plot.height);
        ctx.closePath();
        ctx.fillStyle = gradient;
        ctx.fill();
        // 重新描线，避免填充覆盖线条
        ctx.beginPath();
        item.points.forEach((point, index) => {
          const x = xScale(point[0]);
          const y = yScale(item.axis, point[1]);
          if (index === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        });
        ctx.stroke();
      }
    }

    this.geometry = { plot, xMin, xMax, scales, hasRight, panelColor };
    if (this.hover) this.drawHover();
  }

  buildScale(values) {
    if (!values.length) return { min: 0, max: 1, step: 1 };
    let min = Math.min(...values);
    let max = Math.max(...values);
    if (min === max) {
      min -= 1;
      max += 1;
    }
    const rawStep = (max - min) / 4;
    let step = niceStep(rawStep);
    let low = Math.floor(min / step) * step;
    let high = Math.ceil(max / step) * step;
    while ((high - low) / step > 8) {
      step *= 2;
      low = Math.floor(min / step) * step;
      high = Math.ceil(max / step) * step;
    }
    return { min: low, max: high, step };
  }

  /* --------------------------------------------------------------- 交互 */

  handleMove(event) {
    if (!this.geometry) return;
    const rect = this.canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const geometry = this.geometry;
    if (x < geometry.plot.x - 6 || x > geometry.plot.x + geometry.plot.width + 6) {
      this.hover = null;
      this.tooltip.hidden = true;
      this.schedule();
      return;
    }
    const ratio = (x - geometry.plot.x) / geometry.plot.width;
    const time = geometry.xMin + ratio * (geometry.xMax - geometry.xMin);
    const entries = [];
    for (const item of this.series) {
      if (!item.points.length) continue;
      const point = nearestPoint(item.points, time);
      if (!point) continue;
      entries.push({ item, point, x: this.timeToX(point[0]), y: this.valueToY(item.axis, point[1]) });
    }
    if (!entries.length) return;
    entries.sort((a, b) => a.x - b.x);
    this.hover = { x: entries[0].x, time, entries };
    this.showTooltip(event, entries);
    this.schedule();
  }

  timeToX(ts) {
    const { plot, xMin, xMax } = this.geometry;
    return plot.x + ((ts - xMin) / (xMax - xMin)) * plot.width;
  }

  valueToY(axis, value) {
    const { plot, scales } = this.geometry;
    const scale = scales[axis];
    return plot.y + plot.height - ((value - scale.min) / (scale.max - scale.min)) * plot.height;
  }

  drawHover() {
    const ctx = this.ctx;
    const { plot, panelColor } = this.geometry;
    ctx.save();
    ctx.strokeStyle = panelColor === '#fff' ? '#0003' : '#fff4';
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(Math.round(this.hover.x) + 0.5, plot.y);
    ctx.lineTo(Math.round(this.hover.x) + 0.5, plot.y + plot.height);
    ctx.stroke();
    ctx.setLineDash([]);
    for (const entry of this.hover.entries) {
      ctx.beginPath();
      ctx.fillStyle = entry.item.color;
      ctx.arc(entry.x, entry.y, 3.2, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = panelColor;
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }
    ctx.restore();
  }

  showTooltip(event, entries) {
    const lines = entries
      .map(
        (entry) =>
          `<div><span class="swatch" style="background:${entry.item.color}"></span>${escapeHtml(
            entry.item.label,
          )} <strong>${formatValue(entry.point[1], entry.item.digits)}${escapeHtml(entry.item.unit || '')}</strong></div>`,
      )
      .join('');
    const stamp = new Date(entries[0].point[0] * 1000);
    this.tooltip.innerHTML = `<div>${formatStamp(stamp)}</div>${lines}`;
    this.tooltip.hidden = false;
    const wrap = this.canvas.parentElement.getBoundingClientRect();
    const tooltip = this.tooltip.getBoundingClientRect();
    let left = event.clientX - wrap.left + 14;
    if (left + tooltip.width > wrap.width - 6) left = event.clientX - wrap.left - tooltip.width - 14;
    let top = event.clientY - wrap.top - tooltip.height / 2;
    top = Math.max(4, Math.min(wrap.height - tooltip.height - 4, top));
    this.tooltip.style.left = `${Math.max(4, left)}px`;
    this.tooltip.style.top = `${Math.max(4, top)}px`;
  }
}

function nearestPoint(points, time) {
  let low = 0;
  let high = points.length - 1;
  if (time <= points[0][0]) return points[0];
  if (time >= points[high][0]) return points[high];
  while (high - low > 1) {
    const mid = (low + high) >> 1;
    if (points[mid][0] <= time) low = mid;
    else high = mid;
  }
  return time - points[low][0] <= points[high][0] - time ? points[low] : points[high];
}

function niceStep(raw) {
  if (!(raw > 0)) return 1;
  const exponent = Math.floor(Math.log10(raw));
  const base = 10 ** exponent;
  const normalized = raw / base;
  const factor = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 2.5 ? 2.5 : normalized <= 5 ? 5 : 10;
  return factor * base;
}

function formatValue(value, digits = 1) {
  if (!Number.isFinite(value)) return '--';
  const abs = Math.abs(value);
  if (abs >= 1000) return value.toFixed(0);
  if (abs >= 100) return value.toFixed(digits > 1 ? digits - 1 : 0);
  return value.toFixed(digits);
}

function formatTime(ts, span) {
  const date = new Date(ts * 1000);
  const pad = (value) => String(value).padStart(2, '0');
  if (span > 3 * 86400) return `${date.getMonth() + 1}-${pad(date.getDate())}`;
  if (span <= 900) return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function formatStamp(date) {
  const pad = (value) => String(value).padStart(2, '0');
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(
    date.getMinutes(),
  )}:${pad(date.getSeconds())}`;
}

function hexToRgba(color, alpha) {
  const match = /^#([0-9a-f]{6})$/i.exec(color.trim());
  if (!match) return `rgba(0, 160, 234, ${alpha})`;
  const value = parseInt(match[1], 16);
  return `rgba(${(value >> 16) & 255}, ${(value >> 8) & 255}, ${value & 255}, ${alpha})`;
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (char) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[char]);
}
