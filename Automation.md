# TikTok → YouTube Automation — Complete Build Guide

> **How to use this file:** paste it into Claude Code (or any Claude session with file/shell
> access) and say: *"Build this workflow for me exactly as described. Ask me only for the
> inputs listed in Section 2."* Everything else — the code, the repos, the scheduling, the
> secrets wiring — is specified here precisely enough to rebuild from zero.
>
> This is a working, production-tested system running 9+ channels daily. Every design
> decision below exists because something broke without it. Do not "simplify" the parts
> marked ⚠ — they are there for a reason explained inline.

---

## 1. What it does (one paragraph)

Every day, at fixed times chosen for the channel's audience timezone, a GitHub Actions
job wakes up, lists a TikTok creator's profile with **yt-dlp**, picks one video the
channel has not posted yet, downloads it **without watermark**, verifies it has an audio
track, uploads it to a YouTube channel through the **YouTube Data API v3** as a Short,
records the result in a small SQLite database committed back to the repo, and posts a
Discord message. Two uploads per channel per day is the normal setup. The owner never
touches it; a portal app shows live stats.

**No server, no VPS, no PC needs to stay on.** Everything runs on GitHub's free Actions
runners and a free cron-job.org account.

---

## 2. Inputs the builder must ask the user for (and nothing else)

Per channel:

| Input | Example | Notes |
|---|---|---|
| TikTok handle | `@crs_extra` | stored without the @ |
| YouTube channel's Gmail | `thechannel@gmail.com` | **the GCP project is created under THIS account** — one Gmail per channel, never shared |
| Audience country | US / Germany / Japan | picks the publish-time group (Section 7) |
| Videos per day | 2 | 1 or 2 |
| Upload mode | `popular_split` | see Section 6; `popular_split` is the default choice |

Once, globally:

| Input | Notes |
|---|---|
| GitHub account | all repos live here; a **classic PAT** with `repo` + `workflow` scope (add `delete_repo` if Claude should be able to remove channels later) |
| cron-job.org account + API key | free tier is enough (60+ jobs) |
| Discord webhook URL | one channel for all notifications |
| TikTok cookies (Netscape `cookies.txt`) | exported from a logged-in browser; improves reliability (Section 9) |
| YouTube Data API key (read-only) | for the stats portal only; from any GCP project |

---

## 3. Architecture

```
cron-job.org  (fires at an exact UTC minute, e.g. 22:00)
   |  HTTP POST -> api.github.com/repos/OWNER/REPO/actions/workflows/upload-slot1.yml/dispatches
   v
GitHub Actions runner  (fresh Ubuntu VM, fresh IP -- see Section 8)
   |- checkout repo (brings the committed SQLite DB)
   |- pip install -r requirements.txt
   |- write credentials/ + tokens/ + cookies from GitHub Secrets (base64)
   |- python run.py --slot 1 --channel channel_N
   |     |- slot_already_ran_today?  -> skip   (per-day guard)
   |     |- list TikTok profile (yt-dlp, flat, newest 150)
   |     |- pick video  (mode-dependent, Section 6)
   |     |- download -> ./downloads/<id>.mp4   (on the runner, ephemeral)
   |     |- ffprobe: has audio? -> if not, re-download audio-safe; still none -> refuse
   |     |- upload to YouTube (public, category, tags)
   |     '- DB: posted_videos.status='uploaded', runs.status='success'
   |- git commit data/channel_N.db -> push (retry x3 with rebase)
   '- upload logs as an Actions artifact (14 days)
```

**One repo per channel. One GCP project per channel. One Gmail per channel.**
This de-correlates the channels: a termination of one cannot cascade to the others
through a shared project, token, or account. ⚠ Never put two channels in one repo or
one GCP project — it was done once early on and had to be undone.

---

## 4. Repository layout (identical in every channel repo)

```
tiktok-yt-automation-N/
|-- .github/workflows/
|   |-- upload-slot1.yml        # one per slot; workflow_dispatch only (no GitHub cron)
|   |-- upload-slot2.yml
|   |-- daily-summary.yml       # Discord digest, 03:00 UTC
|   |-- update-ytdlp.yml        # daily yt-dlp bump, 06:00 UTC
|   '-- stage.yml               # optional pre-download stage (rarely used)
|-- src/
|   |-- tiktok_downloader.py    # yt-dlp wrapper, format selector, audio check
|   |-- youtube_uploader.py     # Data API v3 upload + token refresh
|   |-- channel_runner.py       # the per-channel pipeline + all pickers
|   |-- orchestrator.py         # loops channels, collects results, notifies
|   |-- db.py                   # SQLite wrapper
|   |-- config.py               # channels.yaml loader + validation
|   |-- notifier.py             # Discord webhook
|   '-- video_converter.py      # ffmpeg helpers (longform modes only)
|-- channels.yaml               # THE channel config (Section 5)
|-- run.py                      # entry: --slot N --channel channel_N [--dry-run]
|-- reauth_nobrowser.py         # mints the OAuth token without auto-opening a browser
|-- requirements.txt
|-- data/channel_N.db           # committed SQLite state  <- the only thing that persists
|-- credentials/                # gitignored; client_secret JSON written by CI from a secret
|-- tokens/                     # gitignored; OAuth token JSON written by CI from a secret
|-- downloads/                  # gitignored; scratch space on the runner
'-- logs/                       # gitignored; uploaded as an Actions artifact
```

### Where do downloaded videos go?

`./downloads/<tiktok_id>.mp4` **inside the runner's workspace**. The runner is a
throwaway VM: the file exists for the ~30 seconds between download and upload and is
gone when the job ends. **Nothing is ever stored on the owner's PC or on any server.**
The only persistent artifact is the tiny SQLite DB (tens of KB) committed to the repo.

`requirements.txt`:

```
yt-dlp>=2026.7.4
curl_cffi>=0.10,<0.15        # see Section 9 #7 — do not loosen
google-api-python-client>=2.100.0
google-auth-httplib2>=0.1.1
google-auth-oauthlib>=1.1.0
pyyaml>=6.0
python-dotenv>=1.0.0
requests>=2.31.0
```

The upload workflow (per slot): `workflow_dispatch` with inputs `channel` and `dry_run`;
`concurrency: group: upload-channel_N`; Python 3.11; installs ffmpeg (apt, falling back
to a static build with timeouts so a slow mirror can never hang the job); restores the
two credential files and the cookie jar from secrets via `base64 -d`; writes `.env`
with `DISCORD_WEBHOOK_URL` and `DRY_RUN`; runs `python run.py --slot N --channel channel_N`;
then checkpoints the SQLite WAL and commits `data/channel_N.db` back to `main` with a
3-attempt fetch/rebase/push loop; finally uploads `logs/` as an artifact.

---

## 5. `channels.yaml` — the full schema

```yaml
channels:
  - id: channel_13                      # unique; used in DB, logs, secret names
    tiktok_username: "thejacquelin"     # no @
    youtube_channel_name: "thejacquelin"
    owner_email: "thechannel@gmail.com" # ALWAYS record: the Gmail that owns the YouTube
                                        # channel AND the GCP project. Not derivable
                                        # later; needed to sign in on a phone.
    google_credentials_file: "credentials/channel_13_client_secret.json"
    oauth_token_file: "tokens/channel_13_token.json"
    videos_per_day: 2
    description_footer: ""
    default_tags: [comedy, sketch, viral]
    youtube_category_id: "23"           # 22 People&Blogs, 23 Comedy, 28 Sci&Tech, 26 HowTo
    enabled: true
    max_retry_days: 7                   # failed download -> retried daily up to this
    shorts_max_seconds: 180             # <= this and vertical -> uploaded as a Short
    upload_mode: popular_split          # Section 6
    max_download_candidates: 20         # SET 15-20. The code default is 3, which lost
                                        # whole days when TikTok refused 3 in a row.
    # tiktok_username_slot2: "other"    # optional secondary account for slot 2
    slot_publish_times_utc:
      1: "17:00"                        # these ARE the cron fire times (Section 7)
      2: "19:00"
```

Optional keys: `min_upload_date: YYYY-MM-DD` (ignore older TikToks),
`min_backlog_for_slot1: N` (skip slot 1 unless N unposted videos exist),
`fixed_title` (otherwise the TikTok caption becomes the YouTube title).

`config.py` validates `upload_mode` against a whitelist — when adding a mode to the
picker, add it to the whitelist too, or the run fails at config load.

---

## 6. Upload modes (what each slot picks)

| mode | slot 1 | slot 2 | used for |
|---|---|---|---|
| `popular_split` ⭐ | **newest** unposted TikTok | **most-viewed** unposted TikTok (whole profile) | default for every new channel |
| `short_only` | newest | newest | simple creators |
| `popular_only` | most-viewed | — | 1/day channels |
| `sequence` | explicit ordered list + N-day gap | — | series content (part 1, part 2, ...) |
| `split`, `tiered_split`, `dual`, `longform_only`, `trim_dual` | ... | ... | legacy longform experiments; **do not use for new channels** |

Selection rules that apply in every mode:

- A video counts as "posted" if `posted_videos` has it with status
  `uploaded | failed_permanent | skipped | pending_retry`.
- Photo/slideshow posts (`/photo/` URL) are filtered out at listing time — no video stream.
- `view_count` must be copied out of the yt-dlp entry when listing, or "most-viewed"
  silently degrades to newest-first (one old repo had this bug).
- Videos in `pending_retry` that are due today are tried **first**.
- If the chosen video fails to download, try the next candidate (up to
  `max_download_candidates`) so the slot still posts on time; the failed one is queued
  for tomorrow.

**Secondary account (`tiktok_username_slot2`):** slot 2 reads a different TikTok
profile. When that profile has no unposted videos left, the picker **falls back to the
primary on its own** — no config change. Use this when one creator cannot supply
2 videos/day (a creator posting fewer than ~15/month will run dry).

---

## 7. Scheduling — timezone groups

`slot_publish_times_utc` is **the exact time the upload goes public**: the cron fires at
that minute and the video is uploaded as `public` immediately (YouTube's fresh-content
boost fires at the right moment, not during a private buffer window).

One schedule per audience country. Do not invent per-channel times.

| Audience | slot 1 UTC | slot 2 UTC | local | Pakistan time |
|---|---|---|---|---|
| **US** | 22:00 | 00:00 | 6 PM / 8 PM ET | 3 AM / 5 AM |
| **Europe** | 17:00 | 19:00 | 7 PM / 9 PM CEST | 10 PM / 12 AM |
| **Japan** | 09:00 | 11:00 | 6 PM / 8 PM JST | 2 PM / 4 PM |

⚠ Australian-content channels still go in the **US** group — the audience is US.

### cron-job.org — 5 jobs per 2-slot channel

| title pattern | time | target workflow |
|---|---|---|
| `TikTok-YT ChN Slot1 (...)` | slot 1 time | `upload-slot1.yml` |
| `TikTok-YT ChN Slot1 (...) RETRY (+90m fresh IP)` | slot 1 + 90 min | `upload-slot1.yml` |
| `TikTok-YT ChN Slot2 (...)` | slot 2 time | `upload-slot2.yml` |
| `TikTok-YT ChN Slot2 (...) RETRY (+90m fresh IP)` | slot 2 + 90 min | `upload-slot2.yml` |
| `TikTok-YT ChN Daily Summary (8AM PKT)` | 03:00 UTC | `daily-summary.yml` |

Each job: `POST https://api.github.com/repos/OWNER/REPO/actions/workflows/<file>/dispatches`,
headers `Accept: application/vnd.github.v3+json`, `Authorization: token <PAT>`,
`Content-Type: application/json`, body `{"ref":"main"}`, timezone UTC.

The RETRY job is safe to fire every day: the **per-day guard** (`runs` has a `success`
row for today + slot → skip) makes it a no-op when the first run succeeded.

⚠ cron-job.org's API rate-limits bursts (HTTP 429). Create/delete jobs with a 10–20 s
pause between calls. When deleting, always match jobs by **target URL**, never by title.

---

## 8. IP addresses — what each upload looks like from outside

**Every workflow run gets a brand-new GitHub Actions VM with a different public IP.**
There is no fixed IP per channel and no shared IP across channels:

- ch1's slot 1, ch1's slot 2, and ch1's retry are three different IPs.
- ch1 and ch2 running at the same minute are on different VMs and different IPs.
- The RETRY jobs are named "fresh IP" for exactly this reason: if TikTok blocked the
  first runner's IP, 90 minutes later a different runner tries.

The IPs belong to Microsoft Azure datacenter ranges (GitHub-hosted runners). Both the
TikTok download and the YouTube upload happen from that runner IP. The owner's home IP
is never involved.

Consequences to design for:

- TikTok is more hostile to datacenter IPs than residential ones. That is why cookies,
  browser impersonation, the Referer header, and retries all exist (Section 9).
- YouTube does not care about the upload IP. What matters there is that each channel has
  **its own** OAuth token from **its own** GCP project under **its own** Gmail.

---

## 9. TikTok download — the hard-won rules ⚠

All in `src/tiktok_downloader.py`. Each one fixed a real outage.

1. **Format selector — never the watermarked stream.**
   `format_id^=download` is TikTok's "Save video" = watermarked. Use `format_id^=play`.
   Prefer `h264` renditions: TikTok's `bytevc1`/h265 streams *claim* `acodec=aac` but
   download **video-only** → silent uploads.

   ```
   bestvideo[format_id^=play][ext=mp4]+bestaudio
   /best[format_id^=play][ext=mp4][vcodec!=none]
   /best[format_id^=play][vcodec!=none]
   /best[format_id^=h264][ext=mp4][vcodec!=none]
   /best[ext=mp4][vcodec!=none]
   /best[vcodec!=none]
   ```

2. **Verify audio with ffprobe after download** (`-select_streams a`). If there is no
   audio track: re-download with an audio-safe selector; if still none, **return None —
   never upload a silent video.** yt-dlp metadata cannot be trusted for this.
3. **Send `Referer: https://www.tiktok.com/` on every request** (`http_headers` in
   ydl_opts). Since 2026-08-14 TikTok serves a bot-challenge page to requests without it
   and yt-dlp fails every video with `Unexpected response from webpage request`
   (yt-dlp issue #17403). This one header restored the whole system.
4. **Retry each download 3× with a pause (4 s, 8 s).** TikTok rejects ~30% of requests
   at random — the same video fails once and succeeds seconds later.
5. **Treat an empty profile listing as a failed attempt, not an empty profile.** yt-dlp
   runs with `ignoreerrors=True`, so a rejected listing returns no entries instead of
   raising. Retry it (3×, 2/4/8 s) before concluding there is nothing to post.
6. **Profile fetch batch = 150**, not 50. When the newest 50 are all posted the code falls
   back to an unbounded fetch, and *that* deeper pagination is what TikTok blocks on
   runners ("Unable to extract secondary user ID"). 150 covers most profiles in one call.
7. **Browser impersonation via `curl_cffi`** — pin `curl_cffi>=0.10,<0.15`. yt-dlp only
   registers 0.5.10 / 0.10–0.14; 0.15+ silently yields zero impersonation targets.
   Resolve the target from what yt-dlp actually registered; never hard-code `"chrome"`.
8. **Cookies** (`TIKTOK_COOKIES_FILE` → `cookiefile`) from a logged-in browser export,
   stored base64 in a secret. Improves listing reliability; not strictly required.
9. **`max_download_candidates: 15–20`** in every channel config (Section 5).
10. **yt-dlp version:** `yt-dlp>=2026.7.4` (stable) works **with** rule 3. The
    `update-ytdlp.yml` workflow bumps the pin daily from PyPI and pings Discord if the
    version check itself fails.

---

## 10. YouTube upload

- Scope: `https://www.googleapis.com/auth/youtube.upload` only.
- Body: title = TikTok caption (or `fixed_title`), description = caption + footer,
  `categoryId`, `tags` (+ a `Shorts` tag added automatically for vertical ≤ 180 s),
  `privacyStatus: public`, `selfDeclaredMadeForKids: false`. No `publishAt` (Section 7).
- Token refresh is handled by `google-auth`. The refresh token must come from an OAuth
  consent screen **published to production** — in "Testing" status refresh tokens die
  after 7 days.
- Error `403 authenticatedUserAccountSuspended` = the channel was terminated. Stop; do
  not retry; remove the channel (Section 13).

---

## 11. Onboarding a channel — exact sequence (Claude does all of it)

1. **Verify the TikTok profile** with `yt-dlp --flat-playlist --dump-json`: count videos,
   photo posts, durations (all ≤ 180 s → all Shorts), median views, the creator's
   posting rate. ⚠ If the creator posts < ~15 videos/month, 2/day will run dry — warn
   the user and suggest a secondary account.
2. **Create the repo** `tiktok-yt-automation-N` by cloning the newest existing channel
   repo (so it carries every fix), deleting `.git` and `data/*.db`, renaming every
   `channel_OLD` / `CHANNEL_OLD` / old-handle reference to the new channel, rewriting
   `channels.yaml`, setting the workflow `name:` lines. Assert no old references remain.
   Push to a new GitHub repo.
3. **Shared secrets** on the repo: `DISCORD_WEBHOOK_URL`, `TIKTOK_COOKIES` (base64 of
   cookies.txt). Use the Actions secrets API with libsodium sealed boxes (`pynacl`).
4. **GCP — under the channel's Gmail** (browser):
   - create project `tiktok-yt-channelN` (accept the GCP Terms of Service dialog)
   - enable **YouTube Data API v3**
   - Google Auth Platform → Branding/consent: app name `chN-uploader`, support email =
     the channel Gmail, audience **External**, contact = the channel Gmail, accept the
     Google API Services User Data Policy checkbox
   - **Audience → Publish app → Confirm** (status must read "In production")
   - Clients → Create client → **Desktop app** → name `chN-desktop`. Read the client ID
     and secret **from the page DOM, not from a screenshot** (OCR confuses `l / I / 1`).
     The secret is shown once. If the JSON download is blocked, write the file yourself:
     `{"installed":{"client_id":"...","client_secret":"...","project_id":"tiktok-yt-channelN","auth_uri":"https://accounts.google.com/o/oauth2/auth","token_uri":"https://oauth2.googleapis.com/token","redirect_uris":["http://localhost"]}}`
5. **Mint the OAuth token**: `python reauth_nobrowser.py channel_N` prints an auth URL;
   open it in the browser **signed in as the channel Gmail**, "Advanced → Go to app
   (unsafe)" (the app is unverified, that is expected) → Continue. Confirm
   `tokens/channel_N_token.json` contains a `refresh_token`.
6. **Channel secrets**: `CHANNEL_N_CLIENT_SECRET`, `CHANNEL_N_TOKEN` (base64 of the two
   JSON files).
7. **Dry run**: dispatch `upload-slot1.yml` with `dry_run=true`; read the log — profile
   found, video selected, "Would upload". Fix anything before going live.
8. **cron-job.org**: create the 5 jobs (Section 7) with pauses between calls.
9. **First real upload**: dispatch slot 1 with `dry_run=false`; verify with the Data API
   that the video is `public` / `processed` and that the watch page is playable.
10. **Portal**: trigger the portal sync; the channel should appear with its email.

Never hand any of these steps back to the user as a to-do list. The only things Claude
cannot do are type a password, create a Google account, or pass a CAPTCHA.

---

## 12. Daily operations & diagnosis

- **Audit script** (read each repo's DB directly — independent of the portal): for each
  channel and UTC day, count `uploaded` rows vs `videos_per_day`; a slot only counts as
  *due* once its time + 100 min (the retry window) has passed. Print the `runs` rows for
  any short day.
- `runs.status` meanings: `success` · `skipped` (per-day guard — **fine**) ·
  `no_content` (nothing left to post — content exhaustion, not a bug) · `failed`
  (read the Actions log).
- A slot that failed and then succeeded on its retry is **not** a miss.
- Retry queue: `pending_retry` rows with `next_retry_date`; when `retry_count` exceeds
  `max_retry_days` the row becomes `failed_permanent`.
- ⚠ When testing a fix, use `dry_run=true`. A real test run consumes that slot's daily
  guard, and the scheduled run will then skip.

### Is the channel alive? (pure-API discriminator — verified on 4 dead, 6 live channels)

```
channels.list(id)          -> 0 items   AND
playlistItems(UU<id>)      -> 404 playlistNotFound        =>  TERMINATED / deleted
channels.list -> 0 items   but playlistItems -> OK        =>  new, not indexed yet (fine)
```

Do **not** decide from the channel web page alone — it answers inconsistently.

---

## 13. Removing a channel (terminated or poor performance)

1. Mirror-backup the repo (`git clone --mirror`) locally.
2. Delete its cron jobs — **match by target URL**, one at a time with pauses.
3. Delete every file in `.github/workflows/`, set `channels: []` with a dated comment
   explaining why, delete `data/*.db`.
4. Archive the repo (deleting needs the `delete_repo` scope on the PAT).
5. Delete the GCP project from the channel's Gmail (browser).
6. Re-sync the portal. **Never reuse the repo or GCP project for a new channel.**

---

## 14. Stats portal — optional but recommended

A single static HTML app hosted on **GitHub Pages** (public repo), installable on an
iPhone home screen and as a Chrome app window on desktop. Design constraints that matter:

- **No secrets in the public repo.** The channel list is published as `channels.enc`
  (AES-GCM; the key lives only in the user's browser via a `#setup=` URL fragment —
  fragments are never sent to servers). Alive/dead status is keyed by SHA-256 of the
  channel ID.
- A scheduled Action (`sync.yml`, every 30 min) walks every `tiktok-yt-automation-*`
  repo (skipping archived ones), reads `channels.yaml` + the DB, resolves the YouTube
  channel from the newest upload, probes alive/dead with the Section-12 discriminator,
  and publishes `channels.enc` + `status.json`. **A new channel appears on its own.**
- Per channel the app shows: subs, views (3-month default, lifetime toggle), ranking by
  views, date-range toggles, the **owner Gmail (tap to copy)**, the **upload schedule in
  the user's local time with a live countdown**, and "N videos missed today" — but only
  once a slot's retry window has passed **and** the published data is newer than that
  moment (otherwise it lies).
- ⚠ The page must self-update: stamp a build id into the HTML, publish it in
  `version.json`, reload when they differ on focus/visibility. An installed PWA never
  reloads by itself; without this, fixes never reach the phone.
- ⚠ iOS: safe-area insets must be the **last** CSS rules (mobile media queries reset
  `padding`), or the toolbar sits under the Dynamic Island.
- ⚠ iOS home-screen apps have storage separate from Safari — the setup link must be
  pasted **inside the installed app** once.

---

## 15. Things that went wrong once and must not happen again

| Symptom | Cause | Rule |
|---|---|---|
| Uploads were silent | bytevc1 stream is video-only | Section 9 #1–2 |
| Every channel stopped on the same day | TikTok bot-challenge page | Referer header, Section 9 #3 |
| One channel posted 1/day for a week, unnoticed | secondary account exhausted; unbounded fetch blocked | batch 150 + secondary fallback |
| Slot lost a whole day after 3 refusals | `max_download_candidates` left at default 3 | set 15–20 |
| Channel "dead" per API but alive in the browser | API not indexed yet | playlist discriminator, Section 12 |
| Token died after 7 days | consent screen left in Testing | publish to production |
| Wrong job deleted on cron-job.org | matched by title, not URL | match by URL |
| App showed a stale version for days | PWA never reloads | build-id self-update |
| Couldn't tell which Gmail owns a channel | never recorded | `owner_email` in config, always |
| Uploaded at 8 AM local instead of evening | times set in the owner's timezone, not the audience's | timezone groups, Section 7 |
| One TikTok uploaded twice to the same channel | cross-format exclusion is per format in split modes | use `popular_split` for new channels |
| Videos queued for "retry tomorrow" never cleared | retry counter only bumps when selected | expected; harmless |

---

*End of build guide. Everything above is the current production configuration as of
August 2026.*
