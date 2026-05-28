# Spec: SQLite 9 表 schema 与 DAL 接口

版本：v0.3.3（时间单位语义修订）
日期：2026-05-27（v0.3.3 更新：2026-05-28）
状态：定稿，进入开发
owner：境熙

变更记录：
- v0.3.3（2026-05-28 ASR 对接决策落地）：`transcript_segments.start_frame / end_frame` 语义从「16 kHz 帧」改为「毫秒（秒 × 1000 取整）」；§1 时间表示约定更新；§2.7 DDL 注释更新；§6 `TranscriptSegment` dataclass 注释更新；不起 migration，字段名保留。后续需要高精度时间对齐时另开 ticket。
- v0.3.2（2026-05-27 spec 内部一致性收敛）：§6 Take 数据类 performer_issues 类型改 dict | list | None，与同节 update_take_np_output 签名对齐；§5.2 apply_migrations 伪代码移除 DELETE active_observers，补注说明清理由独立 purge_volatile_tables 函数负责。
- v0.3.1（2026-05-27 quality review 同步）：§6 update_take_np_output 签名 performer_issues 改 dict | list | None；§6 末句接口计数改 23 + 4；§7 两条测试名同步实现 (test_apply_migrations_does_not_purge_observers / test_purge_volatile_tables_clears_observers / test_take_events_payload_json_validation)。
- v0.3（2026-05-27 codex/lead review 收敛）：FTS5 tokenizer 从 unicode61 改 trigram（unicode61 实测对 CJK phrase query 不命中，spec v0.2 描述错）；§3.2 选型说明 + §8.2 开放问题重写；jieba 升级路径降级为「召回不达标再上」。
- v0.2（2026-05-27 评审决议）：删除 transcript_segments.is_partial 列；take_id 回 NOT NULL + ON DELETE CASCADE；DAL insert_segment / list_segments 签名同步收敛；§8 新增「评审决议」小节留痕。
- v0.1：初稿

依赖 spec（按权威级别排序）：
1. system-architecture v0.1（`docs/specs/2026-05-26-system-architecture.md`）
2. onset-llm-ux v1.1（`docs/specs/2026-05-22-onset-llm-ux.md`）
3. llm-service-design v1.0（`docs/specs/2026-05-25-llm-service-design.md`）
4. development-plan v0.2（`docs/specs/2026-05-27-development-plan.md`）

覆盖范围：9 张 SQLite 表的 schema（DDL、约束、索引）、FTS5 配置与触发器同步、迁移脚本骨架、DAL 接口签名草案（contract C2）、测试入口清单、开放问题。不实现任何代码，仅定义接口形状，供 ticket 1.D 实现。

已知上游不一致处理规则：
- L1 Pipeline 已废除（B1 决议）。schema 里不为 L1 留任何字段。
- llm-service-design v1.0 保留的旧 `prompt: str` 接口已被 system-architecture v0.1 §6 三处修订覆盖，本 spec 按修订后的 `messages: list` 接口写。
- `transcript_segments` 需要在 system-architecture v0.1 §7 草案基础上加 `speaker TEXT`（dev-plan v0.2 §6 与 §8 contract C2 强制）。
- 声道命名统一 `ch1 / ch2`（一基命名）；`transcript_segments.ch` 列存整数 `1` 或 `2`。

---

## 1. 时间表示约定

本 spec 使用两种时间单位，按场景分别选型：

**业务时间戳**：用 `REAL`（Unix epoch 秒，含小数秒）。理由：`SessionState.take_start_ts` 定义为 `float`，对齐可以直接赋值，无需转换。所有表中 `_ts` 后缀列（`start_ts`、`end_ts`、`created_at` 等）均为 `REAL`。

**音频时间偏移**：用 `INTEGER`（**毫秒**，秒 × 1000 取整）。理由：整数精度足以满足 MVP 时间对齐需求，转换公式简单（`round(sec * 1000)`），与 ASR 离线 JSON 输出（秒）之间的换算无歧义。`transcript_segments.start_frame` 和 `end_frame` 均为 `INTEGER`，字段名沿用历史命名（原定义为 16 kHz 帧，Lead 拍板改为毫秒，不起 migration）。

⚠ 字段名 `start_frame / end_frame` 与实际语义（毫秒）不匹配，是历史遗留。后续若需要高精度时间对齐（16 kHz 帧，精度 62.5 μs），另开 ticket 更改字段名和语义并起 migration。

示例：`21.023 秒 → start_frame = 21023`。

---

## 2. 9 张表 schema

### 2.1 scenes 表

场次（一部戏拍摄期间按场次编号拆分，一个场次对应一个拍摄单元）。

```sql
CREATE TABLE IF NOT EXISTS scenes (
    scene_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    scene_code  TEXT    NOT NULL,                   -- 场次编号，如 "Scene_3A"
    description TEXT,                               -- 场次说明（可选）
    shoot_date  TEXT,                               -- 拍摄日期，ISO 8601 格式（YYYY-MM-DD）
    is_active   INTEGER NOT NULL DEFAULT 0          -- 1 = 当前活跃场次，0 = 非活跃
        CHECK (is_active IN (0, 1)),
    created_at  REAL    NOT NULL DEFAULT (unixepoch('now', 'subsec'))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_scenes_scene_code
    ON scenes (scene_code);

CREATE INDEX IF NOT EXISTS ix_scenes_is_active
    ON scenes (is_active);
```

字段说明：
- `scene_code`：录音师口头称呼的场次编号，项目内唯一。
- `is_active`：标记当前录制所在场次。NP Pipeline 默认绑定 `is_active=1` 的场次。每次只有一个场次处于活跃状态，由 Orchestrator 在 take.start 时更新。
- `description` 和 `shoot_date` 可选，供导出场记单时使用。

### 2.2 takes 表

take 记录，每条 take 对应一次拍摄尝试。

```sql
CREATE TABLE IF NOT EXISTS takes (
    take_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scene_id        INTEGER NOT NULL
        REFERENCES scenes (scene_id) ON DELETE RESTRICT,
    take_number     INTEGER NOT NULL,               -- 本场次内的 take 编号，从 1 起
    shot            TEXT,                           -- 镜次编号，如 "Shot_2B"
    start_ts        REAL    NOT NULL,               -- take 开始 Unix 时间戳（秒，含小数）
    end_ts          REAL,                           -- take 结束 Unix 时间戳，进行中时为 NULL
    status          TEXT    NOT NULL DEFAULT 'tbd'
        CHECK (status IN ('keeper', 'ng', 'hold', 'tbd')),
    performer_issues TEXT,                          -- NP 解析输出，JSON 文本，可选
    audio_quality   TEXT,                           -- NP 解析输出，如 'clean' / 'noisy' / 'clipped'
    script_diff     TEXT,                           -- L2 输出，JSON 文本，剧本偏差报告
    notes           TEXT,                           -- Ch2 原文拼接 + 候补 note，可选
    created_at      REAL    NOT NULL DEFAULT (unixepoch('now', 'subsec')),
    updated_at      REAL    NOT NULL DEFAULT (unixepoch('now', 'subsec')),
    UNIQUE (scene_id, take_number)
);

CREATE INDEX IF NOT EXISTS ix_takes_scene_id
    ON takes (scene_id);

CREATE INDEX IF NOT EXISTS ix_takes_scene_take
    ON takes (scene_id, take_number);

CREATE INDEX IF NOT EXISTS ix_takes_status
    ON takes (status);
```

字段说明：
- `status` 枚举值刻意小写化（`keeper / ng / hold / tbd`），与 onset-llm-ux v1.1 的 Title Case（Keeper / NG / Hold）不同，理由是 SQLite CHECK 大小写敏感，统一小写可避免比较错误；前端显示层做大写转换。
- `shot`：镜次编号，来自 SessionState，取当前活跃 shot 值。无则 NULL。
- `performer_issues` / `audio_quality`：NP Pipeline 的解析输出，写入 `take_events` 同时在 takes 表做冗余存储，方便直接查 take 记录。
- `script_diff`：L2 Pipeline 输出，JSON 文本，格式由 L2 定义。
- `notes`：录音师所有文本备注汇总，含 Ch2 原文转录与候补 note。
- `end_ts` 为 NULL 表示 take 正在进行中。
- `UNIQUE (scene_id, take_number)` 防止重复编号。

### 2.3 take_events 表

take 内手动 mark 及 NP Pipeline 写入的结构化事件，时序记录。

```sql
CREATE TABLE IF NOT EXISTS take_events (
    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    take_id     INTEGER NOT NULL
        REFERENCES takes (take_id) ON DELETE CASCADE,
    event_type  TEXT    NOT NULL,                   -- 事件类型，见下方约定值
    ts          REAL    NOT NULL,                   -- 事件发生时间戳（Unix 秒，含小数）
    payload     TEXT    NOT NULL DEFAULT '{}'       -- JSON 文本，事件结构化载荷
        CHECK (json_valid(payload)),
    created_at  REAL    NOT NULL DEFAULT (unixepoch('now', 'subsec'))
);

CREATE INDEX IF NOT EXISTS ix_take_events_take_id
    ON take_events (take_id);

CREATE INDEX IF NOT EXISTS ix_take_events_take_type_ts
    ON take_events (take_id, event_type, ts);
```

字段说明：
- `event_type` 已知取值：`manual.mark`（录音师手动触屏）/ `np.write`（NP Pipeline 解析写入）/ `system.note`（系统自动记录）。开放扩展，不用 CHECK 限制，但 DAL 层可做校验。
- `payload` 存储 JSON 文本，`json_valid()` 约束确保插入值是合法 JSON。`manual.mark` 的 payload 含 `{"mark": "keeper"}` 等；`np.write` 含 `{"performer_issues": [...], "audio_quality": "clean", "status": "hold"}`。
- 选用「JSON TEXT + 必要字段冗余成列」策略：核心检索字段（`event_type`、`ts`）已单独成列并建索引，其余明细留 JSON。避免字段膨胀，同时保留按类型和时间范围检索能力。

### 2.4 scripts 表

已上传的剧本原文，每场次允许多版本。

```sql
CREATE TABLE IF NOT EXISTS scripts (
    script_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    scene_id    INTEGER NOT NULL
        REFERENCES scenes (scene_id) ON DELETE RESTRICT,
    raw_text    TEXT    NOT NULL,                   -- 剧本原文（纯文本或分行文本）
    version     INTEGER NOT NULL DEFAULT 1,         -- 同场次版本号，从 1 起递增
    created_at  REAL    NOT NULL DEFAULT (unixepoch('now', 'subsec'))
);

CREATE INDEX IF NOT EXISTS ix_scripts_scene_id
    ON scripts (scene_id);

CREATE UNIQUE INDEX IF NOT EXISTS ux_scripts_scene_version
    ON scripts (scene_id, version);
```

字段说明：
- 一个场次允许多个剧本版本（对应不同修订稿），`version` 递增。与「UNIQUE(scene_id)」限制只有一个版本的方案相比，多版本更灵活，但 SP Pipeline 每次解析后须明确当前使用哪一版；建议以最大 `version` 为当前版（开放问题 §8.3 有记录）。
- `raw_text` 原文保留，SP Pipeline 的解析结果落到 `script_lines`，两者保持关联（外键 `script_lines.script_id → scripts.script_id`）。

### 2.5 audit_log 表

所有写操作审计日志，仅追加（不允许 UPDATE / DELETE）。

```sql
CREATE TABLE IF NOT EXISTS audit_log (
    log_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    actor       TEXT    NOT NULL,                   -- 操作发起方，如 'orchestrator' / 'user'
    action      TEXT    NOT NULL,                   -- 操作描述，如 'take.start' / 'script.upload'
    payload     TEXT    NOT NULL DEFAULT '{}'       -- JSON 文本，操作明细
        CHECK (json_valid(payload)),
    ts          REAL    NOT NULL DEFAULT (unixepoch('now', 'subsec'))
);

CREATE INDEX IF NOT EXISTS ix_audit_log_actor
    ON audit_log (actor);

CREATE INDEX IF NOT EXISTS ix_audit_log_ts
    ON audit_log (ts);
```

字段说明：
- `actor`：区分自动化操作（`orchestrator` / `l2_pipeline` / `np_pipeline`）与人工操作（`user:admin` 等）。
- `action`：与 Orchestrator 事件类型对齐，便于审计链路追踪。
- 不设外键，审计日志与业务表解耦，即使业务行被删也保留日志。

### 2.6 active_observers 表

当前活跃的 `/view` 只读连接，服务启动时清空（见 §5 迁移骨架中的 runtime 说明）。

```sql
CREATE TABLE IF NOT EXISTS active_observers (
    connection_id   TEXT    PRIMARY KEY,            -- WebSocket 连接 ID（UUID 或 session token）
    name            TEXT    NOT NULL,               -- 观察者自填名字（导演 / 场记）
    joined_at       REAL    NOT NULL DEFAULT (unixepoch('now', 'subsec'))
);
```

字段说明：
- `connection_id` 对应前端用 `POST /view/register` 换取的 ID，WS 连接时带入。
- 不设外键，与 takes / scenes 表无关联。
- 数据完全易失，服务重启后清空（运行时 `DELETE FROM active_observers` 由启动逻辑执行，不靠 DDL）。

### 2.7 transcript_segments 表

ch1 / ch2 转录片段，每个 ASR 最终确认片段写一行。

```sql
CREATE TABLE IF NOT EXISTS transcript_segments (
    segment_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    take_id         INTEGER NOT NULL                -- 必须关联到 take
        REFERENCES takes (take_id) ON DELETE CASCADE,
    ch              INTEGER NOT NULL                -- 声道，1 或 2（一基命名）
        CHECK (ch IN (1, 2)),
    speaker         TEXT,                           -- 说话人标签，NULL 表示未知或未分离
    text            TEXT    NOT NULL,               -- 转录文本
    start_frame     INTEGER NOT NULL,               -- 片段开始时间（毫秒，秒×1000取整）⚠ 字段名沿用历史命名
    end_frame       INTEGER NOT NULL,               -- 片段结束时间（毫秒，秒×1000取整）⚠ 字段名沿用历史命名
    created_at      REAL    NOT NULL DEFAULT (unixepoch('now', 'subsec')),
    CHECK (end_frame > start_frame)
);

CREATE INDEX IF NOT EXISTS ix_transcript_take_ch
    ON transcript_segments (take_id, ch);

CREATE INDEX IF NOT EXISTS ix_transcript_take_speaker
    ON transcript_segments (take_id, speaker);

CREATE INDEX IF NOT EXISTS ix_transcript_frames
    ON transcript_segments (start_frame, end_frame);
```

字段说明：
- `take_id`：必须关联到 take。system-architecture v0.1 §4 字段说明明示「`take_active` 驱动事件路由。take 开始前，ASR 转录结果只做流式推送」，因此 take 未开始时的 ASR 片段不写库。Orchestrator 的 `asr.final` handler 在写库前必须检查 `session.take_active`，false 则只走 WS 推送不写库。take 记录被删时，级联删除其历史片段。
- `ch`：存整数 `1` 或 `2`，对应声道 ch1（对白，Boom/Lav）和 ch2（录音师备注）。
- `speaker`：来自 ASR diarization 输出的说话人标签（如 `"SPEAKER_00"`），ch1 片段有值时来自 speaker diarization；ch2 默认 NULL（录音师自己，不做分离）。diarization 跨 take 不保证标签一致性（开放问题 §8.5 有记录）。

### 2.8 script_lines 表（含 FTS5 虚拟表）

SP Pipeline 解析出的剧本台词行，含 FTS5 全文检索索引。

```sql
-- 物理表
CREATE TABLE IF NOT EXISTS script_lines (
    line_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    script_id       INTEGER NOT NULL
        REFERENCES scripts (script_id) ON DELETE CASCADE,
    line_no         INTEGER NOT NULL,               -- 行号，同 script 内从 1 起
    character       TEXT,                           -- 角色名（NULL 表示舞台指示行）
    text            TEXT    NOT NULL,               -- 台词文本
    created_at      REAL    NOT NULL DEFAULT (unixepoch('now', 'subsec')),
    UNIQUE (script_id, line_no)
);

CREATE INDEX IF NOT EXISTS ix_script_lines_script_id
    ON script_lines (script_id);

CREATE INDEX IF NOT EXISTS ix_script_lines_character
    ON script_lines (character);

-- FTS5 虚拟表（见 §3 FTS5 配置）
```

字段说明：
- `script_id` 外键列名用 `script_id`（不是 system-architecture §8 中的笔误 `scripts_id`）。
- `character` 为 NULL 表示舞台指示或场景描述行，不是台词。
- FTS5 虚拟表定义和同步触发器见第 3 节。

### 2.9 take_line_matches 表

take 转录与剧本行的比对结果，由 L2 Pipeline 写入。

```sql
CREATE TABLE IF NOT EXISTS take_line_matches (
    match_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    take_id         INTEGER NOT NULL
        REFERENCES takes (take_id) ON DELETE CASCADE,
    line_id         INTEGER NOT NULL
        REFERENCES script_lines (line_id) ON DELETE CASCADE,
    diff_type       TEXT    NOT NULL                -- 偏差类型
        CHECK (diff_type IN ('match', 'missing', 'substitution', 'insertion')),
    payload         TEXT    NOT NULL DEFAULT '{}'   -- JSON 文本，偏差明细
        CHECK (json_valid(payload)),
    created_at      REAL    NOT NULL DEFAULT (unixepoch('now', 'subsec'))
);

CREATE INDEX IF NOT EXISTS ix_take_line_matches_take_id
    ON take_line_matches (take_id);

CREATE INDEX IF NOT EXISTS ix_take_line_matches_line_id
    ON take_line_matches (line_id);
```

字段说明：
- `diff_type` 枚举：`match`（完全匹配）/ `missing`（演员漏词）/ `substitution`（改词）/ `insertion`（加词）。对应 onset-llm-ux v1.1 用例 3 描述的「漏词 / 改词 / 加词」。
- `payload`：存偏差明细，如偏差位置、实际转录文本片段。

---

## 3. FTS5 配置

### 3.1 虚拟表定义

```sql
-- FTS5 虚拟表，对 script_lines 的 text 列做全文检索
CREATE VIRTUAL TABLE IF NOT EXISTS script_lines_fts
    USING fts5(
        text,                           -- 被索引的列，对应 script_lines.text
        character UNINDEXED,            -- 不参与检索，只做 retrieve
        content='script_lines',         -- content table 模式，不重复存文本
        content_rowid='line_id',        -- 关联 script_lines 主键
        tokenize='trigram'
    );
```

### 3.2 tokenizer 选型说明

**选型：`trigram` tokenizer**

FTS5 自带 `trigram` tokenizer（SQLite 3.34+ 内置），按 3 个 unicode codepoint 切分 token。对中英文混合的剧本台词检索行为如下：

- 中文：query `不想` 能匹配「我不想走，请别让我走」，因为 trigram 把文本切成包含「不想」的 3-gram token 子串。
- 英文：单词「together」会被切成 `tog / oge / get / eth / the / her`，query `gether` 能匹配。
- 短于 3 字符的 query（如单字「走」）走 trigram 的 prefix / partial 匹配行为，实测命中率良好。

为什么不选 `unicode61`：`unicode61` 对 CJK 字符按 unicode category 切，每个汉字作为独立 token，phrase query 在汉字数组上能匹配连续 token，但实测在 SQLite 3.45 上对 query `不想` 在「我不想走」中也不命中（已由 codex review 2026-05-27 验证）。spec v0.2 草稿选 `unicode61` 是错的描述，已收敛到 trigram。

为什么不上 jieba 词分：trigram 召回足够覆盖 MVP 剧本检索。jieba 词粒度更准确但代价是新增 Python 依赖 + 写入双列 + migration 升级，列入 §8.2 后续可能性，等召回率实测不达标再上。

### 3.3 同步触发器

FTS5 content table 模式不自动同步，需要手动建触发器：

```sql
-- INSERT 触发器
CREATE TRIGGER IF NOT EXISTS tg_script_lines_fts_insert
    AFTER INSERT ON script_lines
BEGIN
    INSERT INTO script_lines_fts (rowid, text, character)
        VALUES (NEW.line_id, NEW.text, NEW.character);
END;

-- DELETE 触发器
CREATE TRIGGER IF NOT EXISTS tg_script_lines_fts_delete
    BEFORE DELETE ON script_lines
BEGIN
    INSERT INTO script_lines_fts (script_lines_fts, rowid, text, character)
        VALUES ('delete', OLD.line_id, OLD.text, OLD.character);
END;

-- UPDATE 触发器（先删旧 FTS 行，再插新 FTS 行）
CREATE TRIGGER IF NOT EXISTS tg_script_lines_fts_update
    AFTER UPDATE ON script_lines
BEGIN
    INSERT INTO script_lines_fts (script_lines_fts, rowid, text, character)
        VALUES ('delete', OLD.line_id, OLD.text, OLD.character);
    INSERT INTO script_lines_fts (rowid, text, character)
        VALUES (NEW.line_id, NEW.text, NEW.character);
END;
```

---

## 4. 索引清单

以下为除主键自动索引外的所有显式索引，附使用场景说明：

| 表 | 索引名 | 列 | 使用场景 |
|---|---|---|---|
| `scenes` | `ux_scenes_scene_code` | `(scene_code)` | 按编号查场次，唯一约束 |
| `scenes` | `ix_scenes_is_active` | `(is_active)` | NP 快速找活跃场次 |
| `takes` | `ix_takes_scene_id` | `(scene_id)` | `GET /takes?scene_id=` 过滤 |
| `takes` | `ix_takes_scene_take` | `(scene_id, take_number)` | take 列表分页查询、编号递增计算 |
| `takes` | `ix_takes_status` | `(status)` | 按状态筛 take，如「所有 keeper」 |
| `take_events` | `ix_take_events_take_id` | `(take_id)` | 查某 take 的所有事件 |
| `take_events` | `ix_take_events_take_type_ts` | `(take_id, event_type, ts)` | 按类型和时间范围查 take 内事件 |
| `scripts` | `ix_scripts_scene_id` | `(scene_id)` | 查场次下所有剧本版本 |
| `scripts` | `ux_scripts_scene_version` | `(scene_id, version)` | 防重复版本号，唯一约束 |
| `audit_log` | `ix_audit_log_actor` | `(actor)` | 按操作方过滤审计日志 |
| `audit_log` | `ix_audit_log_ts` | `(ts)` | 按时间范围查审计日志 |
| `transcript_segments` | `ix_transcript_take_ch` | `(take_id, ch)` | L2 Pipeline 拉 take 全量 ch1 片段 |
| `transcript_segments` | `ix_transcript_take_speaker` | `(take_id, speaker)` | L2 prompt 按 speaker 聚合片段 |
| `transcript_segments` | `ix_transcript_frames` | `(start_frame, end_frame)` | 按帧范围查重叠片段（时间对齐用） |
| `script_lines` | `ix_script_lines_script_id` | `(script_id)` | SP Pipeline 批量写入、按剧本查行 |
| `script_lines` | `ix_script_lines_character` | `(character)` | 按角色名查台词 |
| `take_line_matches` | `ix_take_line_matches_take_id` | `(take_id)` | 查某 take 的偏差报告 |
| `take_line_matches` | `ix_take_line_matches_line_id` | `(line_id)` | 反查哪些 take 出现过某行台词 |

---

## 5. 迁移脚本骨架

### 5.1 v1_init.sql 文件结构

迁移脚本路径：`backend/db/migrations/v1_init.sql`

内容顺序：
1. 9 张物理表 DDL（按依赖顺序：scenes → takes → take_events → scripts → script_lines → take_line_matches → transcript_segments → audit_log → active_observers）
2. 显式索引（按表顺序）
3. FTS5 虚拟表
4. 三个 FTS5 同步触发器
5. `PRAGMA user_version = 1;`（标记版本，供 migration runner 读取）

注意：`PRAGMA journal_mode`、`PRAGMA foreign_keys`、`PRAGMA busy_timeout` 均为 per-connection 设置，不放在 `.sql` 文件里，由 migration runner 和 `DAL.__init__` 在每次 `sqlite3.connect()` 后立即执行。这样更清晰，`.sql` 文件只包含 schema 定义，PRAGMA 生命周期由 Python 代码管理。

最低 SQLite 版本要求：3.42（`unixepoch('now', 'subsec')` 含小数秒支持自 3.42 起）。实现期发现 Python 自带版本不足时，回退为 `CAST(strftime('%s', 'now') AS REAL)`（精度降至整数秒）。跨平台兼容性检查项（dev-plan §2 硬要求）：在 Windows 和 macOS 环境各执行 `sqlite3.sqlite_version_info` 确认版本。

### 5.2 migration runner 接口草案（Python 伪代码）

```python
from __future__ import annotations
from pathlib import Path
import sqlite3


MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# 每个迁移版本对应一个 .sql 文件
MIGRATION_FILES: dict[int, str] = {
    1: "v1_init.sql",
}


def apply_migrations(db_path: Path) -> None:
    """
    将数据库升级到最新 schema 版本。
    使用 PRAGMA user_version 记录已应用版本号。
    幂等：已应用的迁移不会重复执行。

    Args:
        db_path: SQLite 数据库文件路径。文件不存在时自动创建。
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute(f"PRAGMA busy_timeout = 5000;")  # WAL 并发等待 5s

        current_version: int = conn.execute("PRAGMA user_version;").fetchone()[0]
        target_version: int = max(MIGRATION_FILES.keys())

        for version in sorted(MIGRATION_FILES.keys()):
            if version <= current_version:
                continue  # 已应用，跳过
            sql_file = MIGRATIONS_DIR / MIGRATION_FILES[version]
            sql = sql_file.read_text(encoding="utf-8")
            conn.executescript(sql)  # executescript 自动 commit
            # user_version 由 sql 文件末尾的 PRAGMA user_version = N 设置

        conn.commit()
    finally:
        conn.close()
```

说明：
- `PRAGMA user_version` 由各 `.sql` 文件末尾的 `PRAGMA user_version = N;` 设置，runner 只读不写版本号。
- runner 内的三条 PRAGMA（`foreign_keys` / `journal_mode` / `busy_timeout`）是对 runner 自己这个 connection 的初始化，与 `DAL.__init__` 各自负责自己的 connection，两者互不影响。

注意：active_observers 等 volatile 表的清理不在 apply_migrations 内，由 backend/db/lifecycle.py 中独立函数 purge_volatile_tables(db_path) 负责，FastAPI 在服务启动 hook 中显式调用一次。apply_migrations 只管 schema 演进。
- 生产环境接入多个连接时，建议上层调用 `apply_migrations` 前先做进程锁（或 SQLite 的 `BEGIN EXCLUSIVE`），避免两个进程同时跑迁移。

---

## 6. DAL 接口签名草案（contract C2）

所有签名仅为接口形状定义，不在本 spec 实现。实现在 ticket 1.D（`backend/db/dal.py`）。

```python
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── 数据类（read 方法的返回类型）────────────────────────────────────────────


@dataclass
class Take:
    take_id: int
    scene_id: int
    take_number: int
    shot: str | None
    start_ts: float
    end_ts: float | None
    status: str                         # 'keeper' | 'ng' | 'hold' | 'tbd'
    performer_issues: dict | list | None  # NP 解析输出，可为 dict（按 issue 类别分组）或 list（issue 列表），DAL 写入时 json.dumps、读取时 json.loads 还原
    audio_quality: str | None
    script_diff: dict | None            # L2 输出，DAL 负责 json.loads；写入时也传 dict
    notes: str | None
    created_at: float
    updated_at: float


@dataclass
class TranscriptSegment:
    segment_id: int
    take_id: int
    ch: int                             # 1 或 2
    speaker: str | None
    text: str
    start_frame: int                    # 毫秒（秒×1000取整）⚠ 字段名沿用历史命名，实际语义为毫秒
    end_frame: int                      # 毫秒（秒×1000取整）⚠ 字段名沿用历史命名，实际语义为毫秒
    created_at: float


@dataclass
class ScriptLine:
    line_id: int
    script_id: int
    line_no: int
    character: str | None
    text: str
    created_at: float


@dataclass
class TakeEvent:
    event_id: int
    take_id: int
    event_type: str
    ts: float
    payload: dict[str, Any]
    created_at: float


# ── DAL 类 ──────────────────────────────────────────────────────────────────


class DAL:
    """
    数据访问层。所有数据库读写必须通过此类，不允许外部拼接 SQL。
    构造时传入数据库文件路径，自动应用迁移（调用 apply_migrations）。
    """

    def __init__(self, db_path: Path) -> None:
        """
        初始化 DAL，自动调用 apply_migrations 确保 schema 最新。
        每次 sqlite3.connect() 后必须立即执行以下 per-connection PRAGMA，
        否则外键约束不生效、WAL busy_timeout 不起效：
            PRAGMA foreign_keys = ON;
            PRAGMA journal_mode = WAL;
            PRAGMA busy_timeout = 5000;
        """
        ...

    # ── scenes ──────────────────────────────────────────────────────────────

    def create_scene(
        self,
        scene_code: str,
        description: str | None = None,
        shoot_date: str | None = None,
    ) -> int:
        """创建场次，返回 scene_id。"""
        ...

    def set_active_scene(self, scene_id: int) -> None:
        """将指定 scene_id 设为活跃场次，清除其他场次的 is_active。"""
        ...

    def get_active_scene_id(self) -> int | None:
        """返回当前活跃场次 ID，无则返回 None。"""
        ...

    def list_scenes(self) -> list[dict]:
        """返回所有场次的基本信息列表。"""
        ...

    # ── takes ────────────────────────────────────────────────────────────────

    def start_take(
        self,
        scene_id: int,
        take_number: int,
        start_ts: float,
        shot: str | None = None,
    ) -> int:
        """新建 take 行，返回 take_id。"""
        ...

    def end_take(
        self,
        take_id: int,
        end_ts: float,
        status: str,
        script_diff: dict | None = None,
        notes: str | None = None,
    ) -> None:
        """
        更新 take 结束时间、状态、L2 输出。
        script_diff 传 dict，DAL 内部 json.dumps 后存库；读取时（get_take / list_takes）
        DAL 内部 json.loads 还原为 dict，保持调用方口径一致。
        """
        ...

    def update_take_np_output(
        self,
        take_id: int,
        performer_issues: dict | list | None,
        audio_quality: str | None,
        status: str | None,
    ) -> None:
        """NP Pipeline 写入结构化字段，不覆盖 end_ts。performer_issues 传 dict / list，DAL 内部 json.dumps 存库；read 路径（get_take / list_takes）json.loads 还原；str 入参不再支持，类型已收敛。"""
        ...

    def get_take(self, take_id: int) -> Take | None:
        """按 take_id 获取单条 take，不存在返回 None。"""
        ...

    def list_takes(self, scene_id: int | None = None) -> list[Take]:
        """返回 take 列表，可按 scene_id 过滤，按 take_number 升序。"""
        ...

    # ── take_events ──────────────────────────────────────────────────────────

    def insert_take_event(
        self,
        take_id: int,
        event_type: str,
        payload: dict,
        ts: float,
    ) -> int:
        """写入 take 事件行，返回 event_id。"""
        ...

    def list_take_events(
        self,
        take_id: int,
        event_type: str | None = None,
    ) -> list[TakeEvent]:
        """返回某 take 的事件列表，可按 event_type 过滤，按 ts 升序。"""
        ...

    # ── transcript_segments ──────────────────────────────────────────────────

    def insert_segment(
        self,
        take_id: int,
        ch: int,
        speaker: str | None,
        text: str,
        start_frame: int,
        end_frame: int,
    ) -> int:
        """写入一条转录片段，返回 segment_id。ch 必须为 1 或 2。"""
        ...

    def list_segments(
        self,
        take_id: int,
        ch: int | None = None,
        speaker: str | None = None,
    ) -> list[TranscriptSegment]:
        """
        返回某 take 的转录片段列表。
        ch=None 表示不过滤声道。
        """
        ...

    # ── scripts ──────────────────────────────────────────────────────────────

    def insert_script(
        self,
        scene_id: int,
        raw_text: str,
        version: int | None = None,
    ) -> int:
        """
        插入剧本原文，返回 script_id。
        version=None 时自动取该场次最大版本 +1。
        """
        ...

    def get_latest_script(self, scene_id: int) -> dict | None:
        """返回场次最新版本剧本（script_id + raw_text），无则返回 None。"""
        ...

    # ── script_lines ─────────────────────────────────────────────────────────

    def insert_script_line(
        self,
        script_id: int,
        line_no: int,
        character: str | None,
        text: str,
    ) -> int:
        """插入一行台词，返回 line_id。FTS5 触发器自动同步。"""
        ...

    def match_script_line(
        self,
        query: str,
        scene_id: int | None = None,
    ) -> list[ScriptLine]:
        """
        用 FTS5 MATCH 检索台词，返回匹配行列表（按 BM25 排序）。
        scene_id 不为 None 时限制在该场次剧本内。
        """
        ...

    # ── take_line_matches ────────────────────────────────────────────────────

    def insert_take_line_match(
        self,
        take_id: int,
        line_id: int,
        diff_type: str,
        payload: dict,
    ) -> int:
        """写入 take-剧本行比对结果，返回 match_id。"""
        ...

    def list_take_line_matches(self, take_id: int) -> list[dict]:
        """返回某 take 的所有偏差记录，含 line_id + diff_type + payload。"""
        ...

    # ── active_observers ─────────────────────────────────────────────────────

    def upsert_observer(self, connection_id: str, name: str) -> None:
        """插入或更新观察者记录（INSERT OR REPLACE）。"""
        ...

    def remove_observer(self, connection_id: str) -> None:
        """删除观察者记录。"""
        ...

    def list_observers(self) -> list[dict]:
        """返回当前所有在线观察者列表。"""
        ...

    # ── audit_log ─────────────────────────────────────────────────────────────

    def append_audit(
        self,
        actor: str,
        action: str,
        payload: dict,
    ) -> int:
        """追加一条审计日志，返回 log_id。"""
        ...
```

DAL 接口签名总数：23 个公开方法（按 0.E §6 列出的 DAL 类方法，不计 `__init__`） + 4 个数据类（Take / TranscriptSegment / ScriptLine / TakeEvent）。

---

## 7. 测试入口与验收（ticket 1.D 应覆盖的测试用例）

以下为 ticket 1.D 应覆盖的测试用例标题，不实现，供 quality 侧 1.C / 1.E 接入时知道 DAL 哪些行为已被覆盖：

**迁移与初始化**
- `test_apply_migrations_creates_all_tables` — 迁移后 9 张表和 FTS5 虚拟表全部存在
- `test_apply_migrations_idempotent` — 多次调用 `apply_migrations` 不报错、不重复建表
- `test_apply_migrations_user_version` — 迁移后 `PRAGMA user_version` 等于 1
- `test_apply_migrations_does_not_purge_observers` —— DAL 多次构造，已存在的 observer 行还在（apply_migrations 不再清观察者）
- `test_purge_volatile_tables_clears_observers` —— 显式调 lifecycle.purge_volatile_tables 后 observers 清空

**scenes**
- `test_create_scene_returns_id` — 插入场次返回正确 scene_id
- `test_set_active_scene_clears_others` — 设活跃场次后其余场次 is_active 为 0

**takes**
- `test_start_take_returns_id` — 插入 take 返回正确 take_id
- `test_end_take_sets_end_ts` — end_take 后 end_ts 不为 NULL
- `test_take_status_check_constraint` — 插入非法 status 值触发 CHECK 约束异常
- `test_list_takes_filter_by_scene_id` — list_takes 按 scene_id 过滤正确
- `test_get_take_not_found_returns_none` — get_take 不存在时返回 None

**take_events**
- `test_insert_take_event_with_valid_json` — 合法 JSON payload 正常写入
- `test_take_events_payload_json_validation` —— 裸连接插入 'not-valid-json' 触发 json_valid(payload) CHECK 约束

**transcript_segments**
- `test_insert_segment_with_speaker` — 含 speaker 字段的片段正常写入，speaker 可查
- `test_insert_segment_rejects_null_take_id` — 传 None 作为 take_id 触发 IntegrityError
- `test_insert_segment_ch_check_constraint` — ch 值非 1/2 触发 CHECK 约束异常
- `test_insert_segment_frame_order_check` — end_frame <= start_frame 触发 CHECK 约束异常
- `test_list_segments_filter_by_speaker` — 按 speaker 过滤返回正确子集

**scripts 与 script_lines**
- `test_insert_script_auto_version` — 同场次多次插入版本号自动递增
- `test_insert_script_line_and_fts_sync` — 插入台词行后 FTS5 可立即 MATCH 到
- `test_match_script_line_fts5_basic` — FTS5 MATCH 基本英文词查询正确返回
- `test_match_script_line_fts5_chinese` — FTS5 MATCH 中文字符级子串查询（trigram）
- `test_delete_script_line_fts_removed` — 删除台词行后 FTS5 不再匹配

**take_line_matches**
- `test_insert_take_line_match_valid_diff_type` — 合法 diff_type 写入正常
- `test_insert_take_line_match_invalid_diff_type` — 非法 diff_type 触发 CHECK 约束异常

**active_observers**
- `test_upsert_observer_updates_existing` — 同 connection_id 二次 upsert 覆盖 name

**audit_log**
- `test_append_audit_returns_log_id` — 审计日志写入并返回 log_id
- `test_audit_payload_check_constraint` — 非法 JSON payload 触发 CHECK 约束异常

---

## 8. 开放问题与风险

### 评审决议（2026-05-27）

Lead（境熙）评审本 spec 时拍定两条收敛，将 spec 从初稿放宽方向拉回 system-architecture v0.1 §4 字段说明的原意：

- transcript_segments 取消 `is_partial` 列：架构 §4 明示 partial 只推 WS、不写库，列建在 schema 里是冗余。后续若需要 partial 入库做 audit / 回放，单开 P3 ticket 评估再加迁移（约束放宽是无损改动）。
- transcript_segments.take_id 维持 `NOT NULL` + `ON DELETE CASCADE`，contract C2 不放宽：Orchestrator 的 asr.final handler 写库前必须检查 `session.take_active`，take 未开始时仅推 WS 不写库。后续若需要保留 take 外 ASR 片段做 audit，单开 P3 ticket 评估再放宽。

这两项决议落实在本 spec §2.7、§6、§7；dev plan v0.2 §8 contract C2 与 architecture v0.1 §4 不变。

### 8.1 SQLite 并发与 WAL 模式

Soundspeed 后端是单 FastAPI worker（uvicorn 单进程）加 asyncio 事件循环。SQLite 在单进程内是线程安全的（`check_same_thread=False`），但 asyncio 任务可能并发调用 DAL。

建议方案：启用 WAL 模式（`PRAGMA journal_mode = WAL`）+ 设置 `PRAGMA busy_timeout = 5000`（5 秒等待锁）。WAL 允许一个写事务与多个读事务并发运行，减少锁冲突。DAL 层的所有写操作用 `BEGIN IMMEDIATE` 显式事务，避免隐式事务竞争。

风险点：asyncio 的多个并发任务同时写库，加上 SQLite WAL 的单写约束，实际上退化为串行写。如果 L2 Pipeline 的写操作与实时 ASR 片段写入发生竞争，`busy_timeout` 是最后一道防线。若写入延迟超过阈值，需考虑引入写队列（与 LLMService 的 PriorityQueue 类似）。

### 8.2 FTS5 召回上限与 jieba 词分

当前用 `trigram` tokenizer，中文 / 英文 / 混合检索按 3-gram 子串匹配。已知召回上限：

- 字符长度 < 3 的 query 走 trigram 的 prefix / partial 行为，准召率比 3+ 字符 query 低。
- 不做语义同义改写、不做近义匹配。语义检索由 QP Pipeline（Gemma 4 E4B）补足。

若实测召回率不足，候选升级路径（已建 P3 ticket，未启动）：

- 写 `script_lines` 时用 jieba 预分词、存到独立 `text_segmented` 列、FTS5 索引该列、tokenizer 改 `simple`。代价：新增 jieba 依赖、写入双列、benchmark 召回率与写吞吐。
- 完全不上 FTS5 词分、跟 QP Pipeline 联合做语义检索，FTS5 只做精确关键词兜底。

本期不做，记录留痕。

### 8.3 scripts 多版本当前版本策略

一场次允许多个剧本版本，但未定义「当前使用哪个版本」的显式标记。当前约定：以最大 `version` 为当前版。影响：SP Pipeline 解析后写 `script_lines` 时绑定到最新 `script_id`；L2 Pipeline 做 diff 时取最大版本的台词行。若需要显式标记「当前版」，可加 `scripts.is_current INTEGER DEFAULT 0`，本期不加，留开放问题。

### 8.4 take_events.payload JSON 拆字段问题

`take_events.payload` 当前设计：JSON TEXT + `event_type` / `ts` 冗余成单独列以支持索引。这一策略下 payload 内容不可被索引，仅支持 `event_type + ts` 范围查询。若未来需要按 `payload` 内字段（如 `status = 'keeper'`）过滤事件，需要升级为生成列（`GENERATED ALWAYS AS (json_extract(payload, '$.status'))`）或拆出专用列。本期不拆，记录问题。

### 8.5 speaker 标签跨 take 稳定性

diarization 模型（pyannote / WhisperX）的 speaker 标签（如 `SPEAKER_00`）是每次运行独立分配的，跨 take 不保证同一演员标签一致。例如 take 1 中 `SPEAKER_00` 是 A 演员，take 2 中 `SPEAKER_00` 可能变成 B 演员。

影响：L2 Pipeline 基于 speaker 聚合片段时会出现跨 take 语义不连贯。前端按 speaker_id 分色显示也会出现跨 take 颜色跳变。

本期不引入 `speaker_map` 辅表（记录 speaker 标签到真实演员名的映射），但记录问题。后续若准确率达标，可加辅表 + 前端配置界面让录音师确认对应关系。

### 8.6 scenes.is_active 对 NP 默认绑定的影响

`scenes.is_active` 标记当前活跃场次，NP Pipeline 处理候补 note 时默认绑定到活跃场次（onset-llm-ux v1.1 用例 2）。此设计假设每次只有一个场次活跃。若拍摄流程需要「同时记录多场次」（如分机位同时拍摄），此约束不成立。当前按单活跃场次设计，`set_active_scene` 在切换时清除其他场次的 `is_active`。

