"""视频拼接 + 转场：把指定文件夹下的视频按文件名自然排序依次拼接，
相邻片段之间插入转场效果（视频 xfade + 音频 acrossfade）。

用法：
    python -m gameplay_clipper.splice [--overwrite]

配置写在本文件顶部的「配置区」。
片段按文件名自然排序（clip-1 < clip-2 < ... < clip-10 < clip-100）；
转场需要重编码：libx264 + crf 23（可改）。
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from gameplay_clipper.common import next_output_path

# ============================================================
# 配置区（按需修改）
# ============================================================
INPUT_DIR: str = "cut_output"  # 待拼接视频所在文件夹（相对当前运行目录，或绝对路径）
OUTPUT_DIR: str = "connect_output"  # 输出目录（相对当前运行目录，或绝对路径）
OUTPUT_PREFIX: str = "connect"  # 输出文件名前缀，如 connect-1.mp4

# 片段间转场效果。填某个具体名字（如 "fade"）则所有转场均用该效果；
# 填 "random" 则每对相邻片段从 TRANSITION_POOL 中随机选一个。
TRANSITION: str = "random"
TRANSITION_POOL: list[str] = ["fade", "dissolve", "wipeleft"]

TRANSITION_DURATION: float = 1.0  # 转场时长（秒）；每段视频时长必须不小于它
CRF: str = "23"  # x264 质量（越小越好，18 高质量 / 23 均衡 / 28 省体积）
PRESET: str = "medium"  # x264 速度预设
VIDEO_EXTS: set[str] = {".mp4", ".mkv", ".mov", ".ts", ".flv", ".webm", ".avi"}

# 可用转场效果（本机 ffmpeg 的 xfade 支持列表，程序启动时动态读取校验，
# 可复制到 TRANSITION 或 TRANSITION_POOL 中）：
#   fade 淡入淡出          wipeleft 向左擦除        wiperight 向右擦除
#   wipeup 向上擦除        wipedown 向下擦除        slideleft 向左滑入
#   slideright 向右滑入    slideup 向上滑入         slidedown 向下滑入
#   circlecrop 圆形裁剪    rectcrop 矩形裁剪        distance 距离推移
#   fadeblack 渐隐到黑     fadewhite 渐隐到白       radial 径向旋转
#   smoothleft 平滑左移    smoothright 平滑右移     smoothup 平滑上移
#   smoothdown 平滑下移    circleopen 圆形开窗      circleclose 圆形闭窗
#   vertopen 垂直开窗      vertclose 垂直闭窗       horzopen 水平开窗
#   horzclose 水平闭窗     dissolve 溶解            pixelize 像素化
#   diagtl 对角线↘         diagtr 对角线↙           diagbl 对角线↗
#   diagbr 对角线↖         hlslice 水平左切片       hrslice 水平右切片
#   vuslice 垂直上切片     vdslice 垂直下切片       hblur 水平模糊
#   fadegrays 渐隐到灰度   wipetl 擦除↘             wipetr 擦除↙
#   wipebl 擦除↗           wipebr 擦除↖             squeezeh 水平挤压
#   squeezev 垂直挤压      zoomin 缩放进入          fadefast 快速淡入淡出
#   fadeslow 慢速淡入淡出  hlwind 水平左卷帘        hrwind 水平右卷帘
#   vuwind 垂直上卷帘      vdwind 垂直下卷帘        coverleft 左覆盖
#   coverright 右覆盖      coverup 上覆盖           coverdown 下覆盖
#   revealleft 左揭示      revealright 右揭示       revealup 上揭示
#   revealdown 下揭示
# ============================================================

# 转场效果中文说明（与上表一致；用于运行时输出与报错提示）
TRANSITION_HELP: dict[str, str] = {
    "fade": "淡入淡出",
    "wipeleft": "向左擦除",
    "wiperight": "向右擦除",
    "wipeup": "向上擦除",
    "wipedown": "向下擦除",
    "slideleft": "向左滑入",
    "slideright": "向右滑入",
    "slideup": "向上滑入",
    "slidedown": "向下滑入",
    "circlecrop": "圆形裁剪",
    "rectcrop": "矩形裁剪",
    "distance": "距离推移",
    "fadeblack": "渐隐到黑",
    "fadewhite": "渐隐到白",
    "radial": "径向旋转",
    "smoothleft": "平滑左移",
    "smoothright": "平滑右移",
    "smoothup": "平滑上移",
    "smoothdown": "平滑下移",
    "circleopen": "圆形开窗",
    "circleclose": "圆形闭窗",
    "vertopen": "垂直开窗",
    "vertclose": "垂直闭窗",
    "horzopen": "水平开窗",
    "horzclose": "水平闭窗",
    "dissolve": "溶解",
    "pixelize": "像素化",
    "diagtl": "对角线（左上→右下）",
    "diagtr": "对角线（右上→左下）",
    "diagbl": "对角线（左下→右上）",
    "diagbr": "对角线（右下→左上）",
    "hlslice": "水平左切片",
    "hrslice": "水平右切片",
    "vuslice": "垂直上切片",
    "vdslice": "垂直下切片",
    "hblur": "水平模糊",
    "fadegrays": "渐隐到灰度",
    "wipetl": "擦除（左上→右下）",
    "wipetr": "擦除（右上→左下）",
    "wipebl": "擦除（左下→右上）",
    "wipebr": "擦除（右下→左上）",
    "squeezeh": "水平挤压",
    "squeezev": "垂直挤压",
    "zoomin": "缩放进入",
    "fadefast": "快速淡入淡出",
    "fadeslow": "慢速淡入淡出",
    "hlwind": "水平左卷帘",
    "hrwind": "水平右卷帘",
    "vuwind": "垂直上卷帘",
    "vdwind": "垂直下卷帘",
    "coverleft": "左覆盖",
    "coverright": "右覆盖",
    "coverup": "上覆盖",
    "coverdown": "下覆盖",
    "revealleft": "左揭示",
    "revealright": "右揭示",
    "revealup": "上揭示",
    "revealdown": "下揭示",
}


@dataclass(frozen=True)
class ClipInfo:
    """一个待拼接片段的探测信息。"""

    path: Path
    duration: float
    has_audio: bool
    width: int
    height: int
    fps: float


def natural_sort_key(name: str) -> list[object]:
    """自然排序键：'clip-2' < 'clip-10' < 'clip-100'。"""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)]


def find_videos(directory: Path, exts: set[str]) -> list[Path]:
    """收集目录下所有视频文件，按文件名自然排序。"""
    files = [p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in exts]
    return sorted(files, key=lambda p: natural_sort_key(p.name))


def parse_xfade_transitions(help_text: str) -> set[str]:
    """从 `ffmpeg -h filter=xfade` 输出中解析全部转场名。"""
    names: set[str] = set()
    for line in help_text.splitlines():
        match = re.match(r"\s+([a-z][a-z0-9]*)\s+-?\d+\s+\.\.FV", line)
        if match:
            names.add(match.group(1))
    return names


def get_supported_transitions(ffmpeg: str) -> set[str]:
    out = subprocess.run(
        [ffmpeg, "-hide_banner", "-h", "filter=xfade"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    # custom 需要额外的 expr 参数，本工具暂不支持，从列表中排除
    return parse_xfade_transitions(out) - {"custom"}


def probe(ffprobe: str, path: Path) -> dict:
    """ffprobe 获取时长与流信息（JSON）。"""
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-show_entries",
        "stream=codec_type,width,height,avg_frame_rate",
        "-of",
        "json",
        str(path),
    ]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def clip_info_from_probe(path: Path, data: dict) -> ClipInfo:
    streams = data.get("streams", [])
    videos = [s for s in streams if s.get("codec_type") == "video"]
    if not videos:
        raise ValueError(f"{path.name} 不包含视频流")
    video = videos[0]
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if width <= 0 or height <= 0:
        raise ValueError(f"{path.name} 无法读取视频分辨率")
    try:
        fps = float(Fraction(video.get("avg_frame_rate") or "0"))
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    if fps <= 0:
        raise ValueError(f"{path.name} 无法读取帧率")
    duration = float(data["format"]["duration"])
    if duration <= 0:
        raise ValueError(f"{path.name} 无法读取时长")
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    return ClipInfo(
        path=path,
        duration=duration,
        has_audio=has_audio,
        width=width,
        height=height,
        fps=fps,
    )


def pick_transitions(clips: list[ClipInfo], transition: str, pool: list[str]) -> list[str]:
    """返回 N-1 个转场名：固定转场全部相同；random 从 pool 中随机选择。"""
    pairs = len(clips) - 1
    if transition == "random":
        return [random.choice(pool) for _ in range(pairs)]
    return [transition] * pairs


def build_filter_complex(
    clips: list[ClipInfo],
    transitions: list[str],
    duration: float,
) -> str:
    """构建 ffmpeg filter_complex：视频 xfade 链 + 音频 acrossfade 链。

    所有片段统一到第一个片段的分辨率与帧率；音频统一为 48kHz 立体声，
    无音轨的片段用静音填充。转场 offset 按各段时长精确计算。
    """
    width, height = clips[0].width, clips[0].height
    fps = max(clips[0].fps, *(c.fps for c in clips[1:]))
    fps = round(fps)
    parts: list[str] = []

    # 各输入的视频/音频预处理
    for i, clip in enumerate(clips):
        parts.append(
            f"[{i}:v]setpts=PTS-STARTPTS,"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
            f"setsar=1,fps={fps}[v{i}]"
        )
        if clip.has_audio:
            parts.append(
                f"[{i}:a]aresample=48000,pan=stereo|c0=c0|c1=c0,"
                f"aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                f"asetpts=PTS-STARTPTS[a{i}]"
            )
        else:
            parts.append(
                f"anullsrc=r=48000:cl=stereo,atrim=duration={clip.duration:.6f},"
                f"asetpts=PTS-STARTPTS[a{i}]"
            )

    # 视频 xfade 链：offset 为“已拼接总时长 - 转场时长”；最后一步输出固定标签 vout
    acc = clips[0].duration
    v_prev = "v0"
    for i in range(1, len(clips)):
        offset = acc - duration
        out_label = f"xv{i}" if i < len(clips) - 1 else "vout"
        parts.append(
            f"[{v_prev}][v{i}]xfade=transition={transitions[i - 1]}:"
            f"duration={duration}:offset={offset:.6f}[{out_label}]"
        )
        acc = acc + clips[i].duration - duration
        v_prev = out_label

    # 音频 acrossfade 链；最后一步输出固定标签 aout
    a_prev = "a0"
    for i in range(1, len(clips)):
        out_label = f"xa{i}" if i < len(clips) - 1 else "aout"
        parts.append(f"[{a_prev}][a{i}]acrossfade=d={duration}[{out_label}]")
        a_prev = out_label

    return ";".join(parts)


def splice(
    ffmpeg: str,
    clips: list[ClipInfo],
    transitions: list[str],
    duration: float,
    output: Path,
    overwrite: bool,
) -> None:
    """执行拼接转场；先写 *.part 临时文件，成功后原子改名。"""
    filter_complex = build_filter_complex(clips, transitions, duration)
    tmp = output.with_name(f"{output.stem}.part{output.suffix}")
    if tmp.exists():
        tmp.unlink()
    cmd = [
        ffmpeg,
        "-y" if overwrite else "-n",
    ]
    for clip in clips:
        cmd += ["-i", str(clip.path)]
    cmd += [
        "-filter_complex",
        filter_complex,
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-c:v",
        "libx264",
        "-crf",
        CRF,
        "-preset",
        PRESET,
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(tmp),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        if tmp.exists():
            tmp.unlink()
        tail = exc.stderr.strip().splitlines()[-5:]
        raise RuntimeError("拼接失败：" + " | ".join(tail)) from exc
    tmp.replace(output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="按文件名自然排序拼接视频并添加转场（配置见 splice.py 顶部配置区）"
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

    in_dir = Path(INPUT_DIR)
    if not in_dir.is_dir():
        print(
            f"错误：找不到输入目录 {in_dir}（可修改 splice.py 顶部的 INPUT_DIR）", file=sys.stderr
        )
        return 1

    files = find_videos(in_dir, VIDEO_EXTS)
    if len(files) < 2:
        print(f"错误：{in_dir} 下至少需要 2 个视频文件（当前 {len(files)} 个）", file=sys.stderr)
        return 1

    try:
        supported = get_supported_transitions(ffmpeg)
    except subprocess.CalledProcessError:
        print("错误：无法获取 ffmpeg xfade 转场列表", file=sys.stderr)
        return 1
    if TRANSITION != "random" and TRANSITION not in supported:
        listed = ", ".join(f"{n}（{TRANSITION_HELP.get(n, '')}）" for n in sorted(supported))
        print(f"错误：未知转场 {TRANSITION!r}。本机 ffmpeg 支持的转场：{listed}", file=sys.stderr)
        return 1
    unknown = [t for t in TRANSITION_POOL if t not in supported]
    if unknown:
        listed = ", ".join(f"{n}（{TRANSITION_HELP.get(n, '')}）" for n in sorted(supported))
        print(f"错误：TRANSITION_POOL 包含未知转场 {unknown}。可用转场：{listed}", file=sys.stderr)
        return 1
    if TRANSITION == "random" and not TRANSITION_POOL:
        print("错误：TRANSITION 为 random 时 TRANSITION_POOL 不能为空", file=sys.stderr)
        return 1

    clips: list[ClipInfo] = []
    for path in files:
        try:
            data = probe(ffprobe, path)
            clips.append(clip_info_from_probe(path, data))
        except (subprocess.CalledProcessError, ValueError, KeyError) as exc:
            print(f"错误：读取 {path.name} 失败：{exc}", file=sys.stderr)
            return 1

    short = [c.path.name for c in clips if c.duration < TRANSITION_DURATION]
    if short:
        print(f"错误：以下片段时长小于转场时长 {TRANSITION_DURATION}s：{short}", file=sys.stderr)
        return 1

    transitions = pick_transitions(clips, TRANSITION, TRANSITION_POOL)

    print(f"输入目录：{in_dir}（{len(files)} 个片段，按文件名自然排序）")
    for i, clip in enumerate(clips):
        extra = "" if clip.has_audio else "（无音轨，静音填充）"
        print(
            f"  {i + 1}. {clip.path.name}  "
            f"{clip.width}x{clip.height}@{clip.fps:.2f}fps  {clip.duration:.2f}s{extra}"
        )
    for i, t in enumerate(transitions):
        hint = TRANSITION_HELP.get(t, "")
        print(f"  转场 {i + 1}（{clips[i].path.name} -> {clips[i + 1].path.name}）：{t}（{hint}）")

    output = next_output_path(Path(OUTPUT_DIR), OUTPUT_PREFIX, ".mp4", args.overwrite)
    print(f"输出：{output}（x264 crf {CRF}，转场 {TRANSITION_DURATION}s）")
    try:
        splice(ffmpeg, clips, transitions, TRANSITION_DURATION, output, args.overwrite)
    except RuntimeError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    total = sum(c.duration for c in clips) - TRANSITION_DURATION * (len(clips) - 1)
    print(f"完成：{output}，预计总时长 {total:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
