'use strict';

/* 自绘下拉选择器：把原生 <select> 增强为与面板主题一致的下拉浮层。

   原生 select 的弹出层由浏览器 / 操作系统绘制，无法跟随面板的浅色、深色主题，
   因此在保留原生 <select> 作为取值与事件来源（``select.value`` 与 ``change`` 事件
   照旧可用）的前提下，只替换可见外观。

   键盘：Enter / Space / ↓ 打开，↑↓ 移动，Home / End 首尾，Enter 选中，Esc 关闭，
   直接输入字符可快速定位；ARIA 使用 combobox + listbox + option。

   本文件属于自绘控件层（``web/ui/``）。动态重建选项后调用
   ``window.RoomSenseUI.dropdown.refresh(select)`` 同步显示。 */
(() => {
  const ui = (window.RoomSenseUI = window.RoomSenseUI || {});
  const instances = new WeakMap();
  const live = new Set();
  let nextId = 0;

  class Dropdown {
    constructor(select) {
      this.select = select;
      this.id = `dropdown-${(nextId += 1)}`;
      this.items = [];
      this.activeIndex = -1;
      this.typed = '';
      this.typedTimer = null;

      this.wrapper = document.createElement('span');
      this.wrapper.className = 'select';
      select.parentNode.insertBefore(this.wrapper, select);
      this.wrapper.append(select);
      select.classList.add('select-native');
      select.setAttribute('tabindex', '-1');
      select.setAttribute('aria-hidden', 'true');

      this.trigger = document.createElement('button');
      this.trigger.type = 'button';
      this.trigger.className = 'select-trigger';
      this.trigger.setAttribute('role', 'combobox');
      this.trigger.setAttribute('aria-haspopup', 'listbox');
      this.trigger.setAttribute('aria-expanded', 'false');
      this.trigger.setAttribute('aria-controls', `${this.id}-menu`);
      this.label = document.createElement('span');
      this.label.className = 'select-label';
      this.trigger.append(this.label);

      this.menu = document.createElement('ul');
      this.menu.className = 'select-menu';
      this.menu.id = `${this.id}-menu`;
      this.menu.setAttribute('role', 'listbox');
      this.menu.hidden = true;
      this.wrapper.append(this.trigger, this.menu);
      // 原生 select 被隐藏后，点击仍会冒泡给外层 label 并转发到 select，这里拦下。
      this.wrapper.addEventListener('click', (event) => event.stopPropagation());
      this.wrapper.addEventListener('pointerdown', (event) => event.stopPropagation());

      this.trigger.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        if (this.disabled()) return;
        if (this.isOpen()) this.close();
        else this.open();
      });
      this.trigger.addEventListener('keydown', (event) => this.onKeyDown(event));
      this.menu.addEventListener('mousemove', (event) => {
        const option = event.target.closest('.select-option');
        if (option && !option.classList.contains('disabled')) {
          this.setActive(Number(option.dataset.index));
        }
      });
      this.menu.addEventListener('click', (event) => {
        const option = event.target.closest('.select-option');
        if (!option || option.classList.contains('disabled')) return;
        this.commit(Number(option.dataset.index));
      });

      live.add(this);
      this.refresh();
    }

    // ------------------------------------------------------------- 数据同步

    refresh() {
      this.items = Array.from(this.select.options).map((option) => ({
        value: option.value,
        label: option.textContent,
        disabled: option.disabled,
      }));
      this.menu.replaceChildren();
      this.items.forEach((item, index) => {
        const node = document.createElement('li');
        node.className = 'select-option';
        node.setAttribute('role', 'option');
        node.dataset.index = String(index);
        node.textContent = item.label;
        if (item.disabled) {
          node.classList.add('disabled');
          node.setAttribute('aria-disabled', 'true');
        }
        this.menu.append(node);
      });
      const option = this.select.options[this.select.selectedIndex];
      this.label.textContent = option ? option.textContent : '—';
      this.trigger.disabled = this.disabled();
      this.wrapper.classList.toggle('is-disabled', this.disabled());
      this.markSelected();
    }

    markSelected() {
      const selected = this.select.selectedIndex;
      this.menu.querySelectorAll('.select-option').forEach((node, index) => {
        node.setAttribute('aria-selected', index === selected ? 'true' : 'false');
        node.classList.toggle('is-selected', index === selected);
        node.classList.toggle('is-active', index === this.activeIndex);
        node.id = `${this.id}-option-${index}`;
      });
    }

    disabled() {
      return Boolean(this.select.disabled);
    }

    isOpen() {
      return !this.menu.hidden;
    }

    // ------------------------------------------------------------------ 开关

    open() {
      for (const other of live) if (other !== this) other.close();
      this.menu.hidden = false;
      this.trigger.setAttribute('aria-expanded', 'true');
      this.activeIndex = Math.max(0, this.select.selectedIndex);
      this.menu.classList.toggle('drop-up', this.shouldFlipUp());
      this.markSelected();
      this.scrollActiveIntoView();
    }

    close() {
      if (this.menu.hidden) return;
      this.menu.hidden = true;
      this.trigger.setAttribute('aria-expanded', 'false');
      this.trigger.removeAttribute('aria-activedescendant');
      this.activeIndex = -1;
    }

    shouldFlipUp() {
      const rect = this.wrapper.getBoundingClientRect();
      const estimated = Math.min(this.items.length * 34 + 12, 280);
      const spaceBelow = window.innerHeight - rect.bottom;
      return spaceBelow < estimated + 12 && rect.top > estimated + 12;
    }

    // ------------------------------------------------------------------ 选择

    setActive(index) {
      if (index < 0 || index >= this.items.length) return;
      this.activeIndex = index;
      this.markSelected();
      const node = this.menu.querySelector(`#${this.id}-option-${index}`);
      if (node) this.trigger.setAttribute('aria-activedescendant', node.id);
      this.scrollActiveIntoView();
    }

    move(delta) {
      if (!this.items.length) return;
      let index = this.activeIndex;
      for (let step = 0; step < this.items.length; step += 1) {
        index = (index + delta + this.items.length) % this.items.length;
        if (!this.items[index].disabled) {
          this.setActive(index);
          return;
        }
      }
    }

    commit(index) {
      const item = this.items[index];
      if (!item || item.disabled) return;
      this.select.selectedIndex = index;
      this.refresh();
      this.close();
      this.trigger.focus();
      this.select.dispatchEvent(new Event('change', { bubbles: true }));
    }

    scrollActiveIntoView() {
      const node = this.menu.querySelector('.select-option.is-active');
      if (node) node.scrollIntoView({ block: 'nearest' });
    }

    // ------------------------------------------------------------------ 键盘

    onKeyDown(event) {
      const key = event.key;
      if (key === 'Tab') {
        this.close();
        return;
      }
      if (!this.isOpen()) {
        if (key === 'Enter' || key === ' ' || key === 'ArrowDown' || key === 'ArrowUp') {
          event.preventDefault();
          this.open();
          if (key === 'ArrowDown') this.move(1);
          if (key === 'ArrowUp') this.move(-1);
          return;
        }
      } else {
        if (key === 'Escape') {
          event.preventDefault();
          this.close();
          return;
        }
        if (key === 'ArrowDown' || key === 'ArrowUp') {
          event.preventDefault();
          this.move(key === 'ArrowDown' ? 1 : -1);
          return;
        }
        if (key === 'Home' || key === 'End') {
          event.preventDefault();
          this.setActive(key === 'Home' ? 0 : this.items.length - 1);
          return;
        }
        if (key === 'Enter' || key === ' ') {
          event.preventDefault();
          if (this.activeIndex >= 0) this.commit(this.activeIndex);
          return;
        }
      }
      if (key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
        this.typeAhead(key);
      }
    }

    typeAhead(char) {
      clearTimeout(this.typedTimer);
      this.typed += char.toLowerCase();
      this.typedTimer = setTimeout(() => {
        this.typed = '';
      }, 700);
      const index = this.items.findIndex(
        (item) => !item.disabled && item.label.toLowerCase().startsWith(this.typed),
      );
      if (index >= 0) {
        if (!this.isOpen()) this.open();
        this.setActive(index);
      }
    }
  }

  function enhance(select) {
    if (!select || select.classList.contains('select-native')) return null;
    if (instances.has(select)) return instances.get(select);
    const instance = new Dropdown(select);
    instances.set(select, instance);
    return instance;
  }

  function refresh(select) {
    const instance = instances.get(select);
    if (instance) instance.refresh();
  }

  function enhanceAll(root = document) {
    root.querySelectorAll('select').forEach((select) => enhance(select));
  }

  document.addEventListener('pointerdown', (event) => {
    for (const instance of live) {
      if (!instance.wrapper.contains(event.target)) instance.close();
    }
  });
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    for (const instance of live) instance.close();
  });
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => enhanceAll());
  } else {
    enhanceAll();
  }

  ui.dropdown = { enhance, refresh, enhanceAll };
})();
