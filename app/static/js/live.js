(async function () {
  const grid = document.getElementById("liveGrid");
  let cameras = [];
  let layoutCount = 4;

  async function loadCameras() {
    try {
      cameras = await PiNVR.api("/cameras");
    } catch (e) {
      PiNVR.toast(e.message, true);
      cameras = [];
    }
    render();
  }

  function render() {
    grid.innerHTML = "";
    const visible = cameras.slice(0, layoutCount);
    grid.style.gridTemplateColumns = layoutCount === 1
      ? "1fr"
      : "repeat(auto-fill, minmax(260px, 1fr))";

    visible.forEach((cam) => {
      const tile = document.createElement("div");
      tile.className = "camera-tile";
      tile.innerHTML = `
        <img src="/api/cameras/${cam.id}/mjpeg" alt="${cam.name}" />
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

  loadCameras();
})();
