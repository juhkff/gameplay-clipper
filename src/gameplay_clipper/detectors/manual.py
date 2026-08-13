"""手动标记检测器：在配置区写死精彩时间点，不做任何自动分析。

最可靠的兜底方案；也用于与自动检测器的结果对比调参。
"""

from __future__ import annotations

from pathlib import Path

from gameplay_clipper.detectors import Segment, register

# ============================================================
# 配置区（按需修改）
# ============================================================
MANUAL_SEGMENTS: list[tuple[float, float]] = [
    # (起点, 终点)，单位秒。例如：
    # (125.0, 145.0),
    # (300.0, 320.0),
]
# ============================================================


@register("manual")
class ManualDetector:
    """手动标记检测器：直接读取配置区的时间点。"""

    name = "manual"

    def detect(self, source: Path) -> list[Segment]:
        if not source.is_file():
            raise ValueError(f"找不到视频文件 {source}")
        segments: list[Segment] = []
        for start, end in MANUAL_SEGMENTS:
            if end <= start:
                raise ValueError(f"终点必须晚于起点：(起点 {start}, 终点 {end})")
            segments.append(Segment(start=start, end=end, score=1.0, reason="手动标记"))
        if not segments:
            raise ValueError(
                "manual 检测器的 MANUAL_SEGMENTS 为空（在 detectors/manual.py 配置区填写）"
            )
        return segments
