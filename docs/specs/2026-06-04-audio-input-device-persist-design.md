# 音频输入设备选择持久化 + 跨平台解析 设计 Spec

- 日期：2026-06-04
- 分支：feat/audio-input-device-persist
- 版本：v1.0
- 状态：设计定稿，待实现

---

## 背景与问题

设置页已有麦克风下拉（`SettingsDialog.tsx` 的 `AudioInputSelect` 调用 `POST /api/v1/devices/select`），但选择是 session 级的，后端重启即丢。

`backend/api/entrypoint.py` 启动时盲选「第一个枚举设备」作为默认（mac 上通常是 iPhone 连续互通麦克风，采集静音）。这个非 None 值导致 `SOUNDSPEED_AUDIO_DEVICE` 环境变量永远不生效——env 分支被跳过，变成死代码。

目标：设置页选麦克风能持久化、重启仍生效，且 macOS CoreAudio 与 Windows WASAPI 同一套代码都解析正确。

---

## 设计决策

### 1. 存名字，不存序号

设备序号（index）在重启、插拔、切换 host API 时漂移：Windows 同一台设备在 MME / DirectSound / WASAPI 下序号不同，且顺序与接口无关。名字对本机用户可读，跨重启稳定。

存储原始名字字符串（如 `"MacBook Pro Microphone"`、`"Focusrite USB Audio"`）。

### 2. 名字不直接传给 sounddevice

Windows 多 host API 下同名设备歧义匹配会选错（可能落到高延迟 MME）。正确做法：名字 → 调用去重后的 `list_input_devices()` 匹配当前 index → 按 index 开流。查询在每次 take-start 时做，支持热插拔。

### 3. 持久化存储

SQLite 新增通用 kv 表 `app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)`，v8 migration。key `audio_input_device` 存原始设备名字符串。该表可复用于后续 ASR 语言、VAD 灵敏度等设置，不再为每类设置加列。

DAL 新增两个纯操作：`get_setting(key) -> str | None` 和 `set_setting(key, value)`（UPSERT）。

### 4. 解析优先级

纯函数 `resolve_audio_device() -> int | None`，每步 log，顺序如下：

1. `app_settings` 中的持久化名字（存在且能匹配当前设备列表）
2. `SOUNDSPEED_AUDIO_DEVICE` 环境变量（作为首次引导手段）
3. 系统默认输入设备（`sounddevice.default.device[0]`）
4. 第一个可用输入设备

明确语义：UI 选过后重启，持久化赢过 env。env 只用于从未通过 UI 配置的场景（首次部署、CI、无 UI 的开发机）。

### 5. 采集时解析位置

解析逻辑放在 `_source_factory`（每次 take-start 调用），而非进程启动时一次性解析。持久化设备当前不在场时，退回系统默认 + 发 warning 事件，take 不崩，下一次 take-start 重试。

### 6. GET /devices 语义

`GET /api/v1/devices` 返回：

- 设备列表（含解析后的实际 index，去重后）
- `selected_name`：当前持久化存储的名字（可能为 null）
- `selected_available`：该名字在当前枚举中是否匹配到
- `selected`：本次真正会用的 index（含 fallback 结果）

前端高亮「真正在用的」，而不是单纯回显存储值。避免重现「下拉显示设备 A、实际采集设备 B」的历史问题。

### 7. 前端行为

下拉选项高亮 `selected` 对应的设备名。若 `selected_available = false`，下拉上方显示「已保存的设备未连接，当前使用 XXX」提示，不静默。

---

## 跨平台说明

`data/soundspeed.db` 是本机的（gitignored），设置不在 mac / Windows 间同步，也不需要。「跨平台」= 同一套解析代码在两个 OS 上都能正确匹配设备，不是设置共享。名字的价值是本机重启/插拔后稳定可读。设备枚举（`list_input_devices`）已对 Windows 三套 host API 按名去重，本设计沿用该函数，不额外处理。

---

## 不在本次范围

顶部 header 电平条读的是浏览器 `getUserMedia` 默认麦，与后端选哪个设备无关，本次不动。正路是后端把采集 RMS 通过现有 WebSocket 推给前端，让电平条反映「ASR 真正听到的声音」——作为独立后续任务处理。

---

## 测试要点

- DAL kv：UPSERT 写入/覆盖、key 不存在时返回 None。
- 解析纯函数：四条优先级全路径（持久化命中、env 命中、系统默认、第一个可用）+ 持久化设备不在场时 fallback 行为。
- `POST /devices/select`：写入持久化 + 响应正确。
- 重启恢复：set_setting 后模拟进程重启（新 DB 连接），resolve 返回同一设备 index。
- `GET /devices`：`selected_available = true` / `false` 两种情况下 `selected` 正确。
- v8 migration：从 v7 库升级后 `app_settings` 表存在、可读写。
