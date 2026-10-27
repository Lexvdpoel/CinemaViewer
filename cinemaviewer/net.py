"""Tiny JSON-over-HTTP helper based on the standard library."""

import json
import ssl
import urllib.error
import urllib.request

from . import __version__

# Proxies such as Cloudflare reject the default "Python-urllib" agent (error 1010 → HTTP 403).
USER_AGENT = f"CinemaViewer/{__version__}"

# Let Windows itself verify HTTPS certificates. Python's own check only knows the root certificates
# already in the Windows store, while Windows fetches a missing root on demand (as browsers do).
try:
    import truststore
    SSL_CONTEXT = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
except ImportError:  # runtime installed before truststore was bundled
    SSL_CONTEXT = None


class HttpError(Exception):
    pass


def request(method, url, headers=None, data=None, timeout=15):
    hdrs = {"Accept": "application/json", "User-Agent": USER_AGENT}
    hdrs.update(headers or {})
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise HttpError("niet geautoriseerd (401) – controleer inloggegevens/token") from e
        if e.code == 403:
            body = e.read(200).decode("utf-8", "replace")
            # Cloudflare's own block page says "error code: 1010" etc. (its CF-RAY header is on every
            # response through Cloudflare, so it can't tell a proxy block from a server refusal).
            if body.lstrip().startswith("error code:"):
                raise HttpError(f"geweigerd door Cloudflare/proxy (403, {body.strip()})") from e
            raise HttpError("toegang geweigerd (403) – mag deze gebruiker van buitenaf inloggen?") from e
        raise HttpError(f"HTTP {e.code} {e.reason}") from e
    except urllib.error.URLError as e:
        raise HttpError(f"server niet bereikbaar ({e.reason})") from e
    except TimeoutError as e:
        raise HttpError("time-out bij verbinden met server") from e
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None
