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
  const SIDE_RAIL_MAX = 4; // thumbnails on the right column before overflowing to the bottom row

  function getFavoriteId() {
    const raw = localStorage.getItem(FAVORITE_KEY);
    return raw ? parseInt(raw, 10) : null;
  }

  function setFavoriteId(id) {
    if (id == null) localStorage.removeItem(FAVORITE_KEY);
    else localStorage.setItem(FAVORITE_KEY, String(id));
  }

  async function loadCameras() {
    try {
      cameras = await PiNVR.api("/cameras");
    } catch (e) {
      PiNVR.toast(e.message, true);
      cameras = [];
    }
    if (featuredCameraId == null || !cameras.some((c) => c.id === featuredCameraId)) {
      const favoriteId = getFavoriteId();
      featuredCameraId = (favoriteId != null && cameras.some((c) => c.id === favoriteId))
        ? favoriteId
        : (cameras[0] ? cameras[0].id : null);
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
          <img src="/api/cameras/${featured.id}/mjpeg?t=${Date.now()}" alt="${featured.name}" />
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
          <img src="/api/cameras/${cam.id}/mjpeg?t=${Date.now()}" alt="${cam.name}" />
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

  function toggleListen(camIdRaw) {
    const camId = parseInt(camIdRaw, 10);
    const btn = spotlightMain.querySelector(`[data-listen="${camId}"]`);
    if (listeningCameraId === camId) {
      stopListening();
      if (btn) { btn.textContent = "🔊"; btn.title = "Listen to camera audio"; }
      return;
    }
    // Switching which camera we're listening to (or starting fresh) --
    // tear down any previous stream first. Same single-RTSP-client
    // concern as live video: don't leave a stale audio connection open
    // on the camera while starting a new one.
    spotlightAudio.pause();
    spotlightAudio.src = `/api/cameras/${camId}/audio?t=${Date.now()}`;
    spotlightAudio.play().catch((err) => {
      PiNVR.toast("Could not start audio: " + err.message, true);
      listeningCameraId = null;
      if (btn) { btn.textContent = "🔊"; btn.title = "Listen to camera audio"; }
    });
    listeningCameraId = camId;
    if (btn) { btn.textContent = "🔇"; btn.title = "Stop listening"; }
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
    catch (err) { /* auto-stops server-side regardless; no need to surface every failure */ }
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
        if (result.supports_ptz) {
          PiNVR.toast("PTZ supported — controls added");
          const cam = cameras.find((c) => c.id === parseInt(detectId, 10));
          if (cam) cam.supports_ptz = true;
          renderedMainCameraId = null;
          renderCurrentView();
        } else {
          PiNVR.toast("This camera does not support ONVIF PTZ", true);
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
