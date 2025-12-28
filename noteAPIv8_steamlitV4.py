import streamlit as st
import requests
import json
import traceback
import csv
from datetime import datetime
import os
import sqlite3
import hashlib
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dotenv import load_dotenv

# Optional: PostgreSQL support
try:
    import psycopg2
    from psycopg2.extras import execute_values
    HAS_POSTGRES = True
except ImportError:
    HAS_POSTGRES = False

# .envファイルを読み込む
load_dotenv()

# =========================================================================
# 1. データベース接続・環境判別・認証
# =========================================================================
def get_db_info():
    """環境変数からDBタイプを判別する"""
    db_url = os.getenv("DATABASE_URL")
    if db_url and HAS_POSTGRES:
        return "postgres", db_url
    else:
        return "sqlite", "note_dashboard.db"

def get_connection():
    """適切なデータベース接続を返す"""
    db_type, db_target = get_db_info()
    if db_type == "postgres":
        return psycopg2.connect(db_target)
    else:
        return sqlite3.connect(db_target)

def get_neon_api_url(endpoint):
    """
    環境変数 NEON_DATA_API_URL を元に正しいAPIエンドポイントを構築する。
    """
    base_url = os.getenv("NEON_DATA_API_URL", "").rstrip("/")
    # 既に /rpc が含まれている場合は endpoint を足さない
    if "/rpc/" in base_url:
        return base_url
    return f"{base_url}/rpc/{endpoint}"

def neon_auth_login(email, password):
    """Neon Data APIを使用してログイン認証を行う"""
    api_key = os.getenv("NEON_API_KEY")
    url = get_neon_api_url("sign_in")
    
    if not api_key: return True, "local"
    try:
        # Authorizationヘッダーではなく Neon-Api-Key ヘッダーを使用する
        headers = {
            "Neon-Api-Key": api_key,
            "Content-Type": "application/json"
        }
        response = requests.post(
            url,
            headers=headers,
            json={"email": email, "password": password},
            timeout=10
        )
        if response.status_code == 200:
            res_data = response.json()
            if isinstance(res_data, dict) and res_data.get("status") == "error":
                return False, res_data.get("message", "ログイン失敗")
            return True, res_data.get("token", "logged_in")
        else:
            return False, f"ログイン失敗 ({response.status_code}): {response.text}"
    except Exception as e:
        return False, f"認証エラー: {str(e)}"

def neon_auth_signup(email, password):
    """Neon Data APIを使用して新規ユーザー登録を行う"""
    api_key = os.getenv("NEON_API_KEY")
    url = get_neon_api_url("sign_up")
    
    try:
        # Authorizationヘッダーではなく Neon-Api-Key ヘッダーを使用する
        headers = {
            "Neon-Api-Key": api_key,
            "Content-Type": "application/json"
        }
        response = requests.post(
            url,
            headers=headers,
            json={"email": email, "password": password},
            timeout=10
        )
        if response.status_code == 200:
            res_data = response.json()
            # 戻り値がリスト形式で返ってくる場合があるため対応
            if isinstance(res_data, list) and len(res_data) > 0:
                res_data = res_data[0]
                
            if res_data.get("status") == "success":
                return True, "登録が完了しました。ログインタブからログインしてください。"
            else:
                return False, res_data.get("message", "登録に失敗しました。")
        else:
            try:
                err_msg = response.json().get("message", response.text)
            except:
                err_msg = response.text
            return False, f"登録エラー ({response.status_code}): {err_msg}"
    except Exception as e:
        return False, f"通信エラー: {str(e)}"

# --- 管理者専用機能 ---
def admin_get_all_users():
    """全ユーザーのリストを取得（管理者用）"""
    try:
        conn = get_connection()
        df = pd.read_sql("SELECT email, created_at FROM app_users ORDER BY created_at DESC", conn)
        conn.close()
        return df
    except Exception as e:
        st.error(f"管理者エラー (UserList): {e}")
        return pd.DataFrame()

def admin_reset_password(email, new_password):
    """指定したユーザーのパスワードをリセット（管理者用）"""
    db_type, _ = get_db_info()
    if db_type != "postgres":
        st.error("パスワードリセットはCloud版でのみ利用可能です。")
        return False
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE app_users SET password_hash = crypt(%s, gen_salt('bf')) WHERE email = %s", (new_password, email))
        conn.commit()
        success = cursor.rowcount > 0
        conn.close()
        return success
    except Exception as e:
        st.error(f"管理者エラー (Reset): {e}")
        return False

def get_current_user_id(note_email):
    """ユーザーIDを取得する。"""
    email = st.session_state.get("app_user_email", note_email)
    if not email: return "guest"
    return hashlib.sha256(email.encode()).hexdigest()[:16]

def get_default_credentials():
    """Secrets または環境変数からデフォルトのメールとパスワードを取得する"""
    email = ""
    password = ""
    try:
        if "note" in st.secrets:
            email = st.secrets["note"].get("email", "")
            password = st.secrets["note"].get("password", "")
    except:
        pass
    if not email: email = os.getenv("NOTE_EMAIL", "")
    if not password: password = os.getenv("NOTE_PASSWORD", "")
    return email, password

def note_auth(session, email_address, password):
    """noteにログインしてセッションを認証する"""
    user_data = {"login": email_address, "password": password}
    url = 'https://note.com/api/v1/sessions/sign_in'
    try:
        r = session.post(url, json=user_data)
        r.raise_for_status()
        res_json = r.json()
        if "error" in res_json:
            st.error(f"noteログインエラー: {res_json.get('error', '不明なエラー')}")
            return None
        return session
    except Exception:
        st.error("noteの認証中にエラーが発生しました。")
        return None

# =========================================================================
# 2. データ操作ロジック
# =========================================================================
def get_articles(session, user_id):
    """noteの統計APIから記事データを全件取得する"""
    articles = []
    tdy = datetime.now().strftime('%Y-%m-%d')
    page = 1
    progress_bar = st.progress(0)
    status_text = st.empty()
    while True:
        status_text.text(f"ページ {page} を取得中...")
        url = f'https://note.com/api/v1/stats/pv?filter=all&page={page}&sort=pv'
        try:
            r = session.get(url)
            r.raise_for_status()
            data = r.json()
        except Exception: break
        stats = data.get('data', {}).get('note_stats', [])
        if not stats: break
        for item in stats:
            name = item.get('name')
            if name:
                articles.append((user_id, tdy, item.get('id'), name, item.get('read_count', 0), item.get('like_count', 0), item.get('comment_count', 0)))
        page += 1
        progress_bar.progress(min(page * 0.05, 1.0))
    status_text.text("完了！")
    progress_bar.empty()
    return articles

def save_data(articles_data, save_dir):
    """DBタイプに合わせて保存する"""
    if not os.path.exists(save_dir): os.makedirs(save_dir)
    db_type, _ = get_db_info()
    try:
        conn = get_connection()
        cursor = conn.cursor()
        if db_type == "postgres":
            cursor.execute('CREATE TABLE IF NOT EXISTS article_stats (user_id TEXT, acquired_at TEXT, article_id BIGINT, title TEXT, views INTEGER, likes INTEGER, comments INTEGER, PRIMARY KEY (user_id, acquired_at, article_id))')
            insert_query = "INSERT INTO article_stats (user_id, acquired_at, article_id, title, views, likes, comments) VALUES %s ON CONFLICT (user_id, acquired_at, article_id) DO NOTHING"
            execute_values(cursor, insert_query, articles_data)
        else:
            cursor.execute('CREATE TABLE IF NOT EXISTS article_stats (user_id TEXT, acquired_at TEXT, article_id INTEGER, title TEXT, views INTEGER, likes INTEGER, comments INTEGER, PRIMARY KEY (user_id, acquired_at, article_id))')
            cursor.executemany('INSERT OR IGNORE INTO article_stats VALUES (?, ?, ?, ?, ?, ?, ?)', articles_data)
        conn.commit()
        conn.close()
    except Exception as e: st.error(f"保存エラー: {e}")
    return db_type

# =========================================================================
# 4. Streamlit UI
# =========================================================================
def main():
    db_type, _ = get_db_info()
    st.set_page_config(page_title=f"note分析 v7 ({db_type.capitalize()})", layout="wide")

    if "app_auth_token" not in st.session_state: st.session_state.app_auth_token = None
    if "app_user_email" not in st.session_state: st.session_state.app_user_email = None

    # --- アプリログイン（Neon Auth） ---
    if db_type == "postgres" and os.getenv("NEON_API_KEY") and not st.session_state.app_auth_token:
        st.title("🛡️ note分析アプリ ログイン")
        tab_login, tab_signup = st.tabs(["ログイン", "新規登録"])
        
        with tab_login:
            with st.form("login_form"):
                email = st.text_input("メールアドレス")
                password = st.text_input("パスワード", type="password")
                if st.form_submit_button("ログイン"):
                    success, result = neon_auth_login(email, password)
                    if success:
                        st.session_state.app_auth_token = result
                        st.session_state.app_user_email = email
                        st.success("ログイン成功！")
                        st.rerun()
                    else: st.error(result)
        
        with tab_signup:
            with st.form("signup_form"):
                st.write("新しくアカウントを作成します。")
                new_email = st.text_input("メールアドレス")
                new_password = st.text_input("パスワード", type="password")
                confirm_password = st.text_input("パスワード（確認）", type="password")
                if st.form_submit_button("新規登録"):
                    if new_password != confirm_password: st.error("パスワードが一致しません。")
                    elif len(new_password) < 4: st.error("パスワードは4文字以上で設定してください。")
                    else:
                        success, message = neon_auth_signup(new_email, new_password)
                        if success: st.success(message)
                        else: st.error(message)
        return

    # --- 管理者判定 ---
    is_admin = False
    admin_email_env = os.getenv("ADMIN_EMAIL")
    if st.session_state.app_user_email and admin_email_env:
        if st.session_state.app_user_email == admin_email_env:
            is_admin = True

    # --- サイドバー設定 ---
    st.sidebar.header("🔑 note取得設定")
    if st.session_state.app_user_email:
        st.sidebar.info(f"👤 {st.session_state.app_user_email}")
        if st.sidebar.button("ログアウト"):
            st.session_state.app_auth_token = None
            st.session_state.app_user_email = None
            st.rerun()

    menu = ["ダッシュボード"]
    if is_admin:
        menu.append("🛠️ 管理者画面")
    
    choice = st.sidebar.radio("メニュー", menu)

    # --- [管理者画面] ---
    if choice == "🛠️ 管理者画面":
        st.title("🛠️ 管理者ダッシュボード")
        st.write("登録済みユーザーの管理を行います。")
        
        tab_user_list, tab_reset = st.tabs(["ユーザー一覧", "パスワードリセット"])
        
        with tab_user_list:
            users_df = admin_get_all_users()
            if not users_df.empty:
                st.dataframe(users_df, use_container_width=True)
            else:
                st.write("ユーザーはまだ登録されていません。")
        
        with tab_reset:
            st.warning("指定したユーザーのパスワードを強制的に書き換えます。")
            with st.form("reset_form"):
                target_email = st.text_input("リセット対象のメールアドレス")
                new_pwd = st.text_input("新しいパスワード", type="password")
                if st.form_submit_button("パスワードをリセット"):
                    if target_email and new_pwd:
                        if admin_reset_password(target_email, new_pwd):
                            st.success(f"{target_email} のパスワードを更新しました。")
                        else: st.error("更新に失敗しました。メールアドレスが正しいか確認してください。")
                    else: st.error("全ての項目を入力してください。")
        return

    # --- [ダッシュボード画面] ---
    st.title(f"📝 note分析ダッシュボード (v7 {db_type.capitalize()})")
    default_email, default_pw = get_default_credentials()

    note_email = st.sidebar.text_input("noteメールアドレス", value=default_email)
    note_password = st.sidebar.text_input("noteパスワード", type="password", value=default_pw)
    current_user_id = get_current_user_id(note_email)
    
    if st.sidebar.button("最新データを取得する"):
        if not note_email or not note_password: st.sidebar.error("情報を入力してください。")
        else:
            session = requests.session()
            if note_auth(session, note_email, note_password):
                data = get_articles(session, current_user_id)
                if data:
                    res_db_type = save_data(data, "note_data")
                    st.sidebar.success(f"更新成功！ ({res_db_type})")
                    st.balloons()
                    st.rerun()
                else: st.sidebar.warning("記事なし")

    # データ表示
    try:
        conn = get_connection()
        query = "SELECT * FROM article_stats WHERE user_id = %s" if db_type == "postgres" else "SELECT * FROM article_stats WHERE user_id = ?"
        df_all = pd.read_sql(query, conn, params=(current_user_id,))
        conn.close()
    except Exception:
        df_all = pd.DataFrame()
        st.info("データがありません。サイドバーから取得してください。")

    if not df_all.empty:
        df_all['acquired_at'] = pd.to_datetime(df_all['acquired_at'], format='mixed')
        df_all = df_all.sort_values('acquired_at')
        unique_dates = sorted(df_all['acquired_at'].unique())
        latest_date = unique_dates[-1]
        df_latest = df_all[df_all['acquired_at'] == latest_date].sort_values('views', ascending=False)
        
        has_previous = len(unique_dates) >= 2
        total_views_delta = 0
        df_delta = pd.DataFrame()

        if has_previous:
            previous_date = unique_dates[-2]
            df_prev = df_all[df_all['acquired_at'] == previous_date]
            df_merge = pd.merge(df_latest[['article_id', 'title', 'views']], df_prev[['article_id', 'views']], on='article_id', suffixes=('', '_prev'), how='left').fillna(0)
            df_merge['views_delta'] = df_merge['views'] - df_merge['views_prev']
            total_views_delta = int(df_merge['views_delta'].sum())
            df_delta = df_merge.sort_values('views_delta', ascending=False)

        st.info(f"最終更新: {latest_date.strftime('%Y-%m-%d %H:%M')}")
        c1, c2, c3 = st.columns(3)
        c1.metric("公開記事数", f"{len(df_latest)} 記事")
        c2.metric("累計ビュー", f"{df_latest['views'].sum():,}", delta=f"+{total_views_delta:,}" if has_previous else None)
        c3.metric("累計スキ", f"{df_all[df_all['acquired_at'] == latest_date]['likes'].sum():,}")

        st.markdown("---")
        if has_previous:
            st.subheader("📈 全体累計ビュー推移")
            total_views_df = df_all.groupby('acquired_at')['views'].sum().reset_index()
            fig_total = px.line(total_views_df, x='acquired_at', y='views')
            fig_total.update_layout(xaxis_type='date', yaxis=dict(tickformat=',d', rangemode='tozero'))
            st.plotly_chart(fig_total, use_container_width=True)

        tab1, tab2, tab3 = st.tabs(["📊 累計ランキング", "🔥 本日の伸び", "📈 生データ"])
        with tab1:
            fig = px.bar(df_latest.head(20), x='views', y='title', orientation='h', text_auto=True)
            fig.update_layout(yaxis={'autorange': 'reversed'}, height=600)
            st.plotly_chart(fig, use_container_width=True)
        with tab2:
            if has_previous:
                fig_delta = px.bar(df_delta.head(20), x='views_delta', y='title', orientation='h', text_auto=True)
                fig_delta.update_layout(yaxis={'autorange': 'reversed'}, height=600)
                st.plotly_chart(fig_delta, use_container_width=True)
            else: st.info("明日また取得してください。")
        with tab3: st.dataframe(df_latest, use_container_width=True)

        st.markdown("---")
        if has_previous:
            st.subheader("📊 個別ビュー数推移")
            df_pivot_src = df_all[['acquired_at', 'title', 'views']].drop_duplicates(['acquired_at', 'title'])
            pivot_df = df_pivot_src.pivot(index='acquired_at', columns='title', values='views')
            fig_all = go.Figure()
            for title in pivot_df.columns:
                fig_all.add_trace(go.Scatter(x=pivot_df.index, y=pivot_df[title], mode='lines', name=title, connectgaps=True))
            fig_all.update_layout(hovermode='closest', showlegend=False, height=700, xaxis_type='date', yaxis=dict(tickformat=',d'))
            st.plotly_chart(fig_all, use_container_width=True)

        if db_type == "sqlite":
            with st.expander("📥 データのダウンロード"):
                with open("note_dashboard.db", "rb") as f:
                    st.download_button("SQLite DBダウンロード", f, file_name="note_dashboard.db")
    else: st.info("データがありません。")

if __name__ == "__main__":
    main()