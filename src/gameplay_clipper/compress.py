"""视频压缩转码：把大体积录屏压到适合上传/存档的体积。

x264 + crf 重编码（参数与 splice 保持一致风格），可选缩放分辨率与统一帧率。
输入可以是单个视频文件，也可以是目录（目录下所有视频按文件名自然排序逐个压缩）。

用法：
    python -m gameplay_clipper.compress [--overwrite]

配置写在本文件顶部的「配置区」。
输出命名：compress-1.mp4、compress-2.mp4……默认不覆盖。
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from gameplay_clipper.common import next_output_path
from gameplay_clipper.splice import find_videos

# ============================================================
# 配置区（按需修改）
# ============================================================
INPUT: str = "media/video.mp4"  # 待压缩的视频文件，或包含视频的目录
OUTPUT_DIR: str = "compress_output"  # 输出目录（相对当前运行目录，或绝对路径）
OUTPUT_PREFIX: str = "compress"  # 输出文件名前缀，如 compress-1.mp4

CRF: str = "23"  # x264 质量（越小越好，18 高质量 / 23 均衡 / 28 省体积）
PRESET: str = "medium"  # x264 速度预设（medium 均衡 / slower 更小体积但更慢）
AUDIO_BITRATE: str = "128k"  # 重编码后的音频码率
RESIZE: str = ""  # 目标分辨率，如 "1280:720"；留空保持原分辨率
FPS: float = 0.0  # 统一帧率，如 30；0 = 保持原帧率
VIDEO_EXTS: set[str] = {".mp4", ".mkv", ".mov", ".ts", ".flv", ".webm", ".avi"}
# ============================================================


def build_command(
    ffmpeg: str,
    source: Path,
    output: Path,
    overwrite: bool,
    crf: str,
    preset: str,
    audio_bitrate: str,
    resize: str,
    fps: float,
) -> list[str]:
    """构建 ffmpeg 压缩命令：libx264 + crf，可选缩放/帧率，音频 aac。"""
    cmd = [ffmpeg, "-y" if overwrite else "-n", "-i", str(source)]
    vf: list[str] = []
    if resize:
        vf.append(f"scale={resize}")
    if fps > 0:
        vf.append(f"fps={fps:g}")
    if vf:
        cmd += ["-vf", ",".join(vf)]
    cmd += [
        "-c:v",
        "libx264",
        "-crf",
        crf,
        "-preset",
        preset,
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        "-movflags",
        "+faststart",
        "-progress",
        "pipe:1",  # key=value 进度写到 stdout，供实时显示；日志仍在 stderr
        str(output),
    ]
    return cmd


def parse_progress(line: str, duration: float | None) -> float | None:
    """从 `-progress pipe:1` 输出行解析进度百分比（0~100）。

    支持 out_time_us / out_time_ms（旧版 ffmpeg）；时长未知或行无关返回 None。
    """
    match = re.match(r"out_time_(us|ms)=(\d+)", line)
    if not match:
        return None
    micros = int(match.group(2))
    if match.group(1) == "ms":
        micros *= 1000
    if not duration or duration <= 0:
        return None
    return min(micros / 1e6 / duration * 100.0, 100.0)


def probe_duration(ffprobe: str, path: Path) -> float | None:
    """探测输入视频时长（秒）；失败返回 None（进度降级为无百分比）。"""
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
        return float(out) if out else None
    except (subprocess.CalledProcessError, ValueError):
        return None


def human_size(nbytes: float) -> str:
    """字节数的人类可读格式：≥1 MB 显示 MB，否则显示 KB。"""
    if nbytes >= 1024 * 1024:
        return f"{nbytes / (1024 * 1024):.1f} MB"
    return f"{nbytes / 1024:.0f} KB"


def compress(
    ffmpeg: str,
    source: Path,
    output: Path,
    overwrite: bool,
    duration: float | None = None,
) -> None:
    """压缩单个文件；先写 *.part 临时文件，成功后原子改名。

    duration 已知时实时刷新进度百分比（ffmpeg -progress 输出）；
    未知（未装 ffprobe 或探测失败）则静默执行。
    stderr 落临时文件，避免管道填满死锁，失败时取尾部信息报错。
    """
    tmp = output.with_name(f"{output.stem}.part{output.suffix}")
    if tmp.exists():
        tmp.unlink()
    cmd = build_command(ffmpeg, source, tmp, overwrite, CRF, PRESET, AUDIO_BITRATE, RESIZE, FPS)

    fd, err_name = tempfile.mkstemp(prefix="gc-compress-", suffix=".err")
    os.close(fd)
    err_path = Path(err_name)
    proc: subprocess.Popen[str] | None = None
    try:
        with open(err_path, "w", encoding="utf-8") as err_f:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=err_f,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            assert proc.stdout is not None
            shown = False
            if duration:
                last_pct = 0.0
                for line in proc.stdout:
                    pct = parse_progress(line, duration)
                    if pct is None:
                        continue
                    if pct < last_pct:
                        continue  # 音视频交错编码时 out_time 可能回退，保持单调
                    last_pct = pct
                    print(f"\r  进度 {pct:3.0f}%", end="", flush=True)
                    shown = True
            proc.wait()
        if shown:
            print()
        if proc.returncode != 0:
            tail = err_path.read_text(encoding="utf-8").strip().splitlines()[-5:]
            raise RuntimeError(f"压缩 {source.name} 失败：" + " | ".join(tail))
    finally:
        err_path.unlink(missing_ok=True)
        if proc is not None and proc.returncode != 0 and tmp.exists():
            tmp.unlink()  # 失败时清理 *.part 残留；覆盖模式下不动已存在的正式文件
    tmp.replace(output)


def resolve_inputs(raw: str) -> list[Path]:
    """把配置 INPUT 解析为待压缩文件列表：文件返回自身，目录返回其中所有视频。"""
    path = Path(raw)
    if path.is_file():
        return [path]
    if path.is_dir():
        return find_videos(path, VIDEO_EXTS)
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="视频压缩转码（x264 + crf；配置见 compress.py 顶部配置区）"
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="允许覆盖已存在的输出文件（默认：自动递增编号，不覆盖）",
    )
    args = parser.parse_args(argv)

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        print(
            "错误：未找到 ffmpeg，请先安装（Ubuntu: sudo apt install ffmpeg）",
            file=sys.stderr,
        )
        return 1
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        print("提示：未找到 ffprobe，压缩过程不显示进度百分比")

    sources = resolve_inputs(INPUT)
    if not sources:
        print(
            f"错误：找不到输入 {INPUT}（应为视频文件或包含视频的目录，"
            "可修改 compress.py 顶部的 INPUT）",
            file=sys.stderr,
        )
        return 1

    extras = []
    if RESIZE:
        extras.append(f"缩放 {RESIZE}")
    if FPS > 0:
        extras.append(f"帧率 {FPS:g}")
    desc = f"x264 crf {CRF}（{PRESET}）" + (f"，{'，'.join(extras)}" if extras else "")
    print(f"输入：{Path(INPUT)}（{len(sources)} 个文件）")
    print(f"压缩参数：{desc}，音频 aac {AUDIO_BITRATE}")

    suffix = sources[0].suffix or ".mp4"
    for i, source in enumerate(sources, start=1):
        output = next_output_path(Path(OUTPUT_DIR), OUTPUT_PREFIX, suffix, args.overwrite)
        size_in = source.stat().st_size
        print(f"[{i}/{len(sources)}] {source.name}（{human_size(size_in)}）=> {output.name}")
        duration = probe_duration(ffprobe, source) if ffprobe else None
        try:
            compress(ffmpeg, source, output, args.overwrite, duration)
        except RuntimeError as exc:
            print(f"错误：{exc}", file=sys.stderr)
            return 1
        size_out = output.stat().st_size
        pct = 100.0 * (1 - size_out / size_in) if size_in > 0 else 0.0
        print(f"  完成：{human_size(size_out)}（压缩 {pct:.0f}%）")

    print(f"完成：共 {len(sources)} 个文件，输出目录 {Path(OUTPUT_DIR).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
