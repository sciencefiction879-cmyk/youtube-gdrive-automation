import os
import sys
import json
import base64
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly"
]

def main():
    print("=" * 65)
    print("   YouTube OAuth Token Generator (One-Time Setup)")
    print("=" * 65)

    client_secret_file = Path("credentials/client_secret.json")
    if not client_secret_file.exists():
        print(f"\n[ERROR] '{client_secret_file}' not found!")
        print("Please download your OAuth 2.0 Client Secret JSON from Google Cloud Console")
        print(f"and place it at: {client_secret_file.resolve()}\n")
        sys.exit(1)

    tokens_dir = Path("tokens")
    tokens_dir.mkdir(parents=True, exist_ok=True)
    token_file = tokens_dir / "oauth_token.json"

    print("\nStarting OAuth flow...")
    print("Select authentication mode:")
    print("1. Local browser (Default - opens a browser window)")
    print("2. Manual / Headless (Prints URL, you paste the authorization code)")
    
    choice = input("\nEnter choice [1/2, default: 1]: ").strip() or "1"

    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secret_file),
        scopes=SCOPES
    )

    if choice == "2":
        # Headless console flow
        auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
        print("\n" + "=" * 65)
        print("1. Open the following URL in your browser:")
        print(auth_url)
        print("=" * 65)
        code = input("\n2. Sign in with your YouTube channel Gmail, approve permissions,\nand paste the authorization code here: ").strip()
        flow.fetch_token(code=code)
        creds = flow.credentials
    else:
        # Local server flow
        creds = flow.run_local_server(port=8080, prompt="consent", access_type="offline")

    with open(token_file, "w", encoding="utf-8") as f:
        f.write(creds.to_json())

    print("\n" + "=" * 65)
    print(f"✅ Success! OAuth Token saved to: {token_file.resolve()}")
    print("=" * 65)

    # Print GitHub Secrets Helper
    print("\n📋 GITHUB SECRETS BASE64 STRINGS (Copy these to GitHub Settings > Secrets):\n")
    
    with open(client_secret_file, "rb") as f:
        b64_client_secret = base64.b64encode(f.read()).decode("utf-8")
    with open(token_file, "rb") as f:
        b64_token = base64.b64encode(f.read()).decode("utf-8")

    print(f"YOUTUBE_CLIENT_SECRET_B64:\n{b64_client_secret}\n")
    print(f"YOUTUBE_OAUTH_TOKEN_B64:\n{b64_token}\n")

if __name__ == "__main__":
    main()
