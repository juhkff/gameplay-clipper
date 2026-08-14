"""视频裁剪：从视频中截取片段。

用法：
    python -m gameplay_clipper.cut [--overwrite] [--exact]

默认无损裁剪（stream copy，不重编码）：画质、音质等源属性完全不变，
ffmpeg 仅做容器层级的拷贝（-c copy）。切割点会吸附到最近的关键帧（GOP 边界），
误差通常在 1 秒以内——这是无损裁剪的固有特性。

传 --exact（或配置 EXACT=True）则改为帧级精确裁剪：重编码（libx264），
切割点精确到帧，代价是输出会重新编码、速度较慢。两种模式均可保留音频流。

输出命名：clip-1{ext}、clip-2{ext}……（{ext} 沿用输入文件的扩展名）。
默认不覆盖：若编号已被占用则自动递增到下一个空位；
传 --overwrite 则允许直接覆盖已存在的文件。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from gameplay_clipper.common import next_output_path  # noqa: F401  # re-export

# ============================================================
# 配置区（按需修改；支持任意多段裁剪）
# ============================================================
VIDEO_PATH: str = "media/video.mp4"  # 待裁剪视频（素材统一放 media/，相对运行目录或绝对路径）
CLIPS: list[tuple[str, str]] = [
    # (起点, 终点)；时间格式支持 HH:MM:SS、MM:SS、秒数（可为小数）
    ("00:00:05", "00:00:30"),
    ("00:01:00", "00:02:00"),
]
OUTPUT_DIR: str = "cut_output"  # 输出目录（相对当前运行目录，或绝对路径）
OUTPUT_PREFIX: str = "clip"  # 输出文件名前缀，如 clip-1.mp4、clip-2.mp4

# 帧级精确裁剪（重编码）：True 时所有片段按帧级精确切割。
# 也可用命令行参数 --exact 临时开启，命令行优先。
EXACT: bool = False
EXACT_CRF: str = "23"  # x264 质量（越小越好，18 高质量 / 23 均衡 / 28 省体积）
EXACT_PRESET: str = "medium"  # x264 速度预设
EXACT_AUDIO_BITRATE: str = "192k"  # 重编码后的音频码率
# ============================================================


def parse_time(value: str) -> float:
    """把 HH:MM:SS / MM:SS / 秒数（可为小数）解析为秒数。"""
    try:
        parts = [float(p) for p in value.split(":")]
    except ValueError:
        raise ValueError(f"无法解析时间 {value!r}，支持 HH:MM:SS、MM:SS 或秒数") from None
    if not 1 <= len(parts) <= 3 or any(p < 0 for p in parts):
        raise ValueError(f"无法解析时间 {value!r}，支持 HH:MM:SS、MM:SS 或秒数")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


def build_cut_command(
    ffmpeg: str,
    source: Path,
    start: str,
    end: str,
    output: Path,
    overwrite: bool,
    exact: bool,
) -> list[str]:
    """构建 ffmpeg 裁剪命令。

    exact=False（stream copy）：-ss 放在 -i 之前（input seeking），快速、无损，
    切割点吸附最近关键帧；-map 0 保留全部流（视频/音频/字幕）；-c copy 只拷贝不重编码。

    exact=True（帧级精确）：-ss/-to 放在 -i 之后（output seeking），解码到精确帧后
    重编码（libx264），切割点精确到帧；字幕流不保留（重编码容器兼容性原因）。
    """
    flag = "-y" if overwrite else "-n"
    if not exact:
        return [
            ffmpeg,
            flag,
            "-ss",
            start,
            "-to",
            end,
            "-i",
            str(source),
            "-map",
            "0",
            "-c",
            "copy",
            str(output),
        ]
    return [
        ffmpeg,
        flag,
        "-i",
        str(source),
        "-ss",
        start,
        "-to",
        end,
        "-map",
        "0:v",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-crf",
        EXACT_CRF,
        "-preset",
        EXACT_PRESET,
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        EXACT_AUDIO_BITRATE,
        "-movflags",
        "+faststart",
        str(output),
    ]


def cut_segment(
    ffmpeg: str,
    source: Path,
    start: str,
    end: str,
    output: Path,
    overwrite: bool,
    exact: bool = False,
) -> None:
    """用 ffmpeg 裁剪一段。

    先写入 *.part 临时文件，成功后原子改名，避免留下半成品。
    exact=False 为 stream copy（无损、关键帧吸附）；exact=True 为帧级精确重编码。
    """
    tmp = output.with_name(f"{output.stem}.part{output.suffix}")
    if tmp.exists():
        tmp.unlink()
    cmd = build_cut_command(ffmpeg, source, start, end, tmp, overwrite, exact)
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        if tmp.exists():
            tmp.unlink()  # 非覆盖模式下清理失败残留；覆盖模式下不动已存在的正式文件
        tail = exc.stderr.strip().splitlines()[-5:]
        raise RuntimeError(f"裁剪失败（起点 {start}，终点 {end}）：" + " | ".join(tail)) from exc
    tmp.replace(output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="无损裁剪视频片段（stream copy，不重编码；配置见 cut.py 顶部配置区）"
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="允许覆盖已存在的输出文件（默认：自动递增编号，不覆盖）",
    )
    parser.add_argument(
        "--exact",
        action="store_true",
        help="帧级精确裁剪（重编码 libx264；默认：stream copy 无损、切割点吸附关键帧）",
    )
    args = parser.parse_args(argv)

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        print(
            "错误：未找到 ffmpeg，请先安装（Ubuntu: sudo apt install ffmpeg）",
            file=sys.stderr,
        )
        return 1

    source = Path(VIDEO_PATH)
    if not source.is_file():
        print(
            f"错误：找不到视频文件 {source}（可修改 cut.py 顶部的 VIDEO_PATH）",
            file=sys.stderr,
        )
        return 1
    suffix = source.suffix or ".mp4"

    segments: list[tuple[str, str]] = []
    for start, end in CLIPS:
        try:
            s, e = parse_time(start), parse_time(end)
        except ValueError as exc:
            print(f"错误：{exc}", file=sys.stderr)
            return 1
        if e <= s:
            print(f"错误：终点必须晚于起点：(起点 {start!r}, 终点 {end!r})", file=sys.stderr)
            return 1
        segments.append((start, end))

    out_dir = Path(OUTPUT_DIR)
    exact = args.exact or EXACT
    mode = "帧级精确（重编码）" if exact else "无损（stream copy）"
    print(f"裁剪模式：{mode}")
    for i, (start, end) in enumerate(segments, start=1):
        output = next_output_path(out_dir, OUTPUT_PREFIX, suffix, args.overwrite)
        print(f"[{i}/{len(segments)}] {start} -> {end}  =>  {output}")
        try:
            cut_segment(ffmpeg, source, start, end, output, args.overwrite, exact)
        except RuntimeError as exc:
            print(f"错误：{exc}", file=sys.stderr)
            return 1

    print(f"完成：共 {len(segments)} 段，输出目录 {out_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
