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
# 填 "random" 则每对相邻片段从 TRANSITION_POOL 中随机选一个；
# 填 "none" 则不添加转场，纯拼接（concat 无缝衔接，成片时长 = 各段时长之和）。
TRANSITION: str = "random"
TRANSITION_POOL: list[str] = ["fade", "dissolve", "wipeleft"]

TRANSITION_DURATION: float = 1.0  # 转场时长（秒）；每段视频时长必须不小于它

# 响度归一化（EBU R128）：各单元独立归一化到目标响度，多段素材音量不一致时统一听感。
# 两遍式 linear 模式（先测量后应用，无动态压缩损失）。目标响度建议：
# -14 LUFS（流媒体/B站常用）~ -16 LUFS（短视频平台常用，声音更响）。
LOUDNESS: bool = True
LOUDNESS_TARGET: float = -16.0

# 视频编码器与质量：
# - ENCODER="auto"：优先 h264_nvenc（NVIDIA 硬件编码，快 5-10 倍），不可用时回退 libx264
# - ENCODER="libx264" / "h264_nvenc"：强制指定
# - CRF：质量参数（libx264 的 crf / NVENC 的 cq；越小越好，18 高质量 / 23 均衡 / 28 省体积）
# - PRESET：速度预设（libx264: ultrafast..veryslow；NVENC: p1..p7 或 fast/medium/slow）
ENCODER: str = "auto"
CRF: str = "26"  # 质量（libx264 的 crf / NVENC 的 cq；越小越好，23 近无损 / 26 推荐 / 28 省体积）
PRESET: str = "medium"  # x264 速度预设
VIDEO_EXTS: set[str] = {".mp4", ".mkv", ".mov", ".ts", ".flv", ".webm", ".avi"}

# 成片首尾淡入淡出（秒；设为 0 关闭）。片头淡入、片尾淡出，
# 分别作用于整条拼接链的最后一级（视频 fade + 音频 afade）。
FADE_IN: float = 0.5
FADE_OUT: float = 1.0

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
    """返回 N-1 个转场名。

    固定转场全部相同；random 从 pool 中随机选择；none 返回空列表（纯拼接）。
    """
    pairs = len(clips) - 1
    if transition == "none":
        return []
    if transition == "random":
        return [random.choice(pool) for _ in range(pairs)]
    return [transition] * pairs


def build_filter_complex(
    clips: list[ClipInfo],
    transitions: list[str],
    duration: float,
    fade_in: float = 0.0,
    fade_out: float = 0.0,
) -> str:
    """构建 ffmpeg filter_complex：视频 xfade 链 + 音频 acrossfade 链。

    所有片段统一到第一个片段的分辨率与帧率；音频统一为 48kHz 立体声，
    无音轨的片段用静音填充。转场 offset 按各段时长精确计算。
    最后一级统一输出标签 vout/aout：fade_in/fade_out 大于 0 时追加
    视频 fade + 音频 afade 首尾淡入淡出，否则用 null/anull 原样透传。
    """
    width, height = clips[0].width, clips[0].height
    # 输出帧率取所有片段中的最高值并取整（避免低帧率片段被降帧）
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

    # 拼接链：无转场用 concat 无缝拼接（transitions 为空），
    # 有转场用 xfade + acrossfade；最后一步均输出 vlast/alast，
    # 由末尾的 fade（或 null）链统一转成 vout/aout
    if not transitions:
        # concat 要求各输入同分辨率/帧率（视频）与同采样率/声道/格式（音频），
        # 上面的预处理链已统一；成片时长 = 各段时长之和
        interleaved = []
        for i in range(len(clips)):
            interleaved += [f"[v{i}]", f"[a{i}]"]
        parts.append("".join(interleaved) + f"concat=n={len(clips)}:v=1:a=1[vlast][alast]")
        acc = sum(c.duration for c in clips)
    else:
        # 视频 xfade 链：offset 为“已拼接总时长 - 转场时长”
        acc = clips[0].duration
        v_prev = "v0"
        for i in range(1, len(clips)):
            offset = acc - duration
            out_label = f"xv{i}" if i < len(clips) - 1 else "vlast"
            parts.append(
                f"[{v_prev}][v{i}]xfade=transition={transitions[i - 1]}:"
                f"duration={duration}:offset={offset:.6f}[{out_label}]"
            )
            acc = acc + clips[i].duration - duration
            v_prev = out_label

        # 音频 acrossfade 链；最后一步输出 alast
        a_prev = "a0"
        for i in range(1, len(clips)):
            out_label = f"xa{i}" if i < len(clips) - 1 else "alast"
            parts.append(f"[{a_prev}][a{i}]acrossfade=d={duration}[{out_label}]")
            a_prev = out_label

    # 首尾淡入淡出（视频 fade / 音频 afade）；未开启时 null/anull 透传
    if fade_in > 0 or fade_out > 0:
        v_fades = []
        a_fades = []
        if fade_in > 0:
            v_fades.append(f"fade=t=in:st=0:d={fade_in}")
            a_fades.append(f"afade=t=in:st=0:d={fade_in}")
        if fade_out > 0:
            v_fades.append(f"fade=t=out:st={acc - fade_out:.6f}:d={fade_out}")
            a_fades.append(f"afade=t=out:st={acc - fade_out:.6f}:d={fade_out}")
        parts.append(f"[vlast]{','.join(v_fades)}[vout]")
        parts.append(f"[alast]{','.join(a_fades)}[aout]")
    else:
        parts.append("[vlast]null[vout]")
        parts.append("[alast]anull[aout]")

    return ";".join(parts)


def _has_encoder(name: str) -> bool:
    """检查本机 ffmpeg 是否支持指定编码器（如 h264_nvenc）。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    try:
        out = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except subprocess.CalledProcessError:
        return False
    return any(line.strip().startswith("V") and name in line for line in out.splitlines())


def build_encode_args() -> list[str]:
    """按 ENCODER 配置构建视频编码参数；auto 时优先探测 h264_nvenc。"""
    encoder = ENCODER
    if encoder == "auto":
        encoder = "h264_nvenc" if _has_encoder("h264_nvenc") else "libx264"
    if encoder == "h264_nvenc":
        # NVENC 恒定质量模式：-cq 等价 x264 的 -crf；-preset 控制编码速度
        return ["-c:v", "h264_nvenc", "-cq", CRF, "-preset", PRESET]
    return ["-c:v", "libx264", "-crf", CRF, "-preset", PRESET]


@dataclass(frozen=True)
class TimelineUnit:
    """时间线切分单元：主体段（1 路输入）或转场段（2 路输入 + xfade）。

    - segment：单独编码 clips[index] 的 [src_start, src_end) 区间
    - transition：xfade 混合 clips[index] 尾部与 clips[other_index] 头部，
      各取 transition_duration 长，offset=0（从开头即混合）
    timeline_start：本单元在最终时间线上的起点（秒），用于定位首尾淡入淡出。
    """

    timeline_start: float
    kind: str  # "segment" | "transition"
    index: int
    src_start: float
    src_end: float
    transition: str = ""
    other_index: int | None = None
    other_start: float = 0.0
    other_end: float = 0.0

    @property
    def duration(self) -> float:
        return self.src_end - self.src_start


def build_timeline(
    clips: list[ClipInfo], transitions: list[str], duration: float
) -> tuple[list[TimelineUnit], float]:
    """把片段序列切成时间线单元，返回 (单元列表, 成片总时长)。

    xfade 语义与整链一致：片段 i 在时间线上占 [S_i, S_i+d_i]，
    转场 i 覆盖 [S_i+d_i-T, S_i+d_i]（即片段 i 尾部 T 秒 + 片段 i+1 头部 T 秒）。
    每个单元独立编码（1-2 路输入），内存峰值与片段总数无关。
    """
    units: list[TimelineUnit] = []
    t = 0.0
    n = len(clips)
    for i in range(n):
        d = clips[i].duration
        if i == 0:
            # 首片段：头部未被消费，尾部 T 进入转场
            head = d - duration
            if head > 1e-3:
                units.append(TimelineUnit(t, "segment", i, 0.0, head))
                t += head
        elif i == n - 1:
            # 尾片段：头部被前一转场消费 T，尾部无转场
            body = d - duration
            if body > 1e-3:
                units.append(TimelineUnit(t, "segment", i, duration, d))
                t += body
        else:
            # 中间片段：头尾各被相邻转场消费 T
            body = d - 2 * duration
            if body > 1e-3:
                units.append(TimelineUnit(t, "segment", i, duration, d - duration))
                t += body
        if i < n - 1:
            units.append(
                TimelineUnit(
                    t,
                    "transition",
                    i,
                    d - duration,
                    d,
                    transitions[i] if transitions else "fade",
                    i + 1,
                    0.0,
                    duration,
                )
            )
            t += duration
    return units, t


def _audio_chain(inp: str, start: float, end: float, label: str, has_audio: bool) -> str:
    """音频预处理链：截取区间 → 统一 48kHz 立体声浮点格式。"""
    if has_audio:
        return (
            f"[{inp}:a]atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS,"
            f"aresample=48000,pan=stereo|c0=c0|c1=c0,"
            f"aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[{label}]"
        )
    return (
        f"anullsrc=r=48000:cl=stereo,atrim=duration={end - start:.6f},asetpts=PTS-STARTPTS[{label}]"
    )


def _loudnorm_filter(target: float, measured: dict | None = None) -> str:
    """loudnorm 滤镜参数：无 measured 时为测量模式（json），有则为两遍式应用（linear）。"""
    base = f"I={target}:TP=-1.5:LRA=11"
    if measured:
        return (
            f"loudnorm={base}"
            f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
            f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
            f":linear=true:print_format=summary"
        )
    return f"loudnorm={base}:print_format=json"


def _measure_loudness(
    ffmpeg: str,
    unit: TimelineUnit,
    clips: list[ClipInfo],
    duration: float,
) -> dict:
    """第一遍：测量单元音频的响度参数（input_i/input_tp/input_lra/input_thresh）。"""
    parts: list[str] = []
    if unit.kind == "segment":
        parts.append(
            _audio_chain("0", unit.src_start, unit.src_end, "a0", clips[unit.index].has_audio)
        )
    else:
        other = unit.other_index
        assert other is not None
        parts.append(
            _audio_chain("0", unit.src_start, unit.src_end, "a0", clips[unit.index].has_audio)
        )
        parts.append(
            _audio_chain("1", unit.other_start, unit.other_end, "a1", clips[other].has_audio)
        )
        parts.append(f"[a0][a1]acrossfade=d={duration}[aloud]")
        parts.append(f"[aloud]{_loudnorm_filter(LOUDNESS_TARGET)}")
    if unit.kind == "segment":
        parts.append(f"[a0]{_loudnorm_filter(LOUDNESS_TARGET)}")

    cmd = [ffmpeg, "-hide_banner", "-i", str(clips[unit.index].path)]
    if unit.other_index is not None:
        cmd += ["-i", str(clips[unit.other_index].path)]
    cmd += ["-filter_complex", ";".join(parts), "-f", "null", "-"]
    try:
        out = subprocess.run(cmd, check=True, capture_output=True, text=True).stderr
    except subprocess.CalledProcessError:
        # 测量失败（如纯静音）时回退为不归一化
        return {}
    result: dict = {}
    for key in ("input_i", "input_tp", "input_lra", "input_thresh"):
        m = re.search(rf'"{key}"\s*:\s*"?(-?[\d.]+)"?', out)
        if m:
            result[key] = m.group(1)
    return result


def build_unit_filter_complex(
    unit: TimelineUnit,
    clips: list[ClipInfo],
    width: int,
    height: int,
    fps: float,
    duration: float,
    total: float,
    fade_in: float,
    fade_out: float,
    loudness_args: dict | None = None,
) -> str:
    """构建单个时间线单元的 filter_complex（局部输入编号 0/1）。

    loudness_args 非空时在混音链之后应用两遍式 loudnorm（linear），
    空 dict 表示测量失败（保持原音量）；None 表示关闭归一化。
    """
    parts: list[str] = []

    def _video_chain(inp: str, start: float, end: float, label: str) -> str:
        return (
            f"[{inp}:v]trim=start={start:.6f}:end={end:.6f},setpts=PTS-STARTPTS,"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}[{label}]"
        )

    if unit.kind == "segment":
        parts.append(_video_chain("0", unit.src_start, unit.src_end, "vout"))
        parts.append(
            _audio_chain("0", unit.src_start, unit.src_end, "aout", clips[unit.index].has_audio)
        )
    else:
        other = unit.other_index
        assert other is not None
        parts.append(_video_chain("0", unit.src_start, unit.src_end, "v0"))
        parts.append(_video_chain("1", unit.other_start, unit.other_end, "v1"))
        parts.append(
            f"[v0][v1]xfade=transition={unit.transition}:duration={duration}:offset=0[vout]"
        )
        parts.append(
            _audio_chain("0", unit.src_start, unit.src_end, "a0", clips[unit.index].has_audio)
        )
        parts.append(
            _audio_chain("1", unit.other_start, unit.other_end, "a1", clips[other].has_audio)
        )
        parts.append(f"[a0][a1]acrossfade=d={duration}[aout]")

    # 响度归一化：在混音链之后、淡入淡出之前应用（linear 两遍式）。
    # loudness_args 为 None 关闭；空 dict 表示测量失败，保持原音量。
    a_mix = "aout"
    if loudness_args:
        parts.append(f"[aout]{_loudnorm_filter(LOUDNESS_TARGET, loudness_args)}[aloud]")
        a_mix = "aloud"

    # 首尾淡入淡出：仅当落在本单元覆盖的时间线区间时叠加
    v_fades: list[str] = []
    a_fades: list[str] = []
    if fade_in > 0 and unit.timeline_start <= 1e-6:
        v_fades.append(f"fade=t=in:st=0:d={fade_in}")
        a_fades.append(f"afade=t=in:st=0:d={fade_in}")
    if fade_out > 0:
        fo_start = total - fade_out
        unit_end = unit.timeline_start + unit.duration
        if unit.timeline_start - 1e-6 <= fo_start <= unit_end + 1e-6:
            st = max(0.0, fo_start - unit.timeline_start)
            v_fades.append(f"fade=t=out:st={st:.6f}:d={fade_out}")
            a_fades.append(f"afade=t=out:st={st:.6f}:d={fade_out}")
    if v_fades:
        parts.append(f"[vout]{','.join(v_fades)}[vfinal]")
        parts.append(f"[{a_mix}]{','.join(a_fades)}[afinal]")
    else:
        parts.append("[vout]null[vfinal]")
        parts.append(f"[{a_mix}]anull[afinal]")

    return ";".join(parts)


def encode_unit(
    ffmpeg: str,
    unit: TimelineUnit,
    clips: list[ClipInfo],
    width: int,
    height: int,
    fps: float,
    duration: float,
    total: float,
    fade_in: float,
    fade_out: float,
    out: Path,
) -> None:
    """编码单个时间线单元（1-2 路输入，内存恒定）。

    开启响度归一化（LOUDNESS）时先测量单元响度，再以 linear 模式应用，
    保证各单元（多段素材）听感响度一致。
    """
    loudness_args: dict | None = None
    if LOUDNESS:
        has_any_audio = clips[unit.index].has_audio or (
            unit.other_index is not None and clips[unit.other_index].has_audio
        )
        if has_any_audio:
            loudness_args = _measure_loudness(ffmpeg, unit, clips, duration)
    fc = build_unit_filter_complex(
        unit, clips, width, height, fps, duration, total, fade_in, fade_out, loudness_args
    )
    cmd = [ffmpeg, "-y", "-hwaccel", "cuda", "-i", str(clips[unit.index].path)]
    if unit.other_index is not None:
        cmd += ["-i", str(clips[unit.other_index].path)]
    cmd += [
        "-filter_complex",
        fc,
        "-map",
        "[vfinal]",
        "-map",
        "[afinal]",
        *build_encode_args(),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(out),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        if out.exists():
            out.unlink()
        tail = exc.stderr.strip().splitlines()[-5:]
        raise RuntimeError("单元编码失败：" + " | ".join(tail)) from exc


def concat_units(ffmpeg: str, unit_files: list[Path], output_tmp: Path) -> None:
    """把所有单元文件按时间线顺序无损合并（-c copy，不重编码）。"""
    list_file = output_tmp.with_name("concat-list.txt")
    list_file.write_text("".join(f"file '{p.resolve()}'\n" for p in unit_files), encoding="utf-8")
    try:
        cmd = [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c",
            "copy",
            str(output_tmp),
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        tail = exc.stderr.strip().splitlines()[-5:]
        raise RuntimeError("单元合并失败：" + " | ".join(tail)) from exc
    finally:
        list_file.unlink(missing_ok=True)


def splice(
    ffmpeg: str,
    clips: list[ClipInfo],
    transitions: list[str],
    duration: float,
    output: Path,
    overwrite: bool,
    fade_in: float = 0.0,
    fade_out: float = 0.0,
) -> None:
    """NLE 式时间线切分拼接：按转场把片段切成主体/转场单元，各自独立编码
    （任意时刻 1-2 路输入，内存峰值与片段总数无关），最后 -c copy 无损合并。

    相比 xfade 整链（所有输入同时解码）与分段递归（多次重编码）：
    - 内存恒定，片段再多也不会 OOM
    - 每段内容只编码一次，画质只损耗一次
    - 单元之间可并行编码（NVENC 多会话）
    """
    units, total = build_timeline(clips, transitions, duration)
    width, height = clips[0].width, clips[0].height
    fps = round(max(clips[0].fps, *(c.fps for c in clips[1:])))

    tmp_dir = output.parent / ".splice_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = output.with_name(f"{output.stem}.part{output.suffix}")
    if tmp.exists():
        tmp.unlink()

    from concurrent.futures import ThreadPoolExecutor

    try:
        unit_files: list[Path] = []

        def _encode_one(item: tuple[int, TimelineUnit]) -> Path:
            idx, unit = item
            out = tmp_dir / f"{output.stem}-u{idx:03d}.mp4"
            encode_unit(
                ffmpeg, unit, clips, width, height, fps, duration, total, fade_in, fade_out, out
            )
            return out

        # 单元并行编码（NVENC 多会话；并发过高时 CPU 滤镜会争抢）
        with ThreadPoolExecutor(max_workers=4) as pool:
            for out in pool.map(_encode_one, enumerate(units)):
                unit_files.append(out)

        concat_units(ffmpeg, unit_files, tmp)
        tmp.replace(output)
    except RuntimeError:
        if tmp.exists():
            tmp.unlink()
        raise
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


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

    if TRANSITION != "none":
        try:
            supported = get_supported_transitions(ffmpeg)
        except subprocess.CalledProcessError:
            print("错误：无法获取 ffmpeg xfade 转场列表", file=sys.stderr)
            return 1
        if TRANSITION != "random" and TRANSITION not in supported:
            listed = ", ".join(f"{n}（{TRANSITION_HELP.get(n, '')}）" for n in sorted(supported))
            msg = f"错误：未知转场 {TRANSITION!r}。本机 ffmpeg 支持的转场：{listed}"
            print(msg, file=sys.stderr)
            return 1
        unknown = [t for t in TRANSITION_POOL if t not in supported]
        if unknown:
            listed = ", ".join(f"{n}（{TRANSITION_HELP.get(n, '')}）" for n in sorted(supported))
            msg = f"错误：TRANSITION_POOL 包含未知转场 {unknown}。可用转场：{listed}"
            print(msg, file=sys.stderr)
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

    short = [
        c.path.name for c in clips if TRANSITION != "none" and c.duration < TRANSITION_DURATION
    ]
    if short:
        print(f"错误：以下片段时长小于转场时长 {TRANSITION_DURATION}s：{short}", file=sys.stderr)
        return 1

    transitions = pick_transitions(clips, TRANSITION, TRANSITION_POOL)

    total = sum(c.duration for c in clips)
    if TRANSITION != "none":
        total -= TRANSITION_DURATION * (len(clips) - 1)
    if FADE_IN + FADE_OUT >= total:
        print(
            f"错误：淡入 {FADE_IN}s + 淡出 {FADE_OUT}s 不小于成片总时长 {total:.2f}s，"
            "请调小 FADE_IN / FADE_OUT（设为 0 可关闭）",
            file=sys.stderr,
        )
        return 1

    print(f"输入目录：{in_dir}（{len(files)} 个片段，按文件名自然排序）")
    for i, clip in enumerate(clips):
        extra = "" if clip.has_audio else "（无音轨，静音填充）"
        print(
            f"  {i + 1}. {clip.path.name}  "
            f"{clip.width}x{clip.height}@{clip.fps:.2f}fps  {clip.duration:.2f}s{extra}"
        )
    if not transitions:
        print("  转场：无（纯拼接，concat 无缝衔接）")
    for i, t in enumerate(transitions):
        hint = TRANSITION_HELP.get(t, "")
        print(f"  转场 {i + 1}（{clips[i].path.name} -> {clips[i + 1].path.name}）：{t}（{hint}）")

    output = next_output_path(Path(OUTPUT_DIR), OUTPUT_PREFIX, ".mp4", args.overwrite)
    transition_desc = "无转场" if not transitions else f"转场 {TRANSITION_DURATION}s"
    encode_args = build_encode_args()
    print(
        f"输出：{output}（编码器 {encode_args[1]}，{transition_desc}，"
        f"淡入 {FADE_IN}s / 淡出 {FADE_OUT}s）"
    )
    try:
        splice(
            ffmpeg,
            clips,
            transitions,
            TRANSITION_DURATION,
            output,
            args.overwrite,
            FADE_IN,
            FADE_OUT,
        )
    except RuntimeError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    print(f"完成：{output}，预计总时长 {total:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
