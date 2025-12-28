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
# 1. データベース接続・環境判別・初期化
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

def init_db_schema():
    """データベースのテーブル構造を自動でセットアップ/更新する"""
    db_type, _ = get_db_info()
    try:
        conn = get_connection()
        cursor = conn.cursor()
        if db_type == "postgres":
            cursor.execute('CREATE EXTENSION IF NOT EXISTS pgcrypto;')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS app_users (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    is_approved BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
            ''')
            cursor.execute('''
                DO $$ 
                BEGIN 
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='app_users' AND column_name='is_approved') THEN
                        ALTER TABLE app_users ADD COLUMN is_approved BOOLEAN DEFAULT FALSE;
                    END IF;
                END $$;
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS article_stats (
                    user_id TEXT, acquired_at TEXT, article_id BIGINT, title TEXT,
                    views INTEGER, likes INTEGER, comments INTEGER,
                    PRIMARY KEY (user_id, acquired_at, article_id)
                );
            ''')
        else:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS article_stats (
                    user_id TEXT, acquired_at TEXT, article_id INTEGER, title TEXT,
                    views INTEGER, likes INTEGER, comments INTEGER,
                    PRIMARY KEY (user_id, acquired_at, article_id)
                );
            ''')
        conn.commit()
        conn.close()
    except Exception: pass

def check_stripe_subscription(email):
    """
    Stripe APIを呼び出し、ユーザーが有効なサブスクリプションを持っているか確認。
    管理者(ADMIN_EMAIL)は常にTrueを返す。
    """
    if email == os.getenv("ADMIN_EMAIL"): return True
    if not stripe.api_key: return True
    try:
        customers = stripe.Customer.list(email=email, limit=1).data
        if not customers: return False
        customer_id = customers[0].id
        subs = stripe.Subscription.list(customer=customer_id, status='all', limit=5).data
        for sub in subs:
            # active（支払い済み）または trialling（クーポンによる無料期間）ならOK
            if sub.status in ['active', 'trialling']: return True
        return False
    except Exception: return False

def neon_auth_login(email, password):
    """ログイン認証を行い、Stripeの最新状況を反映させる"""
    db_type, _ = get_db_info()
    if db_type != "postgres": return True, "local"
    try:
        conn = get_connection()
        cursor = conn.cursor()
        query = "SELECT email, is_approved FROM app_users WHERE email = %s AND password_hash = crypt(%s, password_hash)"
        cursor.execute(query, (email, password))
        result = cursor.fetchone()
        
        if result:
            email_res, current_approved = result
            
            # --- サブスク状況の同期チェック ---
            # ログインのたびにStripeを確認し、DBの状態を最新に保つ
            is_currently_paid = check_stripe_subscription(email)
            
            if is_currently_paid != current_approved:
                # Stripeの状態とDBの状態が食い違っていれば更新
                cursor.execute("UPDATE app_users SET is_approved = %s WHERE email = %s", (is_currently_paid, email))
                conn.commit()
                current_approved = is_currently_paid
            
            conn.close()
            
            if current_approved:
                return True, "logged_in"
            else:
                payment_link = os.getenv("STRIPE_PAYMENT_LINK", "#")
                return False, f"⚠️ サブスクリプションが有効ではありません。[こちらの決済リンク]({payment_link}) から再開、または決済を完了させてください。"
        else:
            conn.close()
            return False, "メールアドレスまたはパスワードが正しくありません。"
    except Exception as e: return False, f"認証エラー: {str(e)}"

def neon_auth_signup(email, password):
    """新規ユーザー登録"""
    db_type, _ = get_db_info()
    if db_type != "postgres": return False, "Local mode doesn't support signup."
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
        return True, f"✅ 登録完了！[こちらから決済]({payment_link}) を完了させてください。クーポン `FREE30` (初月無料) 等も利用可能です。完了後にログイン可能になります。"
    except Exception as e: return False, f"登録エラー: {str(e)}"

# --- 管理者メニュー ---
def admin_get_all_users():
    try:
        conn = get_connection(); df = pd.read_sql("SELECT email, is_approved, created_at FROM app_users ORDER BY created_at DESC", conn); conn.close()
        return df
    except Exception: return pd.DataFrame()

def admin_approve_user(email):
    try:
        conn = get_connection(); cursor = conn.cursor()
        cursor.execute("UPDATE app_users SET is_approved = TRUE WHERE email = %s", (email,)); conn.commit(); conn.close()
        return True
    except Exception: return False

def admin_delete_user(email):
    try:
        uid = hashlib.sha256(email.encode()).hexdigest()[:16]
        conn = get_connection(); cursor = conn.cursor()
        cursor.execute("DELETE FROM article_stats WHERE user_id = %s", (uid,))
        cursor.execute("DELETE FROM app_users WHERE email = %s", (email,))
        conn.commit(); conn.close()
        return True
    except Exception: return False

def admin_reset_password(email, new_pwd):
    try:
        conn = get_connection(); cursor = conn.cursor()
        cursor.execute("UPDATE app_users SET password_hash = crypt(%s, gen_salt('bf')) WHERE email = %s", (new_pwd, email))
        conn.commit(); conn.close()
        return True
    except Exception: return False

def get_current_user_id(note_email):
    email = st.session_state.get("app_user_email", note_email)
    if not email: return "guest"
    return hashlib.sha256(email.encode()).hexdigest()[:16]

def get_default_credentials():
    email = ""; password = ""
    try:
        if "note" in st.secrets:
            email = st.secrets["note"].get("email", ""); password = st.secrets["note"].get("password", "")
    except: pass
    if not email: email = os.getenv("NOTE_EMAIL", "")
    if not password: password = os.getenv("NOTE_PASSWORD", "")
    return email, password

def note_auth(session, email, password):
    try:
        r = session.post('https://note.com/api/v1/sessions/sign_in', json={"login": email, "password": password})
        r.raise_for_status()
        if "error" in r.json(): return None
        return session
    except Exception: return None

# =========================================================================
# 2. データ取得・保存
# =========================================================================
def get_articles(session, user_id):
    articles = []; tdy = datetime.now().strftime('%Y-%m-%d'); page = 1
    pb = st.progress(0); txt = st.empty()
    while True:
        txt.text(f"ページ {page} 取得中..."); r = session.get(f'https://note.com/api/v1/stats/pv?filter=all&page={page}&sort=pv')
        data = r.json(); stats = data.get('data', {}).get('note_stats', [])
        if not stats: break
        for item in stats:
            name = item.get('name')
            if name: articles.append((user_id, tdy, item.get('id'), name, item.get('read_count', 0), item.get('like_count', 0), item.get('comment_count', 0)))
        page += 1; pb.progress(min(page * 0.05, 1.0))
    pb.empty(); return articles

def save_data(data, save_dir):
    if not os.path.exists(save_dir): os.makedirs(save_dir)
    db_type, _ = get_db_info()
    try:
        conn = get_connection(); cursor = conn.cursor()
        if db_type == "postgres":
            q = "INSERT INTO article_stats (user_id, acquired_at, article_id, title, views, likes, comments) VALUES %s ON CONFLICT (user_id, acquired_at, article_id) DO NOTHING"
            execute_values(cursor, q, data)
        else: cursor.executemany('INSERT OR IGNORE INTO article_stats VALUES (?, ?, ?, ?, ?, ?, ?)', data)
        conn.commit(); conn.close()
    except Exception as e: st.error(f"保存エラー: {e}")

# =========================================================================
# 4. Streamlit UI
# =========================================================================
def main():
    init_db_schema(); db_type, _ = get_db_info()
    st.set_page_config(page_title="note分析 v7", layout="wide")
    if "app_auth_token" not in st.session_state: st.session_state.app_auth_token = None
    if "app_user_email" not in st.session_state: st.session_state.app_user_email = None

    if db_type == "postgres" and not st.session_state.app_auth_token:
        st.title("🛡️ note分析アプリ ログイン")
        tab_l, tab_s = st.tabs(["ログイン", "新規利用登録"])
        with tab_l:
            with st.form("login"):
                e = st.text_input("メールアドレス"); p = st.text_input("パスワード", type="password")
                if st.form_submit_button("ログイン"):
                    ok, res = neon_auth_login(e, p)
                    if ok: st.session_state.app_auth_token=res; st.session_state.app_user_email=e; st.rerun()
                    else: st.error(res)
        with tab_s:
            st.write("✨ 月額300円のプレミアムツールです。1ヶ月無料クーポン `FREE30` 配信中！")
            with st.form("signup"):
                ne = st.text_input("メールアドレス"); np = st.text_input("パスワード", type="password"); cp = st.text_input("パスワード(確認)", type="password")
                if st.form_submit_button("利用申請を送る"):
                    if np != cp: st.error("パスワード不一致")
                    elif len(np)<4: st.error("4文字以上必要")
                    else: ok, msg = neon_auth_signup(ne, np); st.markdown(msg) if ok else st.error(msg)
        return

    is_admin = (st.session_state.app_user_email == os.getenv("ADMIN_EMAIL")) if os.getenv("ADMIN_EMAIL") else False
    st.sidebar.header("🔑 設定")
    if st.session_state.app_user_email:
        st.sidebar.info(f"👤 {st.session_state.app_user_email}")
        if st.sidebar.button("ログアウト"): st.session_state.app_auth_token=None; st.session_state.app_user_email=None; st.rerun()

    menu = ["ダッシュボード"]; 
    if is_admin: menu.append("🛠️ 管理者画面")
    choice = st.sidebar.radio("メニュー", menu)

    if choice == "🛠️ 管理者画面":
        st.title("🛠️ 管理者ダッシュボード")
        t1, t2 = st.tabs(["ユーザー管理", "一括操作"])
        with t1:
            df = admin_get_all_users()
            if not df.empty:
                df['status'] = df['is_approved'].apply(lambda x: "✅ 承認済" if x else "⏳ 承認待ち")
                st.dataframe(df[['email', 'status', 'created_at']], use_container_width=True)
        with t2:
            with st.form("admin_act"):
                te = st.text_input("対象メール"); act = st.selectbox("操作", ["---", "承認する", "パスワードリセット", "アカウント削除"]); pw = st.text_input("新パスワード", type="password")
                if st.form_submit_button("実行"):
                    if act == "承認する": admin_approve_user(te)
                    elif act == "パスワードリセット": admin_reset_password(te, pw)
                    elif act == "アカウント削除": admin_delete_user(te)
                    st.rerun()
        return

    st.title("📝 note分析ダッシュボード")
    de, dp = get_default_credentials(); ne = st.sidebar.text_input("noteメールアドレス", value=de); np = st.sidebar.text_input("noteパスワード", type="password", value=dp); uid = get_current_user_id(ne)
    
    if st.sidebar.button("最新データを取得する"):
        s = requests.session()
        if note_auth(s, ne, np):
            data = get_articles(s, uid)
            if data: save_data(data, "note_data"); st.rerun()

    try:
        conn = get_connection(); q = "SELECT * FROM article_stats WHERE user_id = %s" if db_type == "postgres" else "SELECT * FROM article_stats WHERE user_id = ?"
        df_all = pd.read_sql(q, conn, params=(uid,)); conn.close()
    except Exception: df_all = pd.DataFrame()

    if not df_all.empty:
        df_all['acquired_at'] = pd.to_datetime(df_all['acquired_at'], format='mixed'); df_all = df_all.sort_values('acquired_at')
        ud = sorted(df_all['acquired_at'].unique()); latest = ud[-1]; df_latest = df_all[df_all['acquired_at'] == latest].sort_values('views', ascending=False)
        has_prev = len(ud) >= 2; vd = 0; df_d = pd.DataFrame()
        if has_prev:
            df_p = df_all[df_all['acquired_at'] == ud[-2]]; df_m = pd.merge(df_latest[['article_id', 'title', 'views']], df_p[['article_id', 'views']], on='article_id', suffixes=('', '_prev'), how='left').fillna(0)
            df_m['views_delta'] = df_m['views'] - df_m['views_prev']; vd = int(df_m['views_delta'].sum()); df_d = df_m.sort_values('views_delta', ascending=False)

        st.info(f"最終更新: {latest.strftime('%Y-%m-%d %H:%M')}")
        c1, c2, c3 = st.columns(3); c1.metric("公開記事数", f"{len(df_latest)} 記事"); c2.metric("累計ビュー", f"{df_latest['views'].sum():,}", delta=f"+{vd:,}" if has_prev else None); c3.metric("累計スキ", f"{df_latest['likes'].sum():,}")
        st.markdown("---")
        if has_prev:
            st.subheader("📈 全体累計ビュー推移")
            tv = df_all.groupby('acquired_at')['views'].sum().reset_index(); fig = px.line(tv, x='acquired_at', y='views'); fig.update_layout(xaxis_type='date', yaxis=dict(tickformat=',d', rangemode='tozero')); st.plotly_chart(fig, use_container_width=True)
        t1, t2, t3 = st.tabs(["📊 累計ランキング", "🔥 本日の伸び", "📈 生データ"])
        with t1:
            fig = px.bar(df_latest.head(20), x='views', y='title', orientation='h', text_auto=True); fig.update_layout(yaxis={'autorange': 'reversed'}, height=600); st.plotly_chart(fig, use_container_width=True)
        with t2:
            if has_prev: fig = px.bar(df_d.head(20), x='views_delta', y='title', orientation='h', text_auto=True); fig.update_layout(yaxis={'autorange': 'reversed'}, height=600); st.plotly_chart(fig, use_container_width=True)
            else: st.info("明日また取得してください。")
        with t3: st.dataframe(df_latest, use_container_width=True)
        st.markdown("---")
        if has_prev:
            st.subheader("📊 個別ビュー数推移")
            ps = df_all[['acquired_at', 'title', 'views']].drop_duplicates(['acquired_at', 'title']); pdf = ps.pivot(index='acquired_at', columns='title', values='views'); fig = go.Figure()
            for t in pdf.columns: fig.add_trace(go.Scatter(x=pdf.index, y=pdf[t], mode='lines', name=t, connectgaps=True))
            fig.update_layout(hovermode='closest', showlegend=False, height=700, xaxis_type='date', yaxis=dict(tickformat=',d')); st.plotly_chart(fig, use_container_width=True)
        if db_type == "sqlite":
            with st.expander("📥 SQLiteダウンロード"):
                with open("note_dashboard.db", "rb") as f: st.download_button("ダウンロード", f, file_name="note_dashboard.db")
    else: st.info("データがありません。")

if __name__ == "__main__":
    main()