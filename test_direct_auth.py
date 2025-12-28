import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

# DATABASE_URL を取得
DATABASE_URL = os.getenv("DATABASE_URL")

def test_db_auth():
    print(f"--- Testing Direct Database Auth ---")
    
    try:
        # 1. 接続テスト
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        print("✅ Connected to Neon successfully!")

        # 2. 既存ユーザー確認
        cursor.execute("SELECT COUNT(*) FROM app_users")
        count = cursor.fetchone()[0]
        print(f"Current user count: {count}")

        # 3. テストユーザーの登録（新規登録のシミュレーション）
        test_email = f"test_{int(os.urandom(2).hex(), 16)}@example.com"
        test_pass = "password123"
        
        print(f"\nAttempting to register: {test_email}")
        
        # SQLで直接新規登録
        insert_sql = """
        INSERT INTO app_users (email, password_hash)
        VALUES (%s, crypt(%s, gen_salt('bf')))
        RETURNING email;
        """
        cursor.execute(insert_sql, (test_email, test_pass))
        registered_email = cursor.fetchone()[0]
        conn.commit()
        print(f"✅ User registered: {registered_email}")

        # 4. ログイン照合（ログインのシミュレーション）
        print(f"\nAttempting to login: {test_email}")
        
        login_sql = """
        SELECT email FROM app_users 
        WHERE email = %s AND password_hash = crypt(%s, password_hash);
        """
        cursor.execute(login_sql, (test_email, test_pass))
        result = cursor.fetchone()
        
        if result:
            print(f"✅ Login success for: {result[0]}")
        else:
            print("❌ Login failed: User not found or password incorrect")

        cursor.close()
        conn.close()
        return True

    except Exception as e:
        print(f"💥 Database Error: {e}")
        return False

if __name__ == "__main__":
    if not DATABASE_URL:
        print("Error: DATABASE_URL is missing in .env")
    else:
        test_db_auth()
