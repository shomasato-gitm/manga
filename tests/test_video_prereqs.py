"""Video tooling is strictly optional for legacy manga workflows."""
from manga import video_prereqs


def test_ffmpeg_optional(monkeypatch):
    monkeypatch.setattr(video_prereqs.shutil, "which", lambda _: None)
    assert video_prereqs.probe_executables() == {"ffmpeg": None, "ffprobe": None}
    assert video_prereqs.video_ready() is False


def test_both_required(monkeypatch):
    monkeypatch.setattr(
        video_prereqs.shutil, "which",
        lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None,
    )
    assert video_prereqs.video_ready() is False
    monkeypatch.setattr(video_prereqs.shutil, "which", lambda name: "/usr/bin/" + name)
    assert video_prereqs.video_ready() is True
