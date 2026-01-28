"""IP address utilities for FastAPI rate limiting.

This module provides FastAPI-specific utilities for extracting and validating
client IP addresses from HTTP requests, with proper handling of proxy headers
and security considerations.

For general identifier utilities (hash_identifier, combine_identifiers),
see ratesync.domain.value_objects.identifier or import directly from ratesync.
"""

from __future__ import annotations

import ipaddress
import logging
from typing import TYPE_CHECKING

# Lazy import for FastAPI - allows graceful handling if not installed
try:
    from starlette.requests import Request

    from ratesync.config import get_fastapi_config
    from ratesync.domain.value_objects.identifier import combine_identifiers, hash_identifier

    FASTAPI_AVAILABLE = True
except ImportError:
    Request = None  # type: ignore[misc, assignment]
    get_fastapi_config = None  # type: ignore[misc, assignment]
    combine_identifiers = None  # type: ignore[misc, assignment]
    hash_identifier = None  # type: ignore[misc, assignment]
    FASTAPI_AVAILABLE = False

if TYPE_CHECKING:
    pass


logger = logging.getLogger(__name__)


# Default trusted proxy networks (Docker, Kubernetes, load balancers)
DEFAULT_TRUSTED_PROXY_NETWORKS: list[str] = [
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "127.0.0.0/8",
    "::1/128",
    "fc00::/7",
]


def _parse_networks(
    networks: list[str],
) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Parse network strings into ipaddress network objects.

    Args:
        networks: List of network strings in CIDR notation.

    Returns:
        List of parsed network objects.
    """
    result: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for net in networks:
        try:
            result.append(ipaddress.ip_network(net, strict=False))
        except ValueError:
            logger.warning("Invalid network string: %s", net)
    return result


def _is_ip_in_networks(
    ip: str,
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network],
) -> bool:
    """Check if an IP address is within any of the given networks.

    Args:
        ip: IP address string to check.
        networks: List of network objects to check against.

    Returns:
        True if the IP is in any of the networks, False otherwise.
    """
    try:
        addr = ipaddress.ip_address(ip)
        return any(addr in net for net in networks)
    except ValueError:
        return False


def validate_ip(ip: str) -> str | None:
    """Validate and normalize an IP address string.

    Args:
        ip: IP address string to validate.

    Returns:
        Normalized IP string if valid, None otherwise.
    """
    try:
        addr = ipaddress.ip_address(ip.strip())
        return str(addr)
    except ValueError:
        return None


def is_private_ip(ip: str) -> bool:
    """Check if an IP address is private/internal.

    Args:
        ip: IP address string to check.

    Returns:
        True if the IP is private/reserved, False if public.
    """
    try:
        addr = ipaddress.ip_address(ip.strip())
        return addr.is_private or addr.is_loopback or addr.is_reserved
    except ValueError:
        return False


def get_client_ip(
    request: "Request",
    trusted_proxies: list[str] | None = None,
) -> str:
    """Extract the real client IP address from a request.

    Implements secure extraction of client IP considering proxy headers.
    Only trusts proxy headers (X-Forwarded-For, X-Real-IP) when the direct
    connection comes from a trusted proxy network.

    Security considerations:
    - X-Forwarded-For can be spoofed by clients; only trust it from proxies
    - Walks the X-Forwarded-For chain to find the first non-private IP
    - Falls back to direct connection IP if no valid client IP found

    Trusted proxy networks are determined in this order:
    1. `trusted_proxies` parameter (if provided)
    2. `fastapi.trusted_proxy_networks` from rate-sync.toml (if configured)
    3. DEFAULT_TRUSTED_PROXY_NETWORKS (private networks)

    Args:
        request: The Starlette/FastAPI Request object.
        trusted_proxies: Optional list of trusted proxy networks in CIDR notation.
            If None, uses config from rate-sync.toml or defaults.

    Returns:
        The client IP address string. Returns "unknown" if unable to determine.

    Example:
        >>> from fastapi import Request, Depends
        >>> from ratesync.contrib.fastapi import get_client_ip
        >>>
        >>> @app.get("/api/data")
        >>> async def get_data(request: Request):
        ...     client_ip = get_client_ip(request)
        ...     return {"ip": client_ip}
        >>>
        >>> # With custom trusted proxies
        >>> get_client_ip(request, trusted_proxies=["10.0.0.0/8"])
    """
    if not FASTAPI_AVAILABLE:
        raise RuntimeError("FastAPI/Starlette not installed. Install with: pip install rate-sync[fastapi]")

    # Determine trusted proxy networks
    # Priority: parameter > config > defaults
    if trusted_proxies is not None:
        trusted_networks = _parse_networks(trusted_proxies)
    else:
        # Try to get from global config
        if get_fastapi_config is not None:
            config = get_fastapi_config()
            if config.trusted_proxy_networks is not None:
                trusted_networks = _parse_networks(config.trusted_proxy_networks)
            else:
                trusted_networks = _parse_networks(DEFAULT_TRUSTED_PROXY_NETWORKS)
        else:
            trusted_networks = _parse_networks(DEFAULT_TRUSTED_PROXY_NETWORKS)

    # Get direct connection IP
    direct_ip: str | None = None
    if request.client:
        direct_ip = request.client.host

    if not direct_ip:
        logger.warning("Request without client IP")
        return "unknown"

    # Validate direct IP
    validated_direct = validate_ip(direct_ip)
    if not validated_direct:
        logger.warning("Invalid direct IP: %s", direct_ip)
        return "unknown"

    # Only trust proxy headers if connection is from a trusted proxy
    if not _is_ip_in_networks(validated_direct, trusted_networks):
        # Direct connection or untrusted proxy - use connection IP
        return validated_direct

    # Trusted proxy - extract real client IP from headers

    # Try X-Forwarded-For first (standard proxy header)
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        # X-Forwarded-For contains: client, proxy1, proxy2, ...
        # Walk the chain to find first non-private IP (closest to client)
        ips = [ip.strip() for ip in forwarded_for.split(",")]
        for ip in ips:
            validated = validate_ip(ip)
            if validated and not _is_ip_in_networks(validated, trusted_networks):
                return validated

    # Fallback: X-Real-IP (set by some proxies like nginx)
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        validated = validate_ip(real_ip)
        if validated:
            return validated

    # Last resort: use direct IP even if it's a proxy
    return validated_direct


__all__ = [
    "get_client_ip",
    "validate_ip",
    "is_private_ip",
    "hash_identifier",  # Re-exported from domain for backward compatibility
    "combine_identifiers",  # Re-exported from domain for backward compatibility
    "DEFAULT_TRUSTED_PROXY_NETWORKS",
    "FASTAPI_AVAILABLE",
]
