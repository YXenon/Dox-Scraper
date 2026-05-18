import subprocess
from shutil import which
from pathlib import Path
from typing import List

async def convert(metadata: dict):
    if not which("ffmpeg"):
        raise RuntimeError("FFMPEG not installed on the system.")

    video = metadata["video"]
    video_path = Path(video)
    output_path = (video_path.parent / f'{video_path.stem}.mkv').as_posix()
    subtitles: List = metadata["subtitles"]
    
    # Convert VTT → SRT
    srt_subtitles = []
    for subtitle in subtitles:
        srt_path = Path(subtitle).with_suffix(".srt").as_posix()
        subprocess.run(
            ["ffmpeg", "-nostdin", "-i", subtitle, srt_path],
            check=True, capture_output=True, text=True
        )
        srt_subtitles.append(srt_path)

    # Preparing merge command
    cmd = ["ffmpeg", "-nostdin", "-i", video]
    for subtitle in srt_subtitles:
        cmd += ["-i", subtitle]
    cmd += ["-c", "copy", "-c:s", "srt", output_path]

    # run ffmpeg command
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    
    # Change extension to '.mkv' in metadata file
    metadata["video"] = output_path
    return metadata