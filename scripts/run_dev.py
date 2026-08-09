"""开发环境启动脚本：从任意 cwd 启动后端。"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(BASE_DIR)
sys.path.insert(0, BASE_DIR)

import uvicorn

uvicorn.run("app.main:app", host="127.0.0.1", port=8000)
