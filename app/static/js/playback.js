(async function () {
  const cameraSelect = document.getElementById("cameraSelect");
  const monthSelect = document.getElementById("monthSelect");
  const filterType = document.getElementById("filterType");
  const calendarDays = document.getElementById("calendarDays");
  const recordingsBody = document.getElementById("recordingsBody");
  const player = document.getElementById("player");

  const now = new Date();
  monthSelect.value = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;

  function fmtBytes(n) {
    if (n == null) return "--";
    const units = ["B", "KB", "MB", "GB"];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(1)} ${units[i]}`;
  }

  async function loadCameras() {
    const cameras = await PiNVR.api("/cameras");
    cameraSelect.innerHTML = cameras.map((c) => `<option value="${c.id}">${c.name}</option>`).join("");
    if (cameras.length) loadAll();
  }

  async function loadCalendar() {
    const camId = cameraSelect.value;
    if (!camId) return;
    const [year, month] = monthSelect.value.split("-").map(Number);
    try {
      const days = await PiNVR.api(`/playback/calendar?camera_id=${camId}&year=${year}&month=${month}`);
      calendarDays.innerHTML = days.length
        ? days.map((d) => `<div style="display:flex; justify-content:space-between; font-family:var(--font-mono); font-size:12px; padding:4px 0; color:var(--text-dim);">
             <span>${d.date}</span><span>${d.recording_count} rec / ${d.motion_event_count} mtn</span>
           </div>`).join("")
        : `<div style="color:var(--text-faint); font-size:12px;">No activity this month.</div>`;
    } catch (e) { PiNVR.toast(e.message, true); }
  }

  async function loadRecordings() {
    const camId = cameraSelect.value;
    if (!camId) return;
    try {
      const recordings = await PiNVR.api(`/recordings?camera_id=${camId}`);
      const filtered = filterType.value === "motion"
        ? recordings.filter((r) => r.trigger === "motion")
        : recordings;

      recordingsBody.innerHTML = filtered.map((r) => `
        <tr>
          <td>${new Date(r.started_at).toLocaleString()}</td>
          <td>${r.trigger}</td>
          <td>${r.duration_seconds ? r.duration_seconds.toFixed(0) + "s" : "--"}</td>
          <td>${fmtBytes(r.size_bytes)}</td>
          <td style="white-space:nowrap;">
            <button class="btn" data-play="${r.id}" style="padding:4px 8px;">Play</button>
            <a class="btn" href="/api/playback/download/${r.id}" style="padding:4px 8px;">DL</a>
            <button class="btn btn-danger" data-delete="${r.id}" ${r.locked ? "disabled" : ""} style="padding:4px 8px;">Del</button>
          </td>
        </tr>`).join("");
    } catch (e) { PiNVR.toast(e.message, true); }
  }

  function loadAll() {
    loadCalendar();
    loadRecordings();
  }

  recordingsBody.addEventListener("click", async (e) => {
    const playId = e.target.getAttribute("data-play");
    const deleteId = e.target.getAttribute("data-delete");
    if (playId) {
      player.src = `/api/playback/stream/${playId}`;
      player.play().catch(() => {});
    }
    if (deleteId) {
      if (!confirm("Delete this recording?")) return;
      try {
        await PiNVR.api(`/playback/${deleteId}`, { method: "DELETE" });
        PiNVR.toast("Recording deleted");
        loadRecordings();
      } catch (err) { PiNVR.toast(err.message, true); }
    }
  });

  cameraSelect.addEventListener("change", loadAll);
  monthSelect.addEventListener("change", loadCalendar);
  filterType.addEventListener("change", loadRecordings);

  loadCameras();
})();
