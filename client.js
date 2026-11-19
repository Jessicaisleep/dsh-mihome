/**
 * dsh-mihome — Client 半：侧栏「米家」工作台。
 *
 * 形态对齐米家 App，而不是「一堆设置项的列表」：
 *   首页   = 家庭 / 房间分区 + 设备磁贴（真实产品图 + 名称 + 状态 + 开关）
 *   设备页 = 点进去的全屏控制页（大电源键 + 常用设置 + 状态 + 折叠的高级设置）
 *
 * 面板本身不做任何米家协议：只从 /dsh-mihome/api/* 取数、渲染、把用户操作 POST 回去。
 * 真正的设备访问在 Host 侧 lib/panel.js → Python 桥接。
 *
 * 注册两件套（缺一不可）：
 *   sidebar.panellist  —— 侧栏那一行（带图标），点它触发 layout.selectPanel(id)
 *   main               —— id 与那一行相同的全局面板，占据主栏
 * 两者都用 ctx.slots.inject(name, () => ctx.slots.register(...)) 包起来：
 * rc.3 的 slot 是惰性声明的，apply 阶段裸 register() 会被静默丢弃。
 */
window.__ModuleLoader__.load({
  id: 'dsh-mihome',
  factory(require) {
    const React = require('react');
    const h = React.createElement;
    const { useState, useEffect, useCallback, useRef } = React;

    const NS = 'dsh-mihome';
    const PANEL_ID = 'dsh-mihome';
    const API = '/dsh-mihome/api';

    /* ---------------------------------------------------------------- 文案 */

    // 面向不熟悉智能家居的小白用户：少术语、多解释、出错给出下一步。
    const DICT = {
      zh: {
        nav: '米家',
        title: '米家',
        refresh: '刷新',
        loading: '正在读取…',
        retry: '重试',
        back: '返回',

        // 顶部页签（对齐米家 App 底部「设备 / 场景」）
        tabDevices: '设备',
        tabScenes: '场景',

        // 概览
        ovDevices: '台设备',
        ovOnline: '台在线',
        homeHint: '点一下设备就能进入控制页。',

        // 登录
        notLoggedIn: '还没有登录米家账号',
        notLoggedInHint: '登录之后，这里会显示你家里的全部设备，可以直接开关和调设置。',
        loginNow: '扫码登录',
        loginTitle: '用米家 App 扫一扫',
        loginStep1: '打开手机上的「米家」App',
        loginStep2: '点右上角「＋」→「扫一扫」',
        loginStep3: '对准下面的二维码，手机上点「确认登录」',
        loginPending: '已生成二维码，等待手机确认…',
        loginScanned: '扫码成功，正在确认…',
        loginExpired: '二维码过期了，点「刷新二维码」再来一次。',
        loginFailed: '没有登录成功，请再试一次。',
        loginRefresh: '刷新二维码',
        loginValidFor: '分钟内有效',

        // 设备
        devices: '设备',
        online: '在线',
        offline: '离线',
        offlineHint: '这台设备现在没联网，连上之后才能控制。',
        notAssigned: '未分配房间',
        otherDevices: '其他设备',
        noDevices: '还没有设备',
        noDevicesHint: '在手机米家 App 里添加设备后，这里会自动出现。',
        deviceFallback: '设备',

        // 电源
        power: '电源',
        powerOn: '开机',
        powerOff: '关机',
        powerOnState: '已开启',
        powerOffState: '已关闭',

        // 分组
        common: '常用',
        statusSection: '当前状态',
        settings: '更多设置',
        advanced: '高级设置（一般不用动）',
        actions: '更多操作',
        readOnly: '只读',
        write: '设置',
        noProps: '这台设备没有可调的设置。',
        noSpec: '这台设备没有公开的控制面板',
        noSpecHint: '米家没有为它提供可远程控制的参数，只能查看名称和在线状态。',

        // 场景 / 耗材
        scenes: '手动场景',
        noScenes: '还没有手动场景',
        noScenesHint: '在手机米家 App 的「智能」里新建场景，就能在这里一键执行。',
        run: '执行',
        running: '执行中…',
        done: '已完成',
        consumables: '耗材',
        noConsumables: '没有需要关注的耗材',
        lowCount: '项耗材快用完了',
        needsReplace: '需要更换',
        left: '剩余',

        errorPrefix: '出错了：',
        unknown: '未知',

        // 场景页（小米官方手册：米家 App → 场景 → 右上角「＋」→ 通用 → 手动执行）
        sceneHint: '点一下就能一键执行。想新增或修改，在手机米家 App 的「场景」里操作。',
        sceneHowToTitle: '米家里怎么加场景',
        sceneHowTo: '米家 App → 场景 → 右上角「＋」→ 通用 → 手动执行，选好要动的设备后保存。',
        sceneEmptyAction: '知道了',
        sceneCount: '个手动场景',

        // 耗材管理（官方：设备详情页右上角「⋮」→「耗材管理」）
        consumableHint: '这里只列需要更换的耗材，换新之后在米家 App 里重置寿命。',
        consumableHowToTitle: '在米家里看耗材',
        consumableHowTo:
          '米家 App → 点开设备 → 右上角「⋮」→「耗材管理」，能看到刷子、滤芯、拖布还能用多久。',
        consumableDevice: '设备',
        consumableUpdated: '更新于',
        consumableThreshold: '建议更换线',
        consumableAllGood: '所有耗材都还够用',
        consumableAllGoodHint: '等有耗材快用完时，这里会提前提醒你。',
      },
      en: {
        nav: 'Mi Home',
        title: 'Mi Home',
        refresh: 'Refresh',
        loading: 'Loading…',
        retry: 'Retry',
        back: 'Back',

        tabDevices: 'Devices',
        tabScenes: 'Scenes',

        ovDevices: 'devices',
        ovOnline: 'online',
        homeHint: 'Tap a device to open its controls.',

        notLoggedIn: 'Not signed in to Mi Home',
        notLoggedInHint: 'Sign in to see and control every device in your home.',
        loginNow: 'Scan to sign in',
        loginTitle: 'Scan with the Mi Home app',
        loginStep1: 'Open Mi Home on your phone',
        loginStep2: 'Tap "+" at the top right, then Scan',
        loginStep3: 'Point at the code below and confirm on the phone',
        loginPending: 'Code ready, waiting for your phone…',
        loginScanned: 'Scanned, confirming…',
        loginExpired: 'The code expired. Refresh it and try again.',
        loginFailed: 'Sign-in did not complete. Please try again.',
        loginRefresh: 'New code',
        loginValidFor: 'minutes',

        devices: 'Devices',
        online: 'Online',
        offline: 'Offline',
        offlineHint: 'This device is offline; controls are unavailable.',
        notAssigned: 'No room',
        otherDevices: 'Other devices',
        noDevices: 'No devices yet',
        noDevicesHint: 'Add devices in the Mi Home app and they show up here.',
        deviceFallback: 'Device',

        power: 'Power',
        powerOn: 'Turn on',
        powerOff: 'Turn off',
        powerOnState: 'On',
        powerOffState: 'Off',

        common: 'Controls',
        statusSection: 'Status',
        settings: 'More settings',
        advanced: 'Advanced',
        actions: 'Actions',
        readOnly: 'read-only',
        write: 'Set',
        noProps: 'Nothing to adjust on this device.',
        noSpec: 'No control panel available',
        noSpecHint: 'Mi Home exposes no remotely controllable settings for it.',

        scenes: 'Scenes',
        noScenes: 'No scenes yet',
        noScenesHint: 'Create a scene in the Mi Home app and run it here.',
        run: 'Run',
        running: 'Running…',
        done: 'Done',
        consumables: 'Consumables',
        noConsumables: 'Nothing to replace',
        lowCount: 'consumable(s) running low',
        needsReplace: 'Replace',
        left: 'left',

        errorPrefix: 'Error: ',
        unknown: 'unknown',

        sceneHint: 'Tap to run. Create or edit scenes in the Mi Home app.',
        sceneHowToTitle: 'How to add a scene',
        sceneHowTo: 'Mi Home app → Scenes → "+" → General → Manual, pick devices, save.',
        sceneEmptyAction: 'Got it',
        sceneCount: 'manual scenes',

        consumableHint: 'Only consumables that need replacing are listed. Reset life in the app.',
        consumableHowToTitle: 'Consumables in the app',
        consumableHowTo:
          'Mi Home app → open the device → "⋮" at top right → Consumables management.',
        consumableDevice: 'Device',
        consumableUpdated: 'Updated',
        consumableThreshold: 'Replace below',
        consumableAllGood: 'All consumables are fine',
        consumableAllGoodHint: 'You will be warned here before one runs out.',
      },
    };

    /* -------------------------------------------------------------- 主题色 */

    // 全部令牌都给浅色回退值，深色主题下 DSH 会覆盖同名 CSS 变量。
    const C = {
      bgBase: 'var(--dsw-alias-bg-base, #ffffff)',
      bgLayer1: 'var(--dsw-alias-bg-layer-1, #f7f8fa)',
      bgLayer2: 'var(--dsw-alias-bg-layer-2, #f0f1f4)',
      border: 'var(--dsw-alias-border-l2, #e3e5ea)',
      label: 'var(--dsw-alias-label-primary, #1b1d21)',
      label2: 'var(--dsw-alias-label-secondary, #4a4f57)',
      label3: 'var(--dsw-alias-label-tertiary, #858b94)',
      hover: 'var(--dsw-alias-interactive-bg-hover, #f2f3f6)',
      brand: 'var(--dsw-alias-brand-primary, #ff6a00)',
      warn: 'var(--dsw-alias-state-warn-primary, #d97706)',
      success: 'var(--dsw-alias-state-success-primary, #15803d)',
      error: 'var(--dsw-alias-state-error-primary, #c62828)',
      radius: 'var(--dsw-radius-sm, 6px)',
      radiusMd: 'var(--dsw-radius-md, 12px)',
    };

    // 米家 App 的橙色是品牌色；这里用它做磁贴高亮与开关。
    const ORANGE = '#ff6a00';

    /* ---------------------------------------------------------------- 工具 */

    function apiPath(relative) {
      const fallback = `${API}/${relative}`;
      if (typeof document === 'undefined') return fallback;
      try {
        const url = new URL(fallback, document.baseURI);
        // 必须带上 search：device?did=… 这类取数全在查询串里，
        // 只取 pathname 会让服务端收不到参数、直接回 400「需要 did」。
        return `${url.pathname}${url.search}`;
      } catch {
        return fallback;
      }
    }

    /** 取 JSON；HTTP 非 2xx 时也把响应体挂到 error.body 上，好把后端中文提示露出来。 */
    async function fetchJSON(path, init) {
      const res = await fetch(apiPath(path), { credentials: 'same-origin', ...(init ?? {}) });
      let body = null;
      try {
        body = await res.json();
      } catch {
        body = null;
      }
      if (!res.ok) {
        const error = new Error(body?.error || `${res.status} ${path}`);
        error.body = body;
        throw error;
      }
      return body ?? {};
    }

    const getJSON = (path) => fetchJSON(path);
    const postJSON = (path, body) =>
      fetchJSON(path, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body ?? {}),
      });

    const focusable = {
      onFocus: (e) => {
        try {
          e.target.style.outlineColor = C.brand;
        } catch {
          /* noop */
        }
      },
      onBlur: (e) => {
        try {
          e.target.style.outlineColor = 'transparent';
        } catch {
          /* noop */
        }
      },
    };

    /**
     * 按钮样式。
     *
     * 注意：这里只能返回**样式**。onFocus/onBlur 必须由调用方用 `...focusable`
     * 展开到元素属性上——把它们混进 style 会被 React 渲染成
     * `style="...;onFocus:..."`，处理器根本不生效。
     */
    const btn = (extra = {}) => ({
      appearance: 'none',
      border: `1px solid ${C.border}`,
      background: C.bgBase,
      color: C.label,
      borderRadius: C.radius,
      padding: '6px 12px',
      fontSize: 12,
      lineHeight: '18px',
      cursor: 'pointer',
      outline: '2px solid transparent',
      outlineOffset: 1,
      ...extra,
    });

    const card = {
      border: `1px solid ${C.border}`,
      background: C.bgBase,
      borderRadius: C.radiusMd,
    };

    const muted = { color: C.label3, fontSize: 12, lineHeight: '18px' };

    /**
     * 面板根容器样式。
     *
     * 宿主的主面板是 `display:flex; flex-direction:column; min-height:0` 的
     * 定高容器，外层 frame 还有 `overflow:hidden`——子元素不自己撑高、不自己
     * 滚动的话，内容一超高下面就被直接裁掉（用户实测：高级设置看不到、也没
     * 有滚动条）。所以这里必须显式 `height:100%` + `min-height:0` +
     * `overflow-y:auto`，滚动条只出现在面板内部，不影响会话区。
     */
    const PAGE = {
      padding: '0 20px 20px',
      maxWidth: 760,
      height: '100%',
      minHeight: 0,
      flex: '1 1 auto',
      overflowY: 'auto',
      overflowX: 'hidden',
      boxSizing: 'border-box',
    };

    /**
     * 面板顶部标题行：内容滚动时固定在顶上，刷新 / 返回按钮始终够得着。
     *
     * 上内边距放在标题行自己身上（而不是根容器），这样 sticky 贴到顶时
     * 背景能把滚过去的卡片完全盖住，不会在 padding 区漏出一条内容。
     */
    const stickyHeader = (bg = C.bgBase) => ({
      position: 'sticky',
      top: 0,
      zIndex: 2,
      background: bg,
      paddingTop: 20,
    });

    const rowStyle = {
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: 12,
      padding: '10px 0',
      borderTop: `1px solid ${C.border}`,
      minHeight: 40,
      boxSizing: 'border-box',
    };

    const sectionTitle = {
      fontSize: 14,
      fontWeight: 600,
      color: C.label,
      margin: '20px 0 10px',
    };

    /* -------------------------------------------------------------- 图形 */

    /** 产品图缺失时的兜底图标：一个简化的智能设备轮廓。 */
    function DeviceGlyph({ size = 18, color = C.label2 }) {
      return h(
        'svg',
        { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', 'aria-hidden': true },
        h('rect', {
          x: 3.5,
          y: 3.5,
          width: 17,
          height: 17,
          rx: 4.5,
          stroke: color,
          strokeWidth: 1.6,
        }),
        h('circle', { cx: 12, cy: 12, r: 2.2, stroke: color, strokeWidth: 1.6 }),
        h('path', {
          d: 'M12 9.8V6.6M12 17.4v-3.2M9.8 12H6.6M17.4 12h-3.2',
          stroke: color,
          strokeWidth: 1.4,
          strokeLinecap: 'round',
        }),
      );
    }

    /** 侧栏行图标。owner 只给 size / active，这里按 active 换色。 */
    function MihomeIcon({ size = 16, active = false }) {
      return h(DeviceGlyph, { size, color: active ? ORANGE : C.label2 });
    }

    /** 真实产品图；图还没下载到 / 加载失败就退回轮廓图。 */
    function ProductImage({ model, size = 40, icons }) {
      const url = icons?.[model];
      const [failed, setFailed] = useState(false);
      useEffect(() => setFailed(false), [url]);
      if (!url || failed) return h(DeviceGlyph, { size: size * 0.7, color: C.label3 });
      return h('img', {
        src: url,
        alt: '',
        width: size,
        height: size,
        onError: () => setFailed(true),
        style: { width: size, height: size, objectFit: 'contain', display: 'block' },
      });
    }

    /* -------------------------------------------------------------- 控件 */

    /** 开关（不用原生 checkbox，颜色跟主题走）。 */
    function Switch({ checked, disabled, onChange, label }) {
      const on = Boolean(checked);
      return h(
        'button',
        {
          type: 'button',
          role: 'switch',
          'aria-checked': on,
          'aria-label': label,
          disabled,
          onClick: () => onChange(!on),
          ...focusable,
          style: {
            appearance: 'none',
            border: 'none',
            padding: 0,
            width: 42,
            height: 24,
            borderRadius: 12,
            background: on ? ORANGE : C.bgLayer2,
            boxShadow: on ? 'none' : `inset 0 0 0 1px ${C.border}`,
            cursor: disabled ? 'not-allowed' : 'pointer',
            opacity: disabled ? 0.5 : 1,
            position: 'relative',
            transition: 'background .15s ease',
            outline: '2px solid transparent',
          },
        },
        h('span', {
          style: {
            position: 'absolute',
            top: 3,
            left: 3,
            width: 18,
            height: 18,
            borderRadius: 9,
            background: '#fff',
            boxShadow: '0 1px 2px rgba(0,0,0,.25)',
            transform: on ? 'translateX(18px)' : 'none',
            transition: 'transform .15s ease',
          },
        }),
      );
    }

    /** 把属性值压成一行可读文本；布尔显示开关词，数组用逗号连接。 */
    const displayValue = (value, t) => {
      if (value === null || value === undefined || value === '') return '—';
      if (typeof value === 'boolean') return value ? (t ? t('powerOnState') : '开') : (t ? t('powerOffState') : '关');
      if (Array.isArray(value)) return value.join(', ');
      if (typeof value === 'object') return JSON.stringify(value);
      return String(value);
    };

    /** 可选值列表的上游形态是 [{value, description}]，也可能是裸数组。 */
    function enumOptions(prop) {
      const list = prop?.value_list ?? prop?.valueList;
      if (!Array.isArray(list) || !list.length) return null;
      return list.map((item) => {
        if (item && typeof item === 'object') {
          return { value: item.value, label: item.description ?? String(item.value) };
        }
        return { value: item, label: String(item) };
      });
    }

    /** 范围的上游形态是 [min, max, step] 元组或 {min,max,step}。 */
    function rangeOf(prop) {
      const range = prop?.range;
      if (Array.isArray(range) && range.length >= 2) {
        return { min: Number(range[0]), max: Number(range[1]), step: Number(range[2] ?? 1) || 1 };
      }
      if (range && typeof range === 'object' && range.min !== undefined) {
        return {
          min: Number(range.min),
          max: Number(range.max),
          step: Number(range.step ?? 1) || 1,
        };
      }
      return null;
    }

    /**
     * 时间戳（秒或毫秒）→ 本地「YYYY-MM-DD HH:MM」。
     *
     * 米家耗材的 update_time 是秒级时间戳；个别接口给毫秒，用长度兜一下。
     */
    function formatTime(stamp) {
      const value = Number(stamp);
      if (!Number.isFinite(value) || value <= 0) return '';
      const ms = value > 1e12 ? value : value * 1000;
      const date = new Date(ms);
      if (Number.isNaN(date.getTime())) return '';
      const pad = (n) => String(n).padStart(2, '0');
      return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
    }

    /** 带单位的展示值。 */
    function valueText(prop, value, t) {
      const text = displayValue(value, t);
      if (text === '—') return text;
      const unit = prop?.unit ?? '';
      if (!unit) return text;
      if (unit === '°C' || unit === '%') return `${text}${unit}`;
      return `${text} ${unit}`;
    }

    /* ---------------------------------------------------------- 属性控件 */

    /**
     * 一个可写属性 → 一个控件。
     *
     * 面向小白用户的取值顺序：
     *   枚举很少（≤5）→ 分段按钮，一眼能点，不用下拉翻
     *   枚举很多      → 下拉
     *   有范围        → 大号数字 + 滑块 + 加减按钮
     *   布尔          → 开关
     *   其它          → 输入框 + 确定
     */
    function PropControl({ prop, value, t, disabled, onWrite }) {
      const options = enumOptions(prop);
      const range = rangeOf(prop);
      const [draft, setDraft] = useState(() => (value === undefined ? '' : String(value)));
      const [busy, setBusy] = useState(false);

      useEffect(() => {
        setDraft(value === undefined ? '' : String(value));
      }, [value]);

      const commit = async (next) => {
        setBusy(true);
        try {
          await onWrite(prop.name, next);
        } finally {
          setBusy(false);
        }
      };

      const common = { disabled: busy || disabled };

      // 枚举：少量选项用分段按钮
      if (options && options.length <= 5) {
        const current = String(draft);
        return h(
          'div',
          {
            role: 'group',
            style: {
              display: 'inline-flex',
              border: `1px solid ${C.border}`,
              borderRadius: C.radius,
              overflow: 'hidden',
              background: C.bgLayer1,
            },
          },
          options.map((option, index) => {
            const active = String(option.value) === current;
            return h(
              'button',
              {
                key: String(option.value),
                type: 'button',
                ...common,
                ...focusable,
                style: {
                  appearance: 'none',
                  border: 'none',
                  borderLeft: index === 0 ? 'none' : `1px solid ${C.border}`,
                  background: active ? ORANGE : 'transparent',
                  color: active ? '#fff' : C.label2,
                  padding: '6px 12px',
                  fontSize: 12,
                  cursor: common.disabled ? 'not-allowed' : 'pointer',
                  whiteSpace: 'nowrap',
                  opacity: common.disabled ? 0.6 : 1,
                  outline: '2px solid transparent',
                },
                onClick: () => {
                  setDraft(String(option.value));
                  commit(option.value);
                },
              },
              option.label,
            );
          }),
        );
      }

      if (options) {
        return h(
          'select',
          {
            value: draft,
            ...common,
            style: btn({ padding: '6px 8px', minWidth: 130, ...common }),
            onChange: (e) => {
              setDraft(e.target.value);
              const hit = options.find((o) => String(o.value) === e.target.value);
              commit(hit ? hit.value : e.target.value);
            },
          },
          options.map((o) => h('option', { key: String(o.value), value: String(o.value) }, o.label)),
        );
      }

      if (range) {
        const number = Number(draft);
        const current = Number.isFinite(number) ? number : range.min;
        const step = range.step || 1;
        const decimals = String(step).includes('.') ? String(step).split('.')[1].length : 0;
        const clamp = (v) => Math.min(range.max, Math.max(range.min, v));
        const fmt = (v) => (decimals ? v.toFixed(decimals) : String(Math.round(v)));
        const nudge = (delta) => {
          const next = clamp(Number((current + delta).toFixed(decimals || 0)));
          setDraft(fmt(next));
          commit(next);
        };
        return h(
          'div',
          { style: { display: 'inline-flex', alignItems: 'center', gap: 8 } },
          h(
            'button',
            {
              type: 'button',
              ...common,
              style: btn({ width: 28, height: 28, padding: 0, fontSize: 16, lineHeight: '24px' }),
              onClick: () => nudge(-step),
              'aria-label': '-',
            },
            '−',
          ),
          h('input', {
            type: 'range',
            min: range.min,
            max: range.max,
            step,
            value: current,
            ...common,
            onChange: (e) => setDraft(e.target.value),
            onMouseUp: (e) => commit(Number(e.target.value)),
            onTouchEnd: (e) => commit(Number(e.target.value)),
            onKeyUp: (e) => {
              if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') commit(Number(e.target.value));
            },
            style: { width: 120, accentColor: ORANGE },
          }),
          h(
            'button',
            {
              type: 'button',
              ...common,
              style: btn({ width: 28, height: 28, padding: 0, fontSize: 16, lineHeight: '24px' }),
              onClick: () => nudge(step),
              'aria-label': '+',
            },
            '+',
          ),
          h(
            'span',
            { style: { minWidth: 52, fontSize: 13, color: C.label, textAlign: 'right' } },
            valueText(prop, current, t),
          ),
        );
      }

      if (typeof value === 'boolean') {
        return h(Switch, {
          checked: Boolean(value),
          disabled: busy || disabled,
          label: prop.desc || prop.name,
          onChange: (next) => commit(next),
        });
      }

      const numeric = typeof value === 'number';
      const submit = () => {
        const raw = String(draft).trim();
        let next = raw;
        if (numeric) next = Number(raw);
        else if (raw === 'true' || raw === 'false') next = raw === 'true';
        if (raw === '') return;
        commit(next);
      };
      return h(
        'div',
        { style: { display: 'inline-flex', alignItems: 'center', gap: 6 } },
        h('input', {
          type: numeric ? 'number' : 'text',
          value: draft,
          ...common,
          onChange: (e) => setDraft(e.target.value),
          onKeyDown: (e) => {
            if (e.key === 'Enter') submit();
          },
          style: btn({ width: 110, padding: '5px 8px', ...common }),
        }),
        h(
          'button',
          { type: 'button', ...common, style: btn({ ...common }), onClick: submit },
          t('write'),
        ),
      );
    }

    /* ------------------------------------------------------------ 登录卡 */

    function LoginCard({ t, onLoggedIn, onError }) {
      const [phase, setPhase] = useState('idle'); // idle | starting | waiting
      const [qrUrl, setQrUrl] = useState('');
      const [expiresIn, setExpiresIn] = useState(null);
      const [note, setNote] = useState('');
      const pollTimer = useRef(null);

      useEffect(
        () => () => {
          if (pollTimer.current) clearTimeout(pollTimer.current);
        },
        [],
      );

      const poll = useCallback(
        async (loginData) => {
          try {
            const result = await postJSON('login/poll', { login_data: loginData });
            if (result.ok && result.logged_in) {
              setPhase('idle');
              setQrUrl('');
              setNote('');
              onLoggedIn?.();
              return;
            }
            setNote(t('loginExpired'));
            setPhase('idle');
          } catch (error) {
            setNote(`${t('errorPrefix')}${error?.body?.error ?? error.message ?? error}`);
            setPhase('idle');
          }
        },
        [onLoggedIn, t],
      );

      const start = async () => {
        setPhase('starting');
        setNote('');
        setQrUrl('');
        try {
          const result = await postJSON('login/start', {});
          if (!result.need_scan) {
            setPhase('idle');
            if (result.logged_in) onLoggedIn?.();
            return;
          }
          setQrUrl(result.qr_url ?? '');
          setExpiresIn(result.expires_in ?? null);
          setPhase('waiting');
          // 生成后立刻长轮询：扫码动作需要有请求挂在那里才能落地。
          if (result.login_data) poll(result.login_data);
        } catch (error) {
          setNote(`${t('errorPrefix')}${error?.body?.error ?? error.message ?? error}`);
          setPhase('idle');
          onError?.(error);
        }
      };

      const minutes = expiresIn ? Math.max(1, Math.round(expiresIn / 60)) : 5;

      return h(
        'div',
        { style: { ...card, padding: 20, maxWidth: 420 } },
        h('div', { style: { fontSize: 16, fontWeight: 600, color: C.label } }, t('notLoggedIn')),
        h('div', { style: { ...muted, marginTop: 6, lineHeight: '20px' } }, t('notLoggedInHint')),

        phase === 'waiting' && qrUrl
          ? h(
              'div',
              { style: { marginTop: 16, textAlign: 'center' } },
              h(
                'div',
                { style: { fontSize: 13, fontWeight: 600, color: C.label, marginBottom: 10 } },
                t('loginTitle'),
              ),
              h('img', {
                src: qrUrl,
                alt: t('loginTitle'),
                width: 200,
                height: 200,
                style: {
                  width: 200,
                  height: 200,
                  display: 'block',
                  margin: '0 auto',
                  background: '#fff',
                  padding: 8,
                  borderRadius: 8,
                  boxSizing: 'content-box',
                },
              }),
              h(
                'div',
                { style: { ...muted, marginTop: 10, textAlign: 'left', lineHeight: '20px' } },
                h('div', null, `1. ${t('loginStep1')}`),
                h('div', null, `2. ${t('loginStep2')}`),
                h('div', null, `3. ${t('loginStep3')}`),
              ),
              h(
                'div',
                { style: { ...muted, marginTop: 10, color: C.warn } },
                `${minutes} ${t('loginValidFor')}`,
              ),
              h(
                'div',
                { style: { marginTop: 14, display: 'flex', gap: 8, justifyContent: 'center' } },
                h('span', { style: { ...muted, alignSelf: 'center' } }, t('loginPending')),
                h(
                  'button',
                  { type: 'button', style: btn(), onClick: start },
                  t('loginRefresh'),
                ),
              ),
            )
          : h(
              'div',
              { style: { marginTop: 16 } },
              h(
                'button',
                {
                  type: 'button',
                  disabled: phase === 'starting',
                  style: btn({
                    background: ORANGE,
                    borderColor: ORANGE,
                    color: '#fff',
                    padding: '9px 18px',
                    fontSize: 13,
                    fontWeight: 600,
                    opacity: phase === 'starting' ? 0.6 : 1,
                  }),
                  onClick: start,
                },
                phase === 'starting' ? t('loading') : t('loginNow'),
              ),
            ),

        note ? h('div', { style: { marginTop: 12, fontSize: 12, color: C.error } }, note) : null,
      );
    }

    /* ---------------------------------------------------------- 设备磁贴 */

    function DeviceTile({ device, t, icons, power, onOpen, onTogglePower }) {
      const [busy, setBusy] = useState(false);
      const online = device.online !== false;
      const powerKnown = typeof power === 'boolean';
      const on = powerKnown && power;

      return h(
        'div',
        {
          role: 'button',
          tabIndex: 0,
          onClick: () => onOpen(device),
          ...focusable,
          onKeyDown: (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              onOpen(device);
            }
          },
          style: {
            ...card,
            padding: '14px 12px 10px',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: 8,
            cursor: 'pointer',
            position: 'relative',
            opacity: online ? 1 : 0.62,
            outline: '2px solid transparent',
          },
        },
        // 电源开着时给磁贴一个极淡的橙色底，扫一眼就知道哪些在运行
        on
          ? h('span', {
              style: {
                position: 'absolute',
                inset: 0,
                borderRadius: C.radiusMd,
                background: 'rgba(255,106,0,.06)',
                pointerEvents: 'none',
              },
            })
          : null,
        h(ProductImage, { model: device.model, size: 44, icons }),
        h(
          'div',
          {
            style: {
              fontSize: 13,
              fontWeight: 500,
              color: C.label,
              textAlign: 'center',
              maxWidth: '100%',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            },
          },
          device.name || device.cloud_name || device.did,
        ),
        h(
          'div',
          { style: { fontSize: 11, color: online ? C.label3 : C.warn, lineHeight: '16px' } },
          online
            ? powerKnown
              ? on
                ? t('powerOnState')
                : t('powerOffState')
              : t('online')
            : t('offline'),
        ),
        powerKnown
          ? h(
              'button',
              {
                type: 'button',
                disabled: busy || !online,
                onClick: (e) => {
                  e.stopPropagation();
                  setBusy(true);
                  Promise.resolve(onTogglePower(device, !on)).finally(() => setBusy(false));
                },
                ...focusable,
                style: {
                  appearance: 'none',
                  marginTop: 2,
                  border: `1px solid ${on ? ORANGE : C.border}`,
                  background: on ? ORANGE : C.bgBase,
                  color: on ? '#fff' : C.label2,
                  borderRadius: 14,
                  padding: '2px 12px',
                  fontSize: 11,
                  lineHeight: '18px',
                  cursor: busy || !online ? 'not-allowed' : 'pointer',
                  opacity: busy || !online ? 0.55 : 1,
                  outline: '2px solid transparent',
                },
              },
              on ? t('powerOff') : t('powerOn'),
            )
          : null,
      );
    }

    /* ---------------------------------------------------------- 设备详情 */

    function DeviceDetail({ device, t, icons, onBack, onError, onChanged }) {
      const [detail, setDetail] = useState(null);
      const [values, setValues] = useState({});
      const [loading, setLoading] = useState(true);
      const [problem, setProblem] = useState('');
      const [busy, setBusy] = useState(false);
      const [message, setMessage] = useState('');
      const [showAdvanced, setShowAdvanced] = useState(false);

      const online = device.online !== false;

      const load = useCallback(async () => {
        setLoading(true);
        setProblem('');
        try {
          const data = await getJSON(`device?did=${encodeURIComponent(device.did)}`);
          setDetail(data.detail ?? null);
        } catch (error) {
          setDetail(null);
          // 后端会给出中文原因（例如「该设备无公开的功能规格」）
          setProblem(error?.body?.hint || error?.body?.error || error?.message || '');
        }
        try {
          const data = await getJSON(`values?did=${encodeURIComponent(device.did)}`);
          setValues(data.values ?? {});
        } catch {
          /* 读不到实时值不影响控件展示 */
        }
        setLoading(false);
      }, [device.did]);

      useEffect(() => {
        load();
      }, [load]);

      const write = async (name, value) => {
        setBusy(true);
        setMessage('');
        try {
          await postJSON('control', { did: device.did, mode: 'write', name, value });
          setValues((prev) => ({ ...(prev ?? {}), [name]: value }));
          setMessage(t('done'));
          onChanged?.();
        } catch (error) {
          setMessage(`${t('errorPrefix')}${error?.body?.error ?? error.message ?? error}`);
        } finally {
          setBusy(false);
        }
      };

      const runPower = async (next) => {
        setBusy(true);
        setMessage('');
        try {
          const result = await postJSON('control', {
            did: device.did,
            mode: 'power',
            ...(typeof next === 'boolean' ? { state: next } : {}),
          });
          setValues((prev) => ({ ...(prev ?? {}), on: Boolean(result.on) }));
          setMessage(t('done'));
          onChanged?.();
        } catch (error) {
          setMessage(`${t('errorPrefix')}${error?.body?.error ?? error.message ?? error}`);
        } finally {
          setBusy(false);
        }
      };

      const runAction = async (name) => {
        setBusy(true);
        setMessage('');
        try {
          await postJSON('control', { did: device.did, mode: 'action', name });
          setMessage(t('done'));
        } catch (error) {
          setMessage(`${t('errorPrefix')}${error?.body?.error ?? error.message ?? error}`);
        } finally {
          setBusy(false);
        }
      };

      const props = detail?.props ?? [];
      const actions = detail?.actions ?? [];
      const powerProp = props.find((p) => p.name === 'on');
      const powerOn = values?.on;
      const commonProps = props.filter((p) => p.writable && p.name !== 'on' && !p.advanced);
      const readonlyProps = props.filter((p) => !p.writable && !p.advanced);
      const advancedProps = props.filter((p) => p.advanced);

      const header = h(
        'div',
        { style: { ...stickyHeader(), display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 } },
        h(
          'button',
          {
            type: 'button',
            style: btn({ padding: '4px 10px', background: C.bgLayer1 }),
            onClick: onBack,
          },
          `‹ ${t('back')}`,
        ),
      );

      const hero = h(
        'div',
        {
          style: {
            display: 'flex',
            alignItems: 'center',
            gap: 14,
            padding: '16px 0 14px',
          },
        },
        h(
          'div',
          {
            style: {
              width: 64,
              height: 64,
              borderRadius: 16,
              background: C.bgLayer1,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flex: '0 0 auto',
            },
          },
          h(ProductImage, { model: device.model, size: 48, icons }),
        ),
        h(
          'div',
          { style: { minWidth: 0 } },
          h(
            'div',
            { style: { fontSize: 17, fontWeight: 600, color: C.label } },
            device.name || device.cloud_name || device.did,
          ),
          h(
            'div',
            { style: { ...muted, marginTop: 4 } },
            [
              device.room_name && device.room_name !== '未知' ? device.room_name : null,
              device.home_name && device.home_name !== '未知' ? device.home_name : null,
              online ? null : t('offline'),
            ]
              .filter(Boolean)
              .join(' · ') || t('deviceFallback'),
          ),
        ),
      );

      const offlineBanner = online
        ? null
        : h(
            'div',
            {
              style: {
                background: 'rgba(217,119,6,.1)',
                border: `1px solid rgba(217,119,6,.35)`,
                color: C.warn,
                borderRadius: C.radius,
                padding: '8px 12px',
                fontSize: 12,
                marginBottom: 12,
              },
            },
            t('offlineHint'),
          );

      if (loading) {
        return h('div', null, header, hero, h('div', { style: muted }, t('loading')));
      }

      if (problem) {
        return h(
          'div',
          null,
          header,
          hero,
          h(
            'div',
            { style: { ...card, padding: 18 } },
            h('div', { style: { fontSize: 14, fontWeight: 600, color: C.label } }, t('noSpec')),
            h('div', { style: { ...muted, marginTop: 6, lineHeight: '20px' } }, t('noSpecHint')),
            problem && !/公开的功能规格/.test(problem)
              ? h('div', { style: { ...muted, marginTop: 8 } }, problem)
              : null,
            powerProp
              ? h(
                  'div',
                  { style: { marginTop: 14 } },
                  h(
                    'button',
                    {
                      type: 'button',
                      disabled: busy || !online,
                      style: btn({
                        background: powerOn ? ORANGE : C.bgLayer1,
                        borderColor: powerOn ? ORANGE : C.border,
                        color: powerOn ? '#fff' : C.label,
                        padding: '8px 18px',
                      }),
                      onClick: () => runPower(!powerOn),
                      ...focusable,
                    },
                    powerOn ? t('powerOff') : t('powerOn'),
                  ),
                )
              : null,
          ),
        );
      }

      const powerBlock =
        typeof powerOn === 'boolean' || powerProp
          ? h(
              'div',
              {
                style: {
                  ...card,
                  padding: 18,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: 16,
                  background: powerOn ? 'rgba(255,106,0,.07)' : C.bgBase,
                  borderColor: powerOn ? 'rgba(255,106,0,.35)' : C.border,
                },
              },
              h(
                'div',
                null,
                h(
                  'div',
                  { style: { fontSize: 15, fontWeight: 600, color: C.label } },
                  t('power'),
                ),
                h(
                  'div',
                  { style: { ...muted, marginTop: 2 } },
                  powerOn ? t('powerOn') : t('powerOff'),
                ),
              ),
              h(
                'button',
                {
                  type: 'button',
                  disabled: busy || !online,
                  style: {
                    appearance: 'none',
                    border: 'none',
                    width: 64,
                    height: 64,
                    borderRadius: 32,
                    background: powerOn ? ORANGE : C.bgLayer2,
                    color: powerOn ? '#fff' : C.label2,
                    fontSize: 20,
                    cursor: busy || !online ? 'not-allowed' : 'pointer',
                    opacity: busy || !online ? 0.55 : 1,
                    outline: '2px solid transparent',
                  },
                  onClick: () => runPower(!powerOn),
                  ...focusable,
                  'aria-label': powerOn ? t('powerOff') : t('powerOn'),
                },
                h('svg', {
                  width: 26,
                  height: 26,
                  viewBox: '0 0 24 24',
                  fill: 'none',
                  style: { margin: '0 auto' },
                  'aria-hidden': true,
                }, h('path', {
                  d: 'M12 4v8',
                  stroke: powerOn ? '#fff' : C.label2,
                  strokeWidth: 2.2,
                  strokeLinecap: 'round',
                }), h('path', {
                  d: 'M7.4 6.6a7.2 7.2 0 1 0 9.2 0',
                  stroke: powerOn ? '#fff' : C.label2,
                  strokeWidth: 2.2,
                  strokeLinecap: 'round',
                  fill: 'none',
                })),
              ),
            )
          : null;

      const commonBlock = commonProps.length
        ? h(
            'div',
            { style: { ...card, padding: '4px 16px', marginTop: 14 } },
            h('div', { style: { ...sectionTitle, margin: '12px 0 0' } }, t('common')),
            commonProps.map((prop) =>
              h(
                'div',
                { key: prop.name, style: rowStyle },
                h(
                  'div',
                  { style: { minWidth: 0 } },
                  h('div', { style: { fontSize: 13, color: C.label } }, prop.desc || prop.name),
                ),
                h(PropControl, {
                  prop,
                  value: values?.[prop.name],
                  t,
                  disabled: busy || !online,
                  onWrite: write,
                }),
              ),
            ),
          )
        : null;

      const statusBlock = readonlyProps.length
        ? h(
            'div',
            { style: { ...card, padding: '4px 16px', marginTop: 14 } },
            h('div', { style: { ...sectionTitle, margin: '12px 0 0' } }, t('statusSection')),
            readonlyProps.map((prop) =>
              h(
                'div',
                { key: prop.name, style: rowStyle },
                h('div', { style: { fontSize: 13, color: C.label2 } }, prop.desc || prop.name),
                h(
                  'div',
                  { style: { fontSize: 13, color: C.label, textAlign: 'right' } },
                  valueText(prop, values?.[prop.name], t),
                ),
              ),
            ),
          )
        : null;

      const actionsBlock = actions.length
        ? h(
            'div',
            { style: { ...card, padding: '4px 16px', marginTop: 14 } },
            h('div', { style: { ...sectionTitle, margin: '12px 0 0' } }, t('actions')),
            h(
              'div',
              { style: { display: 'flex', flexWrap: 'wrap', gap: 8, padding: '10px 0' } },
              actions.map((action) =>
                h(
                  'button',
                  {
                    key: action.name,
                    type: 'button',
                    disabled: busy || !online,
                    style: btn({ opacity: busy || !online ? 0.55 : 1 }),
                    onClick: () => runAction(action.name),
                  },
                  action.desc || action.name,
                ),
              ),
            ),
          )
        : null;

      const advancedBlock = advancedProps.length
        ? h(
            'div',
            { style: { ...card, padding: '4px 16px', marginTop: 14 } },
            h(
              'button',
              {
                type: 'button',
                style: {
                  appearance: 'none',
                  border: 'none',
                  background: 'transparent',
                  width: '100%',
                  textAlign: 'left',
                  padding: '12px 0',
                  cursor: 'pointer',
                  fontSize: 13,
                  color: C.label2,
                  display: 'flex',
                  justifyContent: 'space-between',
                  outline: '2px solid transparent',
                },
                onClick: () => setShowAdvanced((v) => !v),
                ...focusable,
              },
              h('span', null, t('advanced')),
              h('span', null, showAdvanced ? '▾' : '▸'),
            ),
            showAdvanced
              ? advancedProps.map((prop) =>
                  h(
                    'div',
                    { key: prop.name, style: rowStyle },
                    h(
                      'div',
                      { style: { fontSize: 12, color: C.label2, minWidth: 0 } },
                      prop.desc || prop.name,
                    ),
                    prop.writable
                      ? h(PropControl, {
                          prop,
                          value: values?.[prop.name],
                          t,
                          disabled: busy || !online,
                          onWrite: write,
                        })
                      : h(
                          'div',
                          { style: { fontSize: 12, color: C.label, textAlign: 'right' } },
                          valueText(prop, values?.[prop.name], t),
                        ),
                  ),
                )
              : null,
          )
        : null;

      const emptyBlock =
        !commonProps.length && !readonlyProps.length && !advancedProps.length && !actions.length
          ? h('div', { style: { ...card, padding: 18, marginTop: 14, ...muted } }, t('noProps'))
          : null;

      return h(
        'div',
        null,
        header,
        hero,
        offlineBanner,
        powerBlock,
        commonBlock,
        statusBlock,
        actionsBlock,
        advancedBlock,
        emptyBlock,
        message
          ? h(
              'div',
              {
                style: {
                  marginTop: 12,
                  fontSize: 12,
                  color: message.startsWith(t('errorPrefix')) ? C.error : C.success,
                },
              },
              message,
            )
          : null,
      );
    }

    /* ---------------------------------------------------------------- 面板 */

    function Panel({ t }) {
      const [status, setStatus] = useState(null);
      const [ready, setReady] = useState(false);
      const [error, setError] = useState('');

      const [devices, setDevices] = useState([]);
      const [homes, setHomes] = useState([]);
      const [icons, setIcons] = useState({});
      const [powers, setPowers] = useState({});
      const [scenes, setScenes] = useState([]);
      const [consumables, setConsumables] = useState({ items: [], low_count: 0 });
      const [busyScene, setBusyScene] = useState('');
      const [toast, setToast] = useState('');
      const [openDevice, setOpenDevice] = useState(null);
      // 顶部页签：对齐米家 App 的「设备 / 场景」分区（耗材在 App 里挂在设备详情里，
      // 这里单独给一页，因为它跨设备、放在首页列表底下会把设备区压得很长）。
      const [tab, setTab] = useState('devices');

      const load = useCallback(async () => {
        setError('');
        setReady(false);
        let state = null;
        try {
          state = await getJSON('status');
          setStatus(state);
        } catch (err) {
          setError(err?.body?.error ?? err.message ?? String(err));
          setReady(true);
          return;
        }
        if (!state?.logged_in) {
          setReady(true);
          return;
        }
        const [dev, home, scene, cons] = await Promise.all([
          getJSON('devices').catch(() => ({ devices: [] })),
          getJSON('homes').catch(() => ({ homes: [] })),
          getJSON('scenes').catch(() => ({ scenes: [] })),
          getJSON('consumables').catch(() => ({ items: [], low_count: 0 })),
        ]);
        const list = Array.isArray(dev.devices) ? dev.devices : [];
        setDevices(list);
        setHomes(Array.isArray(home.homes) ? home.homes : []);
        setScenes(Array.isArray(scene.scenes) ? scene.scenes : []);
        setConsumables({
          items: Array.isArray(cons.items) ? cons.items : [],
          low_count: Number(cons.low_count ?? 0),
        });

        // 产品图：一次批量问，失败就用轮廓图兜底
        const models = Array.from(new Set(list.map((d) => d.model).filter(Boolean)));
        if (models.length) {
          try {
            const iconData = await getJSON(`icons?models=${encodeURIComponent(models.join(','))}`);
            setIcons(iconData.icons ?? {});
          } catch {
            setIcons({});
          }
        }

        // 电源状态：批量取，用于磁贴上的「已开启 / 已关闭」
        const dids = list.map((d) => d.did).filter(Boolean);
        if (dids.length) {
          try {
            const powerData = await getJSON(`power?dids=${encodeURIComponent(dids.join(','))}`);
            setPowers(powerData.states ?? {});
          } catch {
            setPowers({});
          }
        }

        setReady(true);
      }, []);

      useEffect(() => {
        load();
      }, [load]);

      const togglePower = useCallback(
        async (device, state) => {
          try {
            const result = await postJSON('control', {
              did: device.did,
              mode: 'power',
              state,
            });
            setPowers((prev) => ({ ...(prev ?? {}), [device.did]: Boolean(result.on) }));
            setToast(t('done'));
            setTimeout(() => setToast(''), 1600);
          } catch (err) {
            setToast(`${t('errorPrefix')}${err?.body?.error ?? err.message ?? err}`);
            setTimeout(() => setToast(''), 3000);
          }
        },
        [t],
      );

      const runScene = async (scene) => {
        setBusyScene(scene.scene_id);
        try {
          await postJSON('scene', {
            scene_id: scene.scene_id,
            home_id: scene.home_id,
            name: scene.name,
          });
          setToast(t('done'));
        } catch (err) {
          setToast(`${t('errorPrefix')}${err?.body?.error ?? err.message ?? err}`);
        } finally {
          setBusyScene('');
          setTimeout(() => setToast(''), 2000);
        }
      };

      const header = h(
        'div',
        {
          style: {
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 12,
            paddingBottom: 12,
            borderBottom: `1px solid ${C.border}`,
            ...stickyHeader(),
          },
        },
        h(
          'div',
          null,
          h('div', { style: { fontSize: 18, fontWeight: 600, color: C.label } }, t('title')),
          status?.logged_in && devices.length
            ? h(
                'div',
                { style: { ...muted, marginTop: 2 } },
                `${devices.length} ${t('ovDevices')} · ${devices.filter((d) => d.online !== false).length} ${t('ovOnline')}`,
              )
            : null,
        ),
        h(
          'button',
          { type: 'button', style: btn({ background: C.bgLayer1 }), onClick: load },
          t('refresh'),
        ),
      );

      if (!ready) {
        return h(
          'div',
          { style: PAGE },
          header,
          h('div', { style: { ...muted, marginTop: 16 } }, t('loading')),
        );
      }

      if (error) {
        return h(
          'div',
          { style: PAGE },
          header,
          h(
            'div',
            { style: { ...card, padding: 18, marginTop: 16 } },
            h('div', { style: { fontSize: 14, color: C.error } }, `${t('errorPrefix')}${error}`),
            h(
              'button',
              { type: 'button', style: btn({ marginTop: 12 }), onClick: load },
              t('retry'),
            ),
          ),
        );
      }

      if (!status?.logged_in) {
        return h(
          'div',
          { style: PAGE },
          header,
          h('div', { style: { marginTop: 16 } }, h(LoginCard, { t, onLoggedIn: load, onError: () => {} })),
        );
      }

      if (openDevice) {
        return h(
          'div',
          { style: PAGE },
          h(DeviceDetail, {
            device: openDevice,
            t,
            icons,
            onBack: () => setOpenDevice(null),
            onError: () => {},
            onChanged: load,
          }),
        );
      }

      /* ---- 首页：按家庭 / 房间分区 ---- */

      const byDid = new Map(devices.map((d) => [String(d.did), d]));
      const used = new Set();
      const sections = [];

      for (const home of homes) {
        const homeRooms = Array.isArray(home.rooms) ? home.rooms : [];
        const roomBlocks = [];
        for (const room of homeRooms) {
          const list = (room.dids ?? [])
            .map((did) => byDid.get(String(did)))
            .filter(Boolean)
            .filter((d) => {
              if (used.has(String(d.did))) return false;
              used.add(String(d.did));
              return true;
            });
          if (list.length) roomBlocks.push({ key: `r-${room.id}`, title: room.name, list });
        }
        // 家庭直属设备（路由器等没分房间的）
        const loose = (home.dids ?? [])
          .map((did) => byDid.get(String(did)))
          .filter(Boolean)
          .filter((d) => {
            if (used.has(String(d.did))) return false;
            used.add(String(d.did));
            return true;
          });
        sections.push({
          key: `h-${home.id}`,
          title: home.name,
          rooms: roomBlocks,
          loose,
        });
      }

      // 完全没落到任何家庭/房间里的设备也别忘了显示
      const orphans = devices.filter((d) => !used.has(String(d.did)));
      if (orphans.length) {
        sections.push({ key: 'orphans', title: t('otherDevices'), rooms: [], loose: orphans });
      }

      const grid = (list) =>
        h(
          'div',
          {
            style: {
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))',
              gap: 10,
              marginTop: 10,
            },
          },
          list.map((device) =>
            h(DeviceTile, {
              key: device.did,
              device,
              t,
              icons,
              power: powers[device.did],
              onOpen: setOpenDevice,
              onTogglePower: togglePower,
            }),
          ),
        );

      const deviceSections = sections.length
        ? sections.map((section) =>
            h(
              'div',
              { key: section.key },
              h('div', { style: { ...sectionTitle, marginBottom: 0 } }, section.title),
              section.rooms.map((room) =>
                h(
                  'div',
                  { key: room.key },
                  h(
                    'div',
                    { style: { ...muted, marginTop: 12, fontWeight: 600, color: C.label2 } },
                    room.title,
                  ),
                  grid(room.list),
                ),
              ),
              section.loose.length ? grid(section.loose) : null,
            ),
          )
        : h(
            'div',
            { style: { ...card, padding: 20, marginTop: 20 } },
            h('div', { style: { fontSize: 14, fontWeight: 600, color: C.label } }, t('noDevices')),
            h('div', { style: { ...muted, marginTop: 6 } }, t('noDevicesHint')),
          );

      /* ---- 场景页：对齐米家 App「场景」标签（大按钮 + 一步执行 + 怎么新增） ---- */

      const sceneRow = (scene) => {
        const running = busyScene === scene.scene_id;
        return h(
          'button',
          {
            key: scene.scene_id,
            type: 'button',
            disabled: running,
            onClick: () => runScene(scene),
            ...focusable,
            style: {
              ...card,
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              width: '100%',
              padding: '14px 16px',
              textAlign: 'left',
              cursor: running ? 'progress' : 'pointer',
              opacity: running ? 0.72 : 1,
              outline: '2px solid transparent',
            },
          },
          h(
            'span',
            {
              style: {
                flex: '0 0 auto',
                width: 34,
                height: 34,
                borderRadius: 17,
                background: running ? ORANGE : 'rgba(255,106,0,.12)',
                color: running ? '#fff' : ORANGE,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              },
            },
            h(
              'svg',
              { width: 16, height: 16, viewBox: '0 0 24 24', 'aria-hidden': true },
              h('path', {
                d: 'M8 5.5v13l11-6.5z',
                fill: 'currentColor',
                stroke: 'currentColor',
                strokeWidth: 1.2,
                strokeLinejoin: 'round',
              }),
            ),
          ),
          h(
            'span',
            { style: { minWidth: 0, flex: '1 1 auto' } },
            h(
              'span',
              {
                style: {
                  display: 'block',
                  fontSize: 14,
                  fontWeight: 500,
                  color: C.label,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                },
              },
              scene.name || t('run'),
            ),
            h(
              'span',
              { style: { ...muted, display: 'block', marginTop: 2 } },
              running ? t('running') : scene.home_name || t('scenes'),
            ),
          ),
          h(
            'span',
            { style: { flex: '0 0 auto', fontSize: 12, color: running ? ORANGE : C.label3 } },
            running ? '···' : t('run'),
          ),
        );
      };

      const howToCard = (title, body) =>
        h(
          'div',
          {
            style: {
              ...card,
              padding: 16,
              marginTop: 14,
              background: C.bgLayer1,
            },
          },
          h('div', { style: { fontSize: 13, fontWeight: 600, color: C.label2 } }, title),
          h('div', { style: { ...muted, marginTop: 6, lineHeight: '20px' } }, body),
        );

      const scenesBlock = h(
        'div',
        null,
        scenes.length
          ? h(
              'div',
              null,
              h(
                'div',
                { style: { ...muted, marginTop: 12 } },
                `${scenes.length} ${t('sceneCount')} · ${t('sceneHint')}`,
              ),
              h(
                'div',
                { style: { display: 'flex', flexDirection: 'column', gap: 10, marginTop: 12 } },
                scenes.map(sceneRow),
              ),
              howToCard(t('sceneHowToTitle'), t('sceneHowTo')),
            )
          : h(
              'div',
              { style: { marginTop: 12 } },
              h(
                'div',
                { style: { ...card, padding: 20 } },
                h(
                  'div',
                  { style: { fontSize: 15, fontWeight: 600, color: C.label } },
                  t('noScenes'),
                ),
                h('div', { style: { ...muted, marginTop: 6, lineHeight: '20px' } }, t('noScenesHint')),
              ),
              howToCard(t('sceneHowToTitle'), t('sceneHowTo')),
            ),
      );

      /* ---- 耗材页：对齐米家 App「设备详情 → ⋮ → 耗材管理」 ---- */

      const percentOf = (item) => {
        const value = Number(item?.value);
        if (!Number.isFinite(value)) return null;
        // 米家耗材值是「剩余寿命百分比」，个别机型给 0~1 的小数，统一成 0~100。
        return Math.max(0, Math.min(100, value <= 1 && value > 0 ? value * 100 : value));
      };

      const consumableRow = (item, index) => {
        const percent = percentOf(item);
        const low = Boolean(item.low);
        const colour = low ? C.warn : C.success;
        return h(
          'div',
          {
            key: `${item.did}-${item.type}-${index}`,
            style: { padding: '12px 0', borderTop: index === 0 ? 'none' : `1px solid ${C.border}` },
          },
          h(
            'div',
            { style: { display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12 } },
            h(
              'div',
              { style: { minWidth: 0 } },
              h(
                'div',
                { style: { fontSize: 13, fontWeight: 500, color: C.label } },
                item.description || item.type || item.name || t('consumables'),
              ),
              h(
                'div',
                { style: { ...muted, marginTop: 2 } },
                [item.name || item.did, item.updated ? `${t('consumableUpdated')} ${formatTime(item.updated)}` : '']
                  .filter(Boolean)
                  .join(' · '),
              ),
            ),
            h(
              'div',
              {
                style: {
                  flex: '0 0 auto',
                  fontSize: 13,
                  fontWeight: 600,
                  color: colour,
                },
              },
              percent === null ? '—' : `${Math.round(percent)}%`,
            ),
          ),
          // 剩余寿命条：一眼看出还剩多少，不用读数字
          h(
            'div',
            {
              style: {
                marginTop: 8,
                height: 6,
                borderRadius: 3,
                background: C.bgLayer2,
                overflow: 'hidden',
              },
            },
            h('div', {
              style: {
                width: `${percent === null ? 0 : percent}%`,
                height: '100%',
                borderRadius: 3,
                background: colour,
                transition: 'width .2s ease',
              },
            }),
          ),
          h(
            'div',
            { style: { ...muted, marginTop: 6 } },
            [
              low ? t('needsReplace') : null,
              item.threshold !== null && item.threshold !== undefined
                ? `${t('consumableThreshold')} ${displayValue(item.threshold, t)}`
                : '',
            ]
              .filter(Boolean)
              .join(' · '),
          ),
        );
      };

      const consumablesBlock = consumables.items.length
        ? h(
            'div',
            null,
            h(
              'div',
              { style: { ...muted, marginTop: 12 } },
              consumables.low_count
                ? `${consumables.low_count} ${t('lowCount')} · ${t('consumableHint')}`
                : t('consumableHint'),
            ),
            h(
              'div',
              { style: { ...card, padding: '4px 16px', marginTop: 12 } },
              consumables.items.map(consumableRow),
            ),
            howToCard(t('consumableHowToTitle'), t('consumableHowTo')),
          )
        : h(
            'div',
            { style: { marginTop: 12 } },
            h(
              'div',
              { style: { ...card, padding: 20 } },
              h(
                'div',
                { style: { fontSize: 15, fontWeight: 600, color: C.label } },
                t('consumableAllGood'),
              ),
              h(
                'div',
                { style: { ...muted, marginTop: 6, lineHeight: '20px' } },
                t('consumableAllGoodHint'),
              ),
            ),
            howToCard(t('consumableHowToTitle'), t('consumableHowTo')),
          );

      /* ---- 顶部页签 ---- */

      const tabBar = h(
        'div',
        {
          role: 'tablist',
          style: {
            display: 'flex',
            gap: 6,
            marginTop: 12,
            padding: 3,
            background: C.bgLayer1,
            borderRadius: C.radius,
          },
        },
        [
          { id: 'devices', label: t('tabDevices'), badge: devices.length },
          { id: 'scenes', label: t('tabScenes'), badge: scenes.length },
          { id: 'consumables', label: t('consumables'), badge: consumables.low_count || 0 },
        ].map((entry) =>
          h(
            'button',
            {
              key: entry.id,
              type: 'button',
              role: 'tab',
              'aria-selected': tab === entry.id,
              onClick: () => setTab(entry.id),
              ...focusable,
              style: {
                flex: '1 1 0',
                appearance: 'none',
                border: 'none',
                borderRadius: C.radius,
                padding: '7px 10px',
                fontSize: 13,
                fontWeight: tab === entry.id ? 600 : 400,
                color: tab === entry.id ? C.label : C.label2,
                background: tab === entry.id ? C.bgBase : 'transparent',
                boxShadow: tab === entry.id ? `0 1px 2px rgba(0,0,0,.06)` : 'none',
                cursor: 'pointer',
                outline: '2px solid transparent',
              },
            },
            entry.label,
            entry.badge
              ? h(
                  'span',
                  {
                    style: {
                      marginLeft: 4,
                      fontSize: 11,
                      color: entry.id === 'consumables' ? C.warn : C.label3,
                    },
                  },
                  entry.id === 'consumables' && entry.badge ? `· ${entry.badge}` : `· ${entry.badge}`,
                )
              : null,
          ),
        ),
      );

      return h(
        'div',
        { style: PAGE },
        header,
        tabBar,
        tab === 'devices'
          ? h(
              'div',
              null,
              h('div', { style: { ...muted, marginTop: 10 } }, t('homeHint')),
              deviceSections,
            )
          : null,
        tab === 'scenes' ? scenesBlock : null,
        tab === 'consumables' ? consumablesBlock : null,
        toast
          ? h(
              'div',
              {
                style: {
                  position: 'sticky',
                  bottom: 12,
                  marginTop: 14,
                  padding: '8px 14px',
                  background: C.bgLayer2,
                  border: `1px solid ${C.border}`,
                  borderRadius: C.radius,
                  fontSize: 12,
                  color: toast.startsWith(t('errorPrefix')) ? C.error : C.success,
                  display: 'inline-block',
                },
              },
              toast,
            )
          : null,
      );
    }

    /* -------------------------------------------------------------- 注册 */

    function Shell({ t }) {
      return h(Panel, { t });
    }

    return {
      inject: ['slots', 'locale'],
      apply(ctx) {
        let t = (key) => key;
        const canLocale =
          ctx.locale &&
          typeof ctx.locale.register === 'function' &&
          typeof ctx.locale.bind === 'function';
        if (canLocale) {
          ctx.effect(() => ctx.locale.register(NS, DICT), 'dsh-mihome: dictionary');
          t = ctx.locale.bind(NS);
        } else {
          // 没有 locale 服务时也要能显示：按当前语言直取字典
          const lang =
            typeof navigator !== 'undefined' && String(navigator.language || '').startsWith('en')
              ? 'en'
              : 'zh';
          t = (key) => DICT[lang]?.[key] ?? DICT.zh[key] ?? key;
        }

        ctx.effect(() => {
          const offs = [];
          // 侧栏一行
          try {
            offs.push(
              ctx.slots.inject('sidebar.panellist', () =>
                ctx.slots.register(
                  {
                    name: 'sidebar.panellist',
                    id: PANEL_ID,
                    order: 8,
                    label: () => t('nav'),
                    locale: NS,
                  },
                  (owner = {}) =>
                    h(MihomeIcon, { size: owner.size ?? 16, active: Boolean(owner.active) }),
                ),
              ),
            );
          } catch (error) {
            console.warn('[dsh-mihome] sidebar row unavailable:', error?.message);
          }
          // 同名全局面板
          try {
            offs.push(
              ctx.slots.inject('main', () =>
                ctx.slots.register({ name: 'main', key: PANEL_ID, locale: NS }, () =>
                  h(Shell, { t }),
                ),
              ),
            );
          } catch (error) {
            console.warn('[dsh-mihome] main panel registration failed:', error?.message);
          }

          return () => {
            for (const off of offs) {
              if (typeof off === 'function') {
                try {
                  off();
                } catch {
                  /* noop */
                }
              }
            }
          };
        }, 'dsh-mihome: workbench panel');
      },
    };
  },
});
