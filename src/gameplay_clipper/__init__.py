"""gameplay-clipper：游戏实况剪辑、拼接转场、精彩集锦制作工具集。

已实现：
- cut       从视频中无损裁剪片段（stream copy，不重编码），见 gameplay_clipper.cut

规划中：
- splice    将多个片段拼接并添加转场
- highlight  挑选/生成精彩集锦

视频处理底层：ffmpeg 命令行（无损裁剪依赖 stream copy，需系统安装 ffmpeg）。
"""

__version__ = "0.1.0"
