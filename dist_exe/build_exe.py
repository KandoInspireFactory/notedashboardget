import PyInstaller.__main__
import os
import streamlit
import sys

# Streamlitのパス取得
streamlit_path = os.path.dirname(streamlit.__file__)

# コマンドライン引数の構築
args = [
    'dist_exe/run_app.py',               # Entry Point (Wrapper)
    '--name=note_dashboard_v2',             # EXE Name (Updated to v2)
    '--onedir',                          # Folder mode
    '--clean',                           # Clean cache
    '--noconfirm',                       # Overwrite
    
    # Add Streamlit static files
    f'--add-data={streamlit_path};streamlit',
    
    # Add the actual application script as data
    # Source: dist_exe/note_dashboard_local.py
    # Destination: . (Root of the internal bundle)
    '--add-data=dist_exe/note_dashboard_local.py;.',
    
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
