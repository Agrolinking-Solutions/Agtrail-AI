"""
Fetches images from URLs supplied by webhook payloads without letting
those URLs reach internal services (SSRF), and without letting a huge or
malicious file exhaust memory.
"""

import io
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx
from PIL import Image

from app.core.config import settings

MAX_REDIRECTS = 5


class UnsafeURLError(ValueError):
    pass


class ImageTooLargeError(ValueError):
    pass


def _allowed_schemes() -> set[str]:
    return {"https", "http"} if settings.IMAGE_ALLOW_HTTP else {"https"}


def validate_public_url(url: str) -> None:
    """
    Raises UnsafeURLError unless url uses an allowed scheme and every
    address its host resolves to is a public internet address. This
    blocks loopback, private networks, link-local (including the cloud
    metadata service at 169.254.169.254) and other reserved ranges.
    """
    parsed = urlparse(url)
    if parsed.scheme not in _allowed_schemes():
        raise UnsafeURLError(f"scheme {parsed.scheme!r} is not allowed")
    if not parsed.hostname:
        raise UnsafeURLError("URL has no host")
    if parsed.username or parsed.password:
        raise UnsafeURLError("URLs with embedded credentials are not allowed")

    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        infos = socket.getaddrinfo(parsed.hostname, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, ValueError) as exc:
        raise UnsafeURLError(f"could not resolve host: {exc}") from exc

    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global or address.is_multicast:
            raise UnsafeURLError(f"host resolves to non-public address {address}")


def fetch_image(url: str, timeout: float = 10.0) -> Image.Image:
    """
    Downloads url and returns it as an RGB PIL image. Redirects are
    followed by hand so every hop is re-validated, and the body is read
    in chunks so a download stops as soon as it passes IMAGE_MAX_BYTES.

    Note: the host is resolved once for validation and again by httpx to
    connect, so a DNS server under an attacker's control could still
    switch addresses in between. Blocking private ranges at the network
    level (egress firewall) closes that gap fully.
    """
    headers = {"User-Agent": settings.user_agent}
    max_bytes = settings.IMAGE_MAX_BYTES

    with httpx.Client(timeout=timeout, follow_redirects=False, headers=headers) as client:
        current_url = url
        for _ in range(MAX_REDIRECTS + 1):
            validate_public_url(current_url)

            with client.stream("GET", current_url) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise httpx.HTTPError("redirect without a Location header")
                    current_url = urljoin(current_url, location)
                    continue

                response.raise_for_status()

                declared = response.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise ImageTooLargeError(f"image is {declared} bytes, limit is {max_bytes}")

                buffer = io.BytesIO()
                for chunk in response.iter_bytes():
                    buffer.write(chunk)
                    if buffer.tell() > max_bytes:
                        raise ImageTooLargeError(f"image exceeds {max_bytes} bytes")

            # Image.open only reads the header, so the pixel count can be
            # checked before the full image is decoded into memory.
            image = Image.open(buffer)
            width, height = image.size
            if width * height > settings.IMAGE_MAX_PIXELS:
                raise ImageTooLargeError(f"image is {width}x{height} pixels")
            image.load()
            return image.convert("RGB")

    raise httpx.HTTPError(f"more than {MAX_REDIRECTS} redirects")
