-- ==========================================================
-- 承認フラグの追加と管理者承認 SQL
-- NeonのSQL Editorで実行してください。
-- ==========================================================

-- 1. 承認フラグ（is_approved）をテーブルに追加
-- ※プログラム側の自動修正機能でも追加されますが、明示的に実行する場合はこちら
ALTER TABLE app_users ADD COLUMN IF NOT EXISTS is_approved BOOLEAN DEFAULT FALSE;

-- 2. 管理者を「承認済み」に変更
-- 以下のメールアドレスをご自身のものに書き換えて実行してください
UPDATE app_users SET is_approved = TRUE WHERE email = 's3792.01@gmail.com';

-- 3. 状態の確認
SELECT email, is_approved FROM app_users;
