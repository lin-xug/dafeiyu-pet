import os
import tempfile

# 必须在导入 app 之前设置：测试用临时数据目录，别碰真实记忆
_tmp = tempfile.mkdtemp(prefix="dafeiyu-test-")
os.environ["DATA_DIR"] = _tmp
os.environ["MOCK_LLM"] = "1"
os.environ["GROUPS"] = ""
os.environ["API_KEY"] = "test-key"
