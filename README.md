# dsh-mihome · 全屋智能工作台

一个 DSH 插件，在同一工作台发现设备、查看实际能力、执行控制并组合自定义场景。名称沿用 `dsh-mihome`；现提供米家云端、Home Assistant 实体及部分本地协议的统一入口。

**推荐接入路径：米家设备直接登录；其他生态优先接入 Home Assistant，再由本插件按实体实际能力控制。** 无需网关的已支持本地协议也可以直接连接。扫描发现不等于已经配对，设备型号和类别也不能证明它支持某项操作。

## 中文

### 当前支持矩阵

| 接入方式 | 已实现的控制/读取 | 条件与边界 |
| --- | --- | --- |
| 米家云端 | 设备、属性读写、规格动作、米家场景、电源及耗材 | 需要扫码登录；写入需米家设备响应明确 code=0；code=1 仅表示网关接收、执行结果未知 |
| Home Assistant | 灯、插座/开关、风扇、空调、窗帘、媒体播放器、扫地机、锁、加湿器、选择器、数值、按钮、场景/脚本和传感器 | 需要 HA 地址及长期访问令牌；依实体状态、服务和 feature 标记生成控件；冰箱、洗衣机、烘干机等取决于其 HA 集成实际暴露的实体 |
| Tuya 局域网 | 显式 DP 映射的读取和写入 | 需要 device ID、本地密钥、协议版本和经过核实的 DP 规格；不把 DP1 猜成开关 |
| Shelly | Gen2 switch/cover；Gen1 已观察到的 relay，roller 位置只读 | 根据返回的组件生成能力；当前仅默认组件 ID 0，Gen1 需配置 `shelly_gen: 1` |
| 传统 TP-Link Kasa | 有 `relay_state` 的设备开关 | 传统 9999 协议；新加密 Tapo/Kasa 和插排子设备未覆盖 |
| Yeelight | 设备报告支持的方法对应的电源、亮度、RGB/色温 | 需要 LAN 控制；能力依 `support` 列表，色温范围需核实配置 |
| Philips Hue | 指定灯具实际状态对应的开关、亮度、颜色；有范围的色温 | 需要桥接器用户及灯 ID；首次配对返回的用户名需自行保存到私有连接配置 |
| Tasmota / WLED | 已校准的状态、单继电器/调光；WLED JSON 控制 | 依实际状态字段生成控件；Tasmota 色彩/色温当前只读 |
| miIO 本地 | 明确映射的读写方法 | UDP 54321、token、显式 `miio_mapping`/`miot_mapping`；不猜型号命令 |
| ONVIF / DLNA | 部分设备信息和只读媒体信息 | ONVIF 鉴权和完整控制未实现；不提供猜测性重启 |
| 路由器 / 交换机 / 光猫 / NAS | 公开管理页的标题、文本、表单输入与链接 | 没有通用的登录、超管提权或设置写入实现；不是官网 APP 的完整复刻 |
| BLE / Matter / HomeKit / Cast / AirPlay / MQTT 等发现线索 | 广播或网络身份识别 | BLE 私有 GATT、Matter/HomeKit 配对、厂商私有控制未实现；可通过合适的 HA 集成补充 |
| LIFX / Samsung / LG / Vizio 原生接口、ESPHome 伪 REST | 当前仅识别或无直接控制项 | 旧版未经核实的写接口已禁用；优先走 HA 集成 |

本轮协议验证使用离线报文、mock 和本机模拟 HTTP 服务，**没有连接真实家电执行控制**。这证明已覆盖的契约和失败边界，不能替代每个品牌、型号、固件的实机验收。

### 安装与依赖

需要 DSH `>=0.2.2-rc.3 <0.3.0`、Python 3.12，以及可访问家庭网络的 DSH Host。通过 DSH 的插件管理器安装本地目录或发布包；请勿手工改写正在使用的 profile。

在插件目录准备 Python 环境（Windows）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-optional.txt
```

核心依赖包括 `mijiaAPI`、`qrcode`、`requests` 和 `pycryptodome`。可选依赖 `zeroconf`、`tinytuya`、`bleak` 分别补充 mDNS、Tuya 和 BLE；缺少可选库会跳过对应通道。BLE 还需要可用的蓝牙硬件、系统权限及设备处于广播状态。

插件配置：`pythonPath` 指向该环境的 Python；留空时使用插件目录下的 `.venv`。`dataDir` 留空使用插件数据目录；`requestTimeoutMs` 为普通请求预算，较长的全屋操作另有预算；`loginTimeoutMs` 为米家扫码等待时间。发布包不会携带本机虚拟环境和登录数据。

### 从空列表开始

1. 打开侧栏「全屋智能」，进入全屋页。米家页可扫码登录原有账号。
2. 点击扫描，或在「配置连接」添加私有连接。即使自动发现失败，也可以手动添加受支持的终端。
3. 推荐先配置 `home_assistant:main`，填入下方 HA 示例，再扫描。HA 的实体会展开为独立设备；多个 HA 实例的同名实体相互隔离。
4. 打开设备详情，确认当前状态、可读/可写权限、数值范围、枚举和动作参数后操作。没有控制项表示没有取得经过适配的能力；不要据类别猜命令。
5. 连接保存后输入框清空。列表只显示是否已配置，不会回显密钥；编辑时提交完整替换对象，删除只移除本插件保存的连接，不修改远端账号。

连接配置仅通过面板提交，保存在 DSH Host 的凭据记录 `mihome/devices`。不要把 token、密码或 local key 发给聊天工具、写进源码、profile 配置或公开设备字段。

Home Assistant 连接 ID：`home_assistant:main`，私有配置：

```json
{
  "base_url": "http://192.168.1.10:8123",
  "token": "<在面板填写长期访问令牌>",
  "name": "家里的 Home Assistant"
}
```

手动 IP 终端使用 `协议:IP`，并在配置中明确 `ip`，如 `tuya:192.168.1.20`。DP 的类型、权限、范围和枚举必须来自该产品规格，示例值不可套用到未知设备：

```json
{
  "ip": "192.168.1.20",
  "device_id": "<设备 ID>",
  "local_key": "<本地密钥>",
  "version": 3.3,
  "dps": {
    "1": {"key": "power", "kind": "bool", "writable": true}
  }
}
```

其他已实现连接字段包括 Hue 的 `hue_username`/`hue_light_id`，Yeelight 的 `support`/`color_temp_range`，以及 miIO 的 `token` 和显式方法映射。它们都是私有连接对象字段；不能由名称猜出凭据或设备的写入能力。

### 发现、中文控件与限制

发现包含 ARP、受限 ping、TCP 端口、HTTP 指纹、SSDP、mDNS、Tuya UDP、miIO UDP 和 BLE 广播。支持逗号或空格分隔的子网/端口参数；只接受 IPv4 CIDR 和有效端口，跨 VLAN/访客网隔离、睡眠设备和不广播设备可能不可见。较大网段不会逐地址全量 ping；不能保证列出整个网络中的每台设备。

已观察到的无开放 TCP 端口主机仍可保留；广播发现不依赖 ARP 候选，BLE 地址不需要伪造 IP。扫描缓存按参数区分，刷新会重新发现。设备列表的型号、厂商和类别是线索；控制能力来自规格或适配器确认的字段。

翻译依次使用官方规格中文、协议字典、AliGenie/华为智慧生活等平台术语及通用拆词；未知词保留原名。保留原始属性 key、数值类型、枚举、范围、单位与权限，UI 和工具使用同一 key。平台字典是术语补充，不代表已经接入该平台账号或完整厂商 API。

OUI 采用 IEEE MA-L 快照，保留组织原文，不把 MAC 前缀当设备型号。本地管理地址、组播地址、非法 MAC 和歧义前缀不报厂商。更新脚本为 `scripts/update-oui.py`，可传 `--input`、`--output`、`--snapshot-date`、`--sha256`；已记录源快照 SHA-256，生成过程可复现。

### 自定义场景

在面板创建/编辑/删除场景，或使用 `smart_home_scenes`。场景按顺序执行，逐步记录成功和错误，单步失败继续其余步骤，汇总只统计明确成功的步骤。当前是**手动执行场景**；「回家模式」是场景名字，没有自动到家、定时或事件触发引擎。

```json
{
  "name": "回家模式",
  "actions": [
    {"device": "shelly:192.168.1.21", "mode": "power", "state": true},
    {"device": "home_assistant:main/cover.living_room", "mode": "write", "name": "position", "value": 0}
  ]
}
```

以上设备 ID 和属性 key 仅示例；实际请从详情复制，动作 `params` 按展示的 schema/输入列表填写 JSON。场景文件原子保存，损坏的文件不会被当作空列表覆盖。

### 失败、超时与取消

适配器失败会保留为失败，不再包装成成功；未知电源状态不会显示为关机。米家 `code=1` 只表示网关接收，不作为设备执行成功。HA 等协议的 HTTP 服务确认也不等于物理动作已完成，必要时读取状态确认。

Node 超时或取消会结束该共享 Python 桥接进程；同一桥接的其他在途请求标记为中断、结果未知。Python 操作超时会先取消协程，再退出专用桥接进程，防止同步调用或后台线程继续本地执行，也不再处理排队请求。下次请求使用新进程。**已经发送到设备或云端的命令无法撤回**，取消后应先读取状态再决定重试，避免重复动作。登录等待使用独立通道。

### 工具与开发验证

最近一次离线回归为 Node 80/80（约 4.47 秒）、Python 109/109（约 2.13 秒）；本机 Home Assistant 模拟端到端用例经过真实 Node/Python 桥接，但不是家中设备验收。另测试了桥接预算超时与设备自身网络超时的区分。fake React 冒烟也不是浏览器验收。详细范围和剩余边界见 `docs/REPAIR-VALIDATION.md`。

保留 `mihome_login`、`mihome_devices`、`mihome_device`、`mihome_control`、`mihome_scenes`、`mihome_sensors`；新增 `smart_home_scan`、`smart_home_devices`、`smart_home_device`、`smart_home_control`、`smart_home_scrape`、`smart_home_scenes`、`smart_home_catalog`。详情输出是控制 key、类型、范围和参数的依据。

```powershell
node --test
.\.venv\Scripts\python.exe -m unittest discover -s python/tests -v
.\.venv\Scripts\python.exe python/smart_home/test_offline.py
.\.venv\Scripts\python.exe python/smart_home/test_repairs.py
.\.venv\Scripts\python.exe python/smart_home/test_discovery_repairs.py
.\.venv\Scripts\python.exe python/smart_home/test_localization_repairs.py
node scripts/check-i18n.mjs client.js
node scripts/render-smoke.mjs client.js
```

测试覆盖失败传播、场景/凭据、取消/进程重启、发现输入与合并、HA 多实例、能力与协议报文等。`node --test tests/whole-home-e2e.test.js` 另用本机模拟 HA REST 服务跑真实 Node→JSON-Lines Python→manager→适配器调用链，并在子进程禁止非回环网络；不访问真实网关或家电。模拟界面测试不等于真实浏览器视觉验收。项目为 Node Cordis Host + Python JSON-Lines 桥接；入口在 `lib/index.js`、`lib/panel.js`、`python/mihome_bridge.py`，全屋实现位于 `python/smart_home/`。

### 资料来源与许可

协议采用官方资料优先、维护中的社区实现补充；并非所有品牌公开完整控制 API。

- [Home Assistant REST](https://developers.home-assistant.io/docs/api/rest/) / [实例发现](https://raw.githubusercontent.com/home-assistant/developers.home-assistant/master/docs/api/instance_discovery.md)
- [MIoT 规格](https://miot-spec.org/miot-spec-v2/) / [mijiaAPI](https://github.com/Do1e/mijia-api) / [miIO 协议社区文档](https://github.com/OpenMiHome/mihome-binary-protocol/blob/master/doc/PROTOCOL.md)
- [Shelly Gen2](https://shelly-api-docs.shelly.cloud/gen2/) / [TinyTuya](https://github.com/jasonacox/tinytuya) / [python-kasa](https://github.com/python-kasa/python-kasa)
- [Yeelight](https://www.yeelight.com/en_US/developer) / [Hue](https://developers.meethue.com/develop/hue-api/)
- [Tasmota 状态](https://tasmota.github.io/docs/JSON-Status-Responses/) / [WLED JSON API](https://kno.wled.ge/interfaces/json-api/) / [ONVIF](https://www.onvif.org/profiles/)
- [zeroconf](https://python-zeroconf.readthedocs.io/) / [Bleak](https://bleak.readthedocs.io/) / [IEEE OUI](https://standards-oui.ieee.org/oui/oui.txt)

本项目 GPL-3.0-or-later；沿用的上游 Python 代码保留相应许可。参考外部协议文档不表示厂商背书。

---

## English

### Purpose and support

One DSH workbench for discovery, capability-based controls, private device connections and manually executed scenes. Mi Home devices use the existing cloud login. For other ecosystems, **Home Assistant is the recommended broad integration path**; supported local protocols can also connect directly.

Implemented direct paths include Mi Home specifications/actions, explicit Tuya DP mappings, Shelly switch/cover components, legacy Kasa relays, supported Yeelight methods, Hue lights, calibrated Tasmota/WLED operations, and explicitly configured miIO methods. Home Assistant entities expose controls based on their states, available services and feature flags: lights, switches, fans, climate, covers, media players, vacuums, locks, humidifiers, selects, numbers, buttons, scenes/scripts and sensors. Appliance coverage depends on the upstream integration's entities.

Routers, switches, ONTs and NAS currently provide public management-page information, not a universal authenticated settings editor or super-admin bypass. BLE, Matter, HomeKit and other discovery clues do not imply pairing or control. Unverified native LIFX/TV/ESPHome write paths are disabled; use suitable HA integrations. ONVIF/DLNA support is partial and read-only. The Chinese matrix above records protocol-specific limits.

### Installation and connection

Requires DSH `>=0.2.2-rc.3 <0.3.0`, Python 3.12, and a Host that can reach the devices. Install through DSH Plugin Manager. Create a virtual environment and install `requirements.txt`; install `requirements-optional.txt` for mDNS, Tuya and BLE. Set `pythonPath` when the environment is outside the plugin directory. Virtual environments, credentials and runtime data are not shipped in the package.

Open the smart-home tab, scan, or add a private connection even when the list is empty. To connect HA, use ID `home_assistant:main` with a JSON object containing `base_url` and a long-lived `token`, then scan. Its entities appear independently; identical entity names on different hubs remain separate. Other endpoints use `protocol:IP` with an explicit `ip` and verified protocol credentials/mappings.

Connection payloads are stored only in the DSH Host credential record `mihome/devices`. Lists return IDs/configured flags, never secret values; saving clears the input. Editing replaces the full payload. Do not send tokens through the chat tools or put them in public fields, source or profile configuration.

### Discovery, controls and scenes

Discovery combines ARP, bounded ping/port probes, HTTP, SSDP, mDNS, Tuya/miIO UDP and BLE advertisements. It accepts IPv4 CIDRs and valid ports, preserves observed hosts without open TCP ports, and keeps BLE addresses without inventing IPs. It cannot enumerate every sleeping, isolated or non-advertising device. Translation preserves original keys, value types, enumerations, ranges, units and access flags; unknown terms keep their original names.

Scenes run sequentially and record each step. Failed steps do not prevent later steps; only explicitly successful results count as success. There is no arrival, timer or event trigger engine. Use actual device IDs/keys from the detail view and follow the action's JSON parameter schema. Corrupt scene files are not silently overwritten.

Failures stay failures, unknown power stays unknown, and Mi Home gateway acceptance (`code=1`) is not a confirmed device write. On timeout/abort, including a Python operation timeout, the shared Python bridge is terminated before accepting queued work; other pending operations report interrupted/unknown outcomes. A later request starts a fresh process. Commands already sent to a device/cloud cannot be recalled. Read state before retrying.

### Verification and license

Run the Node and Python commands in the Chinese development section. Regression coverage uses offline packets, mocks and localhost servers; it does not establish compatibility with every real model/firmware. Mock UI tests do not establish live rendering. IEEE MA-L data can be regenerated with `scripts/update-oui.py`; organization names remain verbatim and ambiguous/local/multicast prefixes do not produce guessed vendors.

GPL-3.0-or-later, with upstream Python license notices retained. Protocol references are linked above; use of those documents does not imply vendor endorsement.
