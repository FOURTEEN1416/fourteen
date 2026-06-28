import traceback
try:
    from api.run_api import app
    print("IMPORT OK")
except Exception as e:
    traceback.print_exc()
