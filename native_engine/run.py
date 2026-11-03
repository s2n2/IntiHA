#!/usr/bin/env python3
"""Launch upstream CLI and announce it; no device protocol or command proxy."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError
from urllib.request import Request, urlopen

WS_PORT = 12347
HEALTH_PORT = 12348
UPSTREAM_VERSION = "4.1.0"


def engine_command(level: str, *, bluetooth: bool = True) -> list[str]:
    if level not in {"error", "warn", "info", "debug"}:
        raise ValueError("Invalid log level")
    command = ["/usr/local/bin/intiface-engine", "--websocket-port", str(WS_PORT),
        "--websocket-use-all-interfaces", "--server-name", "IntiHA Native (Intiface 4.1.0)",
        "--max-ping-time", "10000", "--log", level]
    if bluetooth:
        command.append("--use-bluetooth-le")
    return command


def child_environment(env: dict[str, str]) -> dict[str, str]:
    return {key: val for key, val in env.items() if key not in {"SUPERVISOR_TOKEN", "HASSIO_TOKEN"}}


def check_port_free(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", port))


def supervisor(method: str, path: str, body: dict | None = None) -> dict:
    token = os.environ["SUPERVISOR_TOKEN"]
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    req = Request("http://supervisor" + path, data=json.dumps(body).encode() if body else None,
                  headers=headers, method=method)
    with urlopen(req, timeout=5) as response:
        payload = json.load(response)
    if payload.get("result") != "ok":
        raise RuntimeError("Supervisor request failed")
    return payload


def announce(stop: threading.Event, process: subprocess.Popen) -> None:
    if not os.environ.get("SUPERVISOR_TOKEN"):
        return
    while not stop.wait(5):
        if process.poll() is not None:
            return
        try:
            # Native app uses host networking. Supervisor's advertised host
            # address is reachable from Core; do not assume a container alias.
            network = supervisor("GET", "/network/info")["data"]
            host = next((addr.split("/")[0]
                         for interface in network.get("interfaces", [])
                         if interface.get("primary") and interface.get("enabled")
                         for addr in interface.get("ipv4", {}).get("address", [])), None)
            if not host:
                raise RuntimeError("Supervisor did not provide a primary IPv4 address")
            supervisor("POST", "/discovery", {"service": "intiha", "config": {
                "host": host, "port": WS_PORT, "transport": "native", "upstream": UPSTREAM_VERSION}})
            print("IntiHA Native: Home Assistant discovery announced", flush=True)
            return
        except (URLError, KeyError, RuntimeError, ValueError, OSError):
            # Never print a Request object or auth headers. Manual URL is always
            # available if Supervisor networking/discovery is restricted.
            print("IntiHA Native: discovery pending; manual address uses the HA host and port 12347", flush=True)
            if stop.wait(25):
                return


def main() -> int:
    level = "info"
    if Path("/data/options.json").exists():
        level = json.loads(Path("/data/options.json").read_text()).get("log_level", "info")
    for port in (WS_PORT, HEALTH_PORT):
        check_port_free(port)
    if not Path("/run/dbus/system_bus_socket").exists():
        print("Native Bluetooth needs the host D-Bus socket. ESPHome proxies are not native adapters.", file=sys.stderr)
        return 2
    command = engine_command(level)
    print("Starting unmodified upstream Intiface Engine 4.1.0 on port 12347. Native Bluetooth only.", flush=True)
    print("No client authentication: trusted LAN only; never forward this port to the internet.", flush=True)
    stop = threading.Event()
    process = subprocess.Popen(command, env=child_environment(dict(os.environ)), cwd="/data")

    class Health(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/health":
                self.send_error(404)
                return
            alive = process.poll() is None
            body = json.dumps({"engine_process_alive": alive, "upstream": UPSTREAM_VERSION,
                               "bluetooth_verified": False, "transport": "native"}).encode()
            self.send_response(200 if alive else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            return

    server = ThreadingHTTPServer(("0.0.0.0", HEALTH_PORT), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=announce, args=(stop, process), daemon=True).start()

    def terminate(_sig, _frame):
        stop.set()
        if process.poll() is None:
            process.terminate()
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    try:
        while process.poll() is None and not stop.wait(0.25):
            pass
        if process.poll() is None:
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        return process.returncode or 0
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        if process.poll() is None:
            process.terminate()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"IntiHA Native startup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
