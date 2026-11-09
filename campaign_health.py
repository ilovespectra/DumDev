from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import signal
import subprocess
import time


def heartbeat_expired(status: dict, pid: int, now: float, started: float) -> bool:
    if now - started <= 300:
        return False
    if status.get("worker_pid") != pid:
        return True
    stamp = status.get("updated_at")
    if not isinstance(stamp, str):
        return True
    try:
        last = datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return True
    return now - last > 300


def recovery_delay(failures: int) -> int | None:
    return (30, 60, 120)[failures - 1] if 1 <= failures <= 3 else None


def supervise(command: list[str], status_file: str, report) -> int:
    worker = None
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True
        if worker is not None and worker.poll() is None:
            worker.terminate()

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    restarts = 0
    try:
        while not stopping:
            started = time.time()
            worker = subprocess.Popen(command)
            while worker.poll() is None and not stopping:
                time.sleep(2)
                try:
                    status = json.loads(Path(status_file).read_text())
                except (FileNotFoundError, json.JSONDecodeError) as exc:
                    print(f"supervisor cannot read heartbeat: {exc}", flush=True)
                    status = {}
                if heartbeat_expired(status, worker.pid, time.time(), started):
                    print(f"worker {worker.pid} heartbeat stalled; restarting safely", flush=True)
                    worker.terminate()
                    try:
                        worker.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        worker.kill()
                        worker.wait()
                    break
            try:
                code = worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
                code = worker.wait()
            if stopping or code in (0, 2, 130):
                return 0 if stopping else code
            restarts += 1
            delay = recovery_delay(restarts)
            if delay is None:
                report(status_file, {"state": "needs_attention", "error": "Worker repeatedly crashed or stalled.",
                                     "note": "Automatic relaunch limit reached; inspect logs before restarting."})
                return 2
            report(status_file, {"state": "relaunch_wait", "supervisor_pid": os.getpid(),
                                 "worker_exit_code": code, "relaunches": restarts,
                                 "sleep_seconds": delay, "error": "Worker exited unexpectedly or heartbeat stalled.",
                                 "note": "Relaunching with the same ledger; uncertain submissions cannot be duplicated."})
            deadline = time.monotonic() + delay
            while time.monotonic() < deadline and not stopping:
                time.sleep(min(1, max(0, deadline - time.monotonic())))
        return 0
    finally:
        if worker is not None and worker.poll() is None:
            worker.terminate()
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
