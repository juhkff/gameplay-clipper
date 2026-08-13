"""精彩时刻检测器接口与注册表。

每个检测器实现统一协议：``detect(source) -> list[Segment]``。
编排层（highlight.py）负责排序、截断、去重、裁剪与拼接。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Segment:
    """一个候选精彩片段（时间单位：秒）。"""

    start: float
    end: float
    score: float
    reason: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start


class Detector(Protocol):
    """检测器协议：输入源视频路径，输出候选精彩片段列表。"""

    name: str

    def detect(self, source: Path) -> list[Segment]: ...


_REGISTRY: dict[str, Callable[[], Detector]] = {}


def register(name: str) -> Callable[[Callable[[], Detector]], Callable[[], Detector]]:
    """装饰器：把检测器工厂注册进注册表。"""

    def decorator(factory: Callable[[], Detector]) -> Callable[[], Detector]:
        _REGISTRY[name] = factory
        return factory

    return decorator


def get_detector(name: str) -> Detector:
    """按名字实例化检测器；未注册时报错。"""
    if name not in _REGISTRY:
        raise KeyError(f"未知检测器 {name!r}，可用：{', '.join(sorted(_REGISTRY))}")
    return _REGISTRY[name]()


def available_detectors() -> list[str]:
    return sorted(_REGISTRY)


# 导入各检测器模块以触发 @register 注册（须在 _REGISTRY 定义之后）
from gameplay_clipper.detectors import coarse, manual, vlm  # noqa: E402,F401
