"""Test loading api.run_api:app the way uvicorn does."""
import os
import sys
import traceback

# 不预设环境变量，让 api/run_api.py 自己 load_dotenv
try:
    from api.run_api import app
    print("APP LOAD OK")
except ModuleNotFoundError as e:
    print(f"MODULE NOT FOUND: {e}")
    traceback.print_exc()
    sys.exit(1)
except SystemExit as e:
    print(f"SYS EXIT: code={e.code}")
    sys.exit(e.code)
except Exception as e:
    print(f"EXCEPTION: {type(e).__name__}: {e}")
    traceback.print_exc()
    sys.exit(1)
