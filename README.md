# gameplay-clipper

游戏实况剪辑、拼接转场、精彩集锦制作工具集。

## 已实现功能

### cut —— 无损裁剪视频片段

从指定视频中按 (起点, 终点) 截取任意多段，**stream copy 不重编码**，画质、音质与源完全一致。

- 依赖系统安装的 `ffmpeg`（Ubuntu: `sudo apt install ffmpeg`）
- 裁剪配置写死在代码顶部配置区（`src/gameplay_clipper/cut.py`），无需终端输入：
  - `VIDEO_PATH`：待裁剪视频（默认为相对当前运行目录的路径）
  - `CLIPS`：`[(起点, 终点), ...]` 列表，段数不限；时间格式 `HH:MM:SS` / `MM:SS` / 秒数
  - `OUTPUT_DIR` / `OUTPUT_PREFIX`：输出目录与文件名前缀
- 输出命名 `clip-1.mp4`、`clip-2.mp4`……（扩展名沿用输入文件）；**默认不覆盖**，
  编号被占用时自动递增到下一个空位；传 `--overwrite` 则允许覆盖

运行方式：

```bash
# WSL 内，激活虚拟环境并安装后：
python -m gameplay_clipper.cut            # 默认不覆盖
python -m gameplay_clipper.cut --overwrite  # 允许覆盖
```

注意：stream copy 的切割点会吸附到最近关键帧（GOP 边界），误差通常在 1 秒以内；
这是无损裁剪的固有特性。如需帧级精确切割，需要重编码（尚未实现）。

## 规划中的功能

| 子命令 | 功能 |
| --- | --- |
| `splice` | 将多个片段拼接并添加转场 |
| `highlight` | 挑选片段并生成精彩集锦 |

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
│       └── cut.py          # 无损裁剪
└── tests/                  # 测试
```

## 约定

- **素材与产物不入库**：视频素材放在 `media/`，输出放在 `output/`，两者均已被
  `.gitignore` 忽略；请勿提交大体积视频文件。
- 代码规范：ruff（E/F/I/UP/B 规则集，行宽 100）。
- 测试：pytest，测试文件位于 `tests/`。
