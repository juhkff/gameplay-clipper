"""冒烟测试：确认包可导入、版本号存在。"""

import gameplay_clipper


def test_import_and_version():
    assert isinstance(gameplay_clipper.__version__, str)
    assert gameplay_clipper.__version__
