"""精彩集锦：收集专业录制工具（Outplayed / Medal.tv / NVIDIA Highlights 等）的
成品高光片段，复制到 highlight_output/，可选自动拼接成集锦。

用法：
    python -m gameplay_clipper.highlight [--overwrite]

流程：扫描 CLIPS_DIRS 收集成品高光 → 复制到 highlight_output/
→ AUTO_SPLICE=True 时自动调 splice 拼接出集锦。收集到多少就处理多少，不设上限。
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from gameplay_clipper.common import next_output_path

# ============================================================
# 配置区（按需修改）
# ============================================================
# --- 高光目录（专业录制工具的输出目录，支持同时配置多个）---
CLIPS_DIRS: list[str] = [
    # "/mnt/c/Users/<user>/Videos/Outplayed",
    # "/mnt/c/Users/<user>/Videos/Medal",
    # "/mnt/c/Users/<user>/Videos/NVIDIA/Apex Legends",
]
RECURSIVE: bool = True  # 递归扫描子目录
FILE_PATTERNS: list[str] = ["*.mp4"]  # 收集的文件后缀
# 按文件名排除的正则。Outplayed 整场录像形如
# "Apex Legends_08-12-2026_23-28-12-50.mp4"，可配 r"_\d{2}-\d{2}-\d{4}_\d{2}-\d{2}-\d{2}"
EXCLUDE_NAME_PATTERNS: list[str] = []

# --- 集锦编排 ---
OUTPUT_DIR: str = "highlight_output"  # 收集产物目录
OUTPUT_PREFIX: str = "highlight"  # 输出文件名前缀，如 highlight-1.mp4
AUTO_SPLICE: bool = True  # 收集后自动调用 splice 拼接成集锦
# ============================================================


@dataclass(frozen=True)
class Segment:
    """一个成品高光片段（时间单位：秒）。source 为片段文件本身。"""

    start: float
    end: float
    score: float
    reason: str = ""
    source: Path | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start


def probe_duration(path: Path) -> float | None:
    """用 ffprobe 读取视频时长；失败返回 None。"""
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise ValueError("未找到 ffprobe，请先安装 ffmpeg（Ubuntu: sudo apt install ffmpeg）")
    try:
        out = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return float(out)
    except (subprocess.CalledProcessError, ValueError):
        return None


def scan_clips() -> list[Segment]:
    """扫描 CLIPS_DIRS 配置的目录，把每个视频文件收集为一个成品高光片段。"""
    excludes = [re.compile(pattern) for pattern in EXCLUDE_NAME_PATTERNS]
    segments: list[Segment] = []
    missing: list[Path] = []

    for raw_dir in CLIPS_DIRS:
        root = Path(raw_dir).expanduser()
        if not root.is_dir():
            missing.append(root)
            continue
        for pattern in FILE_PATTERNS:
            files = root.rglob(pattern) if RECURSIVE else root.glob(pattern)
            for path in files:
                if not path.is_file():
                    continue
                if any(expr.search(path.name) for expr in excludes):
                    continue
                duration = probe_duration(path)
                if duration is None:
                    print(f"  警告：无法读取 {path} 的时长，已跳过")
                    continue
                segments.append(
                    Segment(
                        start=0.0,
                        end=duration,
                        score=1.0,
                        reason=f"{root.name}/{path.relative_to(root)}",
                        source=path,
                    )
                )

    if missing:
        print(f"  警告：以下高光目录不存在，已跳过：{', '.join(map(str, missing))}")
    if not segments:
        raise ValueError(
            "未在 CLIPS_DIRS 中找到任何视频文件"
            "（检查 highlight.py 顶部的 CLIPS_DIRS 与 EXCLUDE_NAME_PATTERNS 配置）"
        )
    # 按目录+文件名排序，保证输出顺序稳定（拼接时 splice 会再按自然排序）
    segments.sort(key=lambda s: s.source.as_posix())
    return segments


def collect_segments(
    segments: list[Segment],
    out_dir: Path,
    prefix: str,
    overwrite: bool,
) -> list[Path]:
    """把成品高光片段原样复制到 out_dir（不重编码）。"""
    outputs: list[Path] = []
    for index, seg in enumerate(segments, start=1):
        assert seg.source is not None
        suffix = seg.source.suffix or ".mp4"
        output = next_output_path(out_dir, prefix, suffix, overwrite)
        print(f"  [{index}/{len(segments)}] 复制 {seg.source.name}（{seg.reason}）=> {output.name}")
        shutil.copy2(seg.source, output)
        outputs.append(output)
    return outputs


def auto_splice(out_dir: Path) -> int:
    """把 highlight_output 下的产物交给 splice 拼接。"""
    from gameplay_clipper import splice

    splice.INPUT_DIR = str(out_dir)
    splice.OUTPUT_DIR = "connect_output"
    splice.TRANSITION = "random"
    print("收集完毕，自动调用 splice 拼接…")
    return splice.main([])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=("精彩集锦：收集成品高光 → 复制 → 可选拼接（配置见 highlight.py 顶部配置区）")
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="允许覆盖已存在的输出文件（默认：自动递增编号，不覆盖）",
    )
    args = parser.parse_args(argv)

    try:
        segments = scan_clips()
    except (ValueError, RuntimeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    if not segments:
        print("未收集到高光片段。")
        return 1

    print(f"候选片段（共 {len(segments)} 个，成品片段无间隔约束）：")
    for seg in segments:
        print(f"  {seg.start:8.1f}s -> {seg.end:8.1f}s  score {seg.score:.0f}  {seg.reason}")

    try:
        outputs = collect_segments(segments, Path(OUTPUT_DIR), OUTPUT_PREFIX, args.overwrite)
    except RuntimeError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    print(f"完成：收集 {len(outputs)} 段到 {Path(OUTPUT_DIR).resolve()}")
    if AUTO_SPLICE:
        return auto_splice(Path(OUTPUT_DIR))
    print("提示：AUTO_SPLICE=False，未拼接。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
