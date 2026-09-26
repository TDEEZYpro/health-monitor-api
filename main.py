"""A small health-monitor API.

Monitors a configurable set of services by probing them on an interval and
exposes their status over HTTP. Uses only the Python standard library.

Endpoints
    GET  /health              Liveness + basic liveness checks (no deps)
    GET  /api/v1/services      List monitored services with last-known status
    POST /api/v1/services      Register a service to monitor
    DELETE /api/v1/services/<name>  Stop monitoring a service
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse, unquote

DEFAULT_INTERVAL = float(os.getenv("HEALTH_INTERVAL", "30"))  # seconds
DEFAULT_TIMEOUT = float(os.getenv("HEALTH_TIMEOUT", "5"))     # seconds


@dataclass
class ServiceProbe:
    name: str
    url: str
    method: str = "GET"
    timeout: float = DEFAULT_TIMEOUT
    expected_status: int = 200

    def run(self) -> dict[str, Any]:
        start = time.monotonic()
        try:
            req = urllib.request.Request(self.url, method=self.method)
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status = resp.status
                ok = status == self.expected_status
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            return {"ok": False, "status_code": None, "error": str(exc),
                    "latency_ms": round((time.monotonic() - start) * 1000, 1)}
        return {"ok": ok, "status_code": status, "error": None,
                "latency_ms": round((time.monotonic() - start) * 1000, 1)}


@dataclass
class ServiceRecord:
    probe: ServiceProbe
    healthy: bool = True
    last_checked: float = field(default_factory=time.time)
    consecutive_failures: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.probe.name,
            "url": self.probe.url,
            "method": self.probe.method,
            "healthy": self.healthy,
            "last_checked": self.last_checked,
            "consecutive_failures": self.consecutive_failures,
            "error": self.error,
        }


class Monitor:
    """Owns the set of monitored services and probes them on an interval."""

    def __init__(self, interval: float = DEFAULT_INTERVAL) -> None:
        self.interval = interval
        self._services: dict[str, ServiceRecord] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- service registry ---------------------------------------------------
    def add(self, probe: ServiceProbe) -> ServiceRecord:
        with self._lock:
            rec = self._services.get(probe.name)
            if rec is None:
                rec = ServiceRecord(probe=probe)
                self._services[probe.name] = rec
            else:
                rec.probe = probe
            return rec

    def remove(self, name: str) -> bool:
        with self._lock:
            return self._services.pop(name, None) is not None

    def list(self) -> list[ServiceRecord]:
        with self._lock:
            return list(self._services.values())

    # -- probing ------------------------------------------------------------
    def probe_one(self, rec: ServiceRecord) -> None:
        result = rec.probe.run()
        rec.last_checked = time.time()
        rec.error = result["error"]
        if result["ok"]:
            rec.healthy = True
            rec.consecutive_failures = 0
        else:
            rec.healthy = False
            rec.consecutive_failures += 1

    def probe_all(self) -> None:
        with self._lock:
            records = list(self._services.values())
        for rec in records:
            self.probe_one(rec)

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self.probe_all()


monitor = Monitor()


class Handler(BaseHTTPRequestHandler):
    server_version = "HealthMonitor/1.0"

    # -- helpers ------------------------------------------------------------
    def _send(self, code: int, payload: Any) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code: int, message: str) -> None:
        self._send(code, {"error": message})

    def _read_json(self) -> dict | None:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._error(400, f"invalid JSON: {exc}")
            return None
        if not isinstance(data, dict):
            self._error(400, "expected a JSON object")
            return None
        return data

    def _path_parts(self) -> list[str]:
        return [p for p in urlparse(self.path).path.split("/") if p]

    # -- routing ------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 (stdlib signature)
        parts = self._path_parts()
        if parts == ["health"]:
            return self._handle_health()
        if parts == ["api", "v1", "services"]:
            return self._handle_list_services()
        self._error(404, "not found")

    def do_POST(self) -> None:  # noqa: N802
        parts = self._path_parts()
        if parts == ["api", "v1", "services"]:
            data = self._read_json()
            if data is None:
                return
            return self._handle_add_service(data)
        self._error(404, "not found")

    def do_DELETE(self) -> None:  # noqa: N802
        parts = self._path_parts()
        if len(parts) == 4 and parts[:3] == ["api", "v1", "services"]:
            name = urllib.parse.unquote(parts[3])
            existed = monitor.remove(name)
            return self._send(204 if existed else 404, {})
        self._error(404, "not found")

    # -- handlers -----------------------------------------------------------
    def _handle_health(self) -> None:
        self._send(200, {
            "status": "healthy",
            "uptime_s": round(time.time() - START_TIME, 1),
            "services_monitored": len(monitor.list()),
            "interval_s": monitor.interval,
        })

    def _handle_add_service(self, data: dict | None) -> None:
        if data is None:
            return
        try:
            probe = ServiceProbe(
                name=data["name"],
                url=data["url"],
                method=str(data.get("method", "GET")).upper(),
                timeout=float(data.get("timeout", DEFAULT_TIMEOUT)),
                expected_status=int(data.get("expected_status", 200)),
            )
        except (KeyError, ValueError, TypeError) as exc:
            return self._error(400, f"invalid payload: {exc}")
        record = monitor.add(probe)
        monitor.probe_one(record)  # immediate first probe
        self._send(201, record.to_dict())

    def _handle_list_services(self) -> None:
        self._send(200, {"services": [r.to_dict() for r in monitor.list()]})

    def log_message(self, *args: Any) -> None:  # silence default stderr logging
        pass


START_TIME = time.time()


def build_server(host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), Handler)


def main() -> None:
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    monitor.start()
    httpd = build_server(host, port)
    print(f"health-monitor listening on http://{host}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        monitor.stop()
        httpd.server_close()


if __name__ == "__main__":
    main()
