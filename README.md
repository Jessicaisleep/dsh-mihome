# dsh-mihome

DeepSeek Harness 插件：通过 [mijiaAPI](https://github.com/Do1e/mijia-api) 控制小米米家设备。

插件本体是 Node（Cordis 插件），米家协议栈是 Python。两者之间是一个常驻的
JSON-Lines 桥接进程：Node 侧往 stdin 写一行请求，Python 侧从 stdout 回一行响应。

选这个形状的原因是米家登录（账号扫码、token 刷新、设备 spec 解析、属性加密）
已经有成熟实现在 mijiaAPI 里，在 Node 里重写等于重做整套协议；而 DSH 插件生态
是 Node 的。桥接只用 `app.core.service` 一层——上游的 `app.ui` 与
`app.core.jobs` 都 import PySide6，但米家控制本身不需要 Qt，所以插件不必拖
200 MB 的 Qt 运行时。

## 侧栏工作台

侧栏「全局面板」里有一个**米家**入口（`sidebar.panellist` 一行 + 同名 `main` 面板）。
面板打开后按 家庭 / 房间 分组列出设备卡：点开卡片能看属性、改枚举/范围/开关、
执行动作、切电源；顶部还有场景按钮与耗材汇总；未登录时面板里直接出二维码扫码登录。

前端在 `client.js`，后端在 `lib/panel.js`（前缀 `/dsh-mihome/api`），两者都通过桥接
进程取数。登录长轮询最长约 2 分钟，会独占一个桥接进程，所以面板用了**第二个独立桥接
`loginBridge`**——否则扫码期间设备列表和开关全排队超时。

## 目录

| 路径 | 内容 |
| --- | --- |
| `lib/` | `index.js`（工具注册）、`bridge.js`（JSON-Lines 客户端）、`panel.js`（Web 路由）、`format.js`（文本渲染） |
| `client.js` | Web UI 面板（`window.__ModuleLoader__.load`） |
| `python/` | MiHome-Windows 源码副本 + `mihome_bridge.py`（桥接入口） |
| `_upstream/` | MiHome-Windows 的原始 git 克隆，供对照与升级 |
| `_probe/` | 开发期登录/二维码探针脚本，运行时不使用 |
| `locale/` | `zh.json` / `en.json`（面板标题与描述） |
| `tests/` | `bridge.test.js`（`node --test`） |
| `data/` | 运行时数据：`qr/` 下的二维码 PNG |

## 提供的工具

| 工具 | 作用 |
| --- | --- |
| `mihome_login` | `start` 出二维码 / `poll` 等扫码结果 / `status` 查登录态 |
| `mihome_devices` | 列出全部设备（did、名称、型号、家庭、房间、在线状态） |
| `mihome_device` | 单台设备规格：可读写属性、取值范围、可执行动作 |
| `mihome_control` | `read` / `write` / `action` / `power` 四种模式控制设备 |
| `mihome_scenes` | `list` 列出场景 / `run` 执行场景 |
| `mihome_sensors` | `metrics` 读温湿度等外显值 / `consumables` 汇总耗材余量 |

`did` 是后续所有操作的键，从 `mihome_devices` 拿。

## 安装

插件目录需要一份 Python 虚拟环境和两个依赖（`python/` 是 MiHome-Windows 的源码副本）：

```powershell
cd <插件目录>
python -m venv .venv
.venv\Scripts\python.exe -m pip install "mijiaAPI>=5,<6" "qrcode>=8"
```

然后接入 profile：在 `<profile>/node_modules` 下建指向本目录的符号链接，
并在 `<profile>/package.json` 的 `dependencies` 与 `dsh.profile.bundles` 里各加一条：

```json
"dsh-mihome": "link:E:/DSHWORK/DSH插件/dsh-mihome"
```

## 登录

米家凭据由 mijiaAPI 存在 `~/.config/mijia-api/auth.json`（与 mijiaAPI 命令行共用）。
插件不复制也不改写它。第一次用 `mihome_login(action=start)` 出二维码，手机米家 APP
扫码确认后 `action=poll` 收结果。

二维码同时给两种形态：字符二维码（纯文本会话里直接能扫）和 PNG 文件
（路径随结果返回，落在 `<dataDir>/qr/mihome-login-<ticket前16位>-<毫秒>.png`）。

**文件名必须带 ticket 和毫秒**：早期版本写固定文件名，GUI 按路径缓存，用户反复扫到
第一张早就过期的码，现象就是「一生成就过期」。另一个必要条件是**生成后立刻挂长轮询**，
不能等用户说"扫了"才 poll。

## 配置

在 profile 的 `cordis.patch.yml` 里覆盖 `<profile 的 bundle patch 行>`：

```yaml
- id: mihome
  config:
    pythonPath: ''      # 留空 = 插件自带 .venv
    dataDir: ''         # 留空 = <插件目录>/data
    requestTimeoutMs: 30000
    loginTimeoutMs: 150000
```

## 测试

```powershell
# 桥接协议与错误分类，不需要米家账号（未登录本身就是常态）
node --test tests/bridge.test.js

# 校验 client.js 的 i18n：zh/en 键集合一致、用到的键都存在
node scripts/check-i18n.mjs client.js

# 无浏览器渲染冒烟：把面板渲染成 HTML 并断言关键文案
node scripts/render-smoke.mjs client.js
```

`render-smoke` 用最小 React 替身 + 假 fetch 在 `vm` 里跑 `client.js`，覆盖首页磁贴、
设备详情页、场景页、耗材页四处，是改前端后唯一能在无浏览器环境里验证的闸门。
产物写到 `scripts/.out/`。

## 许可

插件代码 GPL-3.0-or-later；`python/` 目录是 [MiHome-Windows](https://github.com/huanyuejue/MiHome-Windows)
的源码副本，同样 GPL-3.0-or-later，必须保持同许可。米家协议实现来自
[mijiaAPI](https://github.com/Do1e/mijia-api)。
