"""
Raw ONVIF SOAP PTZ fallback.

Some camera firmware (this project was built against a Jooan W5-U-US)
advertises an ONVIF port but fails standard capability negotiation --
GetCapabilities / GetServices / update_xaddrs either hang, time out, or
return malformed XML that onvif-zeep-async can't parse. Confirmed via
diagnostic scripts (see scripts/onvif_probe.py) on this hardware.

Rather than give up on PTZ for cameras like this, this module skips
capability negotiation entirely: it builds ONVIF SOAP envelopes by hand
with WS-Security UsernameToken digest auth, and POSTs them directly to
the *standard* ONVIF media/PTZ service paths that almost every
ONVIF-derived camera stack uses even when it can't answer
GetCapabilities correctly (these paths come from the ONVIF device spec's
conventional layout, e.g. /onvif/device_service, /onvif/PTZ,
/onvif/media_service). We try a short list of common path variants and
use the first one that returns a well-formed SOAP response instead of a
connection error or HTML.

This is inherently a best-effort fallback -- it works because most ONVIF
stacks are built from the same handful of SDKs (which all default to the
same URL conventions), not because of any spec guarantee. It is NOT a
substitute for real ONVIF support. Only used automatically when the
standard onvif-zeep-async path fails (see app/cameras/ptz.py).

Status: built but not yet validated against real hardware. Test with
scripts/test_raw_ptz.py before trusting this in production.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import httpx

logger = logging.getLogger("pi_nvr.onvif_raw")

# Path variants tried in order, most-common-first. Different camera SDKs
# (Hi3516/Hisilicon, GM8135, Novatek, etc.) disagree on exact casing/paths.
PTZ_SERVICE_PATHS = [
    "/onvif/PTZ",
    "/onvif/ptz_service",
    "/onvif/Ptz",
    "/PTZ",
    "/onvif/services/PTZ",
]

MEDIA_SERVICE_PATHS = [
    "/onvif/Media",
    "/onvif/media_service",
    "/onvif/Media_service",
    "/Media",
]

SOAP_NS = {
    "soap": "http://www.w3.org/2003/05/soap-envelope",
    "tptz": "http://www.onvif.org/ver20/ptz/wsdl",
    "trt": "http://www.onvif.org/ver10/media/wsdl",
    "tt": "http://www.onvif.org/ver10/schema",
}

REQUEST_TIMEOUT = 5.0


class RawPTZError(RuntimeError):
    pass


def _ws_security_header(username: str, password: str) -> str:
    """Builds a WS-Security UsernameToken block with PasswordDigest, the
    auth style essentially all ONVIF cameras expect (plaintext password
    auth is usually rejected even when the camera *looks* like it accepts
    HTTP basic auth for RTSP)."""
    nonce_bytes = os.urandom(16)
    nonce_b64 = base64.b64encode(nonce_bytes).decode("ascii")
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    digest = base64.b64encode(
        hashlib.sha1(nonce_bytes + created.encode("utf-8") + password.encode("utf-8")).digest()
    ).decode("ascii")

    return f"""<soap:Header>
  <Security xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
            xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">
    <UsernameToken>
      <Username>{username}</Username>
      <Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</Password>
      <Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{nonce_b64}</Nonce>
      <wsu:Created>{created}</wsu:Created>
    </UsernameToken>
  </Security>
</soap:Header>"""


def _envelope(body: str, username: str, password: str) -> str:
    header = _ws_security_header(username, password) if username else "<soap:Header/>"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope">
{header}
<soap:Body>
{body}
</soap:Body>
</soap:Envelope>"""


async def _post_soap(client: httpx.AsyncClient, url: str, envelope: str) -> ET.Element | None:
    try:
        resp = await client.post(
            url,
            content=envelope.encode("utf-8"),
            headers={"Content-Type": "application/soap+xml; charset=utf-8"},
            timeout=REQUEST_TIMEOUT,
        )
    except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadTimeout):
        return None

    if resp.status_code >= 500:
        # SOAP faults still come back as 500 with a parseable body sometimes,
        # but treat outright server errors as "wrong path" and keep trying.
        return None
    try:
        return ET.fromstring(resp.content)
    except ET.ParseError:
        return None


async def _find_working_path(
    client: httpx.AsyncClient, base_url: str, paths: list[str], probe_body: str, username: str, password: str
) -> str | None:
    """Tries each candidate path with a harmless probe request, returns the
    first one that yields a parseable SOAP response (success or fault --
    a fault still proves the path exists and speaks SOAP)."""
    envelope = _envelope(probe_body, username, password)
    for path in paths:
        url = base_url.rstrip("/") + path
        root = await _post_soap(client, url, envelope)
        if root is not None:
            logger.info("onvif_raw: resolved working service path %s", url)
            return url
    return None


def _get_profile_token_probe() -> str:
    return """<trt:GetProfiles xmlns:trt="http://www.onvif.org/ver10/media/wsdl"/>"""


def _extract_first_profile_token(root: ET.Element) -> str | None:
    profile = root.find(".//trt:Profiles", SOAP_NS)
    if profile is None:
        # some firmware omits the trt: prefix on child elements
        for el in root.iter():
            if el.tag.endswith("Profiles"):
                return el.get("token")
        return None
    return profile.get("token")


async def get_profile_token(host: str, port: int, username: str, password: str) -> str:
    base_url = f"http://{host}:{port}"
    async with httpx.AsyncClient() as client:
        media_url = await _find_working_path(
            client, base_url, MEDIA_SERVICE_PATHS, _get_profile_token_probe(), username, password
        )
        if not media_url:
            raise RawPTZError(f"No working media service path found on {base_url}")

        envelope = _envelope(_get_profile_token_probe(), username, password)
        root = await _post_soap(client, media_url, envelope)
        if root is None:
            raise RawPTZError("GetProfiles request failed")
        token = _extract_first_profile_token(root)
        if not token:
            raise RawPTZError("Camera returned no usable media profile token")
        return token


def _extract_fault_reason(root: ET.Element) -> str | None:
    """A SOAP response can be well-formed XML and still mean "no": a SOAP
    Fault is a normal, successfully-parsed envelope, not a connection
    error, so _post_soap alone can't tell "the camera did it" apart from
    "the camera understood the request and refused it". Cheap PTZ
    firmware commonly faults on Stop specifically while happily accepting
    ContinuousMove -- silently treating that fault as success is exactly
    how a directional button ends up spinning the camera forever with no
    way to halt it short of GotoHomePosition. Returns the fault reason
    text if this response is a fault, else None."""
    for el in root.iter():
        if el.tag.endswith("Fault"):
            for child in el.iter():
                tag = child.tag.rsplit("}", 1)[-1]
                if tag in ("Text", "Reason", "faultstring") and child.text:
                    return child.text.strip()
            return "camera returned a SOAP fault with no reason text"
    return None


def _continuous_move_body(profile_token: str, pan: float, tilt: float, zoom: float) -> str:
    return f"""<tptz:ContinuousMove xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl">
  <tptz:ProfileToken>{profile_token}</tptz:ProfileToken>
  <tptz:Velocity>
    <tt:PanTilt xmlns:tt="http://www.onvif.org/ver10/schema" x="{pan}" y="{tilt}"/>
    <tt:Zoom xmlns:tt="http://www.onvif.org/ver10/schema" x="{zoom}"/>
  </tptz:Velocity>
</tptz:ContinuousMove>"""


def _stop_body(profile_token: str) -> str:
    return f"""<tptz:Stop xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl">
  <tptz:ProfileToken>{profile_token}</tptz:ProfileToken>
  <tptz:PanTilt>true</tptz:PanTilt>
  <tptz:Zoom>true</tptz:Zoom>
</tptz:Stop>"""


def _goto_home_body(profile_token: str) -> str:
    return f"""<tptz:GotoHomePosition xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl">
  <tptz:ProfileToken>{profile_token}</tptz:ProfileToken>
</tptz:GotoHomePosition>"""


async def _resolve_ptz_url(client: httpx.AsyncClient, base_url: str, username: str, password: str, profile_token: str) -> str:
    probe = _stop_body(profile_token)
    ptz_url = await _find_working_path(client, base_url, PTZ_SERVICE_PATHS, probe, username, password)
    if not ptz_url:
        raise RawPTZError(f"No working PTZ service path found on {base_url}")
    return ptz_url


async def raw_move(host: str, port: int, username: str, password: str, pan: float, tilt: float, zoom: float) -> None:
    base_url = f"http://{host}:{port}"
    async with httpx.AsyncClient() as client:
        profile_token = await get_profile_token(host, port, username, password)
        ptz_url = await _resolve_ptz_url(client, base_url, username, password, profile_token)
        envelope = _envelope(_continuous_move_body(profile_token, pan, tilt, zoom), username, password)
        root = await _post_soap(client, ptz_url, envelope)
        if root is None:
            raise RawPTZError("ContinuousMove request failed")
        fault = _extract_fault_reason(root)
        if fault:
            raise RawPTZError(f"Camera rejected ContinuousMove: {fault}")
        logger.info("onvif_raw: move host=%s pan=%s tilt=%s zoom=%s", host, pan, tilt, zoom)


async def raw_stop(host: str, port: int, username: str, password: str) -> None:
    base_url = f"http://{host}:{port}"
    async with httpx.AsyncClient() as client:
        profile_token = await get_profile_token(host, port, username, password)
        ptz_url = await _resolve_ptz_url(client, base_url, username, password, profile_token)
        envelope = _envelope(_stop_body(profile_token), username, password)
        root = await _post_soap(client, ptz_url, envelope)
        fault = _extract_fault_reason(root) if root is not None else "no response"

        if root is None or fault:
            # Known cheap-firmware quirk: Stop is rejected/ignored while
            # ContinuousMove works fine. Zero-velocity ContinuousMove is
            # the standard workaround -- functionally "move at 0 speed",
            # which every firmware that accepts ContinuousMove at all
            # tends to honor as a real halt.
            logger.warning(
                "onvif_raw: Stop command failed on %s (%s) -- falling back to zero-velocity ContinuousMove",
                host, fault,
            )
            zero_envelope = _envelope(_continuous_move_body(profile_token, 0.0, 0.0, 0.0), username, password)
            zero_root = await _post_soap(client, ptz_url, zero_envelope)
            if zero_root is None:
                raise RawPTZError(f"Stop failed ({fault}); zero-velocity fallback also failed (no response)")
            zero_fault = _extract_fault_reason(zero_root)
            if zero_fault:
                raise RawPTZError(f"Stop failed ({fault}); zero-velocity fallback also faulted: {zero_fault}")
            logger.info("onvif_raw: stopped host=%s via zero-velocity ContinuousMove fallback", host)


async def raw_go_home(host: str, port: int, username: str, password: str) -> None:
    base_url = f"http://{host}:{port}"
    async with httpx.AsyncClient() as client:
        profile_token = await get_profile_token(host, port, username, password)
        ptz_url = await _resolve_ptz_url(client, base_url, username, password, profile_token)
        envelope = _envelope(_goto_home_body(profile_token), username, password)
        root = await _post_soap(client, ptz_url, envelope)
        if root is None:
            raise RawPTZError("GotoHomePosition request failed")
        fault = _extract_fault_reason(root)
        if fault:
            raise RawPTZError(f"Camera rejected GotoHomePosition: {fault}")


async def raw_detect(host: str, port: int, username: str, password: str) -> bool:
    """Best-effort probe used by the camera form's 'Test PTZ' action --
    returns True if we could resolve a profile token and a PTZ service
    path at all, without actually moving the camera."""
    try:
        base_url = f"http://{host}:{port}"
        async with httpx.AsyncClient() as client:
            profile_token = await get_profile_token(host, port, username, password)
            await _resolve_ptz_url(client, base_url, username, password, profile_token)
        return True
    except RawPTZError:
        return False
