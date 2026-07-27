(async function () {
  const grid = document.getElementById("liveGrid");
  let cameras = [];
  let layoutCount = 4;
  let renderedCameraIds = null; // tracks which cameras currently have live tiles/connections
  const FAVORITE_KEY = "pinvr_favorite_camera_id";

  function getFavoriteId() {
    const raw = localStorage.getItem(FAVORITE_KEY);
    return raw ? parseInt(raw, 10) : null;
  }

  function setFavoriteId(id) {
    if (id == null) {
      localStorage.removeItem(FAVORITE_KEY);
    } else {
      localStorage.setItem(FAVORITE_KEY, String(id));
    }
  }

  async function loadCameras() {
    try {
      cameras = await PiNVR.api("/cameras");
    } catch (e) {
      PiNVR.toast(e.message, true);
      cameras = [];
    }

    const favoriteId = getFavoriteId();
    if (favoriteId != null && cameras.some((c) => c.id === favoriteId)) {
      // Favorite goes first, and Live view defaults to showing just it
      // (layout 1) on arrival -- the whole point of marking one as a
      // favorite is not having to hunt for it in a multi-camera grid.
      cameras.sort((a, b) => (a.id === favoriteId ? -1 : b.id === favoriteId ? 1 : 0));
      layoutCount = 1;
      document.querySelectorAll("[data-layout]").forEach((btn) => {
        btn.classList.toggle("active", btn.dataset.layout === "1");
      });
    }
    render();
  }

  function applyGridStyle() {
    grid.style.gridTemplateColumns = layoutCount === 1
      ? "1fr"
      : "repeat(auto-fill, minmax(260px, 1fr))";
  }

  function render() {
    const visible = cameras.slice(0, layoutCount);
    const visibleIds = visible.map((c) => c.id).join(",");

    // If the same set of cameras is already rendered, this is a pure
    // layout-arrangement change (e.g. clicking 1 -> 4 -> 9 with only one
    // camera configured) -- just restyle the grid, don't tear down and
    // reconnect streams that are already working fine.
    if (renderedCameraIds === visibleIds) {
      applyGridStyle();
      return;
    }
    renderedCameraIds = visibleIds;

    grid.innerHTML = "";
    applyGridStyle();

    const favoriteId = getFavoriteId();
    visible.forEach((cam) => {
      const tile = document.createElement("div");
      tile.className = "camera-tile";
      const isFavorite = cam.id === favoriteId;
      // Cache-bust with a timestamp so this is always a genuinely fresh
      // request, never something the browser decides to reuse/restore
      // from cache -- important for a live multipart stream, where a
      // stale cached "connection" is worse than useless.
      tile.innerHTML = `
        <img src="/api/cameras/${cam.id}/mjpeg?t=${Date.now()}" alt="${cam.name}" />
        <div class="tile-label">
          ${cam.name}
          <span style="float:right; display:flex; gap:4px;">
            <button class="btn" data-favorite="${cam.id}" title="${isFavorite ? 'Unset favorite' : 'Set as favorite'}"
                    style="padding:2px 8px; ${isFavorite ? 'color:var(--amber); border-color:var(--amber-dim);' : ''}">${isFavorite ? '★' : '☆'}</button>
            <button class="btn" data-snapshot="${cam.id}" style="padding:2px 8px;">Snap</button>
          </span>
        </div>`;
      grid.appendChild(tile);
    });

    if (visible.length === 0) {
      grid.innerHTML = `<div class="card">No cameras configured yet. Add one from the Cameras page.</div>`;
    }
  }

  grid.addEventListener("click", async (e) => {
    const camId = e.target.getAttribute("data-snapshot");
    const favoriteId = e.target.getAttribute("data-favorite");
    if (camId) {
      try {
        await PiNVR.api(`/playback/snapshot/${camId}`, { method: "POST" });
        PiNVR.toast("Snapshot captured");
      } catch (err) {
        PiNVR.toast(err.message, true);
      }
      return;
    }
    if (favoriteId) {
      const id = parseInt(favoriteId, 10);
      const isCurrentlyFavorite = getFavoriteId() === id;
      setFavoriteId(isCurrentlyFavorite ? null : id);
      renderedCameraIds = null; // force a re-render so the star + ordering update
      loadCameras();
    }
  });

  document.querySelectorAll("[data-layout]").forEach((btn) => {
    btn.addEventListener("click", () => {
      layoutCount = parseInt(btn.dataset.layout, 10);
      document.querySelectorAll("[data-layout]").forEach((b) => b.classList.toggle("active", b === btn));
      render();
    });
  });

  document.getElementById("fullscreenBtn").addEventListener("click", () => {
    if (!document.fullscreenElement) {
      grid.requestFullscreen().catch(() => PiNVR.toast("Fullscreen not available", true));
    } else {
      document.exitFullscreen();
    }
  });

  // Safari (and other browsers) can restore this entire page from the
  // back-forward cache on navigation instead of actually reloading it --
  // meaning none of our JS re-runs and the browser just shows the frozen
  // last frame of a connection the server already correctly closed. The
  // `pageshow` event fires on both a normal load AND a bfcache restore;
  // `event.persisted` tells us which one happened. On a bfcache restore,
  // force a real re-render so fresh, cache-busted requests actually go
  // out and hit our reconnect-with-retry logic server-side.
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) {
      renderedCameraIds = null; // force a real reconnect, not just a layout restyle
      render();
    }
  });

  loadCameras();
})();
