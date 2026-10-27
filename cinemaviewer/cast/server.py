"""Network plumbing for Displaycast: SSDP discovery (UDP 1900) and a small HTTP server.

Both the DIAL device (YouTube) and the DLNA MediaRenderer announce themselves through SSDP; phones
then fetch their descriptions and talk to them over HTTP. Addresses in replies use the local IP
of the network the request came from, so it works with several network cards too.
"""

import email.utils
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900
SERVER_HEADER = "Windows/10.0 UPnP/1.0 CinemaViewer/1.0"
NOTIFY_INTERVAL = 60


def local_ip_for(remote):
    """Our address on the network that reaches `remote` (no packet is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((remote, 9))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def local_ips():
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    ips.add(local_ip_for("192.168.1.1"))
    return sorted(ip for ip in ips if not ip.startswith("127."))


class Device:
    """An SSDP-announced device: uuid, description path and the search targets it answers to."""

    def __init__(self, uuid, desc_path, types):
        self.uuid = uuid
        self.desc_path = desc_path
        self.types = types           # e.g. ["upnp:rootdevice", "urn:schemas-upnp-org:device:MediaRenderer:1", …]

    def usn(self, st):
        return f"uuid:{self.uuid}" if st == f"uuid:{self.uuid}" else f"uuid:{self.uuid}::{st}"

    def matches(self, st):
        return st == "ssdp:all" or st in self.types or st == f"uuid:{self.uuid}"

    def targets(self):
        return [f"uuid:{self.uuid}"] + self.types


class SsdpServer(threading.Thread):
    def __init__(self, http_port, on_error=None):
        super().__init__(name="Displaycast SSDP", daemon=True)
        self.http_port = http_port
        self.devices = []
        self.on_error = on_error
        self._stop = threading.Event()
        self._sock = None

    def start_listening(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", SSDP_PORT))  # shared with Windows' own SSDP service through SO_REUSEADDR
        for ip in local_ips() or ["0.0.0.0"]:
            try:
                s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                             socket.inet_aton(SSDP_ADDR) + socket.inet_aton(ip))
            except OSError:
                pass
        s.settimeout(1.0)
        self._sock = s
        self.start()

    def location(self, device, remote):
        return f"http://{local_ip_for(remote)}:{self.http_port}{device.desc_path}"

    def run(self):
        next_notify = 0.0
        while not self._stop.is_set():
            if time.monotonic() >= next_notify:
                self._notify("ssdp:alive")
                next_notify = time.monotonic() + NOTIFY_INTERVAL
            try:
                data, addr = self._sock.recvfrom(4096)
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                if self._stop.is_set():
                    break
                time.sleep(1)
                continue
            try:
                self._handle(data.decode("utf-8", "replace"), addr)
            except Exception as e:  # noqa: BLE001 - one odd packet must not stop discovery
                if self.on_error:
                    self.on_error(f"SSDP: {e}")

    def _handle(self, text, addr):
        lines = text.split("\r\n")
        if not lines or not lines[0].upper().startswith("M-SEARCH"):
            return
        headers = {}
        for line in lines[1:]:
            key, sep, value = line.partition(":")
            if sep:
                headers[key.strip().upper()] = value.strip()
        st = headers.get("ST", "")
        if "ssdp:discover" not in headers.get("MAN", ""):
            return
        for device in self.devices:
            if not device.matches(st):
                continue
            answers = device.targets() if st == "ssdp:all" else [st]
            for target in answers:
                reply = ("HTTP/1.1 200 OK\r\n"
                         "CACHE-CONTROL: max-age=1800\r\n"
                         f"DATE: {email.utils.formatdate(usegmt=True)}\r\n"
                         "EXT:\r\n"
                         f"LOCATION: {self.location(device, addr[0])}\r\n"
                         f"SERVER: {SERVER_HEADER}\r\n"
                         f"ST: {target}\r\n"
                         f"USN: {device.usn(target)}\r\n"
                         "BOOTID.UPNP.ORG: 1\r\n\r\n")
                try:
                    self._sock.sendto(reply.encode(), addr)
                except OSError:
                    pass

    def _notify(self, nts):
        for ip in local_ips():
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as s:
                    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
                    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
                    for device in self.devices:
                        for target in device.targets():
                            msg = ("NOTIFY * HTTP/1.1\r\n"
                                   f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
                                   "CACHE-CONTROL: max-age=1800\r\n"
                                   f"LOCATION: http://{ip}:{self.http_port}{device.desc_path}\r\n"
                                   f"NT: {target}\r\n"
                                   f"NTS: {nts}\r\n"
                                   f"SERVER: {SERVER_HEADER}\r\n"
                                   f"USN: {device.usn(target)}\r\n\r\n")
                            s.sendto(msg.encode(), (SSDP_ADDR, SSDP_PORT))
            except OSError:
                pass

    def stop(self):
        self._stop.set()
        self._notify("ssdp:byebye")
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass


class Request:
    def __init__(self, method, path, query, headers, body, client_ip, local_ip, port):
        self.method, self.path, self.query, self.headers = method, path, query, headers
        self.body, self.client_ip, self.local_ip, self.port = body, client_ip, local_ip, port

    @property
    def base_url(self):
        return f"http://{self.local_ip}:{self.port}"


class Response:
    def __init__(self, status=200, body=b"", headers=None, content_type="text/xml; charset=\"utf-8\""):
        self.status = status
        self.body = body.encode("utf-8") if isinstance(body, str) else body
        self.headers = dict(headers or {})
        if self.body and content_type:
            self.headers.setdefault("Content-Type", content_type)


class HttpServer:
    """Routes requests to handlers registered per path prefix: handler(Request) -> Response."""

    def __init__(self, port):
        self.routes = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = SERVER_HEADER

            def log_message(self, *_args):
                pass

            def _dispatch(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                path, _, query = self.path.partition("?")
                req = Request(self.command, path, query, {k.upper(): v for k, v in self.headers.items()}, body,
                              self.client_address[0], self.connection.getsockname()[0], server.port)
                resp = None
                for prefix, handler in server.routes:
                    if path.startswith(prefix):
                        try:
                            resp = handler(req)
                        except Exception:  # noqa: BLE001 - answer instead of dropping the connection
                            resp = Response(500)
                        break
                resp = resp or Response(404)
                self.send_response(resp.status)
                for k, v in resp.headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(resp.body)))
                self.send_header("Connection", "close")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(resp.body)
                self.close_connection = True

            do_GET = do_POST = do_HEAD = do_SUBSCRIBE = do_UNSUBSCRIBE = do_DELETE = do_OPTIONS = _dispatch

        for candidate in (port, 0):  # the preferred port, else any free one
            try:
                self.httpd = ThreadingHTTPServer(("", candidate), Handler)
                break
            except OSError:
                if candidate == 0:
                    raise
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="Displaycast HTTP", daemon=True)

    def route(self, prefix, handler):
        self.routes.append((prefix, handler))
        self.routes.sort(key=lambda r: -len(r[0]))  # longest prefix first

    def start(self):
        self._thread.start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

