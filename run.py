import os
import sys
import shutil
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone

from src.config import Config
from src.db import Database
from src.drive_manager import DriveManager
from src.video_utils import inspect_video
from src.youtube_uploader import YouTubeUploader
from src.notifier import Notifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("pipeline")


def parse_args():
    parser = argparse.ArgumentParser(description="Google Drive to YouTube Sequential Uploader")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--slot", default=None, help="Slot name for multi-run daily scheduling (e.g. slot1, slot2)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate discovery and download without uploading to YouTube")
    parser.add_argument("--force-video", default=None, help="Force upload a specific video sequence (e.g. V1, V2)")
    return parser.parse_args()


def main():
    args = parse_args()
    logger.info("=== Starting YouTube Google Drive Automation Pipeline ===")

    try:
        config = Config(args.config)
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        sys.exit(1)

    db = Database(config.database_file)
    notifier = Notifier(config.discord_webhook_url)
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 1. Guard against duplicate slot runs on the same day
    if args.slot and not args.dry_run:
        if db.slot_already_ran_today(today_str, args.slot):
            logger.info(f"Slot '{args.slot}' has already executed successfully today ({today_str}). Exiting.")
            sys.exit(0)

    # 2. Connect to Google Drive
    try:
        drive_manager = DriveManager(
            folder_id=config.gdrive_folder_id,
            service_account_file=config.gdrive_service_account_file,
            oauth_token_file=config.youtube_oauth_token_file
        )
    except Exception as e:
        err_msg = f"Failed to initialize Google Drive connection: {e}"
        logger.error(err_msg)
        notifier.send_failure(None, "Google Drive Connection Error", str(e))
        db.record_run(today_str, args.slot, None, "failed", err_msg)
        sys.exit(1)

    # 3. Find target sequence item
    uploaded_keys = db.get_uploaded_keys()
    logger.info(f"Previously uploaded videos ({len(uploaded_keys)}): {sorted(list(uploaded_keys))}")

    target_item = None
    if args.force_video:
        seq_num_str = "".join(c for c in args.force_video if c.isdigit())
        if not seq_num_str:
            logger.error(f"Invalid --force-video value: {args.force_video}. Must be like V1, V2, etc.")
            sys.exit(1)
        seq_num = int(seq_num_str)
        items = drive_manager.scan_sequence_items()
        target_item = items.get(seq_num)
        if not target_item:
            logger.error(f"Force target {args.force_video} was not found on Google Drive!")
            sys.exit(1)
    else:
        target_item = drive_manager.get_next_unposted_item(uploaded_keys)

    if not target_item:
        logger.info("🎉 No pending videos left to upload on Google Drive! All caught up.")
        db.record_run(today_str, args.slot, None, "skipped", "No new videos available")
        sys.exit(0)

    logger.info(f"🎯 Target video selected for upload: {target_item.sequence_key}")

    # 4. Download bundle from Google Drive
    downloads_dir = Path(config.downloads_dir)
    try:
        local_video_path, metadata, local_thumb_path = drive_manager.download_sequence_bundle(
            target_item,
            downloads_dir
        )
    except Exception as e:
        err_msg = f"Failed to download sequence bundle for {target_item.sequence_key}: {e}"
        logger.error(err_msg)
        notifier.send_failure(target_item.sequence_key, "Google Drive Download Error", str(e))
        db.record_run(today_str, args.slot, target_item.sequence_key, "failed", err_msg)
        sys.exit(1)

    try:
        # 5. Inspect video properties & classify Short vs Long-form
        video_info = inspect_video(local_video_path)
        logger.info(f"Video inspection: {video_info}")

        if not video_info.has_audio:
            raise ValueError(f"Video {local_video_path.name} has no audio stream! Aborting upload.")

        # Determine video format
        if "isShort" in metadata:
            is_short = bool(metadata["isShort"])
        else:
            is_short = video_info.is_short(config.shorts_max_seconds)

        video_type_str = "short" if is_short else "longform"
        logger.info(f"Video format classified as: {video_type_str.upper()}")

        # 6. Validate Thumbnail requirements
        if not is_short:
            # Long-form MUST have a thumbnail
            if not local_thumb_path or not local_thumb_path.exists():
                raise ValueError(
                    f"Validation Error: Long-form video {target_item.sequence_key} strictly requires a custom thumbnail, "
                    f"but none was found in Google Drive! Please provide {target_item.sequence_key}.jpg or {target_item.sequence_key}.png."
                )
            logger.info(f"Custom thumbnail verified for long-form video: {local_thumb_path.name}")
        else:
            if local_thumb_path and local_thumb_path.exists():
                logger.info(f"Optional custom thumbnail found for Short: {local_thumb_path.name}")
            else:
                logger.info("No custom thumbnail provided for Short. YouTube will generate default thumbnail.")

        # 7. Dry-run Mode
        if args.dry_run:
            logger.info("--- [DRY RUN SUMMARY] ---")
            logger.info(f"Sequence Key: {target_item.sequence_key}")
            logger.info(f"Video File: {local_video_path.name} ({local_video_path.stat().st_size / (1024*1024):.2f} MB)")
            logger.info(f"Video Type: {video_type_str}")
            logger.info(f"Title: {metadata.get('title', target_item.sequence_key)}")
            logger.info(f"Category ID: {metadata.get('categoryId', config.default_category_id)}")
            logger.info(f"Privacy: {metadata.get('privacyStatus', config.default_privacy_status)}")
            logger.info(f"Thumbnail: {local_thumb_path.name if local_thumb_path else 'None'}")
            logger.info("--- Dry run completed successfully. No YouTube upload performed. ---")
            shutil.rmtree(downloads_dir / target_item.sequence_key, ignore_errors=True)
            sys.exit(0)

        # 8. Upload to YouTube
        uploader = YouTubeUploader(
            client_secret_file=config.youtube_client_secret_file,
            oauth_token_file=config.youtube_oauth_token_file
        )

        upload_result = uploader.upload_video(
            video_path=local_video_path,
            metadata=metadata,
            is_short=is_short,
            description_footer=config.description_footer
        )

        # Upload thumbnail if available
        has_thumb_uploaded = False
        if local_thumb_path and local_thumb_path.exists():
            has_thumb_uploaded = uploader.set_thumbnail(upload_result["id"], local_thumb_path)

        # 9. Update Database State
        db.record_upload(
            sequence_key=target_item.sequence_key,
            sequence_num=target_item.sequence_num,
            youtube_id=upload_result["id"],
            youtube_url=upload_result["url"],
            title=upload_result["title"],
            video_type=video_type_str,
            has_thumbnail=has_thumb_uploaded,
            status="uploaded"
        )

        db.record_run(
            run_date=today_str,
            slot=args.slot,
            sequence_key=target_item.sequence_key,
            status="success",
            message=f"Uploaded to {upload_result['url']}"
        )

        # 10. Send Success Notification
        notifier.send_success(
            sequence_key=target_item.sequence_key,
            title=upload_result["title"],
            youtube_url=upload_result["url"],
            video_type=video_type_str,
            has_custom_thumbnail=has_thumb_uploaded
        )

        logger.info(f"✅ Finished upload workflow for {target_item.sequence_key}: {upload_result['url']}")

    except Exception as e:
        err_msg = f"Upload pipeline failed for {target_item.sequence_key}: {e}"
        logger.exception(err_msg)
        notifier.send_failure(target_item.sequence_key, str(e))
        db.record_run(today_str, args.slot, target_item.sequence_key, "failed", str(e))
        sys.exit(1)
    finally:
        # Clean up temporary downloads
        shutil.rmtree(downloads_dir / target_item.sequence_key, ignore_errors=True)


if __name__ == "__main__":
    main()
