"""Python side of the code check: runs check.mjs (acorn, in Node) on model-written spell code.

check(code, kind) returns (True, guarded_code) or (False, [errors]). Without Node, every piece of code is refused:
the check fails closed, and spells fall back to plain shapes.
"""

import hashlib
import json
import shutil
import subprocess
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
RULES = json.loads((HERE / "rules.json").read_text(encoding="utf-8"))
_cache = {}
_lock = threading.Lock()


def check(code, kind):
    key = hashlib.sha256(f"{kind}\0{code}".encode()).hexdigest()
    with _lock:
        if key in _cache:
            return _cache[key]
    node = shutil.which("node")
    if not node:
        return False, ["Node.js isn't installed, so new code can't be checked"]
    try:
        proc = subprocess.run(
            [node, str(HERE / "check.mjs")],
            input=json.dumps({"code": code, "kind": kind}), capture_output=True, text=True, timeout=10,
        )
        result = json.loads(proc.stdout)
    except (subprocess.TimeoutExpired, ValueError) as err:
        return False, [f"code check failed to run: {err}"]
    answer = (True, result["code"]) if result.get("ok") else (False, result.get("errors") or ["rejected"])
    with _lock:
        _cache[key] = answer
    return answer
