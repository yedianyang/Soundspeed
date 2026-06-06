-- v9: script_uploads —— 上传但尚未分场的原始剧本文档（上传/解析两段拆分）
-- 上传只入此表（秒回、不碰 Gemma）；解析步骤读此表 raw_text 跑 LLM → 建 scenes。
PRAGMA user_version = 9;

CREATE TABLE IF NOT EXISTS script_uploads (
    upload_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    filename    TEXT    NOT NULL,
    raw_text    TEXT    NOT NULL,                   -- 提取出的纯文本（待解析）
    char_count  INTEGER NOT NULL,                   -- 字符数（前端展示用）
    status      TEXT    NOT NULL DEFAULT 'uploaded',-- uploaded | parsing | parsed | error
    detail      TEXT,                               -- 解析结果摘要 / 错误信息
    created_at  REAL    NOT NULL DEFAULT (CAST(strftime('%s', 'now') AS REAL)),
    updated_at  REAL    NOT NULL DEFAULT (CAST(strftime('%s', 'now') AS REAL))
);
