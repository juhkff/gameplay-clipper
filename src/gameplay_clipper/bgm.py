"""背景音乐混音：给主视频（如 splice 产出的集锦）配上 BGM。

可选「自动闪避（ducking）」：用主视频自己的音轨作为侧链信号
（sidechaincompress），游戏音效响起时自动压低 BGM，音效安静时恢复，
避免 BGM 盖住游戏声音。视频流不重编码（-c:v copy），只重编码混音后的音频。

用法：
    python -m gameplay_clipper.bgm [--overwrite]

配置写在本文件顶部的「配置区」。
输出命名：bgm-1{ext}……（{ext} 沿用主视频的扩展名），默认不覆盖。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from gameplay_clipper.common import next_output_path

# ============================================================
# 配置区（按需修改）
# ============================================================
VIDEO_PATH: str = "connect_output/connect-1.mp4"  # 主视频（通常为 splice 的产物）
BGM_PATH: str = "media/bgm.mp3"  # 背景音乐（任意 ffmpeg 支持的音频格式）
OUTPUT_DIR: str = "bgm_output"  # 输出目录（相对当前运行目录，或绝对路径）
OUTPUT_PREFIX: str = "bgm"  # 输出文件名前缀，如 bgm-1.mp4

BGM_VOLUME: float = 0.3  # BGM 音量（1.0 = 原音量，建议 0.2~0.4）
DUCKING: bool = True  # 自动闪避：游戏音效响起时自动压低 BGM
# sidechaincompress 参数（DUCKING=True 时生效）：
DUCK_THRESHOLD: float = 0.05  # 侧链（游戏音）电平阈值，超过则开始压低 BGM
DUCK_RATIO: float = 8.0  # 压缩比，越大压得越狠
DUCK_ATTACK: float = 20.0  # 起音时间（毫秒），压制的响应速度
DUCK_RELEASE: float = 300.0  # 释放时间（毫秒），恢复 BGM 音量的速度
DUCK_MAKEUP: float = 1.0  # 增益补偿（1.0 = 不补偿）

FADE_IN: float = 1.0  # BGM 淡入时长（秒）
FADE_OUT: float = 3.0  # BGM 淡出时长（秒）
LOOP_BGM: bool = True  # BGM 短于主视频时循环播放
AUDIO_BITRATE: str = "192k"  # 混音后音频码率
# ============================================================


def probe_video(ffprobe: str, path: Path) -> tuple[float, bool]:
    """探测主视频：返回 (时长秒, 是否有音轨)。失败抛 ValueError。"""
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "json",
        str(path),
    ]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    data = json.loads(out)
    duration = float(data["format"]["duration"])
    if duration <= 0:
        raise ValueError("无法读取主视频时长")
    has_audio = any(s.get("codec_type") == "audio" for s in data.get("streams", []))
    return duration, has_audio


def build_bgm_chain(
    duration: float,
    volume: float,
    fade_in: float,
    fade_out: float,
    loop: bool,
) -> str:
    """BGM 预处理链：[1:a] → 循环（可选）→ 截取 → 音量 → 统一格式 → 淡入淡出。"""
    parts: list[str] = []
    if loop:
        parts.append("aloop=loop=-1:size=2e9")
    parts.append(f"atrim=duration={duration:.6f}")
    parts.append(f"volume={volume}")
    parts.append("aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo")
    if fade_in > 0:
        parts.append(f"afade=t=in:st=0:d={fade_in}")
    if fade_out > 0:
        # st 钳制到 0，避免 fade_out 长于视频时长时产生负时间戳
        parts.append(f"afade=t=out:st={max(duration - fade_out, 0.0):.6f}:d={fade_out}")
    return ",".join(parts)


def build_filter_complex(
    has_audio: bool,
    duration: float,
    volume: float,
    fade_in: float,
    fade_out: float,
    loop: bool,
    ducking: bool,
    threshold: float,
    ratio: float,
    attack: float,
    release: float,
    makeup: float,
) -> str:
    """构建混音 filter_complex。

    - 主视频有音轨：音轨统一格式后 asplit 一份作侧链；BGM 经预处理链后
      与侧链做 sidechaincompress（闪避），再与主音轨 amix 混音。
    - 主视频无音轨：直接输出 BGM（无侧链可闪避）。
    amix 统一 48kHz 立体声、不归一化（normalize=0），避免混音后音量被压低。
    """
    bgm = build_bgm_chain(duration, volume, fade_in, fade_out, loop)
    if not has_audio:
        return f"[1:a]{bgm}[bgm]"
    main = "[0:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
    if not ducking:
        return (
            f"{main}[a0f];"
            f"[1:a]{bgm}[bgm];"
            f"[a0f][bgm]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[mix]"
        )
    return (
        f"{main},asplit=2[a0f][sc];"
        f"[1:a]{bgm}[bgm];"
        f"[bgm][sc]sidechaincompress=threshold={threshold}:ratio={ratio}:"
        f"attack={attack}:release={release}:makeup={makeup}[ducked];"
        f"[a0f][ducked]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[mix]"
    )


def build_command(
    ffmpeg: str,
    video: Path,
    bgm: Path,
    output: Path,
    overwrite: bool,
    filter_complex: str,
    has_audio: bool,
    audio_bitrate: str,
) -> list[str]:
    """构建 ffmpeg 混音命令：视频流 copy 不重编码，音频混音后 aac 重编码。"""
    cmd = [
        ffmpeg,
        "-y" if overwrite else "-n",
        "-i",
        str(video),
        "-i",
        str(bgm),
        "-filter_complex",
        filter_complex,
        "-map",
        "0:v",
        "-c:v",
        "copy",
        "-map",
        "[mix]" if has_audio else "[bgm]",
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        "-movflags",
        "+faststart",
        str(output),
    ]
    return cmd


def run_mix(
    ffmpeg: str,
    video: Path,
    bgm: Path,
    output: Path,
    overwrite: bool,
    has_audio: bool,
    duration: float,
) -> None:
    """执行混音；先写 *.part 临时文件，成功后原子改名。"""
    filter_complex = build_filter_complex(
        has_audio,
        duration,
        BGM_VOLUME,
        FADE_IN,
        FADE_OUT,
        LOOP_BGM,
        DUCKING,
        DUCK_THRESHOLD,
        DUCK_RATIO,
        DUCK_ATTACK,
        DUCK_RELEASE,
        DUCK_MAKEUP,
    )
    tmp = output.with_name(f"{output.stem}.part{output.suffix}")
    if tmp.exists():
        tmp.unlink()
    cmd = build_command(
        ffmpeg,
        video,
        bgm,
        tmp,
        overwrite,
        filter_complex,
        has_audio,
        AUDIO_BITRATE,
    )
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        if tmp.exists():
            tmp.unlink()
        tail = exc.stderr.strip().splitlines()[-5:]
        raise RuntimeError("混音失败：" + " | ".join(tail)) from exc
    tmp.replace(output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="背景音乐混音 + 自动闪避（配置见 bgm.py 顶部配置区）"
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="允许覆盖已存在的输出文件（默认：自动递增编号，不覆盖）",
    )
    args = parser.parse_args(argv)

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        print(
            "错误：未找到 ffmpeg/ffprobe，请先安装（Ubuntu: sudo apt install ffmpeg）",
            file=sys.stderr,
        )
        return 1

    video = Path(VIDEO_PATH)
    bgm = Path(BGM_PATH)
    if not video.is_file():
        print(
            f"错误：找不到主视频 {video}（可修改 bgm.py 顶部的 VIDEO_PATH）",
            file=sys.stderr,
        )
        return 1
    if not bgm.is_file():
        print(
            f"错误：找不到背景音乐 {bgm}（可修改 bgm.py 顶部的 BGM_PATH）",
            file=sys.stderr,
        )
        return 1

    try:
        duration, has_audio = probe_video(ffprobe, video)
    except (subprocess.CalledProcessError, ValueError, KeyError) as exc:
        print(f"错误：读取主视频失败：{exc}", file=sys.stderr)
        return 1

    if DUCKING and not has_audio:
        print("提示：主视频无音轨，无侧链信号，自动闪避已跳过（BGM 全音量）")

    suffix = video.suffix or ".mp4"
    output = next_output_path(Path(OUTPUT_DIR), OUTPUT_PREFIX, suffix, args.overwrite)
    print(f"主视频：{video}（时长 {duration:.2f}s，{'有音轨' if has_audio else '无音轨'}）")
    print(f"背景音乐：{bgm}（音量 {BGM_VOLUME}，淡入 {FADE_IN}s / 淡出 {FADE_OUT}s）")
    duck_state = "开" if DUCKING and has_audio else "关"
    loop_state = "开" if LOOP_BGM else "关"
    print(f"自动闪避：{duck_state}（BGM 循环：{loop_state}）")
    print(f"输出：{output}")
    try:
        run_mix(ffmpeg, video, bgm, output, args.overwrite, has_audio, duration)
    except RuntimeError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    print(f"完成：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
