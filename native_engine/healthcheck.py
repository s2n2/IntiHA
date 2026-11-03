#!/usr/bin/env python3
"""A process liveness check only; never occupy the engine's control socket."""
from urllib.request import urlopen
import json
import sys
try:
    with urlopen("http://127.0.0.1:12348/health", timeout=3) as response:
        result = json.load(response)
    sys.exit(0 if result.get("engine_process_alive") is True else 1)
except Exception:
    sys.exit(1)
