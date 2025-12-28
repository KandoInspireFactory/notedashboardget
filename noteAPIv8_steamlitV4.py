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

def neon_auth_login(email, password):
    """Neon Data APIを使用してログイン認証を行う"""
    data_api_url = os.getenv("NEON_DATA_API_URL")
    api_key = os.getenv("NEON_API_KEY")
    
    if not data_api_url or not api_key:
        # 設定がない場合は認証スキップ（ローカル配布版など）
        return True, "local"

    try:
        # Perplexityの調査結果に基づいたRPC呼び出し
        response = requests.post(
            f"{data_api_url}/v1/rpc/sign_in",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"email": email, "password": password},
            timeout=10
        )
        if response.status_code == 200:
            return True, response.json().get("token", "logged_in")
        else:
            return False, "ログイン失敗: メールアドレスまたはパスワードが正しくありません。"
    except Exception as e:
        return False, f"認証エラー: {str(e)}"

def get_current_user_id(note_email):
    """
    ユーザーIDを取得する。
    アプリログイン済みの場合はそのメールを、
    ローカル実行時はnoteのメールアドレスのハッシュを使用する。
    """
    # アプリ認証済みのメールを優先
    email = st.session_state.get("app_user_email", note_email)
    
    if not email:
        return "guest"
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
    if not email:
        email = os.getenv("NOTE_EMAIL", "")
    if not password:
        password = os.getenv("NOTE_PASSWORD", "")
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
        st.error("noteの認証中にエラーが発生しました。メールアドレスとパスワードを確認してください。")
        return None

# =========================================================================
# 2. データ取得ロジック
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
        except Exception as e:
            st.error(f"データ取得エラー: {e}")
            break

        stats = data.get('data', {}).get('note_stats', [])
        if not stats:
            break

        for item in stats:
            name = item.get('name')
            if name:
                articles.append((
                    user_id,
                    tdy, 
                    item.get('id'), 
                    name, 
                    item.get('read_count', 0), 
                    item.get('like_count', 0), 
                    item.get('comment_count', 0)
                ))
        page += 1
        progress_bar.progress(min(page * 0.05, 1.0))

    status_text.text("最新データの取得が完了しました！")
    progress_bar.empty()
    return articles

# =========================================================================
# 3. データ保存ロジック
# =========================================================================
def save_data(articles_data, save_dir):
    """DBタイプに合わせて保存する"""
    if not os.path.exists(save_dir):
        os.makedirs(save_dir)

    db_type, _ = get_db_info()
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        if db_type == "postgres":
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS article_stats (
                    user_id TEXT, acquired_at TEXT, article_id BIGINT, title TEXT,
                    views INTEGER, likes INTEGER, comments INTEGER,
                    PRIMARY KEY (user_id, acquired_at, article_id)
                )
            ''')
            insert_query = """
                INSERT INTO article_stats (user_id, acquired_at, article_id, title, views, likes, comments)
                VALUES %s ON CONFLICT (user_id, acquired_at, article_id) DO NOTHING
            """
            execute_values(cursor, insert_query, articles_data)
        else:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS article_stats (
                    user_id TEXT, acquired_at TEXT, article_id INTEGER, title TEXT,
                    views INTEGER, likes INTEGER, comments INTEGER,
                    PRIMARY KEY (user_id, acquired_at, article_id)
                )
            ''')
            cursor.executemany(
                'INSERT OR IGNORE INTO article_stats VALUES (?, ?, ?, ?, ?, ?, ?)', 
                articles_data
            )
        
        conn.commit()
        conn.close()
    except Exception as e:
        st.error(f"データベース保存エラー: {e}")

    # CSV保存 (履歴用)
    today_str = datetime.now().strftime('%Y%m%d')
    csv_path = os.path.join(save_dir, f'noteList_{today_str}.csv')
    df = pd.DataFrame(articles_data, columns=['user_id', 'acquired_at', 'article_id', 'title', 'views', 'likes', 'comments'])
    df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    
    return db_type, csv_path

# =========================================================================
# 4. Streamlit UI
# =========================================================================
def main():
    db_type, _ = get_db_info()
    st.set_page_config(page_title=f"note分析 v7 ({db_type.capitalize()})", layout="wide")

    # セッション状態の初期化
    if "app_auth_token" not in st.session_state:
        st.session_state.app_auth_token = None
    if "app_user_email" not in st.session_state:
        st.session_state.app_user_email = None

    # --- アプリログイン（Neon Auth） ---
    # NEON_API_KEYがある場合のみ強制
    if db_type == "postgres" and os.getenv("NEON_API_KEY") and not st.session_state.app_auth_token:
        st.title("🛡️ アプリログイン")
        with st.form("app_login"):
            st.write("このアプリを利用するにはログインが必要です。")
            app_email = st.text_input("メールアドレス")
            app_password = st.text_input("パスワード", type="password")
            submit = st.form_submit_button("ログイン")
            
            if submit:
                success, result = neon_auth_login(app_email, app_password)
                if success:
                    st.session_state.app_auth_token = result
                    st.session_state.app_user_email = app_email
                    st.success("ログインしました。")
                    st.rerun()
                else:
                    st.error(result)
        return # ログインしていない場合はここで停止

    # --- メインダッシュボード ---
    st.title(f"📝 note分析ダッシュボード (v7 {db_type.capitalize()})")
    
    default_email, default_pw = get_default_credentials()

    # サイドバー: 取得設定
    st.sidebar.header("🔑 note取得設定")
    if st.session_state.app_user_email:
        st.sidebar.info(f"ログインユーザー: {st.session_state.app_user_email}")
        if st.sidebar.button("ログアウト"):
            st.session_state.app_auth_token = None
            st.session_state.app_user_email = None
            st.rerun()

    note_email = st.sidebar.text_input("noteメールアドレス", value=default_email)
    note_password = st.sidebar.text_input("noteパスワード", type="password", value=default_pw)
    
    current_user_id = get_current_user_id(note_email)
    save_dir = "note_data"
    
    if st.sidebar.button("最新データを取得する"):
        if not note_email or not note_password:
            st.sidebar.error("noteのログイン情報を入力してください。")
        else:
            session = requests.session()
            if note_auth(session, note_email, note_password):
                data = get_articles(session, current_user_id)
                if data:
                    res_db_type, _ = save_data(data, save_dir)
                    st.sidebar.success(f"データの更新に成功しました！ ({res_db_type})")
                    st.balloons()
                    st.rerun()
                else:
                    st.sidebar.warning("記事が見つかりませんでした。")

    # データ表示 (現在のユーザーのものに限定)
    try:
        conn = get_connection()
        if db_type == "postgres":
            query = "SELECT * FROM article_stats WHERE user_id = %s"
        else:
            query = "SELECT * FROM article_stats WHERE user_id = ?"
        df_all = pd.read_sql(query, conn, params=(current_user_id,))
        conn.close()
    except Exception:
        df_all = pd.DataFrame()
        st.info("データベースに接続中、またはデータがありません。サイドバーから取得を開始してください。")

    if not df_all.empty:
        df_all['acquired_at'] = pd.to_datetime(df_all['acquired_at'], format='mixed')
        df_all = df_all.sort_values('acquired_at')
        unique_dates = sorted(df_all['acquired_at'].unique())
        
        latest_date = unique_dates[-1]
        df_latest = df_all[df_all['acquired_at'] == latest_date].sort_values('views', ascending=False)
        
        has_previous = len(unique_dates) >= 2
        total_views_delta = 0
        total_likes_delta = 0
        df_delta = pd.DataFrame()

        if has_previous:
            previous_date = unique_dates[-2]
            df_prev = df_all[df_all['acquired_at'] == previous_date]
            df_merge = pd.merge(
                df_latest[['article_id', 'title', 'views', 'likes']], 
                df_prev[['article_id', 'views', 'likes']], 
                on='article_id', suffixes=('', '_prev'), how='left'
            ).fillna(0)
            df_merge['views_delta'] = df_merge['views'] - df_merge['views_prev']
            df_merge['likes_delta'] = df_merge['likes'] - df_merge['likes_prev']
            total_views_delta = int(df_merge['views_delta'].sum())
            total_likes_delta = int(df_merge['likes_delta'].sum())
            df_delta = df_merge.sort_values('views_delta', ascending=False)

        st.info(f"最終更新日: {latest_date.strftime('%Y-%m-%d %H:%M')}")
        c1, c2, c3 = st.columns(3)
        c1.metric("公開中の記事数", f"{len(df_latest)} 記事")
        c2.metric("累計総ビュー数", f"{df_latest['views'].sum():,}", delta=f"+{total_views_delta:,}" if has_previous else None)
        c3.metric("累計総スキ数", f"{df_latest['likes'].sum():,}", delta=f"+{total_likes_delta:,}" if has_previous else None)

        st.markdown("---")
        st.subheader("📈 全体累計ビュー推移")
        if has_previous:
            total_views_df = df_all.groupby('acquired_at')['views'].sum().reset_index()
            fig_total = px.line(total_views_df, x='acquired_at', y='views', labels={'acquired_at':'日付', 'views':'合計累計ビュー数'})
            fig_total.update_traces(mode='lines+markers')
            fig_total.update_layout(xaxis_type='date', yaxis=dict(tickformat=',d', rangemode='tozero'))
            st.plotly_chart(fig_total, use_container_width=True)
        else:
            st.info("📉 推移グラフを表示するには、2日分以上のデータが必要です。明日また最新データを取得してください。")

        st.markdown("---")
        tab1, tab2, tab3 = st.tabs(["📊 累計ランキング", "🔥 本日の伸び", "📈 生データ"])
        with tab1:
            fig = px.bar(df_latest.head(20), x='views', y='title', orientation='h', text_auto=True, title="累計ビュー数 TOP 20")
            fig.update_layout(yaxis={'autorange': 'reversed'}, height=600)
            st.plotly_chart(fig, use_container_width=True)
        with tab2:
            if has_previous:
                fig_delta = px.bar(df_delta.head(20), x='views_delta', y='title', orientation='h', text_auto=True, title="本日のビュー増加数 TOP 20")
                fig_delta.update_layout(yaxis={'autorange': 'reversed'}, height=600)
                st.plotly_chart(fig_delta, use_container_width=True)
            else:
                st.info("🔥 「本日の伸び」ランキングを表示するには、明日もう一度データを取得してください。")
        with tab3:
            st.dataframe(df_latest, use_container_width=True)

        st.markdown("---")
        st.subheader("📊 全記事の個別ビュー数推移")
        if has_previous:
            df_pivot_src = df_all[['acquired_at', 'title', 'views']].drop_duplicates(['acquired_at', 'title'])
            pivot_df = df_pivot_src.pivot(index='acquired_at', columns='title', values='views')
            fig_all = go.Figure()
            for title in pivot_df.columns:
                fig_all.add_trace(go.Scatter(x=pivot_df.index, y=pivot_df[title], mode='lines', name=title, connectgaps=True, hovertemplate='<b>%{fullData.name}</b><br>ビュー数: %{y:,}<extra></extra>'))
            fig_all.update_layout(hovermode='closest', showlegend=False, height=700, xaxis_type='date', yaxis=dict(tickformat=',d'))
            st.plotly_chart(fig_all, use_container_width=True)
        else:
            st.info("📉 個別記事の推移を表示するには、2日分以上のデータが必要です。")

        if db_type == "sqlite":
            with st.expander("📥 データのダウンロード"):
                st.write("SQLiteデータベースファイル（note_dashboard.db）をダウンロードできます。")
                with open("note_dashboard.db", "rb") as f:
                    st.download_button("SQLite DBをダウンロード", f, file_name="note_dashboard.db")
    else:
        st.info("まだデータがありません。サイドバーから取得を開始してください。")

if __name__ == "__main__":
    main()