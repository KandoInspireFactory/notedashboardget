import requests
import os
from dotenv import load_dotenv
import json

load_dotenv()

# SQL API用のURLを構築（rest/v1 ではなく sql にする）
DATA_API_URL = os.getenv("NEON_DATA_API_URL")
# https://.../rest/v1 を https://.../sql に変換
SQL_API_URL = DATA_API_URL.replace("/rest/v1", "/sql")
API_KEY = os.getenv("NEON_API_KEY")

def test_sql_api(sql_query, params=None):
    print(f"\n--- Testing SQL API ---")
    print(f"URL: {SQL_API_URL}")
    print(f"Query: {sql_query}")
    
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "query": sql_query,
        "params": params
    }
    
    try:
        response = requests.post(SQL_API_URL, headers=headers, json=payload, timeout=10)
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.text}")
        if response.status_code == 200:
            print("✅ SUCCESS!")
            return True
        else:
            print("❌ FAILED")
            return False
    except Exception as e:
        print(f"💥 Error: {str(e)}")
        return False

if __name__ == "__main__":
    # app_users テーブルが存在するか確認する簡単なクエリ
    test_sql_api("SELECT COUNT(*) FROM app_users")
