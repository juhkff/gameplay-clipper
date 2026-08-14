"""gameplay-clipper：游戏实况剪辑、拼接转场、精彩集锦制作工具集。

已实现：
- cut       从视频中无损裁剪片段（stream copy，不重编码），见 gameplay_clipper.cut
- splice    按文件名自然排序拼接视频并添加转场（xfade/acrossfade），见 gameplay_clipper.splice
- highlight 收集专业录制工具（Outplayed / Medal.tv / NVIDIA Highlights 等）的
            成品高光片段并拼接成集锦，见 gameplay_clipper.highlight

视频处理底层：ffmpeg 命令行（需系统安装 ffmpeg/ffprobe）。
"""

__version__ = "0.1.0"
