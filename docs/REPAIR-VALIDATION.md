# 全屋智能修复与验证记录

验证日期：2026-11-22。对象：本地工作树中的 `@jessicaisleep/dsh-mihome` 2.0.0，不代表 npm/GitHub 已发布版本。

## 当前结果

插件提供米家云端、Home Assistant 实体和已适配本地协议的统一入口。主路径是“发现或手动配置 → 获取实际能力 → 按原始 key 控制 → 状态回读/逐步场景结果”。没有能力证据时不生成可写控件，不把型号、分类或厂商名称当作控制规格。

本轮修复解决了审计中已复现的失败误报、场景计数、参数丢失、发现与身份、私有连接接线、取消/进程重启、来源校验和界面状态等问题。**尚不能声称直接控制所有品牌、型号和固件，或已经复刻全部官网 APP 功能。** 各协议的条件见 README 的支持矩阵。

## 需求对照

| 需求 | 实现与证据 | 剩余条件 |
| --- | --- | --- |
| 一个插件管理全屋设备 | 保留米家入口；增加 HA 网关/实体、私有连接和本地协议；多个 HA 实例同名实体互相隔离 | 非米家生态推荐先用相应 HA 集成接入；具体家电能力由集成暴露的实体决定 |
| 局域网扫描、BLE 等多种发现 | ARP、受限 ping、TCP/HTTP、SSDP、mDNS、Tuya/miIO UDP、BLE；修复空候选提前退出、无 TCP 主机丢失、字符串参数、蓝牙 ID 冲突和跨协议合并 | 只支持 IPv4 扫描；隔离网络、休眠/不广播设备不保证发现；BLE 发现不等于私有 GATT 控制 |
| 可靠控制和真实反馈 | 严格传播 `ok:false`；米家仅整数 `code=0` 确认，`code=1` 保持未确认；未知电源不显示为关机；动作 JSON 原类型保留 | HTTP/协议确认不保证物理动作完成；需要状态回读及型号实机验收 |
| 中文能力与选项 | 保留 canonical key、类型、权限、范围、单位、枚举、动作 schema；官方规格/协议/平台术语/通用翻译；IEEE OUI 可重建 | 未知术语保留原文；上游米家规格的同名属性及单位缺失仍受上游模型限制 |
| 私有设备连接 | 面板保存到 DSH `mihome/devices` 凭据记录；只返回 ID/配置状态；Node→Python 私有传递，不进入公共列表 | 用户须在面板提供有效凭据、地址和显式映射；不会从聊天工具收集密钥 |
| 自定义场景 | 创建、编辑、删除、顺序执行；失败后继续，逐步显示结果；原子保存，损坏 JSON 不静默覆盖 | 当前是手动执行，没有自动到家、定时或事件触发引擎 |
| 路由器/交换机/光猫/NAS | 公开管理页读取；HTTP 失败保留失败；不把登录页读取当作设置写入 | 未实现通用认证、超管登录及设置编辑；已移除未经核实的重启/登录命令 |
| README/市场介绍中文在前 | README 中文完整在前、English 在后；包描述与 locale 按真实能力说明 | 未发布到市场，因此外部已发布页面尚未更新 |

## 已执行的验证

在项目根目录执行；均未向真实家电发送控制命令。

| 命令或检查 | 最新结果 | 证明范围 |
| --- | --- | --- |
| `node --test` | 80 tests，80 pass，0 fail/skip/cancel（约 4.45 秒） | Node bridge、来源/JSON 校验、私有连接、工具、UI 行为、formatter、localhost E2E、operation/device timeout 分类、进程退出输出排空、子进程替换竞争和旧米家路由输入校验 |
| `.venv\Scripts\python.exe -m unittest discover -s python/tests -q` | 109 tests，OK（约 2.13 秒） | Python manager/bridge、电源状态回读和米家确认、发现、协议和 HA 模拟契约 |
| 四个 `python/smart_home/test_*.py` 自检 | 全部通过，localization 14 tests | 旧离线回归、核心修复、发现、本地化 |
| `.venv\Scripts\python.exe -m pyflakes python/smart_home python/mihome_bridge.py` | 无诊断，exit 0 | 本轮生产 Python 模块静态检查；不声称整个上游 UI 测试目录零告警 |
| `.venv\Scripts\python.exe -m pip check` | No broken requirements found | 当前虚拟环境依赖一致性 |
| `node --check` 六个生产 JS 文件 | 全部 exit 0 | client、index、panel、bridge、connections、format 语法 |
| `node scripts/check-i18n.mjs client.js` | zh/en 134/134，无缺失或未定义 key | 字典一致性；不等于翻译语义逐设备验收 |
| `node scripts/render-smoke.mjs client.js` | 六条页面路径通过，无 undefined/NaN/[object Object] | fake React/hooks/HTTP 的模拟渲染契约；显式切换全屋与米家，检查两端列表、详情和米家场景/耗材；不是浏览器视觉验收 |
| 实际 DSH Electron 运行 `scripts/verify-dsh-tools.cjs` | Node 24.21.0；13 tools；scanSchema、objectAndArrayActionParams、cancellation passed | 使用真实 DSH defineTool/schemastery/credentialKey，mock Python transport 和 Host ctx；不是真实 Host 挂载 |
| `npm pack --dry-run --json` | 必需运行文件齐全，无 `.venv`、运行数据、认证文件或 `_probe` 入包 | 包清单检查，不代表已经发布 |
| `git diff --check` | exit 0 | 工作区补丁空白检查；保留既有 service.py CRLF，Git 有 EOL 提示 |

`tests/whole-home-e2e.test.js` 使用本机临时 HTTP 服务和临时令牌，经过真实 Node Bridge → JSON-Lines Python → SmartHomeManager → HA REST。它验证实体展开、电源控制/状态回读、场景两步中一步 HTTP 503 时汇总 `ok:false, success:1, total:2`，以及秘密不进入结果。子进程网络 guard 禁止非回环访问；这不是家中 HA 或真实家电测试。

回归测试曾在修复前观察到对应红灯，包含失败被包装成功、错误场景计数、字符串子网逐字符解析、空参数容器被转换为 None、旧进程影响新请求、未知电源显示关闭及键盘冒泡等；修复后分别和完整套件复测。最终 UI 复核又补测了设备详情读取错误被误说成“无控制项”，以及打开设备时连接状态误用初始空 ID 的闭包问题；现在显式呈现后端能力/状态诊断，连接状态从私有配置 ID 列表按当前设备派生，HA 子实体也识别所属网关的继承配置。最后控制复核还发现协程取消不能停止同步调用或后台线程；新增真实子进程文件 gate 测试，确认 Python 操作预算耗尽返回 `operation_timeout`/结果未知、进程退出、阻塞本地写入及排队请求均不继续，下一请求启动新进程。另覆盖同步调用和 `asyncio.to_thread` 中由设备自身抛出的 `TimeoutError`：它作为设备级 I/O 错误返回，不终止桥接、不会标成 operation timeout，后续请求仍由同一进程服务。测试还覆盖进程退出后的 stdout 排空、退出与新进程启动重叠，以及旧请求定时器不得杀掉 replacement。测试的外部写入均用本地文件替代。

## 未执行的验收与明确边界

- 未安装或启用此工作树到当前 DSH profile，未提交、推送或发布。现有未提交成果保留在工作区。
- 未在真实设备/固件上执行控制；模拟报文和本机服务器不能替代实机兼容性验收。
- DSH Host URL 的 WCU 浏览器访问被策略拒绝（`WCU_POLICY_DENIED: this address belongs to the DSH host.`）；未绕过，也未把 mock React 画面当作真实 UI 验收。真实 Host 挂载、明暗主题、窄屏布局和运行时控制台仍需在可用环境验收。
- 独立 UI/UX 复核发现的问题已按用例修复，界面行为测试仍使用测试 harness，不证明浏览器布局或辅助技术实际朗读。
- 原生 LIFX、Samsung/LG/Vizio、ESPHome 伪 REST、ONVIF 重启、未知网络设备写入没有经过可靠实现，当前禁用；可由合适的 HA 集成补充。
- 取消/超时会结束共享桥接；其他在途请求标记为结果未知。已经发送的远端命令不能撤回，已开始的外部请求也不因协程取消自动回滚。重试前先读状态。

## 外部经验与来源

- React 键盘事件会冒泡。复合设备磁贴仅在 `event.target === event.currentTarget` 时处理自己的 Enter/Space，避免内层电源按钮触发父磁贴导航。[React 事件传播](https://react.dev/learn/responding-to-events#stopping-propagation)
- 选项按钮用 `aria-pressed` 暴露选择状态；未知布尔状态提供明确开/关操作，不伪造 `aria-checked=false`。[MDN aria-pressed](https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA/Reference/Attributes/aria-pressed)
- HTTP 成功按 200–299 判断，401/403 不当作已读出认证设置。[MDN HTTP 状态](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status)
- 同源包含 scheme、host 和 port；面板 POST 同时检查 Origin 与 JSON Content-Type。[MDN 同源策略](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Same-origin_policy)
- `run_coroutine_threadsafe().result(timeout)` 超时不会自动取消 coroutine；`future.cancel()` 也不能停止正在运行的同步调用或 executor 线程。本插件将操作超时标为终止性错误，退出专用 Python 进程并使排队请求失效；远端已发送的命令仍不能撤回。[Python asyncio](https://docs.python.org/3/library/asyncio-task.html#asyncio.run_coroutine_threadsafe) / [Future.cancel](https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.Future.cancel)
- miIO UDP/AES-CBC、HA REST/实例发现、TinyTuya、Shelly、Yeelight、Hue、Tasmota/WLED、Bleak/zeroconf 与 IEEE 的依据和协议条件集中维护在 README 的资料来源及支持矩阵，不把社区经验表述成所有厂商的官方承诺。
