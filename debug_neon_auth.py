import requests
import os
from dotenv import load_dotenv
import json

load_dotenv()

# 環境変数の取得
DATA_API_URL = os.getenv("NEON_DATA_API_URL")
API_KEY = os.getenv("NEON_API_KEY")

def test_rpc(endpoint, method="POST", payload=None):
    # URLの調整
    # NEON_DATA_API_URLが https://.../v1 の場合、rpcは https://.../v1/rpc/endpoint になる
    base_url = DATA_API_URL.rstrip("/")
    url = f"{base_url}/rpc/{endpoint}"
    
    print(f"\n--- Testing Endpoint: {endpoint} ---")
    print(f"URL: {url}")
    
    patterns = [
        {
            "name": "Pattern A: Neon-Api-Key Header (Recommended for Data API)",
            "headers": {
                "Neon-Api-Key": API_KEY,
                "Content-Type": "application/json"
            }
        },
        {
            "name": "Pattern B: Authorization Bearer (Perplexity suggest)",
            "headers": {
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json"
            }
        }
    ]
    
    for p in patterns:
        print(f"\nTrying {p['name']}...")
        try:
            response = requests.post(url, headers=p['headers'], json=payload, timeout=10)
            print(f"Status Code: {response.status_code}")
            print(f"Response: {response.text}")
            if response.status_code == 200:
                print("✅ SUCCESS!")
            else:
                print("❌ FAILED")
        except Exception as e:
            print(f"💥 Error: {str(e)}")

if __name__ == "__main__":
    if not DATA_API_URL or not API_KEY:
        print("Error: .env file is missing NEON_DATA_API_URL or NEON_API_KEY")
    else:
        # テスト用のダミーデータで sign_in を試みる（存在するはずの関数）
        # 実際のアカウントでなくても、認証ヘッダーが正しければ 400(JWTエラー) ではなく 
        # 関数内部のエラー（Invalid email等）が返ってくるはず。
        test_data = {"email": "test@example.com", "password": "password123"}
        test_rpc("sign_in", payload=test_data)
        
        # sign_up もテスト
        test_rpc("sign_up", payload=test_data)
