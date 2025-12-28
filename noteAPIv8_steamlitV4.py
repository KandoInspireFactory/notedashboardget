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
import stripe

# Optional: PostgreSQL support
try:
    import psycopg2
    from psycopg2.extras import execute_values
    HAS_POSTGRES = True
except ImportError:
    HAS_POSTGRES = False

# .envファイルを読み込む
load_dotenv()

# Stripeの設定
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

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

def check_stripe_subscription(email):
    """
    Stripe APIを呼び出し、ユーザーが有効なサブスクリプションを持っているか確認する。
    """
    if not stripe.api_key: return True
    try:
        customers = stripe.Customer.list(email=email, limit=1).data
        if not customers: return False
        customer_id = customers[0].id
        subscriptions = stripe.Subscription.list(customer=customer_id, status='all', limit=5).data
        for sub in subscriptions:
            if sub.status in ['active', 'trialling']: return True
        return False
    except Exception as e:
        st.error(f"Stripe確認エラー: {e}")
        return False

def neon_auth_login(email, password):
    """ログイン認証を行い、必要に応じてStripeで支払状況を自動更新する"""
    db_type, _ = get_db_info()
    if db_type != "postgres": return True, "local"
    try:
        conn = get_connection()
        cursor = conn.cursor()
        query = "SELECT email, is_approved FROM app_users WHERE email = %s AND password_hash = crypt(%s, password_hash)"
        cursor.execute(query, (email, password))
        result = cursor.fetchone()
        if result:
            email_res, is_approved = result
            if not is_approved:
                if check_stripe_subscription(email):
                    cursor.execute("UPDATE app_users SET is_approved = TRUE WHERE email = %s", (email,))
                    conn.commit()
                    is_approved = True
            conn.close()
            if is_approved: return True, "logged_in"
            else:
                payment_link = os.getenv("STRIPE_PAYMENT_LINK", "#")
                return False, f"⚠️ 支払いが確認できていないか、承認待ちです。[こちらから決済]({payment_link})を完了させてから再度ログインしてください。クーポンもリンク先で入力可能です。"
        else:
            conn.close()
            return False, "メールアドレスまたはパスワードが正しくありません。"
    except Exception as e: return False, f"認証エラー: {str(e)}"

def neon_auth_signup(email, password):
    """新規ユーザー登録を行う"""
    db_type, _ = get_db_info()
    if db_type != "postgres": return False, "ローカルモードでは新規登録できません。"
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM app_users WHERE email = %s", (email,))
        if cursor.fetchone():
            conn.close()
            return False, "このメールアドレスは既に登録されています。"
        cursor.execute("INSERT INTO app_users (email, password_hash) VALUES (%s, crypt(%s, gen_salt('bf')))", (email, password))
        conn.commit()
        conn.close()
        payment_link = os.getenv("STRIPE_PAYMENT_LINK", "#")
        return True, f"✅ アカウントを作成しました！\n\n[こちらのStripe決済リンク]({payment_link}) から月額300円の決済を完了させてください。\n\n**🎁 限定クーポン配信中！**\n決済画面で以下を入力するとお得に利用開始できます：\n- **`FREE30`** (最初の1ヶ月無料)\n- **`SPECIAL200`** (ずっと月額200円)\n\n決済完了後、ログインできるようになります。"
    except Exception as e: return False, f"登録エラー: {str(e)}"

# --- 管理者専用機能 ---
def admin_get_all_users():
    """全ユーザーのリストを取得"""
    try:
        conn = get_connection()
        df = pd.read_sql("SELECT email, is_approved, created_at FROM app_users ORDER BY created_at DESC", conn)
        conn.close()
        return df
    except Exception as e:
        st.error(f"管理者エラー: {e}")
        return pd.DataFrame()

def admin_approve_user(email):
    """ユーザーを承認"""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE app_users SET is_approved = TRUE WHERE email = %s", (email,))
        conn.commit()
        conn.close()
        return True
    except Exception: return False

def admin_delete_user(email):
    """ユーザーとデータを削除"""
    try:
        user_id = hashlib.sha256(email.encode()).hexdigest()[:16]
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM article_stats WHERE user_id = %s", (user_id,))
        cursor.execute("DELETE FROM app_users WHERE email = %s", (email,))
        conn.commit()
        conn.close()
        return True
    except Exception: return False

def admin_reset_password(email, new_password):
    """パスワードリセット"""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE app_users SET password_hash = crypt(%s, gen_salt('bf')) WHERE email = %s", (new_password, email))
        conn.commit()
        success = cursor.rowcount > 0
        conn.close()
        return success
    except Exception: return False

def get_current_user_id(note_email):
    """ユーザーIDを取得"""
    email = st.session_state.get("app_user_email", note_email)
    if not email: return "guest"
    return hashlib.sha256(email.encode()).hexdigest()[:16]

def get_default_credentials():
    """Secrets/環境変数から認証情報取得"""
    email = ""
    password = ""
    try:
        if "note" in st.secrets:
            email = st.secrets["note"].get("email", "")
            password = st.secrets["note"].get("password", "")
    except: pass
    if not email: email = os.getenv("NOTE_EMAIL", "")
    if not password: password = os.getenv("NOTE_PASSWORD", "")
    return email, password

def note_auth(session, email_address, password):
    """noteにログイン"""
    user_data = {"login": email_address, "password": password}
    url = 'https://note.com/api/v1/sessions/sign_in'
    try:
        r = session.post(url, json=user_data)
        r.raise_for_status()
        res_json = r.json()
        if "error" in res_json: return None
        return session
    except Exception: return None

# =========================================================================
# 2. データ操作ロジック
# =========================================================================
def get_articles(session, user_id):
    """記事データを取得"""
    articles = []
    tdy = datetime.now().strftime('%Y-%m-%d')
    page = 1
    progress_bar = st.progress(0)
    status_text = st.empty()
    while True:
        status_text.text(f"ページ {page} 取得中...")
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
            if name: articles.append((user_id, tdy, item.get('id'), name, item.get('read_count', 0), item.get('like_count', 0), item.get('comment_count', 0)))
        page += 1
        progress_bar.progress(min(page * 0.05, 1.0))
    status_text.text("完了！")
    progress_bar.empty()
    return articles

def save_data(articles_data, save_dir):
    """保存"""
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
    st.set_page_config(page_title=f"note分析 v7", layout="wide")

    if "app_auth_token" not in st.session_state: st.session_state.app_auth_token = None
    if "app_user_email" not in st.session_state: st.session_state.app_user_email = None

    # --- アプリログイン ---
    if db_type == "postgres" and not st.session_state.app_auth_token:
        st.title("🛡️ note分析アプリ ログイン")
        tab_login, tab_signup = st.tabs(["ログイン", "新規利用登録"])
        
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
            st.write("✨ **プレミアム note 分析ツール (v7 Cloud)**")
            st.write("記事の伸びを自動で記録し、あなたの運用を加速させる強力なパートナーです。")
            st.info("【ご利用料金】 月額 300 円")
            
            with st.form("signup_form"):
                new_email = st.text_input("メールアドレス")
                new_password = st.text_input("パスワード", type="password")
                confirm_password = st.text_input("パスワード（確認）", type="password")
                if st.form_submit_button("利用申請を送る"):
                    if new_password != confirm_password: st.error("パスワードが一致しません。")
                    elif len(new_password) < 4: st.error("パスワードは4文字以上で設定してください。")
                    else:
                        success, message = neon_auth_signup(new_email, new_password)
                        if success: st.markdown(message)
                        else: st.error(message)
        return

    # --- 管理者判定 ---
    is_admin = (st.session_state.app_user_email == os.getenv("ADMIN_EMAIL")) if os.getenv("ADMIN_EMAIL") else False

    # --- サイドバー設定 ---
    st.sidebar.header("🔑 note取得設定")
    if st.session_state.app_user_email:
        st.sidebar.info(f"👤 {st.session_state.app_user_email}")
        if st.sidebar.button("ログアウト"):
            st.session_state.app_auth_token = None
            st.session_state.app_user_email = None
            st.rerun()

    menu = ["ダッシュボード"]
    if is_admin: menu.append("🛠️ 管理者画面")
    choice = st.sidebar.radio("メニュー", menu)

    # --- 管理者画面 ---
    if choice == "🛠️ 管理者画面":
        st.title("🛠️ 管理者ダッシュボード")
        tab_user_list, tab_actions = st.tabs(["ユーザー管理", "一括操作"])
        with tab_user_list:
            users_df = admin_get_all_users()
            if not users_df.empty:
                users_df['status'] = users_df['is_approved'].apply(lambda x: "✅ 承認済" if x else "⏳ 承認待ち")
                st.dataframe(users_df[['email', 'status', 'created_at']], use_container_width=True)
        with tab_actions:
            with st.form("admin_action_form"):
                target_email = st.text_input("対象のメールアドレス")
                action = st.selectbox("操作", ["---", "承認する", "パスワードリセット", "アカウント削除"])
                new_pwd = st.text_input("新しいパスワード", type="password")
                if st.form_submit_button("実行"):
                    if action == "承認する": admin_approve_user(target_email)
                    elif action == "パスワードリセット": admin_reset_password(target_email, new_pwd)
                    elif action == "アカウント削除": admin_delete_user(target_email)
                    st.rerun()
        return

    # --- メインダッシュボード ---
    st.title(f"📝 note分析ダッシュボード")
    default_email, default_pw = get_default_credentials()
    note_email = st.sidebar.text_input("noteメールアドレス", value=default_email)
    note_password = st.sidebar.text_input("noteパスワード", type="password", value=default_pw)
    current_user_id = get_current_user_id(note_email)
    
    if st.sidebar.button("最新データを取得する"):
        session = requests.session()
        if note_auth(session, note_email, note_password):
            data = get_articles(session, current_user_id)
            if data:
                save_data(data, "note_data")
                st.rerun()

    # データ表示
    try:
        conn = get_connection()
        query = "SELECT * FROM article_stats WHERE user_id = %s" if db_type == "postgres" else "SELECT * FROM article_stats WHERE user_id = ?"
        df_all = pd.read_sql(query, conn, params=(current_user_id,))
        conn.close()
    except Exception: df_all = pd.DataFrame()

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