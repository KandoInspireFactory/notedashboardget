import streamlit.web.cli as stcli
import os, sys

def resolve_path(path):
    if getattr(sys, 'frozen', False):
        basedir = sys._MEIPASS
    else:
        basedir = os.path.dirname(__file__)
    return os.path.join(basedir, path)

if __name__ == "__main__":
    script_path = resolve_path(os.path.join("dist_exe", "note_dashboard_cookie_fixed.py"))
    
    if not os.path.exists(script_path):
        script_path = resolve_path("note_dashboard_cookie_fixed.py")

    sys.argv = [
        "streamlit",
        "run",
        script_path,
        "--global.developmentMode=false",
    ]
    sys.exit(stcli.main())
