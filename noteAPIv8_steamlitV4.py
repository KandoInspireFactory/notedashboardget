import streamlit as st
import requests
import json
import traceback
import csv
from datetime import datetime
import os
import sqlite3
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dotenv import load_dotenv

# .envファイルを読み込む
load_dotenv()

# =========================================================================
# 1. ログイン認証・秘密情報ロード
# =========================================================================
def get_default_credentials():
    """Secrets または環境変数からデフォルトのメールとパスワードを取得する"""
    email = ""
    password = ""
    
    # 1. Streamlit Secrets (Local or Cloud)
    try:
        if "note" in st.secrets:
            email = st.secrets["note"].get("email", "")
            password = st.secrets["note"].get("password", "")
    except:
        pass
    
    # 2. Environment Variables (.env)
    if not email:
        email = os.getenv("NOTE_EMAIL", "")
    if not password:
        password = os.getenv("NOTE_PASSWORD", "")
        
    return email, password

def note_auth(session, email_address, password):
    """noteにログインしてセッションを認証する"""
    user_data = {
        "login": email_address,
        "password": password
    }
    url = 'https://note.com/api/v1/sessions/sign_in'
    try:
        r = session.post(url, json=user_data)
        r.raise_for_status()
        res_json = r.json()
        if "error" in res_json:
            st.error(f"ログインエラー: {res_json.get('error', '不明なエラー')}")
            return None
        return session
    except Exception as e:
        st.error(f"認証中にエラーが発生しました。メールアドレスとパスワードが正しいか確認してください。")
        return None

# =========================================================================
# 2. データ取得ロジック
# =========================================================================
def get_articles(session):
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
                articles.append([
                    tdy, 
                    item.get('id'), 
                    name, 
                    item.get('read_count', 0), 
                    item.get('like_count', 0), 
                    item.get('comment_count', 0)
                ])

        page += 1
        progress_bar.progress(min(page * 0.05, 1.0))

    status_text.text("最新データの取得が完了しました！")
    progress_bar.empty()
    return articles

# =========================================================================
# 3. データ保存 (SQLite / CSV)
# =========================================================================
def save_data(articles_data, save_dir):
    """SQLiteとCSVの両方に保存する"""
    if not os.path.exists(save_dir):
        os.makedirs(save_dir)

    # SQLite保存 (note_dashboard.db に統一)
    db_path = 'note_dashboard.db'
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS article_stats (
            acquired_at TEXT,
            article_id INTEGER,
            title TEXT,
            views INTEGER,
            likes INTEGER,
            comments INTEGER,
            PRIMARY KEY (acquired_at, article_id)
        )
    ''')
    cursor.executemany(
        'INSERT OR IGNORE INTO article_stats VALUES (?, ?, ?, ?, ?, ?)', 
        articles_data
    )
    conn.commit()
    conn.close()

    # CSV保存 (履歴用)
    today_str = datetime.now().strftime('%Y%m%d')
    csv_path = os.path.join(save_dir, f'noteList_{today_str}.csv')
    df = pd.DataFrame(articles_data, columns=['acquired_at', 'article_id', 'title', 'views', 'likes', 'comments'])
    df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    
    return db_path, csv_path

# =========================================================================
# 4. Streamlit UI
# =========================================================================
def main():
    st.set_page_config(page_title="note分析 Basic v4", layout="wide")
    st.title("📝 note分析ダッシュボード (v7 Basic)")
    
    # デフォルト値の取得
    default_email, default_pw = get_default_credentials()

    # サイドバー: ログイン設定
    st.sidebar.header("🔑 取得設定")
    email = st.sidebar.text_input("メールアドレス", value=default_email)
    password = st.sidebar.text_input("パスワード", type="password", value=default_pw)
    
    save_dir = "note_data"
    
    if st.sidebar.button("最新データを取得する"):
        if not email or not password:
            st.sidebar.error("ログイン情報を入力してください。")
        else:
            session = requests.session()
            if note_auth(session, email, password):
                data = get_articles(session)
                if data:
                    db_path, _ = save_data(data, save_dir)
                    st.sidebar.success("データの更新に成功しました！")
                    st.balloons()
                    st.rerun() # 再起動して最新データを表示
                else:
                    st.sidebar.warning("記事が見つかりませんでした。")

    # メメインエリア: データ表示
    db_file = 'note_dashboard.db'
    if os.path.exists(db_file):
        conn = sqlite3.connect(db_file)
        df_all = pd.read_sql('SELECT * FROM article_stats', conn)
        conn.close()

        if not df_all.empty:
            # 日付処理の安定化
            df_all['acquired_at'] = pd.to_datetime(df_all['acquired_at'], format='mixed')
            df_all = df_all.sort_values('acquired_at')
            unique_dates = sorted(df_all['acquired_at'].unique())
            
            latest_date = unique_dates[-1]
            df_latest = df_all[df_all['acquired_at'] == latest_date].sort_values('views', ascending=False)
            
            # --- 前日比（増分）の計算 ---
            has_previous = len(unique_dates) >= 2
            total_views_delta = 0
            total_likes_delta = 0
            df_delta = pd.DataFrame()

            if has_previous:
                previous_date = unique_dates[-2]
                df_prev = df_all[df_all['acquired_at'] == previous_date]
                
                # 最新と直前をマージして差分を出す
                df_merge = pd.merge(
                    df_latest[['article_id', 'title', 'views', 'likes']], 
                    df_prev[['article_id', 'views', 'likes']], 
                    on='article_id', suffixes=('', '_prev'), how='left'
                ).fillna(0)
                
                df_merge['views_delta'] = df_merge['views'] - df_merge['views_prev']
                df_merge['likes_delta'] = df_merge['likes'] - df_merge['likes_prev']
                
                # 合計増分
                total_views_delta = int(df_merge['views_delta'].sum())
                total_likes_delta = int(df_merge['likes_delta'].sum())
                df_delta = df_merge.sort_values('views_delta', ascending=False)

            # サマリー表示
            st.info(f"最終更新日: {latest_date.strftime('%Y-%m-%d %H:%M')}")
            c1, c2, c3 = st.columns(3)
            c1.metric("公開中の記事数", f"{len(df_latest)} 記事")
            c2.metric("累計総ビュー数", f"{df_latest['views'].sum():,}", 
                      delta=f"+{total_views_delta:,}" if has_previous else None)
            c3.metric("累計総スキ数", f"{df_latest['likes'].sum():,}", 
                      delta=f"+{total_likes_delta:,}" if has_previous else None)

            st.markdown("---")

            # --- 上部: 全体累計ビュー推移 ---
            st.subheader("📈 全体累計ビュー推移")
            if has_previous:
                total_views_df = df_all.groupby('acquired_at')['views'].sum().reset_index()
                fig_total = px.line(total_views_df, x='acquired_at', y='views', 
                                    title='全記事の合計累計閲覧数推移',
                                    labels={'acquired_at':'日付', 'views':'合計累計ビュー数'})
                fig_total.update_traces(mode='lines+markers')
                fig_total.update_layout(
                    xaxis_type='date',
                    yaxis=dict(tickformat=',d', rangemode='tozero')
                )
                st.plotly_chart(fig_total, use_container_width=True)
            else:
                st.info("📉 推移グラフを表示するには、2日分以上のデータが必要です。明日また最新データを取得してください。")

            st.markdown("---")
            
            # グラフエリア
            tab1, tab2, tab3 = st.tabs(["📊 累計ランキング", "🔥 本日の伸び", "📈 生データ"])
            
            with tab1:
                fig = px.bar(df_latest.head(20), x='views', y='title', orientation='h',
                             text_auto=True,
                             title="累計ビュー数 TOP 20", 
                             labels={'views':'累計ビュー数', 'title':'記事タイトル'})
                fig.update_layout(yaxis={'autorange': 'reversed'}, height=600)
                st.plotly_chart(fig, use_container_width=True)

            with tab2:
                if has_previous:
                    fig_delta = px.bar(df_delta.head(20), x='views_delta', y='title', orientation='h',
                                 text_auto=True,
                                 title="本日のビュー増加数 TOP 20", 
                                 labels={'views_delta':'本日増えたビュー数', 'title':'記事タイトル'})
                    fig_delta.update_layout(yaxis={'autorange': 'reversed'}, height=600)
                    st.plotly_chart(fig_delta, use_container_width=True)
                else:
                    st.info("🔥 「本日の伸び」ランキングを表示するには、明日もう一度データを取得してください。2日分のデータが溜まると、前回からの増分が表示されます。")

            with tab3:
                st.dataframe(df_latest, use_container_width=True)

            # --- 下部: 全記事の時系列ビュー数推移 ---
            st.markdown("---")
            st.subheader("📊 全記事の個別ビュー数推移")
            
            if has_previous:
                df_pivot_src = df_all[['acquired_at', 'title', 'views']].drop_duplicates(['acquired_at', 'title'])
                pivot_df = df_pivot_src.pivot(index='acquired_at', columns='title', values='views')
                
                fig_all = go.Figure()
                for title in pivot_df.columns:
                    fig_all.add_trace(go.Scatter(
                        x=pivot_df.index, 
                        y=pivot_df[title], 
                        mode='lines', 
                        name=title, 
                        connectgaps=True,
                        hovertemplate='<b>%{fullData.name}</b><br>日付: %{x}<br>ビュー数: %{y:,}<extra></extra>'
                    ))
                fig_all.update_layout(
                    title='全記事の累計閲覧数推移（個別）',
                    xaxis_title='日付',
                    yaxis_title='累計閲覧数',
                    hovermode='closest',
                    showlegend=False,
                    height=700,
                    xaxis_type='date',
                    yaxis=dict(tickformat=',d')
                )
                st.plotly_chart(fig_all, use_container_width=True)
            else:
                st.info("📉 個別記事の推移を表示するには、2日分以上のデータが必要です。")

            # ダウンロード
            with st.expander("📥 データのダウンロード"):
                st.write("SQLiteデータベースファイル（note_dashboard.db）をダウンロードできます。")
                with open(db_file, "rb") as f:
                    st.download_button("SQLite DBをダウンロード", f, file_name="note_dashboard.db")
        else:
            st.info("まだデータがありません。サイドバーから取得を開始してください。")
    else:
        st.info("データファイル（SQLite DB）が見つかりません。サイドバーから取得を開始してください。")

if __name__ == "__main__":
    main()