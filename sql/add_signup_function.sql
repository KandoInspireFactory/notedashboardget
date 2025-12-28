-- ==========================================================
-- Neon Auth 新規登録（Sign Up）用 RPC関数
-- NeonのSQL Editorで実行してください。
-- ==========================================================

CREATE OR REPLACE FUNCTION sign_up(email text, password text)
RETURNS json AS $$
BEGIN
    -- 1. 既に同じメールアドレスが登録されていないかチェック
    IF EXISTS (SELECT 1 FROM app_users WHERE app_users.email = sign_up.email) THEN
        RETURN json_build_object(
            'status', 'error', 
            'message', 'このメールアドレスは既に登録されています。'
        );
    END IF;

    -- 2. ユーザーの挿入（crypt関数を使用してパスワードを暗号化保存）
    -- 拡張機能 pgcrypto が有効である必要があります
    INSERT INTO app_users (email, password_hash)
    VALUES (email, crypt(password, gen_salt('bf')));

    RETURN json_build_object(
        'status', 'success', 
        'message', '登録が完了しました。ログインタブからログインしてください。'
    );
EXCEPTION
    WHEN OTHERS THEN
        RETURN json_build_object(
            'status', 'error', 
            'message', SQLERRM
        );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
