"""跨功能共享的小工具。"""

from __future__ import annotations

from pathlib import Path


def next_output_path(directory: Path, prefix: str, suffix: str, overwrite: bool) -> Path:
    """确定下一个输出文件路径：clip-1{suffix}、clip-2{suffix}……

    overwrite=False 时跳过已存在的编号，保证不会覆盖；
    overwrite=True 时始终从 clip-1 开始（由 ffmpeg -y 负责覆盖）。
    """
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(1, 1_000_000):
        candidate = directory / f"{prefix}-{index}{suffix}"
        if overwrite or not candidate.exists():
            return candidate
    raise RuntimeError("找不到可用的输出文件名")
