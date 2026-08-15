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
  - `EXACT` / `EXACT_CRF` / `EXACT_PRESET` / `EXACT_AUDIO_BITRATE`：帧级精确裁剪
    的重编码参数（见下）
- 输出命名 `clip-1.mp4`、`clip-2.mp4`……（扩展名沿用输入文件）；**默认不覆盖**，
  编号被占用时自动递增到下一个空位；传 `--overwrite` 则允许覆盖

```bash
python -m gameplay_clipper.cut            # 默认不覆盖
python -m gameplay_clipper.cut --overwrite  # 允许覆盖
python -m gameplay_clipper.cut --exact     # 帧级精确裁剪（重编码）
```

注意：默认 stream copy 的切割点会吸附到最近关键帧（GOP 边界），误差通常在
1 秒以内；这是无损裁剪的固有特性。**如需帧级精确切割**，传 `--exact`（或配置
`EXACT=True`）：改用输出端 seeking + libx264 重编码，切割点精确到帧，
代价是输出会重新编码、速度较慢（质量由 `EXACT_CRF` 控制，默认 23）。

### splice —— 拼接视频片段并添加转场

把指定文件夹下的视频**按文件名自然排序**（clip-1 < clip-2 < … < clip-10 < clip-100）
依次拼接，相邻片段之间插入转场（视频 `xfade` + 音频 `acrossfade`），输出到
`connect_output/`（默认）。

**NLE 式时间线切分**：按转场把片段切成主体/转场单元，各自独立编码（任意时刻
仅 1-2 路输入），最后 `concat -c copy` 无损合并——内存峰值与片段总数无关，
片段再多也不会 OOM；单元之间并行编码，每段内容只编码一次。

- 依赖 `ffmpeg` 与 `ffprobe`；转场需要重编码
- 配置写死在代码顶部配置区（`src/gameplay_clipper/splice.py`）：
  - `INPUT_DIR`：待拼接视频所在文件夹
  - `OUTPUT_DIR` / `OUTPUT_PREFIX`：输出目录（默认 `connect_output`）与文件名前缀
  - `TRANSITION`：转场效果。填具体名字（如 `"fade"`）则所有转场均用该效果；
    填 `"random"` 则每对相邻片段从 `TRANSITION_POOL` 中随机选一个；
    填 `"none"` 则**不添加转场**，纯拼接（concat 无缝衔接，成片时长 = 各段时长之和）
  - `TRANSITION_POOL`：`random` 模式下的候选转场集合（xfade 支持 58 种，
    全部列表见配置区注释）
  - `TRANSITION_DURATION`：转场时长（秒），每段视频时长必须不小于它
  - `FADE_IN` / `FADE_OUT`：成片首尾淡入淡出（秒，设为 0 关闭；默认 0.5s / 1.0s）
  - `ENCODER`：视频编码器。`"auto"`（默认）自动探测 `h264_nvenc`（NVIDIA 硬件
    编码，快 5-10 倍，同画质体积略大），不可用时回退 `libx264`；也可强制
    `"libx264"` / `"h264_nvenc"`
  - `CRF`：质量参数（libx264 的 crf / NVENC 的 cq；默认 26，越小越好，
    23 近无损 / 26 推荐 / 28 省体积）
  - `PRESET`：编码速度预设（libx264: ultrafast..veryslow；NVENC: p1..p7）
  - `LOUDNESS` / `LOUDNESS_TARGET`：响度归一化（EBU R128 两遍式 loudnorm，
    默认开启，目标 -16 LUFS）。多段素材音量不一致时统一听感，无音轨单元
    自动跳过
- 片段分辨率/帧率不同会自动统一到最高规格；无音轨的片段用静音填充
- 输出命名 `connect-1.mp4`、`connect-2.mp4`……默认不覆盖，传 `--overwrite` 允许覆盖

```bash
python -m gameplay_clipper.splice            # 默认不覆盖
python -m gameplay_clipper.splice --overwrite  # 允许覆盖
```

### highlight —— 收集高光片段并生成集锦

收集专业录制工具（Outplayed / Medal.tv / NVIDIA Highlights 等）自动检测并
录制的**成品高光片段**，复制到 `highlight_output/`，可选自动 splice 拼接成集锦。
游戏事件驱动的高光准确度远高于算法检测，且零检测成本。

- 流程：收集成品高光 → 复制到 `highlight_output/` → `AUTO_SPLICE=True` 时
  自动调 splice 拼接（收集到多少就处理多少，不设上限）
- 高光目录配置（`highlight.py` 顶部配置区）：
  - `CLIPS_DIRS`：一个或多个高光目录（支持同时配置多个工具）
  - `RECURSIVE` / `FILE_PATTERNS`：扫描方式与文件后缀
  - `EXCLUDE_NAME_PATTERNS`：按文件名正则排除非高光文件（如整场录像）
- 集锦配置（`highlight.py` 顶部配置区，同一文件）：
  - `OUTPUT_DIR` / `OUTPUT_PREFIX`：输出目录与文件名前缀
  - `AUTO_SPLICE`：收集后自动拼接（输出到 `connect_output/`）
- 转场效果配置在 `splice.py`（`TRANSITION` / `TRANSITION_POOL` / `TRANSITION_DURATION`）
- 片段直接复制不重编码；WSL 中 Windows 路径写作 `/mnt/c/Users/<user>/Videos/...`

```bash
python -m gameplay_clipper.highlight            # 默认不覆盖
python -m gameplay_clipper.highlight --overwrite  # 允许覆盖
```

### bgm —— 背景音乐混音 + 自动闪避

给主视频（通常为 splice 产出的集锦）配上 BGM，可开启**自动闪避（ducking）**：
用主视频自己的音轨作为侧链信号（`sidechaincompress`），游戏音效响起时自动压低
BGM，音效安静时恢复——BGM 不盖住游戏声。视频流不重编码（`-c:v copy`），
只重编码混音后的音频，速度很快。

- 依赖 `ffmpeg` 与 `ffprobe`；需 ffmpeg 6.0+（`amix` 的 `normalize=0` 选项）
- 配置写死在代码顶部配置区（`src/gameplay_clipper/bgm.py`）：
  - `VIDEO_PATH` / `BGM_PATH`：主视频与背景音乐（默认 `connect_output/connect-1.mp4`
    与 `media/bgm.mp3`）
  - `BGM_VOLUME`：BGM 音量（1.0 = 原音量，建议 0.2~0.4）
  - `DUCKING`：自动闪避开关；`DUCK_THRESHOLD` / `DUCK_RATIO` / `DUCK_ATTACK` /
    `DUCK_RELEASE` / `DUCK_MAKEUP`：闪避的压缩参数
  - `FADE_IN` / `FADE_OUT`：BGM 首尾淡入淡出（秒）；`LOOP_BGM`：BGM 短于视频时循环
  - `AUDIO_BITRATE`：混音后音频码率
- 主视频无音轨时自动退化为纯 BGM 输出（无侧链可闪避）
- 输出命名 `bgm-1.mp4`……（扩展名沿用主视频），默认不覆盖

```bash
python -m gameplay_clipper.bgm            # 默认不覆盖
python -m gameplay_clipper.bgm --overwrite  # 允许覆盖
```

### compress —— 压缩转码

把大体积录屏压到适合上传/存档的体积：x264 + crf 重编码（参数与 splice 同风格），
可选缩放分辨率与统一帧率。输入可以是单个文件，也可以是目录（目录下所有视频
按文件名自然排序逐个压缩）。

- 依赖 `ffmpeg`；配置写死在代码顶部配置区（`src/gameplay_clipper/compress.py`）：
  - `INPUT`：待压缩的视频文件或目录（默认 `media/video.mp4`）
  - `OUTPUT_DIR` / `OUTPUT_PREFIX`：输出目录与文件名前缀
  - `CRF` / `PRESET`：x264 质量与速度（crf 越小越好，18 高质量 / 23 均衡 / 28 省体积）
  - `AUDIO_BITRATE`：重编码后的音频码率
  - `RESIZE`：目标分辨率（如 `"1280:720"`，留空保持原分辨率）
  - `FPS`：统一帧率（如 30，0 = 保持原帧率）
- 压缩完成后打印输出体积与压缩率
- 处理时**实时显示进度百分比**（基于 ffprobe 探测时长 + ffmpeg `-progress`；
  未安装 ffprobe 或探测失败时自动降级为静默执行）
- 输出命名 `compress-1.mp4`、`compress-2.mp4`……默认不覆盖

```bash
python -m gameplay_clipper.compress            # 默认不覆盖
python -m gameplay_clipper.compress --overwrite  # 允许覆盖
```

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
│       ├── cut.py          # 无损裁剪（可选 --exact 帧级精确重编码）
│       ├── splice.py       # 拼接 + 转场 + 首尾淡入淡出
│       ├── highlight.py    # 精彩集锦（收集高光 → 复制 → 可选拼接）
│       ├── bgm.py          # 背景音乐混音 + 自动闪避
│       ├── compress.py     # 压缩转码（x264 + crf）
│       └── common.py       # 共享工具（输出命名）
└── tests/                  # 测试
```

## 约定

- **素材与产物不入库**：视频素材放在 `media/`，cut 输出放在 `cut_output/`，
  splice 输出放在 `connect_output/`，highlight 收集产物放在 `highlight_output/`，
  bgm 输出放在 `bgm_output/`，compress 输出放在 `compress_output/`，
  均已被 `.gitignore` 忽略；请勿提交大体积视频文件。
- 代码规范：ruff（E/F/I/UP/B 规则集，行宽 100）。
- 测试：pytest，测试文件位于 `tests/`。
