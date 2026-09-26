# health-monitor-api

A small, dependency-free health-monitoring API (Python standard library only).

It monitors a set of HTTP services by probing them on an interval in a background
thread and exposes their live status over HTTP.

## Run

```bash
./run.sh                     # listens on 127.0.0.1:8000
# or
PORT=9000 HOST=0.0.0.0 python3 main.py
```

Env vars: `PORT`, `HOST`, `HEALTH_INTERVAL` (probe seconds, default 30),
`HEALTH_TIMEOUT` (per-request timeout in seconds, default 5).

## Test

```bash
python3 test_main.py         # runs with the standard library only
```

## API

| Method | Path                              | Description                     |
| ------ | --------------------------------- | ------------------------------- |
| GET    | `/health`                         | Liveness + basic server status  |
| GET    | `/api/v1/services`                | List monitored services         |
| POST   | `/api/v1/services`                | Register a service to monitor   |
| DELETE | `/api/v1/services/<name>`         | Stop monitoring a service       |

Register a service:

```bash
curl -XPOST localhost:8000/api/v1/services \
  -H 'Content-Type: application/json' \
  -d '{"name":"example","url":"https://example.com","timeout":3}'

curl localhost:8000/api/v1/services
curl localhost:8000/health
```

A service record reports `healthy`, last `last_checked` timestamp,
`consecutive_failures`, and the last `error`.

## Layout

- `main.py` — server, monitoring loop, request routing
- `test_main.py` — standalone tests (no external deps)
