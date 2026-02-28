# note分析ダッシュボード EXE作成ガイド (v2対応版)

2026年2月27日のnote.com仕様変更（認証エラー）に対応したEXEファイルを作成するための手順です。

## 修正内容
- `dist_exe/note_dashboard_local.py`: ログインエラーを解消する新しい認証ロジックを適用済み。
- `dist_exe/build_exe.py`: 出力ファイル名を `note_dashboard_v2` に変更済み。

## EXEのビルド手順
この環境のターミナル（PowerShell等）で、プロジェクトのルートディレクトリ（`noteanalysis_basic`）に移動し、以下のコマンドを実行してください。

```powershell
# 1. プロジェクトフォルダに移動
cd "C:\Users\s3792\OneDrive - 学校法人先端教育機構\datastrage\GitProjects
oteanalysis_basic"

# 2. ビルドスクリプトを実行
python dist_exe/build_exe.py
```

実行完了後、`dist/note_dashboard_v2/` フォルダ内に新しいEXEファイルが生成されます。

## 注意事項
- PyInstallerなどの必要なライブラリがインストールされている環境で実行してください。
- ビルドには数分かかる場合があります。
- 画面が重くなるのを防ぐため、CLIツール経由ではなく直接ターミナルで実行することをお勧めします。
