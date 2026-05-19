import PyInstaller.__main__
import os
import streamlit
import sys

# Streamlitのパス取得
streamlit_path = os.path.dirname(streamlit.__file__)

# コマンドライン引数の構築
args = [
    'dist2/run_cookie_app.py',               # Entry Point (Fixed Wrapper)
    '--name=note_dashboard_v3',                 # EXE Name (v3 as requested)
    '--onefile',                                 # 単一ファイル形式
    '--clean',                                   # Clean cache
    '--noconfirm',                               # Overwrite
    '--distpath=dist2',                          # EXE出力先を直接 dist2 に指定！
    
    # Add Streamlit static files
    f'--add-data={streamlit_path};streamlit',
    
    # Add the actual application script as data
    '--add-data=dist2/note_dashboard_cookie_fixed.py;.',
    
    # Hooks
    '--additional-hooks-dir=dist2',
    
    # Hidden imports
    '--hidden-import=streamlit',
    '--hidden-import=pandas',
    '--hidden-import=plotly',
    '--hidden-import=openpyxl',
    '--hidden-import=sqlite3',
]

# Run build
PyInstaller.__main__.run(args)
