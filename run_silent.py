import os
import sys
import traceback

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

log_file = os.path.join(BASE_DIR, "scanner_service.log")
try:
    log_fp = open(log_file, "a", encoding="utf-8", buffering=1)
    sys.stdout = log_fp
    sys.stderr = log_fp
except Exception:
    pass

try:
    import sts_live_scanner
    sys.argv = ["sts_live_scanner.py", "--server"]
    sts_live_scanner.main()
except Exception as e:
    with open(os.path.join(BASE_DIR, "silent_error.log"), "w", encoding="utf-8") as ef:
        traceback.print_exc(file=ef)
