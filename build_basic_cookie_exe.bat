@echo off
chcp 65001 > nul
echo =======================================================
echo   Basic版 note分析ダッシュボード EXEビルドスクリプト (v3 クッキー版)
echo =======================================================
echo.
echo 必要ライブラリの確認とインストールを行っています...
pip install pyinstaller streamlit requests pandas plotly openpyxl cryptography dotenv stripe psycopg2-binary
echo.
echo ビルドプロセスを開始します...
echo.
python dist2/build_cookie_exe.py
echo.
echo =======================================================
echo   🎉 ビルドが正常に完了しました！
echo   dist2/ フォルダの中に「note_dashboard_v3.exe」が生成されています。
echo =======================================================
echo.
pause
