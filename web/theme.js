/*
    ____                       _____
   / __ \____  ____  ____ ___ / ___/___  ____  ________
  / /_/ / __ \/ __ \/ __ `__ \\__ \/ _ \/ __ \/ ___/ _ \
 / _, _/ /_/ / /_/ / / / / / /__/ /  __/ / / (__  )  __/
/_/ |_|\____/\____/_/ /_/ /_/____/\___/_/ /_/____/\___/

@File      :   web/theme.js
@Author    :   RmCat
@Contact   :   RemiliaCatNero@gmail.com
@License   :   AGPL
@Copyright :   (C) 2026, RoomSense-Team
@Desc      :   主题切换：跟随系统 / 浅色 / 深色三档。
*/
'use strict';

(() => {
  const storageKey = 'roomsense.panel.theme';
  const systemTheme = window.matchMedia('(prefers-color-scheme: dark)');
  const modes = ['system', 'light', 'dark'];
  let mode = 'system';

  try {
    const saved = localStorage.getItem(storageKey);
    if (modes.includes(saved)) mode = saved;
  } catch {
  }

  function applyTheme() {
    document.documentElement.dataset.theme =
      mode === 'system' ? (systemTheme.matches ? 'dark' : 'light') : mode;
    document.querySelectorAll('[data-theme-select]').forEach((select) => {
      select.value = mode;
      // 自绘下拉要手动同步显示文本。
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
        }
      });
    });
  });
})();
