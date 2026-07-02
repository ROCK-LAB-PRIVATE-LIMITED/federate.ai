import subprocess
import sys

try:
    subprocess.run([sys.executable, '-c', 'import time; time.sleep(5)'], capture_output=True, text=True, timeout=1.0)
except subprocess.TimeoutExpired as e:
    print(f"Type of e.stdout: {type(e.stdout)}")
