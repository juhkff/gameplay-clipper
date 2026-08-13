"""cut 模块单元测试：时间解析、文件名去重、错误路径（不依赖 ffmpeg）。"""

import pytest

from gameplay_clipper import cut


class TestParseTime:
    def test_seconds(self):
        assert cut.parse_time("5") == 5.0

    def test_fractional_seconds(self):
        assert cut.parse_time("12.5") == 12.5

    def test_mm_ss(self):
        assert cut.parse_time("1:30") == 90.0

    def test_hh_mm_ss(self):
        assert cut.parse_time("1:00:30") == 3630.0

    def test_hh_mm_ss_fractional(self):
        assert cut.parse_time("1:02:03.5") == 3723.5

    @pytest.mark.parametrize("bad", ["", "abc", "1:2:3:4", "1:-2", "-5", ":"])
    def test_invalid(self, bad):
        with pytest.raises(ValueError):
            cut.parse_time(bad)


class TestNextOutputPath:
    def test_first_is_clip_1(self, tmp_path):
        assert cut.next_output_path(tmp_path, "clip", ".mp4", overwrite=False) == (
            tmp_path / "clip-1.mp4"
        )

    def test_skips_existing(self, tmp_path):
        (tmp_path / "clip-1.mp4").touch()
        (tmp_path / "clip-3.mp4").touch()
        assert cut.next_output_path(tmp_path, "clip", ".mp4", overwrite=False) == (
            tmp_path / "clip-2.mp4"
        )

    def test_overwrite_always_first(self, tmp_path):
        (tmp_path / "clip-1.mp4").touch()
        assert cut.next_output_path(tmp_path, "clip", ".mp4", overwrite=True) == (
            tmp_path / "clip-1.mp4"
        )


class TestMainErrors:
    def test_missing_video(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(cut, "VIDEO_PATH", str(tmp_path / "nope.mp4"))
        assert cut.main([]) == 1
        assert "找不到视频文件" in capsys.readouterr().err

    def test_end_before_start(self, tmp_path, capsys, monkeypatch):
        video = tmp_path / "v.mp4"
        video.touch()
        monkeypatch.setattr(cut, "VIDEO_PATH", str(video))
        monkeypatch.setattr(cut, "CLIPS", [("00:00:10", "00:00:05")])
        assert cut.main([]) == 1
        assert "终点必须晚于起点" in capsys.readouterr().err
