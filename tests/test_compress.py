"""compress 模块单元测试：命令构建、输入解析（不依赖 ffmpeg）。"""

from gameplay_clipper import compress


class TestBuildCommand:
    def test_basic(self, tmp_path):
        cmd = compress.build_command(
            "ffmpeg",
            tmp_path / "in.mp4",
            tmp_path / "out.mp4",
            overwrite=False,
            crf="23",
            preset="medium",
            audio_bitrate="128k",
            resize="",
            fps=0.0,
        )
        assert cmd[1] == "-n"
        assert cmd[cmd.index("-c:v") + 1] == "libx264"
        assert cmd[cmd.index("-crf") + 1] == "23"
        assert cmd[cmd.index("-preset") + 1] == "medium"
        assert cmd[cmd.index("-b:a") + 1] == "128k"
        assert "-vf" not in cmd
        assert cmd[-1] == str(tmp_path / "out.mp4")

    def test_resize_and_fps(self, tmp_path):
        cmd = compress.build_command(
            "ffmpeg",
            tmp_path / "in.mp4",
            tmp_path / "out.mp4",
            overwrite=True,
            crf="23",
            preset="medium",
            audio_bitrate="128k",
            resize="1280:720",
            fps=30.0,
        )
        assert cmd[1] == "-y"
        assert cmd[cmd.index("-vf") + 1] == "scale=1280:720,fps=30"


class TestResolveInputs:
    def test_single_file(self, tmp_path):
        video = tmp_path / "a.mp4"
        video.touch()
        assert compress.resolve_inputs(str(video)) == [video]

    def test_directory_sorted(self, tmp_path):
        for name in ["b.mp4", "a.mp4", "note.txt", "c.mkv"]:
            (tmp_path / name).touch()
        found = compress.resolve_inputs(str(tmp_path))
        assert [p.name for p in found] == ["a.mp4", "b.mp4", "c.mkv"]

    def test_missing(self, tmp_path):
        assert compress.resolve_inputs(str(tmp_path / "nope")) == []
