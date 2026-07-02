import subprocess
import time
import sys

# Start a process that prints something then sleeps
proc = subprocess.Popen([sys.executable, '-c', 'import sys, time; sys.stdout.write("hello world"); sys.stdout.flush(); time.sleep(5)'], 
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

try:
    # Try to communicate with a short timeout
    stdout, stderr = proc.communicate(timeout=1.0)
except subprocess.TimeoutExpired as e:
    print(f"Caught TimeoutExpired")
    print(f"Type of e.stdout: {type(e.stdout)}")
    print(f"Value of e.stdout: {repr(e.stdout)}")

proc.kill()
