"""精彩集锦编排：检测精彩片段 → 无损裁剪 → 可选自动拼接。

用法：
    python -m gameplay_clipper.highlight [--overwrite]

检测器（DETECTOR 配置）：
- coarse  音频能量 + 画面变化粗筛（零依赖，任何机器可跑）
- manual  手动标记（detectors/manual.py 配置区写时间点）
- vlm     本地视觉大模型精判（需 GPU，见 detectors/vlm.py）

流程：检测 → 按分数取 top-N（间隔约束）→ 复用 cut 无损裁剪到
highlight_output/ → AUTO_SPLICE=True 时自动调 splice 拼接出集锦。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from gameplay_clipper.common import next_output_path
from gameplay_clipper.detectors import Segment, get_detector

# ============================================================
# 配置区（按需修改）
# ============================================================
SOURCE: str = "media/Apex Legends_08-12-2026_23-28-12-50.mp4"  # 待分析视频（素材统一放 media/）
DETECTOR: str = "vlm"  # 检测器：coarse / manual / vlm
OUTPUT_DIR: str = "highlight_output"  # 裁剪产物目录
OUTPUT_PREFIX: str = "highlight"  # 输出文件名前缀，如 highlight-1.mp4
HIGHLIGHT_COUNT: int = 5  # 最多取几个精彩片段
MIN_GAP: float = 30.0  # 片段之间的最小间隔（秒）
AUTO_SPLICE: bool = False  # 裁剪后自动调用 splice 拼接成集锦
# ============================================================


def clamp_segments(
    segments: list[Segment], duration: float, count: int, min_gap: float
) -> list[Segment]:
    """把片段收进 [0, duration]，按分数降序贪心取 top-N（两两间隔 >= min_gap）。"""
    valid: list[Segment] = []
    for seg in segments:
        start = max(0.0, seg.start)
        end = min(duration, seg.end)
        if end > start:
            valid.append(Segment(start=start, end=end, score=seg.score, reason=seg.reason))
    valid.sort(key=lambda s: s.score, reverse=True)
    picked: list[Segment] = []
    for seg in valid:
        if all(max(seg.start - other.end, other.start - seg.end) >= min_gap for other in picked):
            picked.append(seg)
        if len(picked) >= count:
            break
    return sorted(picked, key=lambda s: s.start)


def cut_segments(
    ffmpeg: str,
    source: Path,
    segments: list[Segment],
    out_dir: Path,
    prefix: str,
    overwrite: bool,
) -> list[Path]:
    """复用 cut 的 stream copy 裁剪，返回产物路径列表。"""
    from gameplay_clipper.cut import cut_segment

    suffix = source.suffix or ".mp4"
    outputs: list[Path] = []
    for index, seg in enumerate(segments, start=1):
        output = next_output_path(out_dir, prefix, suffix, overwrite)
        print(
            f"  [{index}/{len(segments)}] {seg.start:.1f}s -> {seg.end:.1f}s"
            f"（score {seg.score:.0f}，{seg.reason}）=> {output.name}"
        )
        cut_segment(
            ffmpeg,
            source,
            f"{seg.start:.3f}",
            f"{seg.end:.3f}",
            output,
            overwrite,
        )
        outputs.append(output)
    return outputs


def auto_splice(out_dir: Path) -> int:
    """把 highlight_output 下的产物交给 splice 拼接。"""
    from gameplay_clipper import splice

    splice.INPUT_DIR = str(out_dir)
    splice.OUTPUT_DIR = "connect_output"
    splice.TRANSITION = "random"
    print("检测完毕，自动调用 splice 拼接…")
    return splice.main([])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="精彩集锦：检测 → 裁剪 → 可选拼接（配置见 highlight.py 顶部配置区）"
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

    source = Path(SOURCE)
    if not source.is_file():
        print(
            f"错误：找不到视频文件 {source}（可修改 highlight.py 顶部的 SOURCE）", file=sys.stderr
        )
        return 1

    # 探测时长
    probe_cmd = [
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
        duration = float(
            subprocess.run(probe_cmd, check=True, capture_output=True, text=True).stdout
        )
    except (subprocess.CalledProcessError, ValueError):
        print(f"错误：无法读取 {source.name} 的时长", file=sys.stderr)
        return 1

    print(f"源视频：{source}（{duration:.1f}s），检测器：{DETECTOR}")
    try:
        detector = get_detector(DETECTOR)
        segments = detector.detect(source)
    except KeyError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except (ValueError, RuntimeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    if not segments:
        print("未检测到精彩片段。")
        return 1

    segments = clamp_segments(segments, duration, HIGHLIGHT_COUNT, MIN_GAP)
    if not segments:
        print("过滤后没有可用的精彩片段（检查 MIN_GAP / HIGHLIGHT_COUNT 配置）。")
        return 1

    print(f"候选片段（已按分数排序、间隔 >= {MIN_GAP}s）：")
    for seg in segments:
        print(f"  {seg.start:8.1f}s -> {seg.end:8.1f}s  score {seg.score:.0f}  {seg.reason}")

    try:
        outputs = cut_segments(
            ffmpeg, source, segments, Path(OUTPUT_DIR), OUTPUT_PREFIX, args.overwrite
        )
    except RuntimeError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    print(f"完成：裁剪 {len(outputs)} 段到 {Path(OUTPUT_DIR).resolve()}")
    if AUTO_SPLICE:
        return auto_splice(Path(OUTPUT_DIR))
    print("提示：可运行 python -m gameplay_clipper.splice 将片段拼接成集锦。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
