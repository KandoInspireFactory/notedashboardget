# note Analysis Dashboard v5

noteの記事ビュー数、スキ数、コメント数を日次で取得し、成長を可視化するダッシュボードアプリです。
過去のExcel/CSVデータを取り込んで統合表示する機能も搭載しています。

## 🚀 主な機能

*   **自動データ取得**: noteから最新の統計データを取得し、データベースに蓄積。
*   **Excelインポート**: 過去に手動で記録していたExcelファイル(.xlsx)をドラッグ＆ドロップで一括取り込み。
*   **可視化**: 日次推移グラフ、ヒートマップカレンダー、記事ごとの成長率ランキング。
*   **データ同期**: クラウド稼働時のデータをSQLite形式でダウンロード可能。

## 🛠️ セットアップ手順 (ローカル実行)

Python 3.10以上が推奨です。

### 1. リポジトリのクローン
```bash
git clone https://github.com/KandoInspireFactory/notedashboardget.git
cd notedashboardget
```

### 2. ライブラリのインストール
```bash
pip install -r requirements.txt
```

### 3. 環境変数の設定
`.env.example` ファイルをコピーして `.env` という名前のファイルを作成してください。

```bash
# Windows (PowerShell)
cp .env.example .env
```
`.env` ファイルを開き、必要に応じて設定を書き換えます。（ローカルで試すだけならそのままでも動きます）

### 4. アプリの起動
```bash
streamlit run noteAPIv8_steamlitV5.py
```
ブラウザが立ち上がり、アプリが表示されます。

## 📂 データ管理について

*   すべてのデータ（データベースや一時ファイル）は自動生成される `note_data/` フォルダ内に保存されます。
*   **過去データの取り込み**: アプリ起動後、サイドバーの「📥 データ管理」メニューからExcelファイルをアップロードしてください。

## ☁️ クラウドデプロイ (Render)

Render.com などのPaaSにデプロイする場合：
1.  **Build Command**: `pip install -r requirements.txt`
2.  **Start Command**: `streamlit run noteAPIv8_steamlitV5.py`
3.  **Environment Variables**: Renderの管理画面で `DATABASE_URL` (PostgreSQL) などを設定してください。
