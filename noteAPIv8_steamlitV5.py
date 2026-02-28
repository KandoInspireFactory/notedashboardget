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
import io
import time

# 2026/02/27 認証仕様変更への対応
DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'X-Requested-With': 'XMLHttpRequest',
    'Origin': 'https://note.com',
    'Referer': 'https://note.com/login'
}

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

# データディレクトリの設定
DATA_DIR = "note_data"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# 旧DBの移行処理 (ルートにDBがあり、新フォルダにない場合)
OLD_DB = "note_dashboard.db"
NEW_DB = os.path.join(DATA_DIR, "note_dashboard.db")
if os.path.exists(OLD_DB) and not os.path.exists(NEW_DB):
    try:
        import shutil
        shutil.move(OLD_DB, NEW_DB)
        # st.toastを使うとUI起動前にエラーになる可能性があるためログのみ
        print(f"INFO: Moved {OLD_DB} to {NEW_DB}")
    except Exception as e:
        print(f"ERROR: Failed to move DB: {e}")

# =========================================================================
# 1. データベース接続・初期化
# =========================================================================
def get_db_info():
    db_url = os.getenv("DATABASE_URL")
    if db_url and HAS_POSTGRES: 
        return "postgres", db_url
    else: 
        return "sqlite", NEW_DB

def get_connection():
    db_type, db_target = get_db_info()
    if db_type == "postgres": return psycopg2.connect(db_target)
    else: return sqlite3.connect(db_target)

def init_db_schema():
    db_type, _ = get_db_info()
    try:
        conn = get_connection(); cursor = conn.cursor()
        if db_type == "postgres":
            cursor.execute('CREATE EXTENSION IF NOT EXISTS pgcrypto;')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS app_users (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    is_approved BOOLEAN DEFAULT FALSE,
                    skip_stripe BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
            ''')
            cols = ["is_approved", "skip_stripe"]
            for col in cols:
                cursor.execute(f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='app_users' AND column_name='{col}') THEN ALTER TABLE app_users ADD COLUMN {col} BOOLEAN DEFAULT FALSE; END IF; END $$")
            cursor.execute('CREATE TABLE IF NOT EXISTS article_stats (user_id TEXT, acquired_at TEXT, article_id BIGINT, title TEXT, views INTEGER, likes INTEGER, comments INTEGER, PRIMARY KEY (user_id, acquired_at, article_id));')
        else:
            cursor.execute('CREATE TABLE IF NOT EXISTS article_stats (user_id TEXT, acquired_at TEXT, article_id INTEGER, title TEXT, views INTEGER, likes INTEGER, comments INTEGER, PRIMARY KEY (user_id, acquired_at, article_id));')
        conn.commit(); conn.close()
    except Exception: pass

def check_stripe_subscription(email):
    if email == os.getenv("ADMIN_EMAIL"): return True
    if not stripe.api_key: return True
    try:
        customers = stripe.Customer.list(email=email, limit=1).data
        if not customers: return False
        customer_id = customers[0].id
        subs = stripe.Subscription.list(customer=customer_id, status='all', limit=5).data
        for sub in subs:
            if sub.status in ['active', 'trialling']: return True
        return False
    except Exception: return False

def neon_auth_login(email, password):
    db_type, _ = get_db_info()
    if db_type != "postgres": return True, "local"
    try:
        conn = get_connection(); cursor = conn.cursor()
        query = "SELECT email, is_approved, skip_stripe FROM app_users WHERE email = %s AND password_hash = crypt(%s, password_hash)"
        cursor.execute(query, (email, password))
        result = cursor.fetchone()
        if result:
            email_res, current_approved, skip_stripe = result
            access_allowed = True if skip_stripe else check_stripe_subscription(email)
            if access_allowed != current_approved:
                cursor.execute("UPDATE app_users SET is_approved = %s WHERE email = %s", (access_allowed, email))
                conn.commit()
            conn.close()
            if access_allowed: return True, "logged_in"
            else:
                p_link = os.getenv("STRIPE_PAYMENT_LINK", "#")
                return False, f"⚠️ サブスクリプションが有効ではありません。[こちらのリンク]({p_link}) から決済を完了させてください。"
        else:
            conn.close(); return False, "メールアドレスまたはパスワードが正しくありません。"
    except Exception as e: return False, f"認証エラー: {str(e)}"

def neon_auth_signup(email, password):
    db_type, _ = get_db_info()
    if db_type != "postgres": return False, "Local mode doesn't support signup."
    try:
        conn = get_connection(); cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM app_users WHERE email = %s", (email,))
        if cursor.fetchone():
            conn.close(); return False, "このメールアドレスは既に登録されています。"
        cursor.execute("INSERT INTO app_users (email, password_hash) VALUES (%s, crypt(%s, gen_salt('bf')))", (email, password))
        conn.commit(); conn.close()
        p_link = os.getenv("STRIPE_PAYMENT_LINK", "#")
        return True, f"✅ 登録が完了しました！\n\n[こちらのStripe決済リンク]({p_link}) から月額300円の決済を完了させてください。\n\nクーポン `FREE30` で1ヶ月無料、`SPECIAL200` でずっと200円になります。決済完了後、すぐにログイン可能です。"
    except Exception as e: return False, f"登録エラー: {str(e)}"

# --- 管理機能 ---
def admin_get_all_users():
    try:
        conn = get_connection(); df = pd.read_sql("SELECT email, is_approved, skip_stripe, created_at FROM app_users ORDER BY created_at DESC", conn); conn.close()
        return df
    except Exception: return pd.DataFrame()

def admin_delete_user(email):
    try:
        uid = hashlib.sha256(email.encode()).hexdigest()[:16]
        conn = get_connection(); cursor = conn.cursor()
        cursor.execute("DELETE FROM article_stats WHERE user_id = %s", (uid,))
        cursor.execute("DELETE FROM app_users WHERE email = %s", (email,))
        conn.commit(); conn.close()
        return True
    except Exception: return False

def admin_update_skip_stripe(email, skip_stripe_value):
    try:
        conn = get_connection(); cursor = conn.cursor()
        cursor.execute("UPDATE app_users SET skip_stripe = %s, is_approved = %s WHERE email = %s", (skip_stripe_value, skip_stripe_value, email))
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
        # 1. 初期Cookie取得
        session.get('https://note.com/login', headers=DEFAULT_HEADERS)
        time.sleep(1)

        # 2. ログイン実行
        r = session.post('https://note.com/api/v1/sessions/sign_in', json={"login": email, "password": password}, headers=DEFAULT_HEADERS)
        
        try:
            r2 = r.json()
        except json.JSONDecodeError:
            return None

        if r.status_code in [200, 201] and "data" in r2:
            return session
        return None
    except Exception: return None

# =========================================================================
# 2. データ取得・表示・インポート
# =========================================================================
def get_articles(session, user_id):
    articles = []; tdy = datetime.now().strftime('%Y-%m-%d'); page = 1
    pb = st.progress(0); txt = st.empty()
    while True:
        txt.text(f"ページ {page} 取得中...")
        try:
            r = session.get(f'https://note.com/api/v1/stats/pv?filter=all&page={page}&sort=pv', headers=DEFAULT_HEADERS)
            data = r.json(); stats = data.get('data', {}).get('note_stats', [])
            if not stats: break
            for item in stats:
                name = item.get('name')
                if name: articles.append((user_id, tdy, item.get('id'), name, item.get('read_count', 0), item.get('like_count', 0), item.get('comment_count', 0)))
            page += 1; pb.progress(min(page * 0.05, 1.0))
        except Exception: break
    pb.empty(); return articles

def save_data(data, save_dir=None):
    db_type, _ = get_db_info()
    try:
        conn = get_connection(); cursor = conn.cursor()
        if db_type == "postgres":
            q = "INSERT INTO article_stats (user_id, acquired_at, article_id, title, views, likes, comments) VALUES %s ON CONFLICT (user_id, acquired_at, article_id) DO NOTHING"
            execute_values(cursor, q, data)
        else: cursor.executemany('INSERT OR IGNORE INTO article_stats VALUES (?, ?, ?, ?, ?, ?, ?)', data)
        conn.commit(); conn.close()
        return True
    except Exception as e: 
        st.error(f"保存エラー: {e}")
        return False

def import_excel_data(uploaded_files, user_id):
    added_dates = set()
    total_added = 0
    
    # 最新のタイトル->ID対応表を作成
    db_type, _ = get_db_info()
    conn = get_connection(); cursor = conn.cursor()
    if db_type == "postgres":
        cursor.execute("SELECT title, article_id FROM article_stats WHERE user_id = %s ORDER BY acquired_at DESC", (user_id,))
    else:
        cursor.execute("SELECT title, article_id FROM article_stats WHERE user_id = ? ORDER BY acquired_at DESC", (user_id,))
    
    title_to_id = {}
    for t, aid in cursor.fetchall():
        if t not in title_to_id: title_to_id[t] = aid
    conn.close()

    for uploaded_file in uploaded_files:
        try:
            fname = uploaded_file.name.lower()
            if fname.endswith(".csv"):
                try:
                    df = pd.read_csv(uploaded_file, encoding="cp932")
                except:
                    uploaded_file.seek(0)
                    df = pd.read_csv(uploaded_file, encoding="utf-8")
            else:
                df = pd.read_excel(uploaded_file)
            
            col_map = {
                '日付': 'acquired_at', 'acquired_at': 'acquired_at', '日時': 'acquired_at',
                'タイトル': 'title', 'title': 'title', '記事名': 'title',
                'ビュー数': 'views', 'views': 'views', 'PV': 'views', 'ビュー': 'views',
                'スキ数': 'likes', 'likes': 'likes', 'スキ': 'likes',
                'コメント数': 'comments', 'comments': 'comments', 'コメント': 'comments'
            }
            df = df.rename(columns=lambda x: col_map.get(str(x).strip(), x))
            
            required_cols = ['acquired_at', 'title', 'views']
            if not all(col in df.columns for col in required_cols):
                st.error(f"ファイル {uploaded_file.name} に必要な列（日付, タイトル, ビュー数）が見つかりません。")
                continue
            
            data_to_save = []
            for _, row in df.iterrows():
                dt = pd.to_datetime(row['acquired_at']).strftime('%Y-%m-%d')
                title = str(row['title']).strip()
                article_id = title_to_id.get(title)
                if article_id is None:
                    article_id = -abs(int(hashlib.md5(title.encode()).hexdigest(), 16) % (10**10))
                
                data_to_save.append((
                    user_id, dt, article_id, title,
                    int(row.get('views', 0)),
                    int(row.get('likes', 0)),
                    int(row.get('comments', 0))
                ))
                added_dates.add(dt)
            
            conn = get_connection(); cursor = conn.cursor()
            if db_type == "postgres":
                cursor.execute("SELECT count(*) FROM article_stats WHERE user_id = %s", (user_id,))
                count_before = cursor.fetchone()[0]
                q = "INSERT INTO article_stats (user_id, acquired_at, article_id, title, views, likes, comments) VALUES %s ON CONFLICT (user_id, acquired_at, article_id) DO NOTHING"
                execute_values(cursor, q, data_to_save)
                cursor.execute("SELECT count(*) FROM article_stats WHERE user_id = %s", (user_id,))
                count_after = cursor.fetchone()[0]
            else:
                cursor.execute("SELECT count(*) FROM article_stats WHERE user_id = ?", (user_id,))
                count_before = cursor.fetchone()[0]
                cursor.executemany('INSERT OR IGNORE INTO article_stats VALUES (?, ?, ?, ?, ?, ?, ?)', data_to_save)
                cursor.execute("SELECT count(*) FROM article_stats WHERE user_id = ?", (user_id,))
                count_after = cursor.fetchone()[0]
            
            conn.commit(); conn.close()
            total_added += (count_after - count_before)
            
        except Exception as e:
            st.error(f"ファイル {uploaded_file.name} の処理中にエラーが発生しました: {e}")
            
    return total_added, sorted(list(added_dates))



def get_sqlite_binary(user_id):


    """PostgresまたはSQLiteのデータをSQLiteバイナリとして取得する"""
    db_type, db_target = get_db_info()
    
    # メモリ上に一時的なSQLiteを作成
    mem_conn = sqlite3.connect(':memory:')
    mem_cursor = mem_conn.cursor()
    mem_cursor.execute('CREATE TABLE article_stats (user_id TEXT, acquired_at TEXT, article_id INTEGER, title TEXT, views INTEGER, likes INTEGER, comments INTEGER, PRIMARY KEY (user_id, acquired_at, article_id));')
    
    # データを取得
    conn = get_connection(); cursor = conn.cursor()
    if db_type == "postgres":
        cursor.execute("SELECT * FROM article_stats WHERE user_id = %s", (user_id,))
    else:
        cursor.execute("SELECT * FROM article_stats WHERE user_id = ?", (user_id,))
    
    rows = cursor.fetchall()
    mem_cursor.executemany('INSERT INTO article_stats VALUES (?, ?, ?, ?, ?, ?, ?)', rows)
    mem_conn.commit()
    
    # バイナリに変換
    query = "".join(line for line in mem_conn.iterdump())
    
    # 実際には iterdump ではなく、バイナリとして保存したファイルを読み出す必要がある
    # 一時ファイルを使用
    temp_db_path = os.path.join(DATA_DIR, f"temp_dl_{user_id}.db")
    temp_conn = sqlite3.connect(temp_db_path)
    mem_conn.backup(temp_conn)
    temp_conn.close()
    mem_conn.close()
    conn.close()
    
    with open(temp_db_path, "rb") as f:
        data = f.read()
    
    os.remove(temp_db_path)
    return data

# =========================================================================
# 4. Streamlit UI
# =========================================================================
def main():
    init_db_schema(); db_type, _ = get_db_info()
    st.set_page_config(page_title="note分析 v5", layout="wide")
    if "app_auth_token" not in st.session_state: st.session_state.app_auth_token = None
    if "app_user_email" not in st.session_state: st.session_state.app_user_email = None

    # --- アプリログイン ---
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
            st.write("✨ **プレミアム note 分析ツール (v5 Cloud)**")
            st.info("【ご利用料金】 月額 300 円")
            with st.form("signup"):
                ne = st.text_input("メールアドレス"); np = st.text_input("パスワード", type="password"); cp = st.text_input("パスワード(確認)", type="password")
                if st.form_submit_button("アカウントを作成して決済へ進む"):
                    if np != cp: st.error("パスワード不一致")
                    elif len(np)<4: st.error("4文字以上必要")
                    else: ok, msg = neon_auth_signup(ne, np); st.info(msg) if ok else st.error(msg)
        return

    is_admin = (st.session_state.app_user_email == os.getenv("ADMIN_EMAIL")) if os.getenv("ADMIN_EMAIL") else False
    st.sidebar.header("🔑 設定")
    if st.session_state.app_user_email:
        st.sidebar.info(f"👤 {st.session_state.app_user_email}")
        if st.sidebar.button("ログアウト"):
            st.session_state.app_auth_token=None
            st.session_state.app_user_email=None
            st.rerun()

    menu = ["ダッシュボード", "📥 データ管理"]; 
    if is_admin: menu.append("🛠️ ユーザー管理")
    choice = st.sidebar.radio("メニュー", menu)

    # 共通データ取得
    de, dp = get_default_credentials()
    ne = st.sidebar.text_input("noteメールアドレス", value=de)
    np = st.sidebar.text_input("noteパスワード", type="password", value=dp)
    uid = get_current_user_id(ne)

    if choice == "🛠️ ユーザー管理":
        st.title("🛠️ 管理者画面")
        df = admin_get_all_users()
        if not df.empty:
            st.subheader("ユーザー一覧・権限設定")
            for index, row in df.iterrows():
                c1, c2 = st.columns([3, 1])
                status_icon = "👑 永久許可" if row['skip_stripe'] else ("✅ 決済済" if row['is_approved'] else "⏳ 未決済")
                with c1:
                    st.write(f"**{row['email']}**")
                    st.caption(f"Status: {status_icon} | Created: {row['created_at']}")
                with c2:
                    if row['skip_stripe']:
                        if st.button("永久許可を解除", key=f"rev_{index}"):
                            if admin_update_skip_stripe(row['email'], False): st.success("解除しました"); st.rerun()
                    else:
                        if st.button("永久許可を与える", key=f"grant_{index}"):
                            if admin_update_skip_stripe(row['email'], True): st.success("許可しました"); st.rerun()
                st.divider()

            with st.expander("🗑️ アカウントの削除"):
                te = st.text_input("削除するメールアドレス")
                if st.button("完全に削除する"):
                    if admin_delete_user(te): st.success(f"{te} を削除しました。"); st.rerun()
        return

    if choice == "📥 データ管理":
        st.title("📥 データ管理 (CSVインポート & 同期)")
        
        # データ取得状況の可視化
        try:
            conn = get_connection()
            q = "SELECT DISTINCT acquired_at FROM article_stats WHERE user_id = %s" if db_type == "postgres" else "SELECT DISTINCT acquired_at FROM article_stats WHERE user_id = ?"
            df_dates = pd.read_sql(q, conn, params=(uid,))
            conn.close()
            
            if not df_dates.empty:
                st.subheader("📅 データ取得状況 (直近6ヶ月)")
                df_dates['acquired_at'] = pd.to_datetime(df_dates['acquired_at'], format='mixed')
                acquisition_dates = df_dates['acquired_at'].dt.date.unique()
                
                end_date = datetime.now().date()
                start_date = end_date - pd.DateOffset(months=5)
                start_date = start_date.replace(day=1).date()
                
                all_days = pd.date_range(start_date, end_date)
                status_df = pd.DataFrame({'date': all_days.date})
                status_df['status'] = status_df['date'].apply(lambda x: 1 if x in acquisition_dates else 0)
                status_df['month'] = pd.to_datetime(status_df['date']).dt.strftime('%Y-%m')
                status_df['day'] = pd.to_datetime(status_df['date']).dt.day
                
                pivot_status = status_df.pivot(index='month', columns='day', values='status').fillna(-1)
                
                colors = [[0.0, "#ffffff"], [0.4, "#ffffff"], [0.5, "#f0f0f0"], [0.6, "#f0f0f0"], [1.0, "#2ea44f"]]
                fig_heat = px.imshow(
                    pivot_status,
                    labels=dict(x="日", y="月", color="取得状況"),
                    x=pivot_status.columns,
                    y=pivot_status.index,
                    color_continuous_scale=colors,
                    zmin=-1, zmax=1, height=280
                )
                fig_heat.update_coloraxes(showscale=False)
                fig_heat.update_layout(xaxis=dict(dtick=1, side="top"), yaxis=dict(autorange="reversed"), margin=dict(l=10, r=10, t=30, b=10))
                st.plotly_chart(fig_heat, use_container_width=True)
                st.caption("🟢: データあり / ⚪: データなし")
            else:
                st.info("データがまだありません。最新データを取得するか、CSVをインポートしてください。")
        except Exception as e:
            st.error(f"取得状況の読み込み中にエラーが発生しました: {e}")

        st.markdown("---")
        st.subheader("1. 過去データの取り込み")
        st.info("CSVまたはExcelファイル (.csv / .xlsx / .xlsm) をアップロードしてください。列名に「日付」「タイトル」「ビュー数」が含まれている必要があります。")
        
        # サンプルデータの提供 (Excelで文字化けしないようShift-JISでエンコード)
        sample_text = "日付,タイトル,ビュー数,スキ数,コメント数\n2023-12-01,サンプル記事A,150,15,2\n2023-12-01,サンプル記事B,80,8,0"
        sample_bytes = sample_text.encode("cp932")
        st.download_button(label="📄 サンプルファイル(CSV)をダウンロード", data=sample_bytes, file_name="note_import_sample.csv", mime="text/csv")
        st.caption("※ サンプルにはダミーデータが入っています。ご自身のデータに書き換えて（または行を削除して）から保存・アップロードしてください。")
        
        files = st.file_uploader("ファイルを選択 (複数可)", type=["csv", "xlsx", "xlsm"], accept_multiple_files=True)
        if st.button("インポート実行"):
            if files:
                added_count, dates = import_excel_data(files, uid)
                st.success(f"インポート完了: {added_count} 件の新しいレコードを追加しました。")
                if dates:
                    with st.expander("対象となった日付一覧"):
                        st.write(", ".join(dates))
                st.rerun()
            else:
                st.warning("ファイルを選択してください。")
        
        st.markdown("---")
        st.subheader("2. データのダウンロード (同期用)")
        st.write("現在のデータベース内容をSQLite形式でダウンロードします。ローカル環境との同期にご活用ください。")
        
        try:
            db_bin = get_sqlite_binary(uid)
            suffix = "cloud" if db_type == "postgres" else "local"
            fn = f"note_dashboard_{suffix}_{datetime.now().strftime('%Y%m%d')}.db"
            st.download_button(label="📥 データベースをダウンロード (.db)", data=db_bin, file_name=fn, mime="application/octet-stream")
        except Exception as e:
            st.error(f"ダウンロード準備中にエラーが発生しました: {e}")
        
        return

    # --- ダッシュボード ---
    st.title("📝 note分析ダッシュボード")
    
    if st.sidebar.button("最新データを取得する"):
        s = requests.session()
        if note_auth(s, ne, np):
            data = get_articles(s, uid)
            if data: 
                if save_data(data): st.success("保存完了！"); st.rerun()
        else: st.sidebar.error("noteの認証に失敗しました。")
    st.sidebar.caption("※ 1日1回の実行をお勧めします。")

    # データ読み込み
    try:
        conn = get_connection()
        q = "SELECT * FROM article_stats WHERE user_id = %s" if db_type == "postgres" else "SELECT * FROM article_stats WHERE user_id = ?"
        df_all = pd.read_sql(q, conn, params=(uid,))
        conn.close()
    except Exception:
        df_all = pd.DataFrame()

    if not df_all.empty:
        df_all['acquired_at'] = pd.to_datetime(df_all['acquired_at'], format='mixed')
        df_all = df_all.sort_values('acquired_at')
        
        # --- サマリー ---
        ud = sorted(df_all['acquired_at'].unique())
        latest = ud[-1]
        df_latest = df_all[df_all['acquired_at'] == latest].sort_values('views', ascending=False)
        
        has_prev = len(ud) >= 2
        vd = 0; ld = 0; ad = 0
        if has_prev:
            df_p = df_all[df_all['acquired_at'] == ud[-2]]
            df_m = pd.merge(df_latest[['article_id', 'views', 'likes']], df_p[['article_id', 'views', 'likes']], on='article_id', suffixes=('', '_prev'), how='left').fillna(0)
            vd = int((df_m['views'] - df_m['views_prev']).sum())
            ld = int((df_m['likes'] - df_m['likes_prev']).sum())
            ad = int(len(df_latest) - len(df_p))

        st.info(f"最終更新: {latest.strftime('%Y-%m-%d')}")
        c1, c2, c3 = st.columns(3)
        
        ad_txt = f"+{ad}" if ad > 0 else (f"{ad}" if ad < 0 else None)
        vd_txt = f"+{vd:,}" if vd > 0 else (f"{vd:,}" if vd < 0 else None)
        ld_txt = f"+{ld:,}" if ld > 0 else (f"{ld:,}" if ld < 0 else None)

        c1.metric("公開記事数", f"{len(df_latest)} 記事", delta=ad_txt)
        c2.metric("累計ビュー", f"{df_latest['views'].sum():,}", delta=vd_txt)
        c3.metric("累計スキ", f"{df_latest['likes'].sum():,}", delta=ld_txt)
        
        st.markdown("---")
        
        # --- メイングラフ ---
        if has_prev:
            st.subheader("📈 全体累計ビュー推移")
            tv = df_all.groupby('acquired_at')['views'].sum().reset_index()
            fig = px.line(tv, x='acquired_at', y='views')
            fig.update_layout(xaxis_type='date', yaxis=dict(tickformat=',d', rangemode='tozero'))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("📉 推移グラフを表示するには、2日分以上のデータが必要です。")

        st.subheader("📊 記事別累計ビューランキング (TOP 20)")
        t1, t2, t3 = st.tabs(["📊 累計ランキング", "🔥 本日の伸び", "📈 生データ"])
        with t1:
            fig = px.bar(df_latest.head(20), x='views', y='title', orientation='h', text_auto=',d')
            fig.update_layout(yaxis={'autorange': 'reversed'}, height=600)
            st.plotly_chart(fig, use_container_width=True)
        with t2:
            if has_prev:
                df_p = df_all[df_all['acquired_at'] == ud[-2]]
                df_m = pd.merge(df_latest[['article_id', 'title', 'views']], df_p[['article_id', 'views']], on='article_id', suffixes=('', '_prev'), how='left').fillna(0)
                df_m['views_delta'] = df_m['views'] - df_m['views_prev']
                df_d = df_m.sort_values('views_delta', ascending=False)
                fig = px.bar(df_d.head(20), x='views_delta', y='title', orientation='h', text_auto=',d')
                fig.update_layout(yaxis={'autorange': 'reversed'}, height=600)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("比較対象の過去データがありません。")
        with t3:
            st.dataframe(df_latest, use_container_width=True)
            
        st.markdown("---")
        if has_prev:
            st.subheader("📊 個別ビュー数推移")
            ps = df_all[['acquired_at', 'title', 'views']].drop_duplicates(['acquired_at', 'title'])
            pdf = ps.pivot(index='acquired_at', columns='title', values='views')
            fig = go.Figure()
            for t in pdf.columns:
                fig.add_trace(go.Scatter(x=pdf.index, y=pdf[t], mode='lines', name=t, connectgaps=True))
            
            fig.update_layout(
                hovermode='closest', # マウスに一番近い記事だけを表示
                showlegend=False,    # 凡例は非表示
                height=700, 
                xaxis_type='date', 
                yaxis=dict(tickformat=',d'),
                margin=dict(l=10, r=10, t=10, b=10)
            )
            # ホバーラベルのタイトルを全文表示する設定
            fig.update_traces(
                hoverlabel=dict(namelength=-1, font_size=12) # タイトル全文表示
            ) 
            
            st.plotly_chart(fig, use_container_width=True)
            
    else:
        st.info("データがありません。「最新データを取得する」ボタンを押すか、CSVをインポートしてください。")

if __name__ == "__main__":
    main()
