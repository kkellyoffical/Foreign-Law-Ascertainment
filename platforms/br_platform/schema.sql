-- 巴西域外法查明系统：应用表（app_ 前缀，与既有检索表隔离）
-- 由 bootstrap.py 幂等执行（按 ';' 拆分逐条运行；勿在此文件加入含分号的函数体）

CREATE TABLE IF NOT EXISTS app_users (
    id                   BIGSERIAL PRIMARY KEY,
    username             TEXT UNIQUE NOT NULL,
    password_hash        TEXT NOT NULL,
    role                 TEXT NOT NULL DEFAULT 'researcher' CHECK (role IN ('admin', 'researcher')),
    display_name         TEXT,
    is_active            BOOLEAN NOT NULL DEFAULT true,
    must_change_password BOOLEAN NOT NULL DEFAULT false,
    created_by           BIGINT REFERENCES app_users(id),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at        TIMESTAMPTZ,
    -- 改密/重置时间戳：早于此时间签发的 JWT 一律失效（服务端撤销）
    password_changed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app_invitations (
    id          BIGSERIAL PRIMARY KEY,
    code        TEXT UNIQUE NOT NULL,
    role        TEXT NOT NULL DEFAULT 'researcher' CHECK (role IN ('admin', 'researcher')),
    note        TEXT,
    created_by  BIGINT REFERENCES app_users(id),
    expires_at  TIMESTAMPTZ,
    used_by     BIGINT REFERENCES app_users(id),
    used_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app_conversations (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    title       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_app_conversations_user ON app_conversations(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS app_messages (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id BIGINT NOT NULL REFERENCES app_conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content         TEXT NOT NULL,
    citations       JSONB,
    tool_trace      JSONB,
    -- 引用核验结果（V1.2 引入；{summary:{verified,partial,declared,unverified,total}, items:[…]}）
    verification    JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_app_messages_conv ON app_messages(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS app_audit_log (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT REFERENCES app_users(id) ON DELETE SET NULL,
    action      TEXT NOT NULL,
    detail      JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_app_audit_user ON app_audit_log(user_id, created_at DESC);
