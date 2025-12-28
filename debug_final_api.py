import requests
import os
from dotenv import load_dotenv

load_dotenv()

# ホスト名を DATABASE_URL から抽出
# 例: postgresql://... @hostname/dbname
db_url = os.getenv("DATABASE_URL")
hostname = db_url.split("@")[1].split("/")[0].replace("-pooler", "")
API_KEY = os.getenv("NEON_API_KEY")

def test_api(url_pattern):
    print(f"\nTesting: {url_pattern}")
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {"query": "SELECT 1"}
    try:
        response = requests.post(url_pattern, headers=headers, json=payload, timeout=10)
        print(f"Status: {response.status_code}")
        print(f"Response: {response.text}")
        return response.status_code == 200
    except Exception as e:
        print(f"Error: {e}")
        return False

if __name__ == "__main__":
    # NeonのHTTP SQL APIの標準的なパス
    url = f"https://{hostname}/sql"
    test_api(url)

