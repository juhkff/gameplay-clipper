# gameplay-clipper

游戏实况剪辑、拼接转场、精彩集锦制作工具集。

## 已实现功能

### cut —— 无损裁剪视频片段

从指定视频中按 (起点, 终点) 截取任意多段，**stream copy 不重编码**，画质、音质与源完全一致。

- 依赖系统安装的 `ffmpeg`（Ubuntu: `sudo apt install ffmpeg`）
- 裁剪配置写死在代码顶部配置区（`src/gameplay_clipper/cut.py`），无需终端输入：
  - `VIDEO_PATH`：待裁剪视频（默认 `media/video.mp4`，素材统一放 `media/`，相对当前运行目录）
  - `CLIPS`：`[(起点, 终点), ...]` 列表，段数不限；时间格式 `HH:MM:SS` / `MM:SS` / 秒数
  - `OUTPUT_DIR` / `OUTPUT_PREFIX`：输出目录与文件名前缀
- 输出命名 `clip-1.mp4`、`clip-2.mp4`……（扩展名沿用输入文件）；**默认不覆盖**，
  编号被占用时自动递增到下一个空位；传 `--overwrite` 则允许覆盖

```bash
python -m gameplay_clipper.cut            # 默认不覆盖
python -m gameplay_clipper.cut --overwrite  # 允许覆盖
```

注意：stream copy 的切割点会吸附到最近关键帧（GOP 边界），误差通常在 1 秒以内；
这是无损裁剪的固有特性。如需帧级精确切割，需要重编码（尚未实现）。

### splice —— 拼接视频片段并添加转场

把指定文件夹下的视频**按文件名自然排序**（clip-1 < clip-2 < … < clip-10 < clip-100）
依次拼接，相邻片段之间插入转场（视频 `xfade` + 音频 `acrossfade`），输出到
`connect_output/`（默认）。

- 依赖 `ffmpeg` 与 `ffprobe`；转场需要重编码：libx264 + crf 23（可改）
- 配置写死在代码顶部配置区（`src/gameplay_clipper/splice.py`）：
  - `INPUT_DIR`：待拼接视频所在文件夹
  - `OUTPUT_DIR` / `OUTPUT_PREFIX`：输出目录（默认 `connect_output`）与文件名前缀
  - `TRANSITION`：转场效果。填具体名字（如 `"fade"`）则所有转场均用该效果；
    填 `"random"` 则每对相邻片段从 `TRANSITION_POOL` 中随机选一个
  - `TRANSITION_POOL`：`random` 模式下的候选转场集合（xfade 支持 58 种，
    全部列表见配置区注释）
  - `TRANSITION_DURATION`：转场时长（秒），每段视频时长必须不小于它
- 片段分辨率/帧率不同会自动统一到最高规格；无音轨的片段用静音填充
- 输出命名 `connect-1.mp4`、`connect-2.mp4`……默认不覆盖，传 `--overwrite` 允许覆盖

```bash
python -m gameplay_clipper.splice            # 默认不覆盖
python -m gameplay_clipper.splice --overwrite  # 允许覆盖
```

### highlight —— 检测精彩片段并生成集锦

从长录像中自动检测精彩时刻，裁剪成片段（可接 splice 拼成集锦）。
检测器可插拔，`highlight.py` 配置区用 `DETECTOR` 选择：

| 检测器 | 说明 | 适用 |
| --- | --- | --- |
| `coarse` | ffmpeg 音频能量 + 画面变化粗筛（零依赖） | 任何机器，效果一般 |
| `manual` | 手动标记（`detectors/manual.py` 配置区写时间点） | 最可靠的兜底 |
| `vlm` | 本地视觉大模型精判（`Qwen2.5-VL-7B-AWQ`） | 需 GPU（4080S 可跑），效果最好 |

- 流程：检测 → 按分数取 top-N（间隔约束）→ 复用 cut 无损裁剪到
  `highlight_output/` → `AUTO_SPLICE=True` 时自动调 splice 拼接
- 配置：`SOURCE`、`DETECTOR`、`HIGHLIGHT_COUNT`、`MIN_GAP`、
  `OUTPUT_DIR` / `OUTPUT_PREFIX`、`AUTO_SPLICE`
- vlm 检测器：抽帧间隔、判定阈值、prompt 等见 `detectors/vlm.py` 配置区；
  帧判定结果缓存到 `highlight_output/.vlm_cache.json`，中断可续跑

```bash
python -m gameplay_clipper.highlight            # 默认不覆盖
python -m gameplay_clipper.highlight --overwrite  # 允许覆盖
```

vlm 检测器需在真机（GPU）的 WSL2 环境安装依赖：
`pip install torch transformers qwen-vl-utils accelerate pillow`
（国内下载模型可设 `HF_ENDPOINT=https://hf-mirror.com`）

## 规划中的功能

| 子命令 | 功能 |
| --- | --- |
| 音频事件/击杀 OCR 检测器 | vlm 检测器的可插拔插件位（预留） |

## 开发环境

- Python 3.14（pyenv 管理，虚拟环境位于 `.venv/`）
- 包管理：标准 `pyproject.toml`（setuptools，src 布局）
- 视频处理：ffmpeg 命令行

```bash
# 激活虚拟环境（WSL 内）
source .venv/bin/activate
# 安装开发依赖
pip install -e ".[dev]"
```

## 目录结构

```
gameplay-clipper/
├── pyproject.toml          # 包元数据与工具配置
├── README.md
├── src/
│   └── gameplay_clipper/   # 主包（src 布局）
│       ├── cut.py          # 无损裁剪
│       ├── splice.py       # 拼接 + 转场
│       ├── highlight.py    # 精彩集锦编排
│       ├── common.py       # 共享工具（输出命名）
│       └── detectors/      # 精彩时刻检测器（coarse/manual/vlm）
└── tests/                  # 测试
```

## 约定

- **素材与产物不入库**：视频素材放在 `media/`，cut 输出放在 `cut_output/`，
  splice 输出放在 `connect_output/`，均已被 `.gitignore` 忽略；请勿提交大体积视频文件。
- 代码规范：ruff（E/F/I/UP/B 规则集，行宽 100）。
- 测试：pytest，测试文件位于 `tests/`。
