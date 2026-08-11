(async function () {
  const cameraSelect = document.getElementById("cameraSelect");
  const monthSelect = document.getElementById("monthSelect");
  const filterType = document.getElementById("filterType");
  const calendarDays = document.getElementById("calendarDays");
  const recordingsBody = document.getElementById("recordingsBody");
  const videoEl = document.getElementById("player");
  const searchStartDate = document.getElementById("searchStartDate");
  const searchStartTime = document.getElementById("searchStartTime");
  const searchEndDate = document.getElementById("searchEndDate");
  const searchEndTime = document.getElementById("searchEndTime");

  // Plyr gives a consistent skin/control layout across browsers (native
  // <video> controls look different on Safari vs Chrome vs mobile) and
  // adds speed control for free. Falls back to the plain native <video>
  // element (still fully functional) if the CDN script didn't load --
  // e.g. offline/local-network-only use where cdnjs isn't reachable.
  const player = (typeof Plyr !== "undefined")
    ? new Plyr(videoEl, {
        controls: [
          "play-large", "rewind", "play", "fast-forward", "progress",
          "current-time", "duration", "mute", "volume", "settings",
          "pip", "airplay", "fullscreen",
        ],
        speed: { selected: 1, options: [0.25, 0.5, 1, 1.5, 2] },
      })
    : null;

  function setVideoSource(url) {
    if (player) {
      player.source = { type: "video", sources: [{ src: url, type: "video/mp4" }] };
      player.play().catch(() => {});
    } else {
      videoEl.src = url;
      videoEl.play().catch(() => {});
    }
  }

  let activeSearchRange = null; // {start, end} while a date/time search is active, else null

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
      const params = new URLSearchParams({ camera_id: camId });
      if (activeSearchRange) {
        if (activeSearchRange.start) params.set("start", activeSearchRange.start);
        if (activeSearchRange.end) params.set("end", activeSearchRange.end);
      }
      if (filterType.value === "motion") params.set("trigger", "motion");

      const recordings = await PiNVR.api(`/recordings?${params.toString()}`);

      recordingsBody.innerHTML = recordings.map((r) => `
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
        </tr>`).join("") || `<tr><td colspan="5" style="color:var(--text-faint);">No recordings match.</td></tr>`;
    } catch (e) { PiNVR.toast(e.message, true); }
  }

  function loadAll() {
    loadCalendar();
    loadRecordings();
  }

  function combineDateTime(dateVal, timeVal, isEnd) {
    if (!dateVal) return null;
    // If a date is picked with no time, default to the very start/end of
    // that day so "search just a date" behaves like "search that whole day".
    const time = timeVal || (isEnd ? "23:59" : "00:00");
    return `${dateVal}T${time}:00`;
  }

  document.getElementById("searchBtn").addEventListener("click", () => {
    const start = combineDateTime(searchStartDate.value, searchStartTime.value, false);
    const end = combineDateTime(searchEndDate.value, searchEndTime.value, true);
    if (!start && !end) {
      PiNVR.toast("Enter at least a start or end date to search", true);
      return;
    }
    activeSearchRange = { start, end };
    PiNVR.toast("Searching…");
    loadRecordings();
  });

  document.getElementById("clearSearchBtn").addEventListener("click", () => {
    activeSearchRange = null;
    searchStartDate.value = "";
    searchStartTime.value = "";
    searchEndDate.value = "";
    searchEndTime.value = "";
    loadRecordings();
  });

  recordingsBody.addEventListener("click", async (e) => {
    const playId = e.target.getAttribute("data-play");
    const deleteId = e.target.getAttribute("data-delete");
    if (playId) {
      setVideoSource(`/api/playback/stream/${playId}`);
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
