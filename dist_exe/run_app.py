import streamlit.web.cli as stcli
import os, sys

def resolve_path(path):
    if getattr(sys, 'frozen', False):
        basedir = sys._MEIPASS
    else:
        basedir = os.path.dirname(__file__)
    return os.path.join(basedir, path)

if __name__ == "__main__":
    script_path = resolve_path(os.path.join("dist_exe", "note_dashboard_local.py"))
    
    # If the script is not found in the expected location (likely due to build structure), 
    # try looking in the root or adjust as needed for the specific build context.
    # For --onedir, it might be in the root of the _internal folder if added directly.
    if not os.path.exists(script_path):
        # Fallback: check if it's just in the root
        script_path = resolve_path("note_dashboard_local.py")

    sys.argv = [
        "streamlit",
        "run",
        script_path,
        "--global.developmentMode=false",
    ]
    sys.exit(stcli.main())
