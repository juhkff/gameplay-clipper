"""粗筛检测器：ffmpeg 音频能量 + 画面变化（零依赖，任何机器可跑）。

原理：精彩时刻常伴随音量峰值（欢呼/击杀音效）与画面剧变（激烈操作），
把两个信号按秒分箱、归一化后加权融合，滑动窗口求和取 top-N 峰值窗口。

适合作为 vlm 精判前的候选窗口筛选，也可独立使用（效果弱于 vlm）。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from gameplay_clipper.detectors import Segment, register

# ============================================================
# 配置区（按需修改）
# ============================================================
AUDIO_WEIGHT: float = 0.6  # 音频能量权重（0~1）
SCENE_WEIGHT: float = 0.4  # 画面变化权重（0~1）
BIN_SIZE: float = 1.0  # 分析分辨率（秒/箱）
WINDOW_SIZE: float = 15.0  # 候选窗口时长（秒）
COUNT: int = 5  # 输出候选片段数量
MIN_GAP: float = 30.0  # 候选片段中心的最小间隔（秒）
SCENE_THRESHOLD: float = 0.4  # ffmpeg scene 判定阈值（0~1，越小越敏感）
# ============================================================

_AMETA_PTS_RE = re.compile(r"pts_time:([\d.]+)")
_ASTATS_RMS_RE = re.compile(r"lavfi\.astats\.Overall\.RMS_level=(-?[\d.]+|-inf)")
_PTS_RE = re.compile(r"pts_time:([\d.]+)")


def parse_audio_events(text: str) -> list[tuple[float, float]]:
    """解析 astats+ametadata 输出，返回 [(时间秒, RMS dB), ...]。"""
    events: list[tuple[float, float]] = []
    current_t: float | None = None
    for line in text.splitlines():
        match = _AMETA_PTS_RE.search(line)
        if match:
            current_t = float(match.group(1))
            continue
        match = _ASTATS_RMS_RE.search(line)
        if match and current_t is not None:
            value = match.group(1)
            events.append((current_t, -100.0 if value == "-inf" else float(value)))
    return events


def parse_scene_times(text: str) -> list[float]:
    """解析 ffmpeg select+showinfo 输出的场景切换时间点（秒）。"""
    times: list[float] = []
    for line in text.splitlines():
        match = _PTS_RE.search(line)
        if match:
            times.append(float(match.group(1)))
    return times


def probe_audio_rms(ffmpeg: str, source: Path) -> list[tuple[float, float]]:
    """逐音频帧 (时间, RMS dB)；无音轨或失败时返回空列表。"""
    cmd = [
        ffmpeg,
        "-i",
        str(source),
        "-af",
        "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level",
        "-f",
        "null",
        "-",
    ]
    try:
        out = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError:
        return []
    return parse_audio_events(out.stderr)


def probe_scene_times(ffmpeg: str, source: Path, threshold: float) -> list[float]:
    """场景切换时间点（秒）；失败时返回空列表。"""
    cmd = [
        ffmpeg,
        "-i",
        str(source),
        "-vf",
        f"select='gt(scene,{threshold})',showinfo",
        "-f",
        "null",
        "-",
    ]
    try:
        out = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError:
        return []
    return parse_scene_times(out.stderr)


def _to_bins(events: list[tuple[float, float]], bin_size: float, duration: float) -> list[float]:
    """把 (时间, 数值) 样本聚合成每秒一箱的均值。"""
    n_bins = max(1, int(duration / bin_size) + 1)
    bins = [0.0] * n_bins
    counts = [0] * n_bins
    for timestamp, value in events:
        if timestamp < 0:
            continue
        index = min(n_bins - 1, int(timestamp / bin_size))
        bins[index] += value
        counts[index] += 1
    return [bins[i] / counts[i] if counts[i] else 0.0 for i in range(n_bins)]


def _normalize(values: list[float]) -> list[float]:
    """把 dB 电平映射到 [0,1]：相对自身峰值，截掉低分位噪声。"""
    finite = [v for v in values if v > -100.0]
    if not finite:
        return [0.0] * len(values)
    floor = min(finite)
    ceiling = max(finite)
    if ceiling <= floor:
        return [0.0] * len(values)
    return [
        max(0.0, min(1.0, (v - floor) / (ceiling - floor))) if v > -100.0 else 0.0 for v in values
    ]


def _merge_score(audio_bins: list[float], scene_bins: list[float]) -> list[float]:
    """加权融合音频与场景信号（按长度取齐）。"""
    n = max(len(audio_bins), len(scene_bins))
    audio = (audio_bins + [0.0] * n)[:n]
    scene = (scene_bins + [0.0] * n)[:n]
    return [AUDIO_WEIGHT * audio[i] + SCENE_WEIGHT * scene[i] for i in range(n)]


def pick_peaks(
    scores: list[float],
    window_bins: int,
    count: int,
    min_gap_bins: int,
) -> list[int]:
    """滑动窗口求和，贪心取 top-N 峰值中心（间隔 >= min_gap_bins）。"""
    if not scores:
        return []
    n = len(scores)
    prefix = [0.0]
    for value in scores:
        prefix.append(prefix[-1] + value)
    windowed = [
        (prefix[min(n, i + window_bins)] - prefix[max(0, i - window_bins)]) for i in range(n)
    ]
    ranked = sorted(range(n), key=lambda i: windowed[i], reverse=True)
    picked: list[int] = []
    for index in ranked:
        if windowed[index] <= 0:
            break
        if all(abs(index - p) >= min_gap_bins for p in picked):
            picked.append(index)
        if len(picked) >= count:
            break
    return sorted(picked)


@register("coarse")
class CoarseDetector:
    """音频能量 + 画面变化粗筛检测器。"""

    name = "coarse"

    def detect(self, source: Path, ffmpeg: str = "ffmpeg") -> list[Segment]:
        import shutil

        ffmpeg = shutil.which("ffmpeg") or ffmpeg

        duration = _probe_duration(source)
        if duration <= 0:
            raise ValueError(f"无法读取 {source.name} 的时长")

        rms = probe_audio_rms(ffmpeg, source)
        audio_bins = _to_bins(rms, BIN_SIZE, duration)
        audio_norm = _normalize(audio_bins)

        scene_times = probe_scene_times(ffmpeg, source, SCENE_THRESHOLD)
        scene_events = [(t, 1.0) for t in scene_times]
        scene_bins = _to_bins(scene_events, BIN_SIZE, duration)
        scene_max = max(scene_bins) if scene_bins else 0.0
        scene_norm = [min(1.0, c / 3.0) for c in scene_bins] if scene_max > 0 else scene_bins

        scores = _merge_score(audio_norm, scene_norm)
        window_bins = max(1, int(WINDOW_SIZE / BIN_SIZE))
        peaks = pick_peaks(scores, window_bins, COUNT, int(MIN_GAP / BIN_SIZE))

        segments: list[Segment] = []
        for index in peaks:
            center = (index + 0.5) * BIN_SIZE
            start = max(0.0, center - WINDOW_SIZE / 2)
            end = min(duration, center + WINDOW_SIZE / 2)
            audio_hint = "音量峰值" if rms else ""
            scene_hint = "画面剧变" if scene_times else ""
            reason = "+".join(x for x in (audio_hint, scene_hint) if x) or "信号峰值"
            segments.append(Segment(start=start, end=end, score=scores[index], reason=reason))
        return segments


def _probe_duration(source: Path) -> float:
    """用 ffprobe 读取时长（秒）。"""
    import shutil

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "csv=p=0",
        str(source),
    ]
    try:
        out = subprocess.run(cmd, check=True, capture_output=True, text=True)
        return float(out.stdout.strip())
    except (subprocess.CalledProcessError, ValueError):
        return 0.0
