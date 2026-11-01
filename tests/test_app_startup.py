"""Exercise the real app entrypoint with a mock Supervisor API and engine process."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

RUN_SCRIPT = Path(__file__).resolve().parents[1] / "engine" / "run.sh"


def wait_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("App entrypoint did not reach the expected state")


def events(directory):
    path = directory / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


@pytest.fixture
def app_process(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    scripts = {
        "intiha-engine": """
import json, os, pathlib, signal, sys, time
root = pathlib.Path(os.environ["INTIHA_APP_TEST_DIR"])
(root / "engine.json").write_text(json.dumps({
    "pid": os.getpid(),
    "credentials": {key: value for key, value in os.environ.items() if key in (
        "SUPERVISOR_TOKEN", "HASSIO_TOKEN", "INTIHA_CLIENT_TOKEN", "INTIHA_BRIDGE_TOKEN"
    )},
}))
if "TEST_ENGINE_EXIT" in os.environ:
    sys.exit(int(os.environ["TEST_ENGINE_EXIT"]))
def stop(*args):
    (root / "stopped").write_text("yes")
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
while True:
    time.sleep(1)
""",
        "curl": """
import json, os, pathlib, sys
root = pathlib.Path(os.environ["INTIHA_APP_TEST_DIR"])
args = sys.argv[1:]
url = args[-1]
header = sys.stdin.read() if "@-" in args else ""
with (root / "events.jsonl").open("a") as stream:
    stream.write(json.dumps({"args": args, "header": header}) + "\\n")
if url.endswith("/health"):
    print('{"status":"ok"}')
elif url.endswith("/addons/self/info"):
    counter = root / "attempts"
    attempts = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(attempts))
    if attempts <= int(os.environ.get("TEST_API_FAILURES", "0")):
        sys.exit(22)
    print('{"result":"ok","data":{"hostname":"abcdef01-intiha-engine"}}')
elif url.endswith("/discovery"):
    (root / "announced.json").write_text(args[args.index("--data") + 1])
    print('{"result":"ok","data":{"uuid":"test-announcement"}}')
else:
    sys.exit(2)
""",
        "sleep": """
import os, pathlib, sys, time
root = pathlib.Path(os.environ["INTIHA_APP_TEST_DIR"])
with (root / "sleeps").open("a") as stream:
    stream.write(sys.argv[1] + "\\n")
time.sleep(0.03)
""",
    }
    for name, body in scripts.items():
        path = bin_dir / name
        path.write_text(f"#!{sys.executable}\n{body}")
        path.chmod(0o755)

    @contextmanager
    def start(**environment):
        env = {
            key: value
            for key, value in os.environ.items()
            if key
            not in (
                "SUPERVISOR_TOKEN",
                "HASSIO_TOKEN",
                "INTIHA_CLIENT_TOKEN",
                "INTIHA_BRIDGE_TOKEN",
            )
        }
        env.update(
            PATH=f"{bin_dir}:{os.environ['PATH']}",
            INTIHA_APP_TEST_DIR=str(tmp_path),
            **environment,
        )
        process = subprocess.Popen(
            ["/bin/sh", str(RUN_SCRIPT)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            yield process, tmp_path
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate(timeout=5)
                raise

    return start


def test_standalone_starts_with_no_configuration_and_preserves_exit_status(app_process):
    with app_process(TEST_ENGINE_EXIT="23") as (process, directory):
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 23, stderr
        assert json.loads((directory / "engine.json").read_text())["credentials"] == {}
        assert events(directory) == []
        assert "token" not in (stdout + stderr).lower()


def test_app_announces_internal_address_and_retries_supervisor_startup(app_process):
    credential = "test-platform-credential"
    with app_process(SUPERVISOR_TOKEN=credential, TEST_API_FAILURES="2") as (process, directory):
        wait_until(lambda: (directory / "announced.json").exists())
        assert process.poll() is None
        assert json.loads((directory / "announced.json").read_text()) == {
            "service": "intiha",
            "config": {"host": "abcdef01-intiha-engine", "port": 12346},
        }
        assert (directory / "sleeps").read_text().splitlines() == ["2", "4"]
        assert json.loads((directory / "engine.json").read_text())["credentials"] == {}
        for event in events(directory):
            assert credential not in " ".join(event["args"])
            if "http://supervisor/" in event["args"][-1]:
                assert event["header"] == f"Authorization: Bearer {credential}\n"
        process.terminate()
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, stderr
        assert (directory / "stopped").exists()
        assert credential not in stdout + stderr


def test_unavailable_discovery_never_blocks_engine_and_stops_with_it(app_process):
    with app_process(SUPERVISOR_TOKEN="test-platform-credential", TEST_API_FAILURES="999") as (
        process,
        directory,
    ):
        wait_until(lambda: (directory / "sleeps").exists())
        assert (directory / "engine.json").exists()
        assert process.poll() is None
        process.terminate()
        _, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, stderr
        assert (directory / "stopped").exists()
        calls_after_exit = len(events(directory))
        time.sleep(0.15)
        assert len(events(directory)) == calls_after_exit
