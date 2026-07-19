(async function () {
  const grid = document.getElementById("liveGrid");
  let cameras = [];
  let layoutCount = 4;
  let renderedCameraIds = null; // tracks which cameras currently have live tiles/connections

  async function loadCameras() {
    try {
      cameras = await PiNVR.api("/cameras");
    } catch (e) {
      PiNVR.toast(e.message, true);
      cameras = [];
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

    visible.forEach((cam) => {
      const tile = document.createElement("div");
      tile.className = "camera-tile";
      // Cache-bust with a timestamp so this is always a genuinely fresh
      // request, never something the browser decides to reuse/restore
      // from cache -- important for a live multipart stream, where a
      // stale cached "connection" is worse than useless.
      tile.innerHTML = `
        <img src="/api/cameras/${cam.id}/mjpeg?t=${Date.now()}" alt="${cam.name}" />
        <div class="tile-label">
          ${cam.name}
          <button class="btn" data-snapshot="${cam.id}" style="float:right; padding:2px 8px;">Snap</button>
        </div>`;
      grid.appendChild(tile);
    });

    if (visible.length === 0) {
      grid.innerHTML = `<div class="card">No cameras configured yet. Add one from the Cameras page.</div>`;
    }
  }

  grid.addEventListener("click", async (e) => {
    const camId = e.target.getAttribute("data-snapshot");
    if (!camId) return;
    try {
      await PiNVR.api(`/playback/snapshot/${camId}`, { method: "POST" });
      PiNVR.toast("Snapshot captured");
    } catch (err) {
      PiNVR.toast(err.message, true);
    }
  });

  document.querySelectorAll("[data-layout]").forEach((btn) => {
    btn.addEventListener("click", () => {
      layoutCount = parseInt(btn.dataset.layout, 10);
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
