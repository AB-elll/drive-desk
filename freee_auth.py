"""
freee OAuth認証スクリプト
実行するとブラウザが開き、認証後にaccess_token+refresh_tokenを保存する
"""
import http.server
import json
import os
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

from dotenv import load_dotenv
import requests

load_dotenv("clients/karas/.env")

CLIENT_ID     = os.environ["FREEE_CLIENT_ID"]
CLIENT_SECRET = os.environ["FREEE_CLIENT_SECRET"]
TOKEN_PATH    = Path(os.environ.get("FREEE_TOKEN_PATH",
                     Path.home() / ".config" / "drivedesk" / "freee_token.json"))

REDIRECT_URI  = "http://localhost:8788/callback"
AUTH_URL      = "https://accounts.secure.freee.co.jp/public_api/authorize"
TOKEN_URL     = "https://accounts.secure.freee.co.jp/public_api/token"

auth_code = None


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params:
            auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2>✅ 認証成功！このタブを閉じてください。</h2>".encode())
        else:
            self.send_response(400)
            self.end_headers()

    def log_message(self, *args):
        pass  # 標準ログ抑制


def main():
    global auth_code

    # ── 認証URLを開く ────────────────────────────────────────────
    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": "read write",
    })
    url = f"{AUTH_URL}?{params}"
    print(f"ブラウザでfreee認証ページを開きます...\n{url}\n")
    webbrowser.open(url)

    # ── コールバック待機 ─────────────────────────────────────────
    server = http.server.HTTPServer(("localhost", 8788), CallbackHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()

    print("認証を待っています... (Ctrl+Cでキャンセル)")
    timeout = 120
    elapsed = 0
    while auth_code is None and elapsed < timeout:
        time.sleep(1)
        elapsed += 1

    server.shutdown()

    if auth_code is None:
        print("❌ タイムアウト。再実行してください。")
        return

    print(f"認証コード取得: {auth_code[:10]}...")

    # ── トークン交換 ─────────────────────────────────────────────
    resp = requests.post(TOKEN_URL, data={
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": auth_code,
        "redirect_uri": REDIRECT_URI,
    })
    resp.raise_for_status()
    data = resp.json()

    data["expires_at"] = time.time() + data.get("expires_in", 21600) - 300

    # ── 保存 ─────────────────────────────────────────────────────
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(json.dumps(data, indent=2))

    print(f"\n✅ トークン保存完了: {TOKEN_PATH}")
    print(f"   access_token : {data['access_token'][:20]}...")
    print(f"   refresh_token: {'あり ✅' if data.get('refresh_token') else 'なし ❌'}")
    print(f"   expires_in   : {data.get('expires_in')}秒")


if __name__ == "__main__":
    main()
