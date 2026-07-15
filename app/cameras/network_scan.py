"""
Local network scan: a fallback for finding cameras when ONVIF
WS-Discovery doesn't work -- which is common with budget consumer
cameras (many only implement a vendor's proprietary cloud/P2P pairing
protocol, not standard ONVIF discovery).

Instead of trying to speak a proprietary protocol, this sweeps the Pi's
own LAN subnet(s) and checks which hosts have camera-typical ports open
(554 = RTSP, 8899 = ONVIF per several vendors including Jooan, 80 = a
camera's built-in web UI). It won't tell you the exact RTSP path or
credentials, but it tells you *which IP* is worth trying -- the part
that's otherwise pure guesswork on a LAN with many devices.

Deliberately does not scan Tailscale (100.64.0.0/10) or loopback
interfaces -- only real LAN interfaces the Pi is directly attached to.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging

import psutil

logger = logging.getLogger("pi_nvr.cameras.scan")

CAMERA_PORTS = {
    554: "rtsp",
    8899: "onvif",
    80: "http",
    8080: "http-alt",
}

CONNECT_TIMEOUT_SECONDS = 0.35
MAX_CONCURRENT_PROBES = 128
MAX_HOSTS_PER_SCAN = 512  # safety cap so a misconfigured /16 doesn't hang forever


def _local_ipv4_networks() -> list[ipaddress.IPv4Network]:
    """Finds the Pi's own LAN subnet(s) from its network interfaces,
    excluding loopback and Tailscale's CGNAT range (100.64.0.0/10)."""
    networks: list[ipaddress.IPv4Network] = []
    for iface_name, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family.name != "AF_INET":
                continue
            try:
                ip = ipaddress.IPv4Address(addr.address)
            except ValueError:
                continue
            if ip.is_loopback:
                continue
            if ip in ipaddress.IPv4Network("100.64.0.0/10"):  # Tailscale CGNAT range
                continue
            if not addr.netmask:
                continue
            try:
                network = ipaddress.IPv4Network(f"{addr.address}/{addr.netmask}", strict=False)
            except ValueError:
                continue
            # Skip absurdly large subnets (misconfigured netmask) to avoid a
            # multi-thousand-host scan; a typical home LAN is a /24.
            if network.num_addresses > MAX_HOSTS_PER_SCAN:
                logger.warning(
                    "Skipping oversized network %s on interface %s (%d addresses)",
                    network, iface_name, network.num_addresses,
                )
                continue
            networks.append(network)
    return networks


async def _probe_port(ip: str, port: int, semaphore: asyncio.Semaphore) -> bool:
    async with semaphore:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port), timeout=CONNECT_TIMEOUT_SECONDS
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            return True
        except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
            return False


async def scan_for_cameras() -> list[dict]:
    """Scans every local LAN subnet for hosts with camera-typical ports
    open. Returns one entry per host that had at least one such port
    respond, e.g.:
        [{"ip": "192.168.1.55", "open_ports": {"rtsp": 554, "onvif": 8899}}]
    """
    networks = _local_ipv4_networks()
    if not networks:
        logger.warning("No scannable local networks found")
        return []

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_PROBES)
    tasks: dict[tuple[str, int], asyncio.Task] = {}

    for network in networks:
        for host in network.hosts():
            ip = str(host)
            for port in CAMERA_PORTS:
                tasks[(ip, port)] = asyncio.create_task(_probe_port(ip, port, semaphore))

    if not tasks:
        return []

    await asyncio.gather(*tasks.values())

    results: dict[str, dict] = {}
    for (ip, port), task in tasks.items():
        if task.result():
            entry = results.setdefault(ip, {"ip": ip, "open_ports": {}})
            entry["open_ports"][CAMERA_PORTS[port]] = port

    # Prioritize hosts that look most like a camera (RTSP and/or ONVIF
    # open) over ones that only answered on a generic web port.
    def _rank(entry: dict) -> int:
        ports = entry["open_ports"]
        return (2 if "rtsp" in ports else 0) + (1 if "onvif" in ports else 0)

    return sorted(results.values(), key=_rank, reverse=True)
