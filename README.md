# gameplay-clipper

游戏实况剪辑、拼接转场、精彩集锦制作工具集。

## 状态

**初始化阶段**：目前只有项目基础结构，尚无具体功能实现。
视频处理底层（ffmpeg 命令行 / moviepy 等 Python 库）待定，确定后再引入运行时依赖。

## 规划中的能力

| 子命令 | 功能 |
| --- | --- |
| `cut` | 从长录像中裁剪片段 |
| `splice` | 将多个片段拼接并添加转场 |
| `highlight` | 挑选片段并生成精彩集锦 |

以上仅为规划，实际形态（CLI / 库 API / 脚本）将在实现时确定。

## 开发环境

- Python 3.14（pyenv 管理，虚拟环境位于 `.venv/`）
- 包管理：标准 `pyproject.toml`（setuptools，src 布局）

```powershell
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
└── tests/                  # 测试
```

## 约定

- **素材与产物不入库**：视频素材放在 `media/`，输出放在 `output/`，两者均已被
  `.gitignore` 忽略；请勿提交大体积视频文件。
- 代码规范：ruff（E/F/I/UP/B 规则集，行宽 100）。
- 测试：pytest，测试文件位于 `tests/`。
