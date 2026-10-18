"""Run the audit hop and LiteLLM together; fail if either process exits."""
import os
import signal
import subprocess
import sys
import time


def main() -> int:
    children = []
    stopping = False

    def stop(_signum=None, _frame=None):
        nonlocal stopping
        stopping = True
        for child in children:
            if child.poll() is None:
                child.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        children.append(subprocess.Popen([sys.executable, "/opt/pilot/upstream_audit_proxy.py"]))
        children.append(subprocess.Popen(["litellm", "--config", "/opt/pilot/gateway_config.yaml",
                                          "--host", "0.0.0.0", "--port", "4000"]))
        while not stopping and all(child.poll() is None for child in children):
            time.sleep(0.5)
        return 0 if stopping else 1
    finally:
        stop()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()


if __name__ == "__main__":
    sys.exit(main())
