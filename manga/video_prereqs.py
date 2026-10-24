"""Optional FFmpeg detection. Existing manga functions work without FFmpeg."""
import shutil


def probe_executables():
    """No shell execution, files written, network calls, or paid APIs."""
    return {
        "ffmpeg": shutil.which("ffmpeg"),
        "ffprobe": shutil.which("ffprobe"),
    }


def video_ready():
    tools = probe_executables()
    return bool(tools["ffmpeg"] and tools["ffprobe"])
