import requests
import os
from dotenv import load_dotenv

load_dotenv()

db_url = os.getenv("DATABASE_URL")
hostname = db_url.split("@")[1].split("/")[0].replace("-pooler", "")
API_KEY = os.getenv("NEON_API_KEY")

def test_api_success():
    url = f"https://{hostname}/sql"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Neon-Connection-String": db_url, # これが鍵！
        "Content-Type": "application/json"
    }
    # ユーザー一覧を取得してみる
    payload = {"query": "SELECT email FROM app_users LIMIT 1"}
    
    print(f"Testing URL: {url}")
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        print(f"Status: {response.status_code}")
        print(f"Response: {response.text}")
        if response.status_code == 200:
            print("\n✅ COMPLETE SUCCESS! We found the correct communication method.")
            return True
    except Exception as e:
        print(f"Error: {e}")
    return False

if __name__ == "__main__":
    test_api_success()
