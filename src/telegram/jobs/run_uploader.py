import asyncio
import json
import logging
import mimetypes
import shutil
import subprocess
import io
from random import uniform
from pathlib import Path

from telethon.tl.functions.messages import SendMediaRequest
from telethon.tl.types import (
    DocumentAttributeFilename,
    DocumentAttributeVideo,
    InputMediaUploadedDocument,
    UpdateMessageID,
)

from shared.models import Metadata
from telegram.bot import client
from constants import STORE_CHANNEL_ID
from telegram.utils.parallel import UploadManager
from core.handlers.process import app_ctx

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

_DEFAULT_VIDEO_INFO = {"width": 0, "height": 0, "duration": 0}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_duration(duration_str: str) -> float:
    """
    Convert an HH:MM:SS.sss timestamp string into total seconds.

    The string is split on ":", reversed so index 0 = seconds, 1 = minutes,
    2 = hours, then each component is weighted by 60^i.

    Returns 0.0 if the string is empty or malformed.
    """
    if not duration_str:
        return 0.0

    parts = list(reversed(duration_str.split(":")))
    return sum(float(part) * (60 ** i) for i, part in enumerate(parts))


def get_video_info(path: str | Path) -> dict:
    """
    Use ffprobe to extract width, height, and duration from a video file.

    Returns a dict with keys ``width``, ``height``, and ``duration`` (seconds).
    Falls back to zeros if no video stream is found.
    """
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    streams = json.loads(result.stdout).get("streams", [])

    for stream in streams:
        if stream.get("codec_type") != "video":
            continue

        raw_duration = stream.get("tags", {}).get("DURATION", "")
        return {
            "width":    stream["width"],
            "height":   stream["height"],
            "duration": _parse_duration(raw_duration),
        }

    return _DEFAULT_VIDEO_INFO.copy()


async def get_thumbnail(video_path, duration):
    result = subprocess.run([
        "ffmpeg", "-ss", str(uniform(0, duration)),
        "-i", video_path, "-vframes", "1", "-q:v", "2",
        "-f", "image2", "pipe:1"
    ], capture_output=True, check=True)

    ref = await client.upload_file(io.BytesIO(result.stdout), file_name="thumb.jpg")
    return ref


def get_msg_id(result: list):
    for update in result.updates:
            if isinstance(update, UpdateMessageID):
                msg_id = update.id
                return msg_id

# ---------------------------------------------------------------------------
# Upload worker
# ---------------------------------------------------------------------------

async def upload_to_telegram() -> None:
    """
    Consume video metadata from the data queue and upload each file to Telegram.

    Flow per iteration
    ------------------
    1. Dequeue a metadata dict containing at least a ``"video"`` path.
    2. Upload the video file via UploadManager.
    3. Send the uploaded file to the store channel as a streaming document.
    4. Signal completion on the ok queue.
    5. Remove the parent folder to free disk space.

    The worker blocks until the "p2" process is ready before entering the loop.
    """
    # Wait for the dependent process to be fully started before consuming work.
    logger = logging.getLogger(__name__)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, app_ctx.wait_for, "p2")

    while True:
        s_metadata = await loop.run_in_executor(None, app_ctx.uploader_conn.recv)
        metadata = Metadata.model_validate_json(s_metadata)
        logger.info("Received data at uploader process")
        video_path = Path(metadata.video)

        # --- Upload raw bytes to Telegram ---
        manager     = UploadManager(client=client, file_path=video_path)
        uploaded    = await manager.upload_file()
        video_info  = get_video_info(video_path)

        mime_type = mimetypes.guess_type(video_path)[0] or "application/octet-stream"
        attributes = [
            DocumentAttributeFilename(video_path.name),
            DocumentAttributeVideo(
                duration=video_info["duration"],
                w=video_info["width"],
                h=video_info["height"],
                supports_streaming=True,
            ),
        ]

        thumb = await get_thumbnail(video_path=video_path, duration=video_info["duration"])

        # --- Send the document to the store channel ---
        result = await client(  # type: ignore[operator]
            SendMediaRequest(
                peer=STORE_CHANNEL_ID,
                media=InputMediaUploadedDocument(
                    file=uploaded,
                    thumb=thumb,
                    mime_type=mime_type,
                    attributes=attributes,
                ),
                message=video_path.name,
            )
        )

        # Acknowledge completion so the coordinator can proceed.
        app_ctx.uploader_conn.send({"job": "upload", "status": "done", "channel_id": STORE_CHANNEL_ID, "msg_id": get_msg_id(result)})

        # Clean up the working directory now that the upload is confirmed.
        shutil.rmtree(video_path.parent)
