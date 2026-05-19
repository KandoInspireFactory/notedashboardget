# note分析ダッシュボード EXE作成ガイド (v3対応版)

このプロジェクトには、これまでの復旧の歴史とビルド方法が格納されています。

---

## 🚀 【最新】v3対応版（クッキー認証・無限ダミー防止・2重安全装置搭載）

2026年2月27日のnote.com仕様変更（reCAPTCHA導入）によるID/PWログイン失敗を、ブラウザ拡張機能「Cookie-Editor」を用いて安全にバイパスし、さらに「1500件超の全コンテンツ取得」と「無限ダミーデータ防止」を完遂した最新版です。

### 📁 関連ファイル
- `dist2/note_dashboard_cookie_fixed.py`: メインのStreamlitアプリ（2重の終端判定付き）
- `dist2/run_cookie_app.py`: Streamlit起動用ラッパー
- `dist2/build_cookie_exe.py`: PyInstallerビルドスクリプト

### 🛠️ EXEのビルド手順
ルートフォルダにあるバッチファイルをダブルクリックするか、ターミナルで以下を実行します。

```powershell
# 1. プロジェクトフォルダに移動
cd "C:\Users\s3792\OneDrive - 学校法人先端教育機構\datastrage\GitProjects\noteanalysis_basic"

# 2. ビルドスクリプトを実行
python dist2/build_cookie_exe.py
```

実行完了後、 **`dist2/` フォルダの中に `note_dashboard_v3.exe`** が直接生成されます。

---

## 📜 過去の遺産: v2対応版（ID/PWログイン復旧版）

仕様変更に一時的にID/PW認証のヘッダー調整で対応していたバージョンです。（※現在はreCAPTCHA制限によりクッキー版v3の使用を強く推奨します）

### 📁 関連ファイル
- `dist_exe/note_dashboard_local.py`: v2版アプリコード
- `dist_exe/build_exe.py`: v2用ビルドスクリプト

### 🛠️ v2版のビルド手順
```powershell
python dist_exe/build_exe.py
```
実行完了後、`dist/note_dashboard_v2/` フォルダ内にEXEファイルが生成されます。

---

## ⚠️ 注意事項
- PyInstallerなどの必要なライブラリがインストールされている環境で実行してください。
- ビルドには数分かかる場合があります。
- 画面が重くなるのを防ぐため、CLIツール経由ではなく直接ターミナルで実行することをお勧めします。
