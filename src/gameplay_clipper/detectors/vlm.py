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
MODEL_ID: str = "Vishva007/Qwen3-VL-8B-Instruct-W4A16-AutoRound-AWQ"  # 本地 VLM 模型
FRAME_INTERVAL: float = 1.0  # 抽帧间隔（秒）；瞬时高光场景可调到 1.0
SCORE_THRESHOLD: int = 70  # 判定为精彩的分数阈值（0-100）
SEGMENT_PAD: float = 3.0  # 高光帧合并成片段时前后各扩几秒
USE_COARSE_PREFILTER: bool = False  # 先用 coarse 粗筛，只送检候选窗口内的帧（省算力）
CACHE_FILE: str = "highlight_output/.vlm_cache.json"  # 帧判定缓存（中断可续跑）
BATCH_SIZE: int = 8  # 批量推理：每批帧数（GPU 利用率与显存占用的折中）
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
        from transformers import AutoModelForImageTextToText, AutoProcessor, AwqConfig
        from transformers.utils.quantization_config import AwqBackend

        # transformers 5.15 bug 规避：qwen2_5_vl 的 apply_multimodal_rotary_pos_emb
        # 没有把 float32 的 cos/sin cast 到 q/k 的 dtype，fp16 模型下激活会被 promote
        # 成 float32，导致后续 attention matmul 混精度报错（视觉塔的 rope 无此问题）
        import transformers.models.qwen2_5_vl.modeling_qwen2_5_vl as qwen_vl_modeling

        _orig_mrope = qwen_vl_modeling.apply_multimodal_rotary_pos_emb

        def _patched_mrope(q, k, cos, sin, mrope_section, unsqueeze_dim=1):
            return _orig_mrope(
                q, k, cos.to(dtype=q.dtype), sin.to(dtype=q.dtype), mrope_section, unsqueeze_dim
            )

        qwen_vl_modeling.apply_multimodal_rotary_pos_emb = _patched_mrope

        # 该 AWQ checkpoint 的 config.json 未声明 backend，transformers 默认 auto
        # 会选到 Marlin 内核，而 Qwen2.5-VL 存在 out_features 不能被 64 整除的层，
        # Marlin 无法处理；这里显式固定为纯 PyTorch 反量化内核（兼容所有维度）。
        from transformers import AutoConfig

        cfg = AutoConfig.from_pretrained(MODEL_ID)
        quant_method = None
        if getattr(cfg, "quantization_config", None):
            quant_method = cfg.quantization_config.get("quant_method")

        print(f"  加载模型 {MODEL_ID}（首次运行需下载，请耐心等待）...")
        if quant_method == "awq":
            quant_config = AwqConfig(
                bits=4,
                group_size=128,
                zero_point=True,
                backend=AwqBackend.GEMM_TRITON,
                modules_to_not_convert=[
                    # transformers 5.x 的 should_convert_module 按 "visual." 前缀匹配，
                    # 但该架构 named_modules 带 "model." 前缀，需两种写法都写上才能跳过视觉塔
                    "visual",
                    "model.visual",
                ],
                version="gemm",
            )
            model = AutoModelForImageTextToText.from_pretrained(
                MODEL_ID,
                dtype=torch.float16,  # 统一 fp16：visual tower 未量化默认 bf16，与 AWQ fp16 混精度会报错
                device_map="auto",
                quantization_config=quant_config,
            )
            # transformers 对量化模型会忽略 dtype 参数、按 checkpoint 原 dtype 加载未量化
            # 部分（视觉塔 / embedding 等为 bf16），与 AWQ fp16 内核混精度 matmul 会报错。
            # 通用方案：把所有浮点参数统一 cast 成 fp16（int32 打包量化权重保持不变）。
            for param in model.parameters():
                if param.dtype in (torch.float32, torch.bfloat16):
                    param.data = param.data.to(torch.float16)
        else:
            # FP8 / 原版 checkpoint：transformers 原生 quantizer 处理（FP8 需 torchao，已随
            # gptqmodel 安装）。浮点参数统一 cast 到计算精度 fp16，fp8 权重保持不动。
            model = AutoModelForImageTextToText.from_pretrained(
                MODEL_ID,
                dtype=torch.float16,
                device_map="auto",
            )
            for param in model.parameters():
                if param.dtype in (torch.float32, torch.bfloat16):
                    param.data = param.data.to(torch.float16)

        # attention 的 causal mask 是 float32，会把 fp16 激活 promote 成 float32；
        # AWQ 量化层内部会 cast 输入所以不报错，但 lm_head 是普通 nn.Linear 会报
        # 混精度错误，这里给 lm_head 加输入 dtype 兜底（cast 开销可忽略）。
        # 仅当 lm_head 是普通浮点权重时 patch（fp8 权重交给 transformers 原生处理）
        if model.lm_head.weight.dtype in (torch.float16, torch.bfloat16, torch.float32):
            _orig_lm_head_forward = model.lm_head.forward

            def _patched_lm_head(x):
                return _orig_lm_head_forward(x.to(dtype=model.lm_head.weight.dtype))

            model.lm_head.forward = _patched_lm_head
        processor = AutoProcessor.from_pretrained(MODEL_ID)

        # 所有帧的 prompt 文本相同：chat template 输出只含 <image> 占位标记，
        # 与具体图像无关，生成一次循环内复用（省每帧 0.5-1s 的 CPU 开销）
        _dummy_img = Image.new("RGB", (8, 8))
        _template_messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": _dummy_img},
                    {"type": "text", "text": PROMPT},
                ],
            }
        ]
        text_template = processor.apply_chat_template(
            _template_messages, tokenize=False, add_generation_prompt=True
        )

        # 逐帧推理（GEMM_TRITON 内核下单帧约 1.5s；批量路径在部分内核上会退化，
        # 保持单帧循环最稳）
        for index, (timestamp, path) in enumerate(pending, start=1):
            image = Image.open(path).convert("RGB")
            inputs = processor(text=[text_template], images=[image], return_tensors="pt").to(
                model.device
            )
            output_ids = model.generate(**inputs, max_new_tokens=128)
            answer = processor.batch_decode(output_ids, skip_special_tokens=True)[0]
            # batch_decode 输出包含完整上下文（system/user/assistant），其中 prompt 自带
            # JSON 模板示例；直接全文切片会误切到模板文本，导致解析结果恒为 0。
            # 先只取最后一个 assistant 回复段，再截 JSON 部分。
            if "assistant" in answer:
                answer = answer.split("assistant")[-1]
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
