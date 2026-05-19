import streamlit as st
import requests
import json
import traceback
import csv
from datetime import datetime
import os
import sys
import sqlite3
import hashlib
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
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

# =========================================================================
# 0. 定数・パス設定
# =========================================================================

# EXE化対応のパス設定
if getattr(sys, 'frozen', False):
    # EXE実行時: EXEファイルのあるディレクトリ
    APPLICATION_PATH = os.path.dirname(sys.executable)
else:
    # スクリプト実行時
    APPLICATION_PATH = os.path.dirname(os.path.abspath(__file__))

DATA_DIR = os.path.join(APPLICATION_PATH, "note_data")
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

DB_PATH = os.path.join(DATA_DIR, "note_dashboard.db")

# クッキーファイルのパス定義
COOKIE_FILE = os.path.join(DATA_DIR, ".note_cookies.json")

# ローカルモード定数
CURRENT_USER_ID = "local_admin"

# =========================================================================
# クッキー認証・ログイン連携ロジック
# =========================================================================
def load_cookies_to_session(session):
    """保存されたクッキーファイルをrequestsセッションに適用する"""
    if os.path.exists(COOKIE_FILE):
        try:
            with open(COOKIE_FILE, 'r', encoding='utf-8') as f:
                cookies = json.load(f)
                
                # Cookie-Editorからエクスポートされた形式（リスト）か確認
                if isinstance(cookies, list):
                    for cookie in cookies:
                        domain = cookie.get('domain', '')
                        if not domain.startswith('.') and 'note.com' in domain:
                            domain = '.note.com'
                        
                        session.cookies.set(
                            cookie.get('name'), 
                            cookie.get('value'), 
                            domain=domain,
                            path=cookie.get('path', '/')
                        )
                    return True
        except Exception as e:
            print(f"DEBUG: クッキー読み込みエラー: {e}")
    return False

def verify_note_session(session):
    """現在のセッションでnote.comにログインできているかAPIで検証する"""
    try:
        r = session.get('https://note.com/api/v1/stats/pv?filter=all&page=1&sort=pv', headers=DEFAULT_HEADERS, timeout=8)
        if r.status_code == 200 and "error" not in r.text:
            return True
    except Exception as e:
        print(f"DEBUG: セッション検証エラー: {e}")
    return False

def note_auth_with_cookie(session):
    """クッキー優先でnoteセッションを認証する"""
    if load_cookies_to_session(session):
        if verify_note_session(session):
            return session
    return None

# =========================================================================
# 1. データベース接続・初期化
# =========================================================================
def get_connection():
    return sqlite3.connect(DB_PATH)

def init_db_schema():
    try:
        conn = get_connection(); cursor = conn.cursor()
        cursor.execute('CREATE TABLE IF NOT EXISTS article_stats (user_id TEXT, acquired_at TEXT, article_id INTEGER, title TEXT, views INTEGER, likes INTEGER, comments INTEGER, PRIMARY KEY (user_id, acquired_at, article_id));')
        conn.commit(); conn.close()
    except Exception: pass

# =========================================================================
# 2. noteデータ取得・保存
# =========================================================================
def note_auth(session, email, password):
    """クッキー優先でnote.comに認証する"""
    # 1. まずクッキー優先で認証テスト
    if note_auth_with_cookie(session):
        return session
        
    # 2. クッキーがない、または切れている場合は通常のID/PW認証をフォールバック試行
    try:
        session.get('https://note.com/login', headers=DEFAULT_HEADERS)
        time.sleep(1)
        r = session.post('https://note.com/api/v1/sessions/sign_in', json={"login": email, "password": password}, headers=DEFAULT_HEADERS)
        try:
            r2 = r.json()
        except json.JSONDecodeError:
            return None

        if r.status_code in [200, 201] and "data" in r2:
            return session
        return None
    except Exception: return None

def get_articles(session, user_id):
    articles = []; tdy = datetime.now().strftime('%Y-%m-%d'); page = 1
    pb = st.progress(0); txt = st.empty()
    consecutive_failures = 0
    max_failures = 3
    
    while True:
        txt.text(f"ページ {page} 取得中... (現在 {len(articles)} 件収集済み)")
        try:
            r = session.get(
                f'https://note.com/api/v1/stats/pv?filter=all&page={page}&sort=pv', 
                headers=DEFAULT_HEADERS, 
                timeout=10
            )
            
            # 速度制限 (429) または一時的エラーへの対処
            if r.status_code == 429:
                consecutive_failures += 1
                if consecutive_failures >= max_failures:
                    st.error(f"⚠️ 速度制限(429)が解除されないため、ページ {page} で取得を中断しました。")
                    break
                txt.warning(f"⚠️ 速度制限を検出しました。10秒待機して自動リトライします ({consecutive_failures}/{max_failures}回目)...")
                time.sleep(10)
                continue
                
            if r.status_code != 200:
                consecutive_failures += 1
                if consecutive_failures >= max_failures:
                    st.error(f"⚠️ サーバーエラー ({r.status_code}) が続いたため、ページ {page} で取得を中断しました。")
                    break
                txt.warning(f"⚠️ エラーが発生しました。3秒待機してリトライします ({consecutive_failures}/{max_failures}回目)...")
                time.sleep(3)
                continue
                
            data = r.json()
            stats = data.get('data', {}).get('note_stats', [])
            
            # データが空になったら正常に全件取得完了
            if not stats: 
                break
                
            new_articles_count = 0
            page_total_pv = 0
            
            for item in stats:
                name = item.get('name')
                art_id = item.get('id')
                read_count = item.get('read_count', 0)
                
                if name: 
                    # 重複チェック (IDまたはタイトル名が既に存在するか両方で強力にチェック)
                    is_duplicate_id = any(x[2] == art_id for x in articles)
                    is_duplicate_name = any(x[3] == name for x in articles)
                    
                    if is_duplicate_id or is_duplicate_name:
                        continue
                        
                    articles.append((
                        user_id, tdy, art_id, name, 
                        read_count, 
                        item.get('like_count', 0), 
                        item.get('comment_count', 0)
                    ))
                    new_articles_count += 1
                    page_total_pv += read_count
            
            # 終了判定1: このページで新規記事が0件（すべて重複）だった場合は終了
            if len(stats) > 0 and new_articles_count == 0:
                break
                
            # 終了判定2: sort=pvで取得しているため、このページの全記事のPVが0だった場合、
            # これ以降はPVを持たない過去の残骸やダミーデータとみなして安全に終了する！
            if len(stats) > 0 and page_total_pv == 0:
                print("DEBUG: このページの合計PVが0になったため、終端とみなして取得を完了します。")
                break
            
            page += 1
            pb.progress(min(page * 0.04, 1.0))
            consecutive_failures = 0 # 成功したら失敗カウントをリセット
            time.sleep(1.0) # 速度制限を防ぐため安全のために待機時間を1.0秒に設定
            
        except Exception as e:
            consecutive_failures += 1
            if consecutive_failures >= max_failures:
                st.error(f"⚠️ 通信エラーが続いたため、取得を中断しました。詳細: {e}")
                break
            txt.warning(f"⚠️ 通信に失敗しました。3秒待機してリトライします ({consecutive_failures}/{max_failures}回目)...")
            time.sleep(3)
            continue
            
    pb.empty()
    txt.empty()
    return articles

def save_data(data):
    if not data:
        return False
    try:
        user_id = data[0][0]
        tdy = data[0][1]
        
        conn = get_connection(); cursor = conn.cursor()
        # 同日・同ユーザーの既存データを一旦綺麗に削除（重複ゴミデータの全自動クレンジング）
        cursor.execute('DELETE FROM article_stats WHERE user_id = ? AND acquired_at = ?', (user_id, tdy))
        
        cursor.executemany('INSERT OR IGNORE INTO article_stats VALUES (?, ?, ?, ?, ?, ?, ?)', data)
        conn.commit(); conn.close()
        return True
    except Exception as e: 
        st.error(f"保存エラー: {e}")
        return False

# =========================================================================
# 3. インポート・エクスポート
# =========================================================================
def import_excel_data(uploaded_files, user_id):
    added_dates = set()
    total_added = 0
    
    conn = get_connection(); cursor = conn.cursor()
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
    with open(DB_PATH, "rb") as f:
        data = f.read()
    return data

# =========================================================================
# 4. Streamlit UI
# =========================================================================
def main():
    st.set_page_config(page_title="note分析 Local (ログイン復旧版)", layout="wide")
    init_db_schema()

    st.sidebar.header("🔑 設定")
    
    # メニュー
    menu = ["ダッシュボード", "📥 データ管理", "🔌 note連携設定"]
    choice = st.sidebar.radio("メニュー", menu)

    # note認証情報 (ローカル保存機能付き)
    st.sidebar.markdown("---")
    st.sidebar.caption("noteデータ取得用")

    CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

    def load_config():
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, 'r') as f:
                    return json.load(f)
            except: pass
        return {}

    def save_config(email, password):
        try:
            with open(CONFIG_PATH, 'w') as f:
                json.dump({"email": email, "password": password}, f)
        except: pass
    
    def clear_config():
        if os.path.exists(CONFIG_PATH):
            os.remove(CONFIG_PATH)

    saved_config = load_config()
    default_email = saved_config.get("email", "")
    default_password = saved_config.get("password", "")

    # セッションステート初期化
    if 'note_email' not in st.session_state:
        st.session_state.note_email = default_email
    if 'note_password' not in st.session_state:
        st.session_state.note_password = default_password

    # UI入力フォーム (サイドバー)
    ne = st.sidebar.text_input("noteメールアドレス", value=st.session_state.note_email)
    np = st.sidebar.text_input("noteパスワード", value=st.session_state.note_password, type="password")
    
    remember_me = st.sidebar.checkbox("次回から自動入力する", value=bool(default_email))
    
    if st.sidebar.button("設定を保存・更新"):
        if remember_me:
            save_config(ne, np)
            st.sidebar.success("設定を保存しました")
        else:
            clear_config()
            st.sidebar.info("設定を削除しました")

    uid = CURRENT_USER_ID

    # =========================================================================
    # クッキー連携設定画面
    # =========================================================================
    if choice == "🔌 note連携設定":
        st.title("🔌 note.com ログイン連携設定")
        st.subheader("セキュリティ強化に伴うクッキー認証の設定")
        st.info("note.comのセキュリティ強化（reCAPTCHA）に対応するため、ブラウザの「ログイン用のクッキー」をコピーして本アプリに貼り付けます。（最初の1回だけ設定すれば、以降は全自動で動作します）")
        
        st.markdown("""
        ### 👉 【簡単4ステップの設定手順】
        
        #### 1. 💻 拡張機能のインストール
        ブラウザに **[Cookie-Editor (Google Chrome Webストア)](https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm)** をインストールします。
        * ※ 類似の拡張機能が多数表示されますが、かじられたクッキーのアイコンで、名前のすぐ下に青字で **`cookie-editor.com`** と書かれている公式のものをインストールしてください。
        
        #### 2. 🔐 noteで通常ログイン ➔ 権限を許可（初回のみ）
        * 普段使っているブラウザで、普通に **[note.com](https://note.com)** にログインします。
        * ログインした画面を開いた状態で、ブラウザ右上（パズルマークまたは拡張機能エリア）にある「**Cookie-Editor** (クッキーのアイコン)」をクリックします。
        * 画面に「*Request permission for...*」と表示されたら、👈 **「This site」** ボタンを押し、Chromeから追加許可のリクエストが出たら 🟢 **「許可する」** をクリックしてください。
        
        #### 3. 📋 クッキー（ログイン情報）のコピー
        * 権限許可後、再度 note.com を開いた状態でブラウザ右上の「**Cookie-Editor**」をクリックすると、クッキー一覧が表示されます。
        * 拡張機能ウィンドウの下部にあるアイコンの並びから、👈 **「右から2番目の 📤（Export）ボタン（紙から右矢印が出ているマーク）」** をクリックします。
        * これで、ログイン情報（JSON）が自動的にクリップボードにコピーされます。
        
        #### 4. 📥 本アプリへの貼り付け
        以下の入力欄に、コピーした内容を貼り付け（`Ctrl + V`）し、保存ボタンを押してください。
        """)
        
        # 既存のクッキー確認
        has_cookie = os.path.exists(COOKIE_FILE)
        if has_cookie:
            st.success("✅ すでにログインクッキーが登録されています。ログインできない場合のみ、再設定を行ってください。")
        else:
            st.warning("⏳ ログインクッキーが未登録です。データを自動取得する前に登録してください。")
            
        cookie_text = st.text_area("コピーしたJSONテキストをここに貼り付けてください", height=200, placeholder='[{"domain": ".note.com", ...}]')
        
        if st.button("💾 設定を保存して再起動"):
            if cookie_text.strip():
                try:
                    parsed_json = json.loads(cookie_text.strip())
                    if not isinstance(parsed_json, list):
                        st.error("JSONの形式が不正です。リスト形式 `[...]` である必要があります。")
                    else:
                        with open(COOKIE_FILE, 'w', encoding='utf-8') as f:
                            json.dump(parsed_json, f, ensure_ascii=False, indent=2)
                        st.success("✅ クッキー情報を保存しました！ダッシュボードを起動します...")
                        time.sleep(1.5)
                        st.rerun()
                except Exception as je:
                    st.error(f"JSONのパースエラー: 正しいエクスポート結果を貼り付けてください。({je})")
            else:
                st.warning("テキストエリアが空欄です。")
        return

    # --- 📥 データ管理画面 ---
    if choice == "📥 データ管理":
        st.title("📥 データ管理")
        
        # データ取得状況の可視化
        try:
            conn = get_connection()
            df_dates = pd.read_sql("SELECT DISTINCT acquired_at FROM article_stats WHERE user_id = ?", conn, params=(uid,))
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
            else:
                st.info("データがまだありません。")
        except Exception as e:
            st.error(f"エラー: {e}")

        st.markdown("---")
        st.subheader("1. 過去データの取り込み")
        
        # サンプルデータ
        sample_text = "日付,タイトル,ビュー数,スキ数,コメント数\n2023-12-01,サンプル記事A,150,15,2\n2023-12-01,サンプル記事B,80,8,0"
        sample_bytes = sample_text.encode("cp932")
        st.download_button(label="📄 サンプルCSV", data=sample_bytes, file_name="note_import_sample.csv", mime="text/csv")
        
        files = st.file_uploader("ファイルを選択 (複数可)", type=["csv", "xlsx", "xlsm"], accept_multiple_files=True)
        if st.button("インポート実行"):
            if files:
                added_count, dates = import_excel_data(files, uid)
                st.success(f"インポート完了: {added_count} 件追加")
                st.rerun()
        
        st.markdown("---")
        st.subheader("2. データのバックアップ")
        try:
            with open(DB_PATH, "rb") as f:
                db_bin = f.read()
            fn = f"note_dashboard_backup_{datetime.now().strftime('%Y%m%d')}.db"
            st.download_button(label="📥 データベースをダウンロード", data=db_bin, file_name=fn, mime="application/octet-stream")
        except Exception: pass
        
        return

    # --- 📝 ダッシュボード ---
    st.title("📝 note分析ダッシュボード (Local)")
    
    if st.sidebar.button("最新データを取得する"):
        s = requests.session()
        # クッキー優先で認証を実行
        auth_session = note_auth(s, ne, np)
        if auth_session:
            st.sidebar.success("🔑 認証成功（クッキー有効）")
            with st.spinner("note.com から最新データを取り込んでいます..."):
                data = get_articles(auth_session, uid)
                if data: 
                    if save_data(data): st.success("保存完了！"); st.rerun()
                else:
                    st.sidebar.warning("取得可能な記事データがありませんでした。")
        else:
            st.sidebar.error("❌ 認証失敗。note連携設定を見直してください。")
            st.sidebar.info("👉 メニューから **🔌 note連携設定** を選択し、クッキー情報を更新してください。")

    st.sidebar.caption("※ 1日1回の実行をお勧めします。")

    # クッキー未設定の場合の親切なサイドバー警告
    if not os.path.exists(COOKIE_FILE):
        st.sidebar.warning("📢 ログイン設定が未完了です。")
        st.sidebar.info("メニューの **🔌 note連携設定** からクッキーを登録してください。")

    # データ読み込み
    try:
        conn = get_connection()
        df_all = pd.read_sql("SELECT * FROM article_stats WHERE user_id = ?", conn, params=(uid,))
        conn.close()
    except Exception:
        df_all = pd.DataFrame()

    if not df_all.empty:
        df_all['acquired_at'] = pd.to_datetime(df_all['acquired_at'], format='mixed')
        df_all = df_all.sort_values('acquired_at')
        
        # サマリー
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
        
        if has_prev:
            st.subheader("📊 分析ダッシュボード")
            
            # タブ作成: ビュー分析 / スキ分析
            tab_views, tab_likes = st.tabs(["📈 ビュー分析", "❤️ スキ分析"])
            
            # --- 1. ビュー分析タブ ---
            with tab_views:
                st.caption("※ 累計ビュー数の推移とランキングを表示します。")
                
                st.subheader("📈 全体累計ビュー推移")
                tv = df_all.groupby('acquired_at')['views'].sum().reset_index()
                fig = px.line(tv, x='acquired_at', y='views')
                fig.update_layout(xaxis_type='date', yaxis=dict(tickformat=',d', rangemode='tozero'))
                st.plotly_chart(fig, use_container_width=True)

                st.subheader("📊 記事別累計ビューランキング (TOP 20)")
                t1, t2, t3 = st.tabs(["📊 累計ランキング", "🔥 本日の伸び", "📈 生データ"])
                with t1:
                    fig = px.bar(df_latest.head(20), x='views', y='title', orientation='h', text_auto=',d')
                    fig.update_layout(yaxis={'autorange': 'reversed'}, height=600)
                    st.plotly_chart(fig, use_container_width=True)
                with t2:
                    df_p = df_all[df_all['acquired_at'] == ud[-2]]
                    df_m = pd.merge(df_latest[['article_id', 'title', 'views']], df_p[['article_id', 'views']], on='article_id', suffixes=('', '_prev'), how='left').fillna(0)
                    df_m['views_delta'] = df_m['views'] - df_m['views_prev']
                    df_d = df_m.sort_values('views_delta', ascending=False)
                    fig = px.bar(df_d.head(20), x='views_delta', y='title', orientation='h', text_auto=',d')
                    fig.update_layout(yaxis={'autorange': 'reversed'}, height=600)
                    st.plotly_chart(fig, use_container_width=True)
                with t3:
                    st.dataframe(df_latest[['title', 'views', 'likes', 'comments']], use_container_width=True)
                
                st.markdown("---")
                st.subheader("📊 個別ビュー数推移")
                ps = df_all[['acquired_at', 'title', 'views']].drop_duplicates(['acquired_at', 'title'])
                pdf = ps.pivot(index='acquired_at', columns='title', values='views')
                fig = go.Figure()
                for t in pdf.columns:
                    fig.add_trace(go.Scatter(x=pdf.index, y=pdf[t], mode='lines', name=t, connectgaps=True))
                
                fig.update_layout(
                    hovermode='closest',
                    showlegend=False,
                    height=700, 
                    xaxis_type='date', 
                    yaxis=dict(tickformat=',d'),
                    margin=dict(l=10, r=10, t=10, b=10)
                )
                fig.update_traces(hoverlabel=dict(namelength=-1, font_size=12))
                st.plotly_chart(fig, use_container_width=True)

            # --- 2. スキ分析タブ ---
            with tab_likes:
                st.caption("※ 累計スキ数の推移とランキングを表示します。")
                
                st.subheader("❤️ 全体累計スキ推移")
                tl = df_all.groupby('acquired_at')['likes'].sum().reset_index()
                fig_l = px.line(tl, x='acquired_at', y='likes')
                fig_l.update_layout(xaxis_type='date', yaxis=dict(tickformat=',d', rangemode='tozero'))
                fig_l.update_traces(line_color='#ff4b4b')
                st.plotly_chart(fig_l, use_container_width=True)

                st.subheader("📊 記事別累計スキランキング (TOP 20)")
                df_latest_likes = df_latest.sort_values('likes', ascending=False)
                
                lt1, lt2, lt3 = st.tabs(["📊 累計ランキング", "🔥 本日の伸び", "📈 生データ"])
                with lt1:
                    fig = px.bar(df_latest_likes.head(20), x='likes', y='title', orientation='h', text_auto=',d')
                    fig.update_layout(yaxis={'autorange': 'reversed'}, height=600)
                    fig.update_traces(marker_color='#ff4b4b')
                    st.plotly_chart(fig, use_container_width=True)
                with lt2:
                    df_p = df_all[df_all['acquired_at'] == ud[-2]]
                    df_m = pd.merge(df_latest[['article_id', 'title', 'likes']], df_p[['article_id', 'likes']], on='article_id', suffixes=('', '_prev'), how='left').fillna(0)
                    df_m['likes_delta'] = df_m['likes'] - df_m['likes_prev']
                    df_d_likes = df_m.sort_values('likes_delta', ascending=False)
                    fig = px.bar(df_d_likes.head(20), x='likes_delta', y='title', orientation='h', text_auto=',d')
                    fig.update_layout(yaxis={'autorange': 'reversed'}, height=600)
                    fig.update_traces(marker_color='#ff4b4b')
                    st.plotly_chart(fig, use_container_width=True)
                with lt3:
                    st.dataframe(df_latest_likes[['title', 'likes', 'views', 'comments']], use_container_width=True)

                st.markdown("---")
                st.subheader("📊 個別スキ数推移")
                ps_l = df_all[['acquired_at', 'title', 'likes']].drop_duplicates(['acquired_at', 'title'])
                pdf_l = ps_l.pivot(index='acquired_at', columns='title', values='likes')
                fig_l_ind = go.Figure()
                for t in pdf_l.columns:
                    fig_l_ind.add_trace(go.Scatter(x=pdf_l.index, y=pdf_l[t], mode='lines', name=t, connectgaps=True))
                
                fig_l_ind.update_layout(
                    hovermode='closest',
                    showlegend=False,
                    height=700, 
                    xaxis_type='date', 
                    yaxis=dict(tickformat=',d'),
                    margin=dict(l=10, r=10, t=10, b=10)
                )
                fig_l_ind.update_traces(hoverlabel=dict(namelength=-1, font_size=12))
                st.plotly_chart(fig_l_ind, use_container_width=True)

        else:
            st.info("📉 推移グラフを表示するには、2日分以上のデータが必要です。")
            
    else:
        st.info("データがありません。「最新データを取得する」ボタンを押すか、CSVをインポートしてください。")

if __name__ == "__main__":
    main()
