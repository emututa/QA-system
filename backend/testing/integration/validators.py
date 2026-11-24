import ipaddress
import socket
from urllib.parse import urlparse

from django.core.exceptions import ValidationError


BLOCKED_HOSTS = {"localhost", "127.0.0.1", "::1", "169.254.169.254", "0.0.0.0"}


def validate_external_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValidationError("Only HTTP and HTTPS URLs are allowed")
    if not parsed.hostname:
        raise ValidationError("A valid hostname is required")
    if parsed.username or parsed.password:
        raise ValidationError("Credentials must not be embedded in URLs")
    hostname = parsed.hostname.lower()
    if hostname in BLOCKED_HOSTS:
        raise ValidationError("Loopback and metadata addresses are blocked")
    try:
        addresses = socket.getaddrinfo(
            hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
        )
    except socket.gaierror as exc:
        raise ValidationError("Hostname could not be resolved") from exc
    for _family, _type, _proto, _canonical, sockaddr in addresses:
        address = ipaddress.ip_address(sockaddr[0].split("%")[0])
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
            raise ValidationError("Private, loopback, link-local and reserved networks are blocked")
    return value
