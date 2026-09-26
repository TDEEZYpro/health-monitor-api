import json
import threading
import urllib.error
import urllib.request

import main


def _request(method, path, body=None):
    server = main.build_server("127.0.0.1", 0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(url, data=body, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:  # non-2xx still returns a body
        return exc.code, exc.read().decode()
    finally:
        server.server_close()


def _reset_monitor():
    main.monitor.stop()
    main.monitor._services.clear()


def test_liveness_endpoint():
    _reset_monitor()
    status, text = _request("GET", "/health")
    assert status == 200
    data = json.loads(text)
    assert data["status"] == "healthy"
    assert "uptime_s" in data


def test_add_and_list_services():
    _reset_monitor()
    body = json.dumps({"name": "db", "url": "http://127.0.0.1:1/x"}).encode()
    status, text = _request("POST", "/api/v1/services", body)
    assert status == 201
    assert json.loads(text)["name"] == "db"

    status, text = _request("GET", "/api/v1/services")
    services = json.loads(text)["services"]
    assert any(s["name"] == "db" for s in services)


def test_unknown_path_returns_404():
    status, _ = _request("GET", "/nope")
    assert status == 404


def _run_standalone() -> int:
    """Run all test_* functions without requiring pytest."""
    import inspect

    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                _reset_monitor()
                fn()
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"FAIL {name}: {exc!r}")
            else:
                passed += 1
                print(f"ok   {name}")
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_standalone())
