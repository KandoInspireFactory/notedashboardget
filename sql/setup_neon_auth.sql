-- ==========================================================
-- Neon Auth (Data API / RPC) セットアップ用SQL
-- NeonのSQL Editorで実行してください。
-- ==========================================================

-- 1. パスワード暗号化のための拡張機能を有効化
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- 2. アプリのユーザーを管理するテーブル
CREATE TABLE IF NOT EXISTS app_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. ログイン用関数（RPC）の作成
-- Streamlitから呼ばれる sign_in 関数を定義します
CREATE OR REPLACE FUNCTION sign_in(email text, password text)
RETURNS json AS $$
DECLARE
    user_record record;
BEGIN
    -- ユーザーの照合
    SELECT * INTO user_record FROM app_users WHERE app_users.email = sign_in.email;
    
    -- パスワードの検証
    IF user_record.password_hash = crypt(password, user_record.password_hash) THEN
        RETURN json_build_object(
            'status', 'success',
            'token', encode(gen_random_bytes(32), 'hex'), -- セッション用トークン
            'email', user_record.email
        );
    ELSE
        RAISE EXCEPTION 'Invalid email or password';
    END IF;
EXCEPTION
    WHEN OTHERS THEN
        RETURN json_build_object(
            'status', 'error',
            'message', SQLERRM
        );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ==========================================================
-- 4. ユーザー登録（実行例）
-- ==========================================================
-- 以下の 'your_password' などを書き換えて実行してください
-- INSERT INTO app_users (email, password_hash)
-- VALUES ('user@example.com', crypt('your_password', gen_salt('bf')));
