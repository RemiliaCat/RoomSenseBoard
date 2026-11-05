/*
    ____                       _____
   / __ \____  ____  ____ ___ / ___/___  ____  ________
  / /_/ / __ \/ __ \/ __ `__ \\__ \/ _ \/ __ \/ ___/ _ \
 / _, _/ /_/ / /_/ / / / / / /__/ /  __/ / / (__  )  __/
/_/ |_|\____/\____/_/ /_/ /_/____/\___/_/ /_/____/\___/

@File      :   web/chart.js
@Author    :   RmCat
@Contact   :   RemiliaCatNero@gmail.com
@License   :   AGPL
@Copyright :   (C) 2026, RoomSense-Team
@Desc      :   轻量 Canvas 折线图：固定时间域、自适应刻度与悬停读数。
*/
'use strict';

class LineChart {
  constructor(canvas, options = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.options = options;
    this.series = [];
    this.guides = [];
    this.domain = null;
    this.emptyText = options.emptyText || '暂无数据';
    this.padding = { top: 14, right: 12, bottom: 28, left: 12 };
    this.hover = null;
    this.frame = null;
    this.destroyed = false;
    this.dragging = false;

    this.tooltip = document.createElement('div');
    this.tooltip.className = 'chart-tooltip';
    this.tooltip.hidden = true;
    const parent = canvas.parentElement;
    if (parent) {
      if (getComputedStyle(parent).position === 'static') parent.style.position = 'relative';
      parent.append(this.tooltip);
    }

    this.onMove = (event) => this.handleMove(event);
    this.onLeave = () => this.clearHover();
    this.onDown = (event) => {
      this.dragging = true;
      this.handleMove(event);
    };
    this.onUp = (event) => {
      this.dragging = false;
      if (event.pointerType && event.pointerType !== 'mouse') this.clearHover();
    };
    canvas.addEventListener('pointermove', this.onMove);
    canvas.addEventListener('pointerdown', this.onDown);
    canvas.addEventListener('pointerup', this.onUp);
    canvas.addEventListener('pointercancel', this.onUp);
    canvas.addEventListener('pointerleave', this.onLeave);
    this.onTheme = () => this.schedule();
    window.addEventListener('roomsense:theme', this.onTheme);

    this.observer = new ResizeObserver(() => this.schedule());
    this.observer.observe(canvas.parentElement || canvas);
    this.schedule();
  }

  setSeries(series) {
    this.series = (series || []).map((item) => {
      const sorted = (item.points || [])
        .filter((point) => Number.isFinite(point[1]))
        .sort((a, b) => a[0] - b[0]);
      const points = [];
      for (const point of sorted) {
        if (points.length && points[points.length - 1][0] === point[0]) points[points.length - 1] = point;
        else points.push(point);
      }
      return {
        axis: 0,
        digits: 1,
        unit: '',
        ...item,
        points,
      };
    });
    this.hover = null;
    this.tooltip.hidden = true;
    this.schedule();
  }

  setDomain(start, end) {
    if (Number.isFinite(start) && Number.isFinite(end) && end > start) {
      this.domain = { start, end };
    } else {
      this.domain = null;
    }
    this.schedule();
  }

  setGuides(guides) {
    this.guides = (guides || []).filter((guide) => Number.isFinite(guide.value));
    this.schedule();
  }

  setEmptyText(text) {
    this.emptyText = text;
    this.schedule();
  }

  destroy() {
    this.destroyed = true;
    cancelAnimationFrame(this.frame);
    this.canvas.removeEventListener('pointermove', this.onMove);
    this.canvas.removeEventListener('pointerdown', this.onDown);
    this.canvas.removeEventListener('pointerup', this.onUp);
    this.canvas.removeEventListener('pointercancel', this.onUp);
    this.canvas.removeEventListener('pointerleave', this.onLeave);
    window.removeEventListener('roomsense:theme', this.onTheme);
    this.observer.disconnect();
    this.tooltip.remove();
  }

  clearHover() {
    this.hover = null;
    this.dragging = false;
    this.tooltip.hidden = true;
    this.schedule();
  }

  schedule() {
    if (this.destroyed || this.frame) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = null;
      this.render();
    });
  }

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
    const compact = width < 420;
    const padding = {
      top: this.padding.top,
      right: hasRight ? (compact ? 42 : 52) : compact ? 14 : 16,
      bottom: this.padding.bottom,
      left: compact ? 42 : 52,
    };
    const plot = {
      x: padding.left,
      y: padding.top,
      width: Math.max(10, width - padding.left - padding.right),
      height: Math.max(10, height - padding.top - padding.bottom),
    };

    let xMin;
    let xMax;
    if (this.domain) {
      xMin = this.domain.start;
      xMax = this.domain.end;
    } else {
      const times = visible.flatMap((item) => item.points.map((point) => point[0]));
      xMin = Math.min(...times);
      xMax = Math.max(...times);
      if (xMax - xMin < 60) {
        xMin -= 30;
        xMax += 30;
      }
    }
    const timeSpan = Math.max(1, xMax - xMin);
    const xScale = (ts) => plot.x + ((ts - xMin) / timeSpan) * plot.width;

    const scales = {
      0: this.buildScale(visible.filter((item) => item.axis === 0).flatMap((item) => item.points.map((p) => p[1]))),
      1: this.buildScale(visible.filter((item) => item.axis === 1).flatMap((item) => item.points.map((p) => p[1]))),
    };
    const yScale = (axis, value) => {
      const scale = scales[axis];
      const position = (value - scale.min) / (scale.max - scale.min);
      return plot.y + plot.height - position * plot.height;
    };
    const axisDigits = (axis) => {
      const items = visible.filter((item) => item.axis === axis);
      return items.reduce((digits, item) => Math.max(digits, Number(item.digits) || 0), 0);
    };
    const ticks = Math.max(2, Math.min(6, Math.round(plot.height / 46)));
    const tickDigits = (axis) => {
      const scale = scales[axis];
      const step = (scale.max - scale.min) / Math.max(1, ticks);
      const needed = step > 0 ? Math.ceil(-Math.log10(step)) : 0;
      return Math.min(3, Math.max(axisDigits(axis), needed));
    };
    const leftDigits = tickDigits(0);
    const rightDigits = hasRight ? tickDigits(1) : 0;

    ctx.lineWidth = 1;
    ctx.strokeStyle = lineColor;
    ctx.fillStyle = mutedColor;
    ctx.font = `${compact ? 10 : 11}px system-ui, "Microsoft YaHei", sans-serif`;
    ctx.textBaseline = 'middle';

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
      ctx.fillText(formatValue(leftValue, leftDigits), plot.x - 7, y);
      if (hasRight) {
        ctx.textAlign = 'left';
        const rightValue = scales[1].max - ((scales[1].max - scales[1].min) / ticks) * index;
        ctx.fillText(formatValue(rightValue, rightDigits), plot.x + plot.width + 7, y);
      }
    }

    const verticalTicks = Math.max(2, Math.min(6, Math.round(plot.width / (compact ? 78 : 110))));
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
      if (index === 0) ctx.textAlign = 'left';
      else if (index === verticalTicks) ctx.textAlign = 'right';
      else ctx.textAlign = 'center';
      ctx.fillText(formatTime(ts, timeSpan), x, plot.y + plot.height + 8);
    }

    ctx.save();
    ctx.beginPath();
    ctx.rect(plot.x, plot.y, plot.width, plot.height);
    ctx.clip();

    for (const guide of this.guides) {
      if ((guide.axis || 0) !== 0) continue;
      const scale = scales[0];
      if (guide.value < scale.min || guide.value > scale.max) continue;
      ctx.save();
      ctx.strokeStyle = guide.color || mutedColor;
      ctx.globalAlpha = 0.55;
      ctx.setLineDash([5, 4]);
      ctx.lineWidth = 1;
      const y = yScale(0, guide.value);
      ctx.beginPath();
      ctx.moveTo(plot.x, Math.round(y) + 0.5);
      ctx.lineTo(plot.x + plot.width, Math.round(y) + 0.5);
      ctx.stroke();
      ctx.restore();
    }

    for (const item of visible) {
      const segments = splitSegments(item.points, item.maxGap);
      ctx.strokeStyle = item.color;
      ctx.lineWidth = 2;
      ctx.lineJoin = 'round';
      ctx.lineCap = 'round';
      for (const segment of segments) {
        if (segment.length === 1) {
          ctx.beginPath();
          ctx.fillStyle = item.color;
          ctx.arc(xScale(segment[0][0]), yScale(item.axis, segment[0][1]), 3.2, 0, Math.PI * 2);
          ctx.fill();
          continue;
        }
        traceSegment(ctx, segment, xScale, (point) => yScale(item.axis, point[1]));
        ctx.stroke();
        if (item.fill) {
          const first = segment[0];
          const last = segment[segment.length - 1];
          const gradient = ctx.createLinearGradient(0, plot.y, 0, plot.y + plot.height);
          gradient.addColorStop(0, hexToRgba(item.color, 0.2));
          gradient.addColorStop(1, hexToRgba(item.color, 0.02));
          ctx.beginPath();
          traceSegment(ctx, segment, xScale, (point) => yScale(item.axis, point[1]));
          ctx.lineTo(xScale(last[0]), plot.y + plot.height);
          ctx.lineTo(xScale(first[0]), plot.y + plot.height);
          ctx.closePath();
          ctx.fillStyle = gradient;
          ctx.fill();
          traceSegment(ctx, segment, xScale, (point) => yScale(item.axis, point[1]));
          ctx.stroke();
        }
      }
    }
    ctx.restore();

    this.geometry = { plot, xMin, xMax, scales, hasRight, lineColor, panelColor, mutedColor };
    if (this.hover) this.drawHover();
  }

  buildScale(values) {
    if (!values.length) return { min: 0, max: 1, step: 1 };
    let min = Math.min(...values);
    let max = Math.max(...values);
    if (min === max) {
      const padding = Math.abs(min) * 0.05 || 1;
      min -= padding;
      max += padding;
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

  handleMove(event) {
    if (!this.geometry) return;
    const rect = this.canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const geometry = this.geometry;
    if (x < geometry.plot.x - 6 || x > geometry.plot.x + geometry.plot.width + 6) {
      if (this.hover) this.clearHover();
      return;
    }
    const ratio = (x - geometry.plot.x) / geometry.plot.width;
    const time = geometry.xMin + ratio * (geometry.xMax - geometry.xMin);
    const entries = [];
    for (const item of this.series) {
      if (!item.points.length) continue;
      const point = nearestPoint(item.points, time);
      if (!point || point[0] < geometry.xMin || point[0] > geometry.xMax) continue;
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
    const { plot, lineColor, panelColor } = this.geometry;
    ctx.save();
    ctx.strokeStyle = lineColor;
    ctx.globalAlpha = 1;
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

function traceSegment(ctx, points, xScale, yScale) {
  ctx.beginPath();
  points.forEach((point, index) => {
    const x = xScale(point[0]);
    const y = yScale(point);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
}

function splitSegments(points, maxGap) {
  if (!maxGap || points.length < 2) return [points];
  const segments = [];
  let current = [points[0]];
  for (let index = 1; index < points.length; index += 1) {
    if (points[index][0] - points[index - 1][0] > maxGap) {
      segments.push(current);
      current = [];
    }
    current.push(points[index]);
  }
  segments.push(current);
  return segments;
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
  if (span >= 3 * 86400) return `${date.getMonth() + 1}-${pad(date.getDate())}`;
  if (span >= 20 * 3600) return `${date.getMonth() + 1}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
  if (span <= 600) return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
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
