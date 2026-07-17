"""
ONVIF WS-Discovery: send a UDP multicast probe on 239.255.255.250:3702 and
collect device responses. This is a minimal, dependency-free implementation
of the WS-Discovery probe/match exchange -- avoids pulling in a full SOAP
stack just to find device XAddrs on the LAN.

Once a device's service address is known, `onvif-zeep-async` (see
app/cameras/ptz.py) is used for the actual ONVIF calls (GetCapabilities,
PTZ, etc.), since those responses are far more useful to parse with a real
SOAP client than to hand-roll.
"""
from __future__ import annotations

import asyncio
import re
import socket
import uuid

MULTICAST_ADDR = "239.255.255.250"
MULTICAST_PORT = 3702

PROBE_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
            xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"
            xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
            xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
  <e:Header>
    <w:MessageID>uuid:{message_id}</w:MessageID>
    <w:To e:mustUnderstand="1">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>
    <w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>
  </e:Header>
  <e:Body>
    <d:Probe>
      <d:Types>dn:NetworkVideoTransmitter</d:Types>
    </d:Probe>
  </e:Body>
</e:Envelope>"""

XADDR_RE = re.compile(r"<[^>]*XAddrs[^>]*>(.*?)</[^>]*XAddrs>", re.IGNORECASE | re.DOTALL)
SCOPES_RE = re.compile(r"<[^>]*Scopes[^>]*>(.*?)</[^>]*Scopes>", re.IGNORECASE | re.DOTALL)


class DiscoveredDevice:
    def __init__(self, xaddr: str, scopes: str, source_ip: str):
        self.xaddr = xaddr
        self.scopes = scopes
        self.source_ip = source_ip
        self.name = _extract_scope(scopes, "name") or xaddr
        self.hardware = _extract_scope(scopes, "hardware")

    def to_dict(self) -> dict:
        return {
            "xaddr": self.xaddr,
            "name": self.name,
            "hardware": self.hardware,
            "source_ip": self.source_ip,
        }


def _extract_scope(scopes: str, key: str) -> str | None:
    for token in scopes.split():
        if f"/{key}/" in token.lower():
            return token.rsplit("/", 1)[-1].replace("%20", " ")
    return None


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    """Collects WS-Discovery probe responses as they arrive.

    Uses the transport/protocol UDP pattern (via
    loop.create_datagram_endpoint) rather than the lower-level
    loop.sock_sendto/sock_recvfrom methods. This matters because uvloop
    -- the faster event loop uvicorn[standard] installs and runs by
    default in production -- does not implement sock_sendto, and raises
    NotImplementedError. create_datagram_endpoint is implemented by both
    the default asyncio loop and uvloop, so this works under either.
    """

    def __init__(self):
        self.responses: list[tuple[bytes, tuple]] = []

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        self.responses.append((data, addr))

    def error_received(self, exc: Exception) -> None:  # noqa: D401
        pass  # Best-effort discovery; a single bad datagram shouldn't abort the scan.


async def discover(timeout_seconds: float = 4.0) -> list[DiscoveredDevice]:
    """Broadcast a WS-Discovery probe and collect responses for
    `timeout_seconds`. Returns one entry per unique XAddr."""
    loop = asyncio.get_event_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.setblocking(False)
    sock.bind(("", 0))

    transport, protocol = await loop.create_datagram_endpoint(
        _DiscoveryProtocol, sock=sock
    )

    devices: dict[str, DiscoveredDevice] = {}
    try:
        message = PROBE_TEMPLATE.format(message_id=uuid.uuid4())
        transport.sendto(message.encode("utf-8"), (MULTICAST_ADDR, MULTICAST_PORT))

        await asyncio.sleep(timeout_seconds)

        for data, addr in protocol.responses:
            text = data.decode("utf-8", errors="replace")
            xaddr_match = XADDR_RE.search(text)
            scopes_match = SCOPES_RE.search(text)
            if xaddr_match:
                xaddr = xaddr_match.group(1).split()[0].strip()
                scopes = scopes_match.group(1).strip() if scopes_match else ""
                devices[xaddr] = DiscoveredDevice(xaddr, scopes, addr[0])
    finally:
        transport.close()

    return list(devices.values())
