import asyncio
import subprocess
from pathlib import Path
from shutil import which
import traceback

import aiofiles

from shared.models import Metadata


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _convert_vtt_to_srt(subtitle_path: str) -> str | None:
    """
    Convert a single VTT subtitle file to SRT format via FFmpeg.

    Returns the path to the converted .srt file, or None if conversion fails.
    The .srt file is written alongside the source .vtt file.
    """
    srt_path = Path(subtitle_path).with_suffix(".srt").as_posix()
    try:
        subprocess.run(
            ["ffmpeg", "-nostdin", "-i", subtitle_path, srt_path],
            check=True,
            capture_output=True,
            text=True,
        )
        return srt_path
    except subprocess.CalledProcessError:
        return None


def _build_merge_command(
    parts_list_file: str,
    srt_subtitles: list[str],
    output_path: str,
) -> list[str]:
    """
    Build the FFmpeg command that concatenates video parts and muxes subtitles.

    ``parts_list_file`` must be a concat-format text file (one ``temp_N.ts``
    path per line). Each subtitle in ``srt_subtitles`` is added as a separate
    input and copied into the output container.
    """
    cmd = ["ffmpeg", "-f", "concat", "-safe", "0", "-i", parts_list_file]

    for subtitle in srt_subtitles:
        cmd += ["-i", subtitle]

    cmd += ["-c", "copy"]
    if len(srt_subtitles):
        cmd += ["-c:s", "srt"]
    cmd += [output_path]

    return cmd


async def _write_parts_list(parts_file: Path, part_count: int) -> None:
    """
    Write a FFmpeg concat-format file listing all .ts segment filenames.

    Produces lines of the form ``temp_0.ts``, ``temp_1.ts``, … up to
    ``part_count - 1``.
    """
    print(parts_file.resolve())
    async with aiofiles.open(parts_file.resolve(), "w", encoding="utf-8") as f:
        for i in range(part_count):
            await f.write(f"file 'temp_{i}.ts'\n")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def convert(metadata: Metadata) -> Metadata | None:
    """
    Merge segmented .ts video parts and VTT subtitles into a single output file.

    Steps
    -----
    1. Verify FFmpeg is available on PATH.
    2. Write a concat list of all .ts segment filenames.
    3. Convert any VTT subtitle files to SRT (skips files that fail).
    4. Run FFmpeg to concatenate the segments and mux the subtitles.
    5. Update ``metadata["video"]`` with the output Path and return it.

    Raises
    ------
    RuntimeError            : FFmpeg is not installed.
    subprocess.CalledProcessError : FFmpeg merge command fails.
    """
    try:
        if not which("ffmpeg"):
            raise RuntimeError("FFmpeg is not installed on the system.")

        loop = asyncio.get_event_loop()
        # Build and write the concat list that FFmpeg will read.
        parts_list_file = Path(metadata.dir) / "files.txt"
        await _write_parts_list(parts_list_file, int(metadata.parts))

        output_path = Path(metadata.video)

        # Convert all subtitles; silently drop any that ffmpeg cannot process.
        srt_subtitles = [
            srt
            for subtitle in metadata.subtitles
            if (srt := await loop.run_in_executor(None, _convert_vtt_to_srt, subtitle)) is not None
        ]

        merge_cmd = _build_merge_command(
            parts_list_file.as_posix(),
            srt_subtitles,
            output_path.as_posix(),
        )
        await loop.run_in_executor(
            None,
            lambda: subprocess.run(merge_cmd, check=True, capture_output=True, text=True)
        )
        return metadata
    except Exception:
        print(traceback.format_exc())