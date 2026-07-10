from __future__ import annotations


def test_camera_manager_parses_frame_rate_fraction():
    from app.cameras.manager import _parse_frame_rate

    assert _parse_frame_rate("30/1") == 30.0
    assert _parse_frame_rate("25/1") == 25.0
    assert _parse_frame_rate("30000/1001") == 29.97


def test_camera_manager_parses_frame_rate_plain_number():
    from app.cameras.manager import _parse_frame_rate

    assert _parse_frame_rate("15") == 15.0


def test_camera_manager_handles_bad_frame_rate():
    from app.cameras.manager import _parse_frame_rate

    assert _parse_frame_rate("not-a-number") is None
    assert _parse_frame_rate("5/0") is None


def test_onvif_discovery_extracts_xaddr_and_scopes():
    from app.cameras.onvif_discovery import DiscoveredDevice

    scopes = (
        "onvif://www.onvif.org/type/NetworkVideoTransmitter "
        "onvif://www.onvif.org/name/Front%20Door%20Camera "
        "onvif://www.onvif.org/hardware/IPC-Model-X"
    )
    device = DiscoveredDevice(
        xaddr="http://192.168.1.50/onvif/device_service",
        scopes=scopes,
        source_ip="192.168.1.50",
    )
    assert device.name == "Front Door Camera"
    assert device.hardware == "IPC-Model-X"
    assert device.to_dict()["xaddr"] == "http://192.168.1.50/onvif/device_service"


def test_motion_zone_mask_include_only(tmp_project, db_session):
    """An 'include' zone should restrict the mask to just that region."""
    import numpy as np
    from app.config import Config
    from app.models import Camera, CameraProtocol, MotionZone
    import json

    camera = Camera(name="Cam", protocol=CameraProtocol.rtsp, rtsp_url="rtsp://x")
    db_session.add(camera)
    db_session.flush()

    zone = MotionZone(
        camera_id=camera.id,
        zone_type="include",
        points_json=json.dumps([[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5]]),
    )
    db_session.add(zone)
    db_session.commit()

    from app.motion.detector import MotionDetectorWorker

    cfg = Config(tmp_project / "config.yaml")
    worker = MotionDetectorWorker(cfg, camera, on_motion=lambda *a: None)
    mask = worker._load_zones(width=100, height=100)

    assert isinstance(mask, np.ndarray)
    # Top-left quadrant should be included (255), bottom-right excluded (0).
    assert mask[10, 10] == 255
    assert mask[90, 90] == 0
