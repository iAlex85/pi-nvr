from __future__ import annotations


def test_config_get_set_roundtrip(tmp_project):
    from app.config import Config

    cfg = Config(tmp_project / "config.yaml")
    assert cfg.get("motion.sensitivity") == 25

    cfg.set("motion.sensitivity", 50)
    assert cfg.get("motion.sensitivity") == 50

    # Reload from disk to confirm the write was persisted, not just cached.
    cfg2 = Config(tmp_project / "config.yaml")
    assert cfg2.get("motion.sensitivity") == 50


def test_config_get_missing_key_returns_default(tmp_project):
    from app.config import Config

    cfg = Config(tmp_project / "config.yaml")
    assert cfg.get("nonexistent.nested.key", "fallback") == "fallback"


def test_password_hashing_roundtrip():
    from app.auth.security import hash_password, verify_password

    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed) is True
    assert verify_password("wrong password", hashed) is False


def test_session_token_roundtrip(monkeypatch):
    monkeypatch.setenv("PI_NVR_SESSION_SECRET", "unit-test-secret")
    from app.auth.security import create_session_token, read_session_token

    token = create_session_token(user_id=42, remember=True)
    payload = read_session_token(token, max_age_seconds=3600)
    assert payload is not None
    assert payload["uid"] == 42
    assert payload["remember"] is True


def test_session_token_expired(monkeypatch):
    monkeypatch.setenv("PI_NVR_SESSION_SECRET", "unit-test-secret")
    from app.auth.security import create_session_token, read_session_token

    token = create_session_token(user_id=1)
    # The signer only tracks time at whole-second resolution, so the gap
    # between "issued" and "checked" must be large enough that no phase
    # alignment (e.g. issued at x.999s) can round down to an apparent age
    # that isn't strictly greater than max_age. Sleeping >1s with
    # max_age=0 guarantees this regardless of when within a second the
    # token was actually issued.
    import time
    time.sleep(1.2)
    payload = read_session_token(token, max_age_seconds=0)
    assert payload is None


def test_credential_encryption_roundtrip(monkeypatch):
    monkeypatch.setenv("PI_NVR_DB_SECRET", "unit-test-db-secret")
    from app.cameras.crypto import decrypt, encrypt

    enc = encrypt("super-secret-password")
    assert enc != "super-secret-password"
    assert decrypt(enc) == "super-secret-password"


def test_credential_encryption_handles_none():
    from app.cameras.crypto import decrypt, encrypt

    assert encrypt(None) is None
    assert decrypt(None) is None


def test_camera_model_roundtrip(db_session):
    from app.models import Camera, CameraProtocol, RecordingMode

    camera = Camera(
        name="Front Door",
        protocol=CameraProtocol.rtsp,
        rtsp_url="rtsp://192.168.1.50:554/stream1",
        recording_mode=RecordingMode.continuous,
    )
    db_session.add(camera)
    db_session.flush()

    assert camera.id is not None
    assert camera.enabled is True  # default
    assert camera.recording_mode == RecordingMode.continuous
