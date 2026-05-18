import subprocess
from shutil import which
from pathlib import Path
from typing import List

async def convert(metadata: dict):
    if not which("ffmpeg"):
        raise RuntimeError("FFMPEG not installed on the system.")

    video = metadata["video"]
    video_path = Path(video)
    output_path = (video_path.parent / f'{video_path.stem}.mp4').as_posix()
    subtitles: List = metadata["subtitles"]

    # preparing command
    cmd = ["ffmpeg", "-nostdin", "-i", video]
    for subtitle in subtitles:
        cmd += ["-i", subtitle]
    cmd += ["-c", "copy", "-c:s", "mov_text", output_path]

    # run ffmpeg command
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    
    # Change extension to '.mp4' in metadata file
    metadata["video"] = output_path
    return metadata