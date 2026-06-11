import PyInstaller.__main__
import os
import streamlit
import sys

# Streamlitのパス取得
streamlit_path = os.path.dirname(streamlit.__file__)

# コマンドライン引数の構築
args = [
    'dist_exe/run_cookie_app.py',               # Entry Point (Fixed Wrapper)
    '--name=note_dashboard_cookie_fixed',       # EXE Name (Updated)
    '--onefile',                                 # 単一ファイル形式
    '--clean',                                   # Clean cache
    '--noconfirm',                               # Overwrite
    
    # Add Streamlit static files
    f'--add-data={streamlit_path};streamlit',
    
    # Add the actual application script as data
    '--add-data=dist_exe/note_dashboard_cookie_fixed.py;.',
    
    # Hooks
    '--additional-hooks-dir=dist_exe',
    
    # Hidden imports
    '--hidden-import=streamlit',
    '--hidden-import=pandas',
    '--hidden-import=plotly',
    '--hidden-import=openpyxl',
    '--hidden-import=sqlite3',
]

# Run build
PyInstaller.__main__.run(args)
