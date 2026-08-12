(function () {
  const spotlightLayout = document.getElementById("spotlightLayout");
  const spotlightMain = document.getElementById("spotlightMain");
  const spotlightSide = document.getElementById("spotlightSide");
  const spotlightBottom = document.getElementById("spotlightBottom");
  const gridLayout = document.getElementById("gridLayout");
  const spotlightAudio = document.getElementById("spotlightAudio");

  let cameras = [];
  let viewMode = "spotlight"; // "spotlight" | "grid"
  let featuredCameraId = null;
  let renderedMainCameraId = null; // avoids reconnecting the main stream on unrelated re-renders
  let renderedGridCameraIds = null; // avoids reconnecting all-camera tiles on unrelated re-renders
  let thumbnailRefreshTimer = null;
  let listeningCameraId = null; // which camera's audio (if any) is currently playing

  const FAVORITE_KEY = "pinvr_favorite_camera_id";
  const QUALITY_KEY = "pinvr_live_quality";
  const SIDE_RAIL_MAX = 4; // thumbnails on the right column before overflowing to the bottom row

  // width/fps pairs. The Pi 3 has no hardware decode for this camera's
  // HEVC substream, so ffmpeg is transcoding on the CPU either way --
  // higher settings cost real responsiveness, hence a user-facing choice
  // rather than one hardcoded guess.
  const QUALITY_PRESETS = {
    low: { width: 480, fps: 8, label: "Low (480p, fast)" },
    medium: { width: 640, fps: 8, label: "Medium (640p)" },
    high: { width: 960, fps: 8, label: "High (960p, more CPU)" },
    max: { width: 1280, fps: 6, label: "Max (1280p, slower)" },
  };

  function getQuality() {
    const raw = localStorage.getItem(QUALITY_KEY);
    return QUALITY_PRESETS[raw] ? raw : "medium";
  }

  function setQuality(key) {
    localStorage.setItem(QUALITY_KEY, key);
  }

  function mjpegSrc(camId) {
    const { width, fps } = QUALITY_PRESETS[getQuality()];
    return `/api/cameras/${camId}/mjpeg?t=${Date.now()}&width=${width}&fps=${fps}`;
  }

  // The camera hardware only tolerates one RTSP connection at a time,
  // shared across both channels and recording (see
  // app/cameras/device_lock.py) -- the mjpeg endpoint returns a 503
  // when that slot is taken and nothing frees up within a few seconds,
  // which a plain <img> just renders as a broken image with no
  // indication of what happened or that it'll resolve itself. Show a
  // status badge and retry every few seconds instead.
  const STREAM_RETRY_MS = 3000;

  function handleMainStreamError(imgEl, camId) {
    const tile = imgEl.closest(".camera-tile");
    if (tile && !tile.querySelector(".stream-busy-badge")) {
      const badge = document.createElement("div");
      badge.className = "stream-busy-badge";
      badge.textContent = "Camera busy — retrying…";
      tile.appendChild(badge);
    }
    setTimeout(() => {
      // Only retry if this tile is still showing the same camera --
      // avoids a stale retry firing after the user has switched to a
      // different camera in the meantime.
      if (renderedMainCameraId === camId) {
        imgEl.src = mjpegSrc(camId);
      }
    }, STREAM_RETRY_MS);
  }

  function handleMainStreamLoad(imgEl) {
    const tile = imgEl.closest(".camera-tile");
    const badge = tile && tile.querySelector(".stream-busy-badge");
    if (badge) badge.remove();
  }

  window.__pinvrMainStreamError = handleMainStreamError;
  window.__pinvrMainStreamLoad = handleMainStreamLoad;

  function getFavoriteId() {
    const raw = localStorage.getItem(FAVORITE_KEY);
    return raw ? parseInt(raw, 10) : null;
  }

  function setFavoriteId(id) {
    if (id == null) localStorage.removeItem(FAVORITE_KEY);
    else localStorage.setItem(FAVORITE_KEY, String(id));
  }

  function getRequestedCameraId() {
    const raw = new URLSearchParams(window.location.search).get("camera");
    return raw ? parseInt(raw, 10) : null;
  }

  async function loadCameras() {
    try {
      cameras = await PiNVR.api("/cameras");
    } catch (e) {
      PiNVR.toast(e.message, true);
      cameras = [];
    }
    if (featuredCameraId == null || !cameras.some((c) => c.id === featuredCameraId)) {
      // A camera passed via ?camera=<id> (e.g. clicked from the Dashboard)
      // takes priority over the favorite -- someone clicking a specific
      // camera clearly wants to see *that* one, not whichever is starred.
      const requestedId = getRequestedCameraId();
      const favoriteId = getFavoriteId();
      if (requestedId != null && cameras.some((c) => c.id === requestedId)) {
        featuredCameraId = requestedId;
      } else {
        featuredCameraId = (favoriteId != null && cameras.some((c) => c.id === favoriteId))
          ? favoriteId
          : (cameras[0] ? cameras[0].id : null);
      }
    }
    renderCurrentView();
  }

  function renderCurrentView() {
    if (viewMode === "spotlight") {
      spotlightLayout.style.display = "grid";
      gridLayout.style.display = "none";
      renderSpotlight();
    } else {
      spotlightLayout.style.display = "none";
      gridLayout.style.display = "grid";
      renderGrid();
    }
  }

  function ptzPadHtml(camId, supportsPtz) {
    if (!supportsPtz) {
      return `<button class="btn ptz-detect-btn" data-detect-ptz="${camId}">Check for PTZ</button>`;
    }
    const dir = (label, direction, extraStyle = "") => `
      <button class="btn ptz-btn" data-ptz-move="${camId}" data-ptz-dir="${direction}" style="${extraStyle}">${label}</button>`;
    return `
      <div class="ptz-pad" data-ptz-camera="${camId}">
        <div class="ptz-pad-grid">
          <span></span>${dir("▲", "up")}<span></span>
          ${dir("◀", "left")}${dir("⌂", "home", "font-size:11px;")}${dir("▶", "right")}
          <span></span>${dir("▼", "down")}<span></span>
        </div>
        <div class="ptz-zoom">${dir("－", "zoom_out")}${dir("＋", "zoom_in")}</div>
      </div>`;
  }

  function stopListening() {
    if (listeningCameraId == null) return;
    spotlightAudio.pause();
    spotlightAudio.removeAttribute("src");
    spotlightAudio.load();
    listeningCameraId = null;
  }

  function renderSpotlight() {
    const featured = cameras.find((c) => c.id === featuredCameraId);
    const others = cameras.filter((c) => c.id !== featuredCameraId);

    if (!featured) {
      spotlightMain.innerHTML = `<div class="card">No cameras configured yet. Add one from the Cameras page.</div>`;
      spotlightSide.innerHTML = "";
      spotlightBottom.innerHTML = "";
      renderedMainCameraId = null;
      stopListening();
      return;
    }

    // Only recreate the main tile (and thus reconnect its stream) if the
    // featured camera actually changed -- avoids an unnecessary reconnect
    // on periodic re-renders that don't change what's featured.
    if (renderedMainCameraId !== featured.id) {
      renderedMainCameraId = featured.id;
      // The Listen button/state belongs to whichever camera was featured
      // before -- recreating the tile means starting fresh rather than
      // trying to carry a "was listening" flag onto a different camera.
      stopListening();
      const favoriteId = getFavoriteId();
      const isFavorite = featured.id === favoriteId;
      spotlightMain.innerHTML = `
        <div class="camera-tile spotlight-main-tile">
          <img src="${mjpegSrc(featured.id)}" alt="${featured.name}"
               onerror="window.__pinvrMainStreamError(this, ${featured.id})"
               onload="window.__pinvrMainStreamLoad(this)" />
          <div class="tile-label">
            ${featured.name}
            <span style="float:right; display:flex; gap:4px;">
              <button class="btn" data-listen="${featured.id}" title="Listen to camera audio" style="padding:2px 8px;">🔊</button>
              <button class="btn" data-favorite="${featured.id}" title="${isFavorite ? 'Unset favorite' : 'Set as favorite'}"
                      style="padding:2px 8px; ${isFavorite ? 'color:var(--amber); border-color:var(--amber-dim);' : ''}">${isFavorite ? '★' : '☆'}</button>
              <button class="btn" data-snapshot="${featured.id}" style="padding:2px 8px;">Snap</button>
            </span>
          </div>
          ${ptzPadHtml(featured.id, featured.supports_ptz)}
        </div>`;
    }

    const thumbHtml = (cam) => `
      <div class="camera-tile spotlight-thumb" data-feature="${cam.id}" title="Show ${cam.name} large">
        <img src="/api/playback/snapshot/${cam.id}/latest?t=${Date.now()}" alt="${cam.name}"
             onerror="this.style.opacity=0.15" />
        <div class="tile-label">${cam.name}</div>
      </div>`;

    const sideCameras = others.slice(0, SIDE_RAIL_MAX);
    const bottomCameras = others.slice(SIDE_RAIL_MAX);
    spotlightSide.innerHTML = sideCameras.map(thumbHtml).join("");
    spotlightBottom.innerHTML = bottomCameras.map(thumbHtml).join("");

    restartThumbnailRefresh();
  }

  function renderGrid() {
    const ids = cameras.map((c) => c.id).join(",");
    if (renderedGridCameraIds === ids) return; // nothing actually changed, avoid reconnecting everyone
    renderedGridCameraIds = ids;

    const favoriteId = getFavoriteId();
    gridLayout.innerHTML = cameras.map((cam) => {
      const isFavorite = cam.id === favoriteId;
      return `
        <div class="camera-tile" data-feature="${cam.id}">
          <img src="${mjpegSrc(cam.id)}" alt="${cam.name}" />
          <div class="tile-label">
            ${cam.name}
            <span style="float:right; display:flex; gap:4px;">
              <button class="btn" data-favorite="${cam.id}" title="${isFavorite ? 'Unset favorite' : 'Set as favorite'}"
                      style="padding:2px 8px; ${isFavorite ? 'color:var(--amber); border-color:var(--amber-dim);' : ''}">${isFavorite ? '★' : '☆'}</button>
              <button class="btn" data-snapshot="${cam.id}" style="padding:2px 8px;">Snap</button>
            </span>
          </div>
        </div>`;
    }).join("") || `<div class="card">No cameras configured yet. Add one from the Cameras page.</div>`;
  }

  function restartThumbnailRefresh() {
    if (thumbnailRefreshTimer) clearInterval(thumbnailRefreshTimer);
    thumbnailRefreshTimer = setInterval(() => {
      if (viewMode !== "spotlight") return;
      document.querySelectorAll(".spotlight-thumb img").forEach((img) => {
        const tile = img.closest(".spotlight-thumb");
        const camId = tile.getAttribute("data-feature");
        img.src = `/api/playback/snapshot/${camId}/latest?t=${Date.now()}`;
      });
    }, 30000);
  }

  // ---- interactions ----

  // The <audio> element's own 'error' event catches failures that happen
  // *after* play() was accepted (e.g. the stream connects then the
  // camera drops it) -- the play().catch() below only catches failures
  // at the initial play() call itself. Both funnel through the same
  // user-facing message since neither can see the backend's actual
  // error detail (browsers don't expose response bodies to <audio>).
  spotlightAudio.addEventListener("error", () => {
    if (listeningCameraId == null) return; // already stopped deliberately, not a real failure
    PiNVR.toast(
      "Camera audio stopped unexpectedly. If Live view video for this " +
      "camera is open at the same time, this hardware may only allow " +
      "one connection at a time.",
      true
    );
    const btn = spotlightMain.querySelector(`[data-listen="${listeningCameraId}"]`);
    if (btn) { btn.textContent = "🔊"; btn.title = "Listen to camera audio"; }
    listeningCameraId = null;
  });

  async function toggleListen(camIdRaw) {
    const camId = parseInt(camIdRaw, 10);
    const btn = spotlightMain.querySelector(`[data-listen="${camId}"]`);
    if (listeningCameraId === camId) {
      stopListening();
      if (btn) { btn.textContent = "🔊"; btn.title = "Listen to camera audio"; }
      return;
    }

    spotlightAudio.pause();
    if (btn) { btn.disabled = true; btn.textContent = "…"; }

    const url = `/api/cameras/${camId}/audio?t=${Date.now()}`;
    try {
      // <audio src="..."> never exposes a failed response's body to JS --
      // every past failure here just showed the browser's generic
      // "AbortError" no matter what the backend actually said. Fetching
      // first lets us read the real 503 detail (e.g. which ffmpeg error,
      // or the connection-limit hint) before handing the stream to
      // <audio>. This does mean starting audio takes a bit longer (an
      // extra connect/teardown cycle against the camera) -- worth it to
      // stop guessing blind.
      const resp = await fetch(url, { credentials: "same-origin" });
      if (!resp.ok) {
        let detail = `HTTP ${resp.status}`;
        try {
          const body = await resp.json();
          if (body.detail) detail = body.detail;
        } catch (e) { /* non-JSON error body, keep the HTTP status */ }
        throw new Error(detail);
      }
      // Only needed this to confirm the stream actually starts --
      // release the probe connection so we're not holding two
      // connections to the same camera-audio process, then let <audio>
      // open its own real stream.
      if (resp.body && resp.body.cancel) resp.body.cancel();

      spotlightAudio.src = url;
      await spotlightAudio.play();
      listeningCameraId = camId;
      if (btn) { btn.textContent = "🔇"; btn.title = "Stop listening"; }
    } catch (err) {
      PiNVR.toast("Could not start audio: " + err.message, true);
      listeningCameraId = null;
      if (btn) { btn.textContent = "🔊"; btn.title = "Listen to camera audio"; }
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function ptzMove(camId, direction) {
    if (direction === "home") {
      try { await PiNVR.api(`/cameras/${camId}/ptz/home`, { method: "POST" }); }
      catch (err) { PiNVR.toast(err.message, true); }
      return;
    }
    try { await PiNVR.api(`/cameras/${camId}/ptz/move`, { method: "POST", body: { direction, speed: 0.5 } }); }
    catch (err) { PiNVR.toast(err.message, true); }
  }

  async function ptzStop(camId) {
    try { await PiNVR.api(`/cameras/${camId}/ptz/stop`, { method: "POST" }); }
    catch (err) {
      // Auto-stop also fires server-side as a safety net, but if the
      // camera's Stop command (and its zero-velocity fallback) both
      // genuinely failed, staying silent here is how "it just kept
      // spinning" goes unreported. Surface it.
      PiNVR.toast("Stop command failed: " + err.message, true);
    }
  }

  function handleContainerClick(e) {
    const camId = e.target.getAttribute("data-snapshot");
    const favoriteId = e.target.getAttribute("data-favorite");
    const detectId = e.target.getAttribute("data-detect-ptz");
    const listenId = e.target.getAttribute("data-listen");
    const featureTile = e.target.closest("[data-feature]");

    if (listenId) {
      toggleListen(listenId);
      return;
    }
    if (camId) {
      PiNVR.api(`/playback/snapshot/${camId}`, { method: "POST" })
        .then(() => PiNVR.toast("Snapshot captured"))
        .catch((err) => PiNVR.toast(err.message, true));
      return;
    }
    if (favoriteId) {
      const id = parseInt(favoriteId, 10);
      setFavoriteId(getFavoriteId() === id ? null : id);
      renderedMainCameraId = null;
      renderedGridCameraIds = null;
      renderCurrentView();
      return;
    }
    if (detectId) {
      PiNVR.toast("Checking for PTZ support…");
      PiNVR.api(`/cameras/${detectId}/ptz/detect`, { method: "POST" }).then((result) => {
        if (result.supported) {
          PiNVR.toast("PTZ supported — controls added");
          const cam = cameras.find((c) => c.id === parseInt(detectId, 10));
          if (cam) cam.supports_ptz = true;
          renderedMainCameraId = null;
          renderCurrentView();
        } else {
          // Show the specific reason (unset ONVIF fields, which stage of
          // the raw SOAP fallback failed, etc.) instead of a flat
          // "not supported" -- that detail is what actually lets you fix
          // it without digging through server logs.
          PiNVR.toast(result.detail || "This camera does not support ONVIF PTZ", true);
        }
      }).catch((err) => PiNVR.toast(err.message, true));
      return;
    }
    // Clicking a thumbnail (spotlight side/bottom rail) or any tile in the
    // "All cameras" grid features that camera in Spotlight view.
    if (featureTile && !e.target.closest(".ptz-pad")) {
      const id = parseInt(featureTile.getAttribute("data-feature"), 10);
      featuredCameraId = id;
      viewMode = "spotlight";
      document.querySelectorAll("[data-view]").forEach((b) => b.classList.toggle("active", b.dataset.view === "spotlight"));
      renderCurrentView();
    }
  }

  spotlightSide.addEventListener("click", handleContainerClick);
  spotlightBottom.addEventListener("click", handleContainerClick);
  spotlightMain.addEventListener("click", handleContainerClick);
  gridLayout.addEventListener("click", handleContainerClick);

  // PTZ directional buttons use press-and-hold (mouse and touch), not
  // click, since continuous-move is a "while held" action. Delegated on
  // the whole document since the main tile is re-created on feature changes.
  document.addEventListener("mousedown", (e) => {
    const camId = e.target.getAttribute("data-ptz-move");
    const dir = e.target.getAttribute("data-ptz-dir");
    if (camId && dir) ptzMove(camId, dir);
  });
  document.addEventListener("touchstart", (e) => {
    const camId = e.target.getAttribute("data-ptz-move");
    const dir = e.target.getAttribute("data-ptz-dir");
    if (camId && dir) { e.preventDefault(); ptzMove(camId, dir); }
  }, { passive: false });
  ["mouseup", "mouseleave", "touchend", "touchcancel"].forEach((evt) => {
    document.addEventListener(evt, (e) => {
      const camId = e.target.getAttribute("data-ptz-move");
      const dir = e.target.getAttribute("data-ptz-dir");
      if (camId && dir && dir !== "home") ptzStop(camId);
    });
  });

  document.querySelectorAll("[data-view]").forEach((btn) => {
    btn.addEventListener("click", () => {
      viewMode = btn.dataset.view;
      // Grid view has no Listen control, and switching away from the
      // spotlight main tile that owns the current audio stream would
      // otherwise leave it playing invisibly with no way to stop it.
      stopListening();
      document.querySelectorAll("[data-view]").forEach((b) => b.classList.toggle("active", b === btn));
      renderCurrentView();
    });
  });
  document.querySelector('[data-view="spotlight"]').classList.add("active");

  document.querySelectorAll("[data-quality]").forEach((btn) => {
    btn.addEventListener("click", () => {
      setQuality(btn.dataset.quality);
      document.querySelectorAll("[data-quality]").forEach((b) => b.classList.toggle("active", b === btn));
      // Force every visible tile to reconnect with the new width/fps --
      // the same "only reconnect what changed" guard that avoids
      // unnecessary reconnects on unrelated re-renders would otherwise
      // also skip the reconnect this change actually needs.
      renderedMainCameraId = null;
      renderedGridCameraIds = null;
      renderCurrentView();
    });
  });
  document.querySelector(`[data-quality="${getQuality()}"]`).classList.add("active");

  document.getElementById("fullscreenBtn").addEventListener("click", () => {
    const target = viewMode === "spotlight" ? spotlightLayout : gridLayout;
    if (!document.fullscreenElement) {
      target.requestFullscreen().catch(() => PiNVR.toast("Fullscreen not available", true));
    } else {
      document.exitFullscreen();
    }
  });

  // Safari (and other browsers) can restore this entire page from the
  // back-forward cache on navigation instead of actually reloading it --
  // meaning none of our JS re-runs and the browser just shows the frozen
  // last frame of a connection the server already correctly closed. Force
  // a real reconnect of the main stream when that happens.
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) {
      renderedMainCameraId = null;
      renderedGridCameraIds = null;
      stopListening();
      renderCurrentView();
    }
  });

  loadCameras();
})();
