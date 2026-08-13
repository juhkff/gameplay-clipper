"""splice 模块单元测试：排序、转场选择、filter 图构建、错误路径（不依赖 ffmpeg）。"""

from pathlib import Path

import pytest

from gameplay_clipper import splice
from gameplay_clipper.splice import ClipInfo

XFADE_HELP = """
Filter xfade
  Cross fade one video with another video.
xfade AVOptions:
   transition        <int>        ..FV....... set cross fade transition
     fade            0            ..FV....... fade transition
     wipeleft        1            ..FV....... wipe left transition
     dissolve        25           ..FV....... dissolve transition
   duration          <duration>   ..FV....... set cross fade duration
"""


def make_clip(name: str, duration: float, audio: bool = True) -> ClipInfo:
    return ClipInfo(
        path=Path(name),
        duration=duration,
        has_audio=audio,
        width=320,
        height=240,
        fps=30.0,
    )


class TestNaturalSort:
    def test_numeric_order(self):
        names = ["clip-10", "clip-2", "clip-1", "clip-100"]
        assert sorted(names, key=splice.natural_sort_key) == [
            "clip-1",
            "clip-2",
            "clip-10",
            "clip-100",
        ]

    def test_mixed_text_and_number(self):
        names = ["part2", "part1", "part10"]
        assert sorted(names, key=splice.natural_sort_key) == [
            "part1",
            "part2",
            "part10",
        ]


class TestFindVideos:
    def test_sorted_and_filtered(self, tmp_path):
        for name in [
            "clip-2.mp4",
            "clip-10.mp4",
            "clip-1.mp4",
            "note.txt",
            "clip-3.MKV",
            "clip-4.mov",
        ]:
            (tmp_path / name).touch()
        found = splice.find_videos(tmp_path, {".mp4", ".mkv", ".mov"})
        assert [p.name for p in found] == [
            "clip-1.mp4",
            "clip-2.mp4",
            "clip-3.MKV",
            "clip-4.mov",
            "clip-10.mp4",
        ]


class TestParseXfadeTransitions:
    def test_extracts_names(self):
        assert splice.parse_xfade_transitions(XFADE_HELP) == {
            "fade",
            "wipeleft",
            "dissolve",
        }


class TestClipInfoFromProbe:
    def test_valid(self):
        data = {
            "format": {"duration": "10.5"},
            "streams": [
                {
                    "codec_type": "video",
                    "width": 1920,
                    "height": 1080,
                    "avg_frame_rate": "30000/1001",
                },
                {"codec_type": "audio"},
            ],
        }
        info = splice.clip_info_from_probe(Path("a.mp4"), data)
        assert info.duration == 10.5
        assert info.width == 1920 and info.height == 1080
        assert round(info.fps) == 30
        assert info.has_audio

    def test_no_video_stream(self):
        data = {"format": {"duration": "1.0"}, "streams": [{"codec_type": "audio"}]}
        with pytest.raises(ValueError, match="不包含视频流"):
            splice.clip_info_from_probe(Path("a.mp4"), data)

    def test_missing_resolution(self):
        data = {"format": {"duration": "1.0"}, "streams": [{"codec_type": "video"}]}
        with pytest.raises(ValueError, match="分辨率"):
            splice.clip_info_from_probe(Path("a.mp4"), data)


class TestPickTransitions:
    def test_fixed(self):
        clips = [make_clip(f"c{i}.mp4", 5) for i in range(3)]
        assert splice.pick_transitions(clips, "fade", ["a", "b"]) == ["fade", "fade"]

    def test_random_draws_from_pool(self, monkeypatch):
        clips = [make_clip(f"c{i}.mp4", 5) for i in range(4)]
        monkeypatch.setattr(splice.random, "choice", lambda pool: pool[0])
        assert splice.pick_transitions(clips, "random", ["fade", "dissolve"]) == [
            "fade",
            "fade",
            "fade",
        ]


class TestBuildFilterComplex:
    def test_chain(self):
        clips = [
            make_clip("a.mp4", 5.0),
            make_clip("b.mp4", 6.0),
            make_clip("c.mp4", 7.0, audio=False),
        ]
        fc = splice.build_filter_complex(clips, ["fade", "dissolve"], 1.0)
        # 视频统一与转场链：offset = 已拼接时长 - 转场时长
        assert "[0:v]setpts=PTS-STARTPTS,scale=320:240" in fc
        assert "[v0][v1]xfade=transition=fade:duration=1.0:offset=4.000000[xv1]" in fc
        assert "[xv1][v2]xfade=transition=dissolve:duration=1.0:offset=9.000000[vout]" in fc
        # 音频：无音轨片段用静音填充
        assert "anullsrc=r=48000:cl=stereo,atrim=duration=7.000000" in fc
        assert "[a0][a1]acrossfade=d=1.0[xa1]" in fc
        assert "[xa1][a2]acrossfade=d=1.0[aout]" in fc

    def test_unifies_to_first_clip_resolution(self):
        clips = [
            ClipInfo(Path("a.mp4"), 5.0, True, 1920, 1080, 60.0),
            ClipInfo(Path("b.mp4"), 5.0, True, 1280, 720, 30.0),
        ]
        fc = splice.build_filter_complex(clips, ["fade"], 1.0)
        assert "scale=1920:1080" in fc
        assert "fps=60" in fc


class TestMainErrors:
    def test_missing_input_dir(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(splice, "INPUT_DIR", str(tmp_path / "nope"))
        assert splice.main([]) == 1
        assert "找不到输入目录" in capsys.readouterr().err

    def test_too_few_videos(self, tmp_path, capsys, monkeypatch):
        (tmp_path / "only.mp4").touch()
        monkeypatch.setattr(splice, "INPUT_DIR", str(tmp_path))
        assert splice.main([]) == 1
        assert "至少需要 2 个视频文件" in capsys.readouterr().err
