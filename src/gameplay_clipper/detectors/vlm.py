"""本地 VLM 精判检测器：抽帧后由视觉大模型判定精彩时刻（需 GPU）。

模型推荐 Qwen2.5-VL-7B-Instruct-AWQ（约 5-6GB 显存，4080S 可跑）。
在 4080S 真机上首次运行前，请在其 WSL2 虚拟环境安装：
    pip install torch transformers qwen-vl-utils accelerate pillow
（模型首次加载会自动从 HuggingFace 下载，国内可设 HF_ENDPOINT=https://hf-mirror.com）

torch/transformers 为延迟导入：本模块在无 GPU 的开发机上仍可导入与单测。
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

from gameplay_clipper.detectors import Segment, register

# ============================================================
# 配置区（按需修改）
# ============================================================
MODEL_ID: str = "Qwen/Qwen2.5-VL-7B-Instruct-AWQ"  # 本地 VLM 模型
FRAME_INTERVAL: float = 2.0  # 抽帧间隔（秒）；瞬时高光场景可调到 1.0
SCORE_THRESHOLD: int = 70  # 判定为精彩的分数阈值（0-100）
SEGMENT_PAD: float = 3.0  # 高光帧合并成片段时前后各扩几秒
USE_COARSE_PREFILTER: bool = True  # 先用 coarse 粗筛，只送检候选窗口内的帧（省算力）
CACHE_FILE: str = "highlight_output/.vlm_cache.json"  # 帧判定缓存（中断可续跑）
PROMPT: str = (
    "你是游戏实况剪辑助手。判断这张游戏画面是否为精彩/高光时刻。"
    "精彩时刻包括：击杀或多杀、团战胜利、极限操作或残血反杀、"
    "搞笑瞬间、重要事件或剧情。"
    "只输出 JSON，不要输出其他内容："
    '{"highlight": true或false, "score": 0到100的整数, "reason": "不超过15字的中文理由"}'
)
# ============================================================

_SCORE_RE = re.compile(r'"score"\s*:\s*(\d+)')
_HIGHLIGHT_RE = re.compile(r'"highlight"\s*:\s*(true|false)')
_REASON_RE = re.compile(r'"reason"\s*:\s*"([^"]*)"')


def parse_response(text: str) -> dict:
    """解析模型输出，尽力提取 highlight/score/reason 三元组。"""
    text = text.strip()
    result: dict = {"highlight": False, "score": 0, "reason": ""}
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            result["highlight"] = bool(data.get("highlight", False))
            try:
                result["score"] = int(data.get("score", 0))
            except (TypeError, ValueError):
                result["score"] = 0
            result["reason"] = str(data.get("reason", ""))
            return result
    except json.JSONDecodeError:
        pass
    # JSON 解析失败时用正则兜底
    m = _HIGHLIGHT_RE.search(text)
    if m:
        result["highlight"] = m.group(1) == "true"
    m = _SCORE_RE.search(text)
    if m:
        result["score"] = int(m.group(1))
    m = _REASON_RE.search(text)
    if m:
        result["reason"] = m.group(1)
    return result


def extract_frames(
    ffmpeg: str, source: Path, interval: float, out_dir: Path
) -> list[tuple[float, Path]]:
    """按固定间隔抽帧到 out_dir，返回 [(时间戳, 帧路径), ...]。"""
    pattern = out_dir / "frame_%06d.jpg"
    cmd = [
        ffmpeg,
        "-i",
        str(source),
        "-vf",
        f"fps=1/{interval},scale=512:-2",
        "-q:v",
        "3",
        str(pattern),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    frames: list[tuple[float, Path]] = []
    for index, path in enumerate(sorted(out_dir.glob("frame_*.jpg"))):
        frames.append((index * interval, path))
    return frames


def _filter_by_coarse(frames: list[tuple[float, Path]], source: Path) -> list[tuple[float, Path]]:
    """用 coarse 检测器的候选窗口过滤帧（粗筛失败则全量送检）。"""
    try:
        from gameplay_clipper.detectors.coarse import CoarseDetector

        windows = [(s.start, s.end) for s in CoarseDetector().detect(source)]
        if not windows:
            return frames
        return [
            (ts, path) for ts, path in frames if any(start <= ts <= end for start, end in windows)
        ]
    except Exception:  # noqa: BLE001 粗筛失败不阻断主流程
        return frames


def merge_highlights(
    results: list[tuple[float, dict]],
    threshold: int,
    interval: float,
    pad: float,
    duration: float,
) -> list[Segment]:
    """把逐帧判定合并为片段：相邻高分帧聚成一段，前后各扩 pad 秒。"""
    hits = [(ts, r) for ts, r in sorted(results) if r["score"] >= threshold]
    if not hits:
        return []
    segments: list[Segment] = []
    start_ts, current = hits[0]
    best = current
    last_ts = start_ts
    for ts, result in hits[1:]:
        if ts - last_ts <= interval * 2 + 1e-6:  # 与上一高分帧相邻则并入同一段
            last_ts = ts
            if result["score"] > best["score"]:
                best = result
            continue
        segments.append(_make_segment(start_ts, last_ts, best, pad, duration))
        start_ts, last_ts, best = ts, ts, result
    segments.append(_make_segment(start_ts, hits[-1][0], best, pad, duration))
    return segments


def _make_segment(
    start_ts: float, end_ts: float, best: dict, pad: float, duration: float
) -> Segment:
    start = max(0.0, start_ts - pad)
    end = min(duration, end_ts + pad)
    return Segment(start=start, end=end, score=float(best["score"]), reason=best.get("reason", ""))


class _VlmCache:
    """帧判定结果缓存：{源路径: {"160.0": {"score":.., "reason":..}}}。"""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, dict] = {}
        if path.is_file():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self.data = {}

    def get(self, key: str, timestamp: float) -> dict | None:
        return self.data.get(key, {}).get(f"{timestamp:.1f}")

    def put(self, key: str, timestamp: float, result: dict) -> None:
        self.data.setdefault(key, {})[f"{timestamp:.1f}"] = result

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")


@register("vlm")
class VlmDetector:
    """本地 VLM 精判检测器（需 GPU 与 torch/transformers 环境）。"""

    name = "vlm"

    def detect(self, source: Path, ffmpeg: str = "ffmpeg") -> list[Segment]:
        import shutil

        ffmpeg = shutil.which("ffmpeg") or ffmpeg
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            raise RuntimeError("未找到 ffprobe")

        duration = self._probe_duration(ffprobe, source)
        if duration <= 0:
            raise ValueError(f"无法读取 {source.name} 的时长")

        cache = _VlmCache(Path(CACHE_FILE))
        key = str(source.resolve())

        with tempfile.TemporaryDirectory(prefix="gpc_vlm_") as tmp:
            frames = extract_frames(ffmpeg, source, FRAME_INTERVAL, Path(tmp))
            if USE_COARSE_PREFILTER:
                before = len(frames)
                frames = _filter_by_coarse(frames, source)
                print(f"  粗筛后送检帧：{len(frames)}/{before}")
            pending = [(ts, path) for ts, path in frames if cache.get(key, ts) is None]
            if pending:
                print(f"  待推理帧：{len(pending)}（已缓存 {len(frames) - len(pending)}）")
                self._infer(pending, cache, key)
                cache.save()
            else:
                print("  全部帧已缓存，跳过推理")

        results: list[tuple[float, dict]] = []
        for ts, _path in frames:
            result = cache.get(key, ts)
            if result is not None:
                results.append((ts, result))
        if not results:
            raise RuntimeError("没有可用的判定结果")

        shown = sum(1 for _, r in results if r["score"] >= SCORE_THRESHOLD)
        print(f"  高光帧：{shown}/{len(results)}（阈值 {SCORE_THRESHOLD}）")
        return merge_highlights(results, SCORE_THRESHOLD, FRAME_INTERVAL, SEGMENT_PAD, duration)

    def _infer(
        self,
        pending: list[tuple[float, Path]],
        cache: _VlmCache,
        cache_key: str,
    ) -> None:
        """逐帧推理并写入缓存（真机路径，延迟导入 torch/transformers）。

        首次运行需在真机安装依赖；模型未下载时会自动从 HuggingFace 拉取。
        """
        import torch  # noqa: F401
        from PIL import Image  # noqa: F401
        from transformers import AutoProcessor, Qwen2VLForConditionalGeneration

        print(f"  加载模型 {MODEL_ID}（首次运行需下载，请耐心等待）...")
        model = Qwen2VLForConditionalGeneration.from_pretrained(
            MODEL_ID, torch_dtype="auto", device_map="auto"
        )
        processor = AutoProcessor.from_pretrained(MODEL_ID)

        for index, (timestamp, path) in enumerate(pending, start=1):
            image = Image.open(path).convert("RGB")
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "image": image},
                        {"type": "text", "text": PROMPT},
                    ],
                }
            ]
            text = processor.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
            inputs = processor(text=[text], images=[image], return_tensors="pt").to(model.device)
            output_ids = model.generate(**inputs, max_new_tokens=128)
            answer = processor.batch_decode(output_ids, skip_special_tokens=True)[0]
            # 答案里混有模板文本，取 JSON 部分
            answer = answer[answer.find("{") : answer.rfind("}") + 1]
            result = parse_response(answer)
            cache.put(cache_key, timestamp, result)
            if index % 50 == 0 or index == len(pending):
                cache.save()
                print(f"  推理进度 {index}/{len(pending)}")

    @staticmethod
    def _probe_duration(ffprobe: str, source: Path) -> float:
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
