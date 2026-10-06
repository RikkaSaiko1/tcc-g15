import base64
import datetime
import json
import logging
import socket
import sys
import threading
import time
from typing import Optional

import bottle
from bottle import Bottle, request, response, HTTPResponse
import waitress

import psutil

# Suppress waitress request logging
logging.getLogger('waitress').setLevel(logging.WARNING)

from Web.WebBridge import WebBridge

# Limit concurrent SSE connections to avoid thread exhaustion
_SSE_SEMAPHORE = threading.Semaphore(4)

# Process collection is deliberately NOT cached: the dashboard is meant to show
# what is happening right now, and a full walk of the process table already
# runs in well under a second. The SSE stream pushes on its own interval, which
# is the pacing that matters.

# psutil's cpu_percent() needs two samples: it divides CPU time by the elapsed
# time since the previous call for that process. A freshly constructed Process
# object therefore always reports 0.0. process_iter() keeps its own Process
# cache, so priming it once at import time makes later reads return real
# values instead of a CPU column full of zeros.
_PROC_ATTRS_CPU = ['pid', 'name', 'memory_info', 'memory_percent', 'cpu_percent']


def _prime_cpu_counters() -> None:
    try:
        for _ in psutil.process_iter(attrs=_PROC_ATTRS_CPU):
            pass
    except Exception:
        # Priming is best-effort: without it CPU reads stay 0, but the
        # process list still works.
        pass


# Prime once, in the background so importing this module stays fast.
threading.Thread(target=_prime_cpu_counters, daemon=True).start()

FAVICON_BYTES: bytes | None = None
_INDEX_HTML_BYTES: bytes | None = None


def _get_index_html_bytes() -> bytes:
    global _INDEX_HTML_BYTES
    if _INDEX_HTML_BYTES is None:
        _INDEX_HTML_BYTES = _build_index_html().encode("utf-8")
    return _INDEX_HTML_BYTES


def _get_favicon_bytes() -> bytes:
    global FAVICON_BYTES
    if FAVICON_BYTES is None:
        import pathlib
        # Candidate locations: frozen bundle first, then the repo tree.
        # Note icons/ lives at the REPO ROOT (not under src/), so from
        # src/Web/WebServer.py we must climb two levels to reach it.
        candidates = []
        if hasattr(sys, '_MEIPASS'):
            candidates.append(pathlib.Path(sys._MEIPASS) / "icons" / "gaugeIcon.ico")
        _here = pathlib.Path(__file__).resolve()
        candidates.append(_here.parent.parent.parent / "icons" / "gaugeIcon.ico")  # repo root
        candidates.append(_here.parent.parent / "icons" / "gaugeIcon.ico")

        FAVICON_BYTES = b""
        for ico_path in candidates:
            try:
                FAVICON_BYTES = ico_path.read_bytes()
                break
            except OSError:
                continue
        if not FAVICON_BYTES:
            logging.getLogger(__name__).warning(
                "favicon not found; tried: %s", [str(c) for c in candidates]
            )
    return FAVICON_BYTES


_RESP_OK = b'{"ok":true}'
_RESP_400_INVALID_JSON = b'{"ok":false,"error":"Invalid JSON"}'
_RESP_400_INVALID_MODE = b'{"ok":false,"error":"Invalid mode"}'
_RESP_400_MISSING_SPEED = b'{"ok":false,"error":"Missing gpu_speed or cpu_speed"}'
_RESP_400_INVALID_SPEED = b'{"ok":false,"error":"Invalid speed values"}'
_RESP_404 = b'{"ok":false,"error":"Not found"}'
_RESP_401 = b'{"ok":false,"error":"Unauthorized"}'


# 'exe' and 'create_time' are expensive on Windows (per-process system calls)
# and are fetched in pass 2 for the finalists only. The attribute lists live
# near the top of this module, next to the CPU priming logic that uses them.

def _collect_processes(sort_by: str = "cpu", num: int = 20) -> list[dict]:
    """Collect top N processes sorted by cpu or memory.

    Strategy: one pass collecting everything that is cheap, then fetch the
    genuinely expensive attrs (exe, create_time) only for the finalists.

    Note on cpu_percent: it is read via process_iter so psutil's per-process
    cache is reused; a throwaway Process object would always report 0.0.
    """
    # Pass 1: single scan. cpu_percent is included because the walk is the
    # expensive part — asking for one more attribute costs almost nothing,
    # whereas a second walk would double the time.
    all_procs = []
    for p in psutil.process_iter(attrs=_PROC_ATTRS_CPU):
        try:
            mem_info = p.info.get('memory_info')
            if mem_info is None:
                continue
            pid = p.info.get('pid', 0)
            name = p.info.get('name', '') or ''
            # PID 0 is the System Idle Process. Its cpu_percent is the share of
            # time the CPU was idle, so it always tops a "busiest first" list
            # while telling the user nothing useful.
            if pid == 0:
                continue
            cpu = p.info.get('cpu_percent', 0.0) or 0.0
            all_procs.append({
                "proc": p,
                "pid": pid,
                "name": name,
                "memory_mb": round(mem_info.rss / (1024 * 1024), 1),
                "memory_percent": round(p.info.get('memory_percent', 0) or 0, 2),
                # psutil reports per-core percentages, so a busy process can
                # exceed 100 on a multi-core machine. Left as-is to match the
                # original behaviour; the UI renders it as a relative bar.
                "cpu_percent": round(cpu, 1),
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    # Pre-sort by the requested key, keeping 2x candidates so the final sort
    # after enrichment still has room to reorder.
    key = "cpu_percent" if sort_by == "cpu" else "memory_percent"
    all_procs.sort(key=lambda x: x[key], reverse=True)
    candidates = all_procs[:num * 2]

    # Pass 2: expensive attrs, finalists only.
    for item in candidates:
        p = item['proc']
        try:
            ct = p.create_time()
            item['create_time'] = datetime.datetime.fromtimestamp(ct).strftime("%Y-%m-%d %H:%M:%S") if ct else ""
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, ValueError):
            item['create_time'] = ""
        try:
            item['exe'] = p.exe() or ''
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            item['exe'] = ''

    # Pass 3: num_threads for finalists (cpu_percent already collected).
    for item in candidates:
        p = item.pop('proc')
        try:
            item['threads'] = p.num_threads()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            item['threads'] = 0

    candidates.sort(key=lambda x: x[key], reverse=True)
    return candidates[:num]


def _parse_process_params(params) -> tuple:
    """Parse sort_by and num query parameters for the /api/processes endpoints."""
    sort_by = params.get('type', 'cpu')
    if sort_by not in ('cpu', 'memory'):
        sort_by = 'cpu'
    try:
        num = int(params.get('num', '20'))
    except (ValueError, TypeError):
        num = 20
    num = max(1, min(100, num))
    return sort_by, num


# ---------------------------------------------------------------------------
# Bottle app factory
# ---------------------------------------------------------------------------

_RESP_429_TOO_MANY = b'{"ok":false,"error":"Too many failed attempts, try again later"}'


def _create_app(bridge: WebBridge,
                auth_enabled: bool = False,
                auth_user: str = "",
                auth_pass: str = "") -> Bottle:
    """Create and configure a Bottle application."""

    app = Bottle()

    # -- Auth rate limiting state --
    _auth_failures: dict[str, list[float]] = {}  # ip -> list of failure timestamps
    _AUTH_MAX_FAILURES = 5
    _AUTH_LOCKOUT_SEC = 60

    # -- CORS hook (runs after every request) --
    @app.hook('after_request')
    def _cors():
        if response.content_type != 'text/event-stream':
            response.set_header('Access-Control-Allow-Origin', '*')
            response.set_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            response.set_header('Access-Control-Allow-Headers', 'Content-Type')

    # -- Auth hook (runs before every request) --
    @app.hook('before_request')
    def _auth():
        if not auth_enabled:
            return
        client_ip = request.get_header('X-Forwarded-For', request.remote_addr)

        # Rate limiting check
        now = time.time()
        if client_ip in _auth_failures:
            # Prune old entries
            _auth_failures[client_ip] = [t for t in _auth_failures[client_ip] if now - t < _AUTH_LOCKOUT_SEC]
            if len(_auth_failures[client_ip]) >= _AUTH_MAX_FAILURES:
                raise HTTPResponse(
                    body=_RESP_429_TOO_MANY,
                    status=429,
                    headers={
                        'Content-Type': 'application/json',
                        'Retry-After': str(_AUTH_LOCKOUT_SEC),
                    },
                )

        auth_header = request.get_header('Authorization', '')
        if not auth_header.startswith('Basic '):
            _auth_failures.setdefault(client_ip, []).append(now)
            raise HTTPResponse(
                body=_RESP_401,
                status=401,
                headers={
                    'Content-Type': 'application/json',
                    'WWW-Authenticate': 'Basic realm="TCC-G15"',
                },
            )
        try:
            decoded = base64.b64decode(auth_header[6:]).decode('utf-8')
            user, passwd = decoded.split(':', 1)
        except Exception:
            _auth_failures.setdefault(client_ip, []).append(now)
            raise HTTPResponse(
                body=_RESP_401,
                status=401,
                headers={'Content-Type': 'application/json'},
            )
        if user != auth_user or passwd != auth_pass:
            _auth_failures.setdefault(client_ip, []).append(now)
            raise HTTPResponse(
                body=_RESP_401,
                status=401,
                headers={
                    'Content-Type': 'application/json',
                    'WWW-Authenticate': 'Basic realm="TCC-G15"',
                },
            )
        # Auth success — clear failures for this IP
        _auth_failures.pop(client_ip, None)

    # -- CORS preflight --
    @app.route('/api/<:re:.*>', method='OPTIONS')
    def _cors_options(path=''):
        return ''

    # -- Index --
    @app.route('/')
    @app.route('/index.html')
    def _index():
        response.content_type = 'text/html; charset=utf-8'
        response.set_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        response.set_header('Pragma', 'no-cache')
        response.set_header('Expires', '0')
        return _get_index_html_bytes()

    # -- API: status --
    @app.route('/api/status')
    def _api_status():
        response.content_type = 'application/json'
        return bridge.get_status()

    # -- API: status SSE stream --
    @app.route('/api/status/stream')
    def _api_status_stream():
        if not _SSE_SEMAPHORE.acquire(timeout=2):
            response.content_type = 'application/json'
            raise HTTPResponse(
                body=b'{"ok":false,"error":"Too many SSE connections"}',
                status=429,
                headers={'Content-Type': 'application/json', 'Retry-After': '5'},
            )
        response.content_type = 'text/event-stream; charset=utf-8'
        response.set_header('Cache-Control', 'no-cache, no-transform')
        response.set_header('X-Accel-Buffering', 'no')
        response.set_header('Access-Control-Allow-Origin', '*')
        _SSE_MAX_LIFETIME = 300  # Force reconnect every 5 minutes
        _SSE_HEARTBEAT_INTERVAL = 30
        def generate():
            start_time = time.time()
            last_heartbeat = start_time
            try:
                while True:
                    if time.time() - start_time > _SSE_MAX_LIFETIME:
                        yield b": lifetime-reached\n\n"
                        break
                    now = time.time()
                    if now - last_heartbeat >= _SSE_HEARTBEAT_INTERVAL:
                        yield b": heartbeat\n\n"
                        last_heartbeat = now
                    try:
                        status = bridge.get_status()
                        data = json.dumps(status)
                        yield f"data: {data}\n\n".encode('utf-8')
                    except Exception as e:
                        yield f"data: {json.dumps({'error': str(e)})}\n\n".encode('utf-8')
                    time.sleep(2)  # Status stream: push every 2s (reduced from 1s to lower CPU)
            finally:
                _SSE_SEMAPHORE.release()
        return generate()

    # -- API: processes (GET) --
    @app.route('/api/processes')
    def _api_processes():
        response.content_type = 'application/json'
        sort_by, num = _parse_process_params(request.params)
        procs = _collect_processes(sort_by, num)
        return {'ok': True, 'type': sort_by, 'num': num, 'processes': procs}

    # -- API: processes SSE stream --
    @app.route('/api/processes/stream')
    def _api_processes_stream():
        if not _SSE_SEMAPHORE.acquire(timeout=2):
            response.content_type = 'application/json'
            raise HTTPResponse(
                body=b'{"ok":false,"error":"Too many SSE connections"}',
                status=429,
                headers={'Content-Type': 'application/json', 'Retry-After': '5'},
            )
        response.content_type = 'text/event-stream; charset=utf-8'
        response.set_header('Cache-Control', 'no-cache, no-transform')
        response.set_header('X-Accel-Buffering', 'no')
        response.set_header('Access-Control-Allow-Origin', '*')
        # Capture request params before the loop; request is a Bottle thread-local
        # proxy that may become invalid after the handler returns.
        initial_sort_by, initial_num = _parse_process_params(request.params)
        _SSE_MAX_LIFETIME = 300
        _SSE_HEARTBEAT_INTERVAL = 30
        def generate():
            start_time = time.time()
            last_heartbeat = start_time
            try:
                sort_by, num = initial_sort_by, initial_num
                # A full collection takes a few hundred ms (one handle per
                # process). Send a placeholder immediately so the table shows
                # "loading" instead of staying blank until the first scan lands.
                warm = json.dumps({'ok': True, 'type': sort_by, 'num': num,
                                   'processes': [], 'loading': True})
                yield f"data: {warm}\n\n".encode('utf-8')

                while True:
                    if time.time() - start_time > _SSE_MAX_LIFETIME:
                        yield b": lifetime-reached\n\n"
                        break
                    now = time.time()
                    if now - last_heartbeat >= _SSE_HEARTBEAT_INTERVAL:
                        yield b": heartbeat\n\n"
                        last_heartbeat = now
                    try:
                        procs = _collect_processes(sort_by, num)
                        data = json.dumps({'ok': True, 'type': sort_by, 'num': num, 'processes': procs})
                        yield f"data: {data}\n\n".encode('utf-8')
                    except Exception as e:
                        yield f"data: {json.dumps({'ok': False, 'error': str(e)})}\n\n".encode('utf-8')
                    time.sleep(5)  # Process stream: push every 5s (reduced from 3s to lower CPU)
            finally:
                _SSE_SEMAPHORE.release()
        return generate()

    # -- Favicon --
    @app.route('/favicon.ico')
    def _favicon():
        favicon = _get_favicon_bytes()
        if favicon:
            response.content_type = 'image/x-icon'
            return favicon
        raise HTTPResponse(body=_RESP_404, status=404, headers={'Content-Type': 'application/json'})

    # -- API: mode --
    @app.route('/api/mode', method='POST')
    def _api_mode():
        response.content_type = 'application/json'
        try:
            body = request.json
        except Exception:
            raise HTTPResponse(body=_RESP_400_INVALID_JSON, status=400, headers={'Content-Type': 'application/json'})
        if body is None:
            raise HTTPResponse(body=_RESP_400_INVALID_JSON, status=400, headers={'Content-Type': 'application/json'})
        mode = body.get('mode')
        if mode not in ('Balanced', 'G_Mode', 'Custom'):
            raise HTTPResponse(body=_RESP_400_INVALID_MODE, status=400, headers={'Content-Type': 'application/json'})
        bridge.set_mode(mode)
        return {'ok': True}

    # -- API: fan --
    @app.route('/api/fan', method='POST')
    def _api_fan():
        response.content_type = 'application/json'
        try:
            body = request.json
        except Exception:
            raise HTTPResponse(body=_RESP_400_INVALID_JSON, status=400, headers={'Content-Type': 'application/json'})
        if body is None:
            raise HTTPResponse(body=_RESP_400_INVALID_JSON, status=400, headers={'Content-Type': 'application/json'})
        gpu = body.get('gpu_speed')
        cpu = body.get('cpu_speed')
        if gpu is None or cpu is None:
            raise HTTPResponse(body=_RESP_400_MISSING_SPEED, status=400, headers={'Content-Type': 'application/json'})
        try:
            gpu = int(gpu)
            cpu = int(cpu)
        except (ValueError, TypeError):
            raise HTTPResponse(body=_RESP_400_INVALID_SPEED, status=400, headers={'Content-Type': 'application/json'})
        bridge.set_fan_speeds(max(0, min(120, gpu)), max(0, min(120, cpu)))
        return {'ok': True}

    return app


# ---------------------------------------------------------------------------
# Threaded WSGI server using waitress (supports streaming/SSE)
# ---------------------------------------------------------------------------

_cached_lan_ip: Optional[str] = None


class ThreadedHTTPServer(threading.Thread):
    def __init__(self, bridge: WebBridge, port: int = 8080,
                 bind_addr: str = "0.0.0.0",
                 auth_enabled: bool = False,
                 auth_user: str = "",
                 auth_pass: str = "") -> None:
        super().__init__(daemon=True)
        self.bridge = bridge
        self.port = port
        self.bind_addr = bind_addr
        self.auth_enabled = auth_enabled
        self.auth_user = auth_user
        self.auth_pass = auth_pass
        self._server = None
        self._started_event = threading.Event()
        self._stop_event = threading.Event()

    def wait_until_ready(self, timeout: float = 5.0) -> bool:
        return self._started_event.wait(timeout)

    def stop(self) -> None:
        self._stop_event.set()
        if self._server:
            self._server.close()

    def run(self) -> None:
        app = _create_app(
            bridge=self.bridge,
            auth_enabled=self.auth_enabled,
            auth_user=self.auth_user,
            auth_pass=self.auth_pass,
        )
        # Warmup psutil cpu_percent baseline for all processes (non-blocking).
        # This ensures subsequent cpu_percent(interval=0) calls return meaningful values.
        try:
            for p in psutil.process_iter(attrs=['pid']):
                try:
                    p.cpu_percent(interval=0)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception:
            pass
        try:
            self._server = waitress.create_server(
                app,
                host=self.bind_addr,
                port=self.port,
                threads=4,  # Match SSE semaphore limit to reduce thread overhead
            )
        except OSError as e:
            print(f"WebServer: failed to start on {self.bind_addr}:{self.port}: {e}")
            return
        self._started_event.set()
        print(f"WebServer: listening on http://{self.bind_addr}:{self.port}")
        self._server.run()
        print("WebServer: stopped")

    @staticmethod
    def get_lan_ip() -> str:
        """Best guess at an address other devices on the LAN can reach.

        The classic trick is to "connect" a UDP socket to a public address and
        read back the local end — that reports whichever interface the default
        route uses. That is wrong when a VPN or a virtual adapter (e.g. a
        benchmark-range 198.18.0.0/15 tunnel) owns the default route: the
        address shown cannot be reached from a phone on the same Wi-Fi.

        So private LAN addresses (RFC 1918) are preferred, and the routing
        trick is only a fallback for unusual setups.
        """
        global _cached_lan_ip
        if _cached_lan_ip is not None:
            return _cached_lan_ip

        def _pick_rfc1918() -> Optional[str]:
            try:
                import psutil
            except ImportError:
                return None
            candidates = []
            try:
                for iface, addrs in psutil.net_if_addrs().items():
                    for a in addrs:
                        if a.family != socket.AF_INET:
                            continue
                        ip = a.address
                        if ip.startswith("127.") or ip.startswith("169.254."):
                            continue  # loopback / APIPA
                        try:
                            octets = [int(x) for x in ip.split(".")]
                        except ValueError:
                            continue
                        if len(octets) != 4:
                            continue
                        if (octets[0] == 10
                                or (octets[0] == 192 and octets[1] == 168)
                                or (octets[0] == 172 and 16 <= octets[1] <= 31)):
                            candidates.append((iface, ip))
            except Exception:
                return None
            if not candidates:
                return None
            # Prefer a wired interface, then Wi-Fi, over anything else.
            def rank(item):
                name = item[0].lower()
                if "eth" in name or "\u4ee5\u592a" in name:
                    return 0
                if "wi-fi" in name or "wlan" in name or "\u65e0\u7ebf" in name:
                    return 1
                return 2
            candidates.sort(key=rank)
            return candidates[0][1]

        ip = _pick_rfc1918()
        if not ip:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.connect(("8.8.8.8", 80))
                    ip = s.getsockname()[0]
            except Exception:
                ip = "127.0.0.1"

        _cached_lan_ip = ip
        return _cached_lan_ip


def _build_index_html() -> str:
    import pathlib, sys
    if hasattr(sys, '_MEIPASS'):
        template_path = pathlib.Path(sys._MEIPASS) / "Web" / "templates" / "index.html"
    else:
        template_path = pathlib.Path(__file__).resolve().parent / "templates" / "index.html"
    return template_path.read_text(encoding="utf-8")
