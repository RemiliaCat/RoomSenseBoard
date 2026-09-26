'use strict';

/* 主题切换：与 OlivOS WebUI 相同的三档模式（跟随系统 / 浅色 / 深色）。 */
(() => {
  const storageKey = 'roomsense.panel.theme';
  const systemTheme = window.matchMedia('(prefers-color-scheme: dark)');
  const modes = ['system', 'light', 'dark'];
  let mode = 'system';

  try {
    const saved = localStorage.getItem(storageKey);
    if (modes.includes(saved)) mode = saved;
  } catch {
    // 浏览器禁用本地存储时仍然允许切换，只是不持久化。
  }

  function applyTheme() {
    document.documentElement.dataset.theme =
      mode === 'system' ? (systemTheme.matches ? 'dark' : 'light') : mode;
    document.querySelectorAll('[data-theme-select]').forEach((select) => {
      select.value = mode;
      // 自绘下拉需要显式同步显示文本（直接赋值不会触发 change 事件）。
      if (window.RoomSenseUI && window.RoomSenseUI.dropdown) {
        window.RoomSenseUI.dropdown.refresh(select);
      }
    });
    window.dispatchEvent(new CustomEvent('roomsense:theme', { detail: { mode } }));
  }

  applyTheme();
  systemTheme.addEventListener('change', applyTheme);
  window.addEventListener('storage', (event) => {
    if (event.key !== storageKey && event.key !== null) return;
    mode = modes.includes(event.newValue) ? event.newValue : 'system';
    applyTheme();
  });

  document.addEventListener('DOMContentLoaded', () => {
    applyTheme();
    document.querySelectorAll('[data-theme-select]').forEach((select) => {
      select.addEventListener('change', () => {
        mode = modes.includes(select.value) ? select.value : 'system';
        applyTheme();
        try {
          localStorage.setItem(storageKey, mode);
        } catch {
          // 无法持久化时保持本次会话生效。
        }
      });
    });
  });
})();
