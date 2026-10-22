"""测试包：导入任何 app 模块之前，先把数据目录切到临时目录。"""

import os
import tempfile

if not os.environ.get("DAFEIYU_TEST_TMP"):
    _tmp = tempfile.mkdtemp(prefix="dafeiyu-test-")
    os.environ["DAFEIYU_TEST_TMP"] = _tmp
    os.environ["DATA_DIR"] = _tmp
    os.environ["MOCK_LLM"] = "1"
    os.environ["GROUPS"] = ""
    os.environ["API_KEY"] = "test-key"
    # 语音默认关掉：避免测试继承线上 .env 的 VOICE_* 配置（改配置时会让测试偶发飘）
    os.environ["VOICE_ENABLED"] = "0"
    os.environ["DASHSCOPE_API_KEY"] = ""
    os.environ["VOICE_CHANCE"] = "0"
    os.environ["VOICE_CMD_USERS"] = ""
