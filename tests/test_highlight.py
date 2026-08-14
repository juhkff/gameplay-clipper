"""highlight 模块单元测试（离线运行，不依赖 ffmpeg 推理）。"""

import pytest

from gameplay_clipper import highlight
from gameplay_clipper.highlight import Segment

# ---------- 扫描收集 ----------


class TestScanClips:
    def _make_tree(self, tmp_path):
        root = tmp_path / "Outplayed"
        (root / "Apex Legends").mkdir(parents=True)
        (root / "Apex Legends" / "clip-1.mp4").touch()
        (root / "Apex Legends" / "clip-2.mp4").touch()
        (root / "Apex Legends" / "readme.txt").touch()
        (root / "Apex Legends" / "full-session.mp4").touch()
        return root

    def test_collects_mp4_with_duration(self, tmp_path, monkeypatch):
        root = self._make_tree(tmp_path)
        monkeypatch.setattr(highlight, "CLIPS_DIRS", [str(root)])
        monkeypatch.setattr(highlight, "EXCLUDE_NAME_PATTERNS", [r"full-session"])
        monkeypatch.setattr(highlight, "probe_duration", lambda p: 15.0)
        segments = highlight.scan_clips()
        assert len(segments) == 2
        assert all(s.start == 0.0 and s.end == 15.0 and s.score == 1.0 for s in segments)
        assert [s.source.name for s in segments] == ["clip-1.mp4", "clip-2.mp4"]
        assert segments[0].reason == "Outplayed/Apex Legends/clip-1.mp4"

    def test_exclude_and_probe_failure_skipped(self, tmp_path, monkeypatch):
        root = self._make_tree(tmp_path)
        monkeypatch.setattr(highlight, "CLIPS_DIRS", [str(root)])
        monkeypatch.setattr(highlight, "EXCLUDE_NAME_PATTERNS", [r"full-session"])
        monkeypatch.setattr(highlight, "probe_duration", lambda p: None)
        with pytest.raises(ValueError, match="未在 CLIPS_DIRS 中找到"):
            highlight.scan_clips()

    def test_no_dirs_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(highlight, "CLIPS_DIRS", [str(tmp_path / "nope")])
        with pytest.raises(ValueError, match="未在 CLIPS_DIRS 中找到"):
            highlight.scan_clips()

    def test_recursive_false(self, tmp_path, monkeypatch):
        root = self._make_tree(tmp_path)
        monkeypatch.setattr(highlight, "CLIPS_DIRS", [str(root)])
        monkeypatch.setattr(highlight, "RECURSIVE", False)
        monkeypatch.setattr(highlight, "EXCLUDE_NAME_PATTERNS", [])
        monkeypatch.setattr(highlight, "probe_duration", lambda p: 15.0)
        with pytest.raises(ValueError, match="未在 CLIPS_DIRS 中找到"):
            highlight.scan_clips()  # 文件都在子目录
        (root / "root-clip.mp4").touch()
        segments = highlight.scan_clips()
        assert [s.source.name for s in segments] == ["root-clip.mp4"]


# ---------- 编排 ----------


class TestCollectSegments:
    def test_copy_finished_clip(self, tmp_path):
        src = tmp_path / "clip.mp4"
        src.write_bytes(b"fake-video-bytes")
        seg = Segment(0.0, 1.0, 1.0, reason="Outplayed/x.mp4", source=src)
        out_dir = tmp_path / "out"
        outputs = highlight.collect_segments([seg], out_dir, "highlight", False)
        assert len(outputs) == 1
        assert outputs[0].read_bytes() == b"fake-video-bytes"
        assert outputs[0].name == "highlight-1.mp4"
