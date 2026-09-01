# YouTube Sequential Uploader via Google Drive & GitHub Actions

An automated, serverless pipeline to upload videos sequentially (`V1`, `V2`, `V3`, ...) from a Google Drive folder to YouTube using GitHub Actions.

---

## 🌟 Key Features

* **Strict Sequential Uploading**: Automatically scans Google Drive for `V1`, `V2`, `V3`, ... and uploads the next unposted video in exact order.
* **Auto-Discovery & Pairing**: Automatically pairs `V{N}.mp4` with `V{N}.json` and its matching thumbnail `V{N}.jpg`/`V{N}.png`.
* **Shorts vs. Long-Form Handling**:
  * **Long-Form Videos**: Enforces custom thumbnail upload. (Fails safely if missing).
  * **YouTube Shorts**: Custom thumbnail is optional. If present, it uploads the custom thumbnail; if absent, it uploads the Short normally without error.
* **Zero Cost / Zero Hardware**: Runs on GitHub Actions free runners. No VPS or PC running 24/7 required.
* **Resilient State Tracking**: SQLite database (`data/state.db`) commits back to the repo to guarantee videos are never duplicated.
* **Discord Webhook Notifications**: Sends rich embed status cards upon successful uploads or errors.

---

## 📁 Google Drive Setup

You can structure your Google Drive folder in either of these two ways:

### Option A: Subfolders (Recommended)
```
[Your Google Drive Folder]
 ├── Videos/
 │    ├── V1.mp4
 │    ├── V2.mp4
 │    └── V3.mp4
 ├── Metadata/
 │    ├── V1.json
 │    ├── V2.json
 │    └── V3.json
 └── Thumbnails/
      ├── V1.jpg   (Required for Long-form)
      └── V2.png   (Optional for Shorts)
```

### Option B: Flat Root Folder
```
[Your Google Drive Folder]
 ├── V1.mp4
 ├── V1.json
 ├── V1.jpg
 ├── V2.mp4
 ├── V2.json
 └── ...
```

---

## 📝 Metadata Schema (`V{N}.json`)

Create a JSON file for each video (e.g. `V1.json`):

```json
{
  "title": "My Awesome Video Title",
  "description": "Video description with timestamps, links, and hashtags.\n\n#trending #tutorial",
  "tags": ["productivity", "guide", "tutorial"],
  "categoryId": "22",
  "privacyStatus": "public",
  "isShort": false,
  "madeForKids": false
}
```

* **`categoryId` reference**: `22` = People & Blogs, `23` = Comedy, `24` = Entertainment, `28` = Science & Technology.
* **`privacyStatus`**: `"public"`, `"unlisted"`, or `"private"`.
* **`isShort`**: `true` for YouTube Shorts, `false` for Long-form. *(If omitted, the system auto-detects based on vertical aspect ratio and duration ≤ 180s).*

---

## 🚀 Setup & Deployment Guide

### Step 1: Google Cloud Project & APIs
1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (e.g. `youtube-automation`).
3. Enable both:
   - **Google Drive API**
   - **YouTube Data API v3**

### Step 2: Google Drive Service Account
1. Under **IAM & Admin > Service Accounts**, click **Create Service Account**.
2. Name it (e.g. `drive-reader`) and create it.
3. Under the **Keys** tab, click **Add Key > Create new key > JSON**.
4. Download the JSON key file and save it as `credentials/gdrive_service_account.json`.
5. **Important**: Open your Google Drive folder in your browser, click **Share**, and share the folder with the Service Account email (e.g., `drive-reader@your-project.iam.gserviceaccount.com`) as **Viewer**.

### Step 3: YouTube OAuth 2.0 Credentials
1. Under **APIs & Services > OAuth consent screen**:
   - Select **External**, fill in the App Name and your contact email.
   - Click **Publish App** (status must show **In production**).
2. Under **APIs & Services > Credentials**:
   - Click **Create Credentials > OAuth client ID**.
   - Application type: **Desktop app**.
   - Download the JSON and save as `credentials/client_secret.json`.
3. Run the one-time authentication helper locally:
   ```bash
   python auth_setup.py
   ```
4. Sign in with the YouTube channel owner Google account. This will generate `tokens/oauth_token.json` and print the Base64 strings for GitHub Secrets.

### Step 4: Configure GitHub Secrets
In your GitHub repository, navigate to **Settings > Secrets and variables > Actions** and add the following repository secrets:

| Secret Name | Description |
|---|---|
| `GDRIVE_FOLDER_ID` | The ID of your Google Drive folder |
| `GDRIVE_SERVICE_ACCOUNT_B64` | Base64 content of `credentials/gdrive_service_account.json` |
| `YOUTUBE_CLIENT_SECRET_B64` | Base64 content of `credentials/client_secret.json` |
| `YOUTUBE_OAUTH_TOKEN_B64` | Base64 content of `tokens/oauth_token.json` |
| `DISCORD_WEBHOOK_URL` | *(Optional)* Discord Webhook URL for notifications |

To get the base64 string on macOS/Linux:
```bash
base64 -i credentials/gdrive_service_account.json
base64 -i credentials/client_secret.json
base64 -i tokens/oauth_token.json
```

---

## 🧪 Local Testing & Dry Runs

Run a dry run locally to test Google Drive discovery and validation without uploading to YouTube:

```bash
# Set up virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run Dry Run
python run.py --dry-run
```

To test a specific video index manually:
```bash
python run.py --force-video V1 --dry-run
```

---

## ⏰ Automated Scheduling

### Option 1: GitHub Actions Cron
Uncomment the `schedule` block in `.github/workflows/upload.yml`:
```yaml
schedule:
  - cron: '0 17 * * *'  # 17:00 UTC (e.g. 10 PM PKT / 7 PM CEST)
  - cron: '0 19 * * *'  # 19:00 UTC (e.g. 12 AM PKT / 9 PM CEST)
```

### Option 2: cron-job.org (High Reliability Webhooks)
Trigger the workflow via GitHub API:
* **URL**: `https://api.github.com/repos/OWNER/REPO/actions/workflows/upload.yml/dispatches`
* **Method**: `POST`
* **Headers**:
  * `Authorization: Bearer YOUR_GITHUB_PAT`
  * `Accept: application/vnd.github+json`
* **Body**: `{"ref": "main"}`
