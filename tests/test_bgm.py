"""bgm 模块单元测试：BGM 预处理链、filter_complex 构建、命令构建（不依赖 ffmpeg）。"""

from gameplay_clipper import bgm


class TestBuildBgmChain:
    def test_loop_and_trim(self):
        chain = bgm.build_bgm_chain(duration=10.0, volume=0.3, fade_in=1.0, fade_out=3.0, loop=True)
        assert chain.startswith("[1:a]") or "aloop" in chain
        assert "aloop=loop=-1:size=2e9" in chain
        assert "atrim=duration=10.000000" in chain
        assert "volume=0.3" in chain
        assert "afade=t=in:st=0:d=1.0" in chain
        # 淡出 st = 10 - 3 = 7
        assert "afade=t=out:st=7.000000:d=3.0" in chain

    def test_no_loop(self):
        chain = bgm.build_bgm_chain(duration=5.0, volume=1.0, fade_in=0.0, fade_out=0.0, loop=False)
        assert "aloop" not in chain
        assert "atrim=duration=5.000000" in chain
        assert "afade" not in chain

    def test_fade_out_longer_than_duration_clamped(self):
        chain = bgm.build_bgm_chain(duration=2.0, volume=0.3, fade_in=0.0, fade_out=5.0, loop=True)
        # st 钳制到 0，避免负时间戳
        assert "afade=t=out:st=0.000000:d=5.0" in chain


class TestBuildFilterComplex:
    def test_with_audio_and_ducking(self):
        fc = bgm.build_filter_complex(
            has_audio=True,
            duration=10.0,
            volume=0.3,
            fade_in=1.0,
            fade_out=3.0,
            loop=True,
            ducking=True,
            threshold=0.05,
            ratio=8.0,
            attack=20.0,
            release=300.0,
            makeup=1.0,
        )
        assert "[0:a]aformat=" in fc
        assert "asplit=2[a0f][sc]" in fc
        assert "[bgm][sc]sidechaincompress=threshold=0.05:ratio=8.0:" in fc
        assert "attack=20.0:release=300.0:makeup=1.0" in fc
        mix = "[a0f][ducked]amix=inputs=2:duration=first:"
        assert f"{mix}dropout_transition=2:normalize=0[mix]" in fc

    def test_with_audio_no_ducking(self):
        fc = bgm.build_filter_complex(
            has_audio=True,
            duration=10.0,
            volume=0.5,
            fade_in=0.0,
            fade_out=0.0,
            loop=False,
            ducking=False,
            threshold=0.05,
            ratio=8.0,
            attack=20.0,
            release=300.0,
            makeup=1.0,
        )
        assert "asplit" not in fc
        assert "sidechaincompress" not in fc
        assert "[a0f][bgm]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[mix]" in fc

    def test_no_audio(self):
        fc = bgm.build_filter_complex(
            has_audio=False,
            duration=10.0,
            volume=0.3,
            fade_in=1.0,
            fade_out=3.0,
            loop=True,
            ducking=True,
            threshold=0.05,
            ratio=8.0,
            attack=20.0,
            release=300.0,
            makeup=1.0,
        )
        # 无音轨：直接输出 BGM 链，无侧链/混音
        assert fc.endswith("[bgm]")
        assert "amix" not in fc
        assert "sidechaincompress" not in fc


class TestBuildCommand:
    def test_maps_video_copy_and_mix(self, tmp_path):
        fc = "[1:a]aloop=loop=-1:size=2e9,atrim=duration=10.000000[bgm]"
        cmd = bgm.build_command(
            "ffmpeg",
            tmp_path / "v.mp4",
            tmp_path / "bgm.mp3",
            tmp_path / "out.mp4",
            overwrite=False,
            filter_complex=fc,
            has_audio=True,
            audio_bitrate="192k",
        )
        assert cmd[1] == "-n"
        assert cmd[cmd.index("-filter_complex") + 1] == fc
        assert cmd[cmd.index("-map") + 1] == "0:v"
        assert cmd[cmd.index("-c:v") + 1] == "copy"
        # 第二个 -map 映射混音标签
        maps = [i for i, x in enumerate(cmd) if x == "-map"]
        assert cmd[maps[1] + 1] == "[mix]"
        assert cmd[-1] == str(tmp_path / "out.mp4")

    def test_no_audio_maps_bgm_label(self, tmp_path):
        cmd = bgm.build_command(
            "ffmpeg",
            tmp_path / "v.mp4",
            tmp_path / "bgm.mp3",
            tmp_path / "out.mp4",
            overwrite=True,
            filter_complex="[1:a]atrim=duration=10.000000[bgm]",
            has_audio=False,
            audio_bitrate="192k",
        )
        assert cmd[1] == "-y"
        maps = [i for i, x in enumerate(cmd) if x == "-map"]
        assert cmd[maps[1] + 1] == "[bgm]"
