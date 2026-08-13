"""highlight 及 detectors 的单元测试（除 vlm 推理外均可离线运行）。"""

import pytest

from gameplay_clipper import highlight
from gameplay_clipper.detectors import Segment, coarse, get_detector, manual, vlm

# ---------- 注册表 ----------


class TestRegistry:
    def test_coarse(self):
        assert get_detector("coarse").name == "coarse"

    def test_manual(self):
        assert get_detector("manual").name == "manual"

    def test_vlm(self):
        assert get_detector("vlm").name == "vlm"

    def test_unknown(self):
        with pytest.raises(KeyError, match="未知检测器"):
            get_detector("nope")


# ---------- coarse 解析与算法 ----------


class TestParseAudioEvents:
    def test_parses(self):
        text = """
[Parsed_ametadata_0] frame:0    pts:0       pts_time:0
[Parsed_ametadata_0] lavfi.astats.Overall.RMS_level=-20.5
[Parsed_ametadata_0] frame:1    pts:1024    pts_time:0.021333
[Parsed_ametadata_0] lavfi.astats.Overall.RMS_level=-inf
[Parsed_ametadata_0] frame:2    pts:2048    pts_time:0.042667
[Parsed_ametadata_0] lavfi.astats.Overall.RMS_level=-15.25
"""
        assert coarse.parse_audio_events(text) == [
            (0.0, -20.5),
            (0.021333, -100.0),
            (0.042667, -15.25),
        ]

    def test_rms_without_timestamp_ignored(self):
        text = "lavfi.astats.Overall.RMS_level=-10.0\n"
        assert coarse.parse_audio_events(text) == []


class TestParseSceneTimes:
    def test_parses(self):
        text = """
[Parsed_showinfo] n:0 pts:123 pts_time:3.5
[Parsed_showinfo] n:1 pts:456 pts_time:12.75
"""
        assert coarse.parse_scene_times(text) == [3.5, 12.75]


class TestNormalize:
    def test_flat_silence(self):
        assert coarse._normalize([-100.0, -100.0]) == [0.0, 0.0]

    def test_range(self):
        result = coarse._normalize([-40.0, -20.0])
        assert result[0] == 0.0 and result[1] == 1.0

    def test_clamp(self):
        assert coarse._normalize([-20.0, -20.0]) == [0.0, 0.0]


class TestPickPeaks:
    def test_gap_rejects_nearby_peak(self):
        # 两个宽峰：5（高）、13（低），B 区域候选（含窗口尾巴）距 5 最远 13
        # < min_gap 15 → 全部被间隔约束拒绝，只保留 [5]
        scores = [0.0] * 30
        scores[3:8] = [3.0] * 5
        scores[11:16] = [2.9] * 5
        peaks = coarse.pick_peaks(scores, window_bins=3, count=3, min_gap_bins=15)
        assert peaks == [5]

    def test_count_limits(self):
        # 三个远距离宽峰，count=2 → 取分数前 2，按位置排序输出
        scores = [0.0] * 70
        scores[3:8] = [3.0] * 5
        scores[28:33] = [2.9] * 5
        scores[53:58] = [2.0] * 5
        peaks = coarse.pick_peaks(scores, window_bins=3, count=2, min_gap_bins=10)
        assert peaks == [5, 30]

    def test_empty(self):
        assert coarse.pick_peaks([], 3, 3, 10) == []


# ---------- vlm 解析与融合 ----------


class TestParseResponse:
    def test_clean_json(self):
        result = vlm.parse_response('{"highlight": true, "score": 85, "reason": "三杀"}')
        assert result == {"highlight": True, "score": 85, "reason": "三杀"}

    def test_json_with_prefix(self):
        text = '好的，这是判定：{"highlight": false, "score": 40, "reason": "普通对线"}'
        result = vlm.parse_response(text)
        assert result["highlight"] is False
        assert result["score"] == 40
        assert result["reason"] == "普通对线"

    def test_string_score(self):
        result = vlm.parse_response('{"highlight": true, "score": "92", "reason": "x"}')
        assert result["score"] == 92

    def test_garbage(self):
        result = vlm.parse_response("无法理解这张图")
        assert result == {"highlight": False, "score": 0, "reason": ""}


class TestMergeHighlights:
    def test_adjacent_merged(self):
        results = [
            (0.0, {"score": 80, "reason": "a"}),
            (2.0, {"score": 90, "reason": "b"}),
            (4.0, {"score": 75, "reason": "c"}),
        ]
        segments = vlm.merge_highlights(results, 70, 2.0, 3.0, 100.0)
        assert len(segments) == 1
        assert segments[0].start == 0.0
        assert segments[0].end == 4.0 + 3.0
        assert segments[0].score == 90  # 取段内最高分

    def test_separated_into_two(self):
        results = [
            (0.0, {"score": 85}),
            (2.0, {"score": 80}),
            (10.0, {"score": 88}),
            (12.0, {"score": 82}),
        ]
        segments = vlm.merge_highlights(results, 70, 2.0, 3.0, 100.0)
        assert len(segments) == 2
        assert (segments[0].start, segments[0].end) == (0.0, 5.0)
        assert (segments[1].start, segments[1].end) == (7.0, 15.0)

    def test_below_threshold_ignored(self):
        results = [(0.0, {"score": 50}), (2.0, {"score": 60})]
        assert vlm.merge_highlights(results, 70, 2.0, 3.0, 100.0) == []

    def test_pad_clamped(self):
        results = [(0.0, {"score": 90})]
        segments = vlm.merge_highlights(results, 70, 2.0, 3.0, 100.0)
        assert (segments[0].start, segments[0].end) == (0.0, 3.0)


class TestVlmCache:
    def test_roundtrip(self, tmp_path):
        cache = vlm._VlmCache(tmp_path / "cache.json")
        cache.put("key", 12.0, {"score": 80, "reason": "x"})
        cache.save()
        reloaded = vlm._VlmCache(tmp_path / "cache.json")
        assert reloaded.get("key", 12.0) == {"score": 80, "reason": "x"}
        assert reloaded.get("key", 14.0) is None
        assert reloaded.get("other", 12.0) is None

    def test_corrupt_file(self, tmp_path):
        bad = tmp_path / "cache.json"
        bad.write_text("{not json")
        cache = vlm._VlmCache(bad)
        assert cache.get("key", 1.0) is None


# ---------- manual ----------


class TestManualDetector:
    def test_segments(self, tmp_path, monkeypatch):
        source = tmp_path / "a.mp4"
        source.touch()
        monkeypatch.setattr(manual, "MANUAL_SEGMENTS", [(10.0, 20.0), (30.0, 40.0)])
        segments = manual.ManualDetector().detect(source)
        assert segments == [
            Segment(10.0, 20.0, 1.0, "手动标记"),
            Segment(30.0, 40.0, 1.0, "手动标记"),
        ]

    def test_empty_config(self, tmp_path, monkeypatch):
        source = tmp_path / "a.mp4"
        source.touch()
        monkeypatch.setattr(manual, "MANUAL_SEGMENTS", [])
        with pytest.raises(ValueError, match="MANUAL_SEGMENTS 为空"):
            manual.ManualDetector().detect(source)

    def test_invalid_range(self, tmp_path, monkeypatch):
        source = tmp_path / "a.mp4"
        source.touch()
        monkeypatch.setattr(manual, "MANUAL_SEGMENTS", [(20.0, 10.0)])
        with pytest.raises(ValueError, match="终点必须晚于起点"):
            manual.ManualDetector().detect(source)


# ---------- highlight 编排 ----------


class TestClampSegments:
    def test_clamp_and_rank(self):
        segments = [
            Segment(5.0, 20.0, 0.5),
            Segment(0.0, 10.0, 0.9),
            Segment(45.0, 200.0, 0.7),  # 尾部超界
        ]
        picked = highlight.clamp_segments(segments, 50.0, 5, 0.0)
        # (45,200) 收进 (45,50)；(5,20) 与 (0,10) 重叠被拒；输出按 start 排序
        assert [(s.start, s.end, s.score) for s in picked] == [
            (0.0, 10.0, 0.9),
            (45.0, 50.0, 0.7),
        ]

    def test_gap_filter(self):
        segments = [
            Segment(0.0, 10.0, 0.9),
            Segment(5.0, 15.0, 0.5),  # 与上一段重叠 → 拒绝
            Segment(30.0, 40.0, 0.8),  # 与第一段间隔 20 >= 15 → 接受
        ]
        picked = highlight.clamp_segments(segments, 60.0, 5, 15.0)
        assert [(s.start, s.end) for s in picked] == [(0.0, 10.0), (30.0, 40.0)]

    def test_count_limit(self):
        segments = [Segment(i * 20, i * 20 + 5, float(i)) for i in range(5)]
        picked = highlight.clamp_segments(segments, 200.0, 3, 0.0)
        assert len(picked) == 3
        # 按分数降序取前 3（score 4/3/2），输出按 start 排序
        assert [s.score for s in picked] == [2.0, 3.0, 4.0]
