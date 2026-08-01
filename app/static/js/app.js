/* Pi-NVR shared frontend logic. No build step, no framework -- plain ES
 * modules-free JS so it runs unmodified on any browser hitting the Pi
 * directly or over Tailscale. */

const PiNVR = (() => {
  async function api(path, options = {}) {
    const opts = Object.assign({ credentials: "same-origin" }, options);
    if (opts.body && typeof opts.body !== "string") {
      opts.body = JSON.stringify(opts.body);
      opts.headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
    }
    const res = await fetch(`/api${path}`, opts);
    if (res.status === 401) {
      window.location.href = "/login";
      throw new Error("Not authenticated");
    }
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const body = await res.json();
        detail = body.detail || detail;
      } catch (_) { /* no JSON body */ }
      throw new Error(detail);
    }
    if (res.status === 204) return null;
    const contentType = res.headers.get("content-type") || "";
    return contentType.includes("application/json") ? res.json() : res.text();
  }

  function toast(message, isError = false) {
    const root = document.getElementById("toastRoot");
    if (!root) return;
    const el = document.createElement("div");
    el.className = "toast" + (isError ? " error" : "");
    el.textContent = message;
    root.appendChild(el);
    setTimeout(() => el.remove(), 4500);
  }

  function markActiveNav() {
    const path = window.location.pathname;
    document.querySelectorAll(".nav-link[data-path]").forEach((el) => {
      el.classList.toggle("active", el.dataset.path === path);
    });
  }

  async function refreshStatusRail() {
    const rail = document.getElementById("statusRail");
    if (!rail) return;
    try {
      const [cameras, statusMap] = await Promise.all([
        api("/cameras"),
        api("/cameras/status/all"),
      ]);
      rail.innerHTML = "";
      cameras.forEach((cam) => {
        const s = statusMap[cam.id];
        const dot = document.createElement("div");
        dot.className = "rail-dot";
        dot.title = `${cam.name}: ${s && s.online ? "online" : "offline"}`;
        if (s && s.online) {
          dot.classList.add("online");
          if (cam.recording_mode !== "off") dot.classList.add("recording");
        } else {
          dot.classList.add("offline");
        }
        rail.appendChild(dot);
      });
    } catch (_) {
      /* dashboard not authed yet, or no cameras -- silently skip */
    }
  }

  function formatPercent(n) {
    return `${Math.round(n)}%`;
  }

  async function refreshTopbarStats() {
    const el = document.getElementById("topbarStats");
    if (!el) return;
    try {
      const stats = await api("/system/stats");
      const sys = stats.system;
      el.querySelector('[data-stat="cpu"]').textContent = `CPU ${formatPercent(sys.cpu_percent)}`;
      el.querySelector('[data-stat="ram"]').textContent = `RAM ${formatPercent(sys.ram.percent)}`;
      const tempEl = el.querySelector('[data-stat="temp"]');
      if (sys.temperature_celsius != null) {
        tempEl.textContent = `${sys.temperature_celsius.toFixed(1)}\u00B0C`;
        tempEl.classList.toggle("warn", sys.temperature_celsius > 75);
      } else {
        tempEl.textContent = "N/A";
      }
      const diskEl = el.querySelector('[data-stat="disk"]');
      diskEl.textContent = `DISK ${formatPercent(sys.disk.percent)}`;
      diskEl.classList.toggle("warn", sys.disk.percent > 90);
    } catch (_) { /* not authenticated yet */ }
  }

  const EVENT_MESSAGES = {
    motion: (data) => `Motion: ${data.camera_name}`,
    camera_offline: (data) => `${data.camera_name} went offline`,
    camera_online: (data) => `${data.camera_name} is back online`,
    storage_full: (data) => `Storage full: ${data.target_name || "a storage target"}`,
    low_disk_space: (data) => `Low disk space: ${data.target_name || "a storage target"}`,
    recording_stopped: (data) => `Recording stopped: ${data.camera_name || "a camera"}`,
  };

  function maybeRequestNotificationPermission() {
    if (!("Notification" in window)) return;
    if (Notification.permission === "default") {
      // Best-effort -- some browsers only honor this from a direct user
      // gesture and will silently ignore it here, which is fine; toasts
      // remain the fallback either way.
      Notification.requestPermission().catch(() => {});
    }
  }

  function showBrowserNotification(title, body) {
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    // Only worth the OS-level notification if the tab isn't the one
    // currently in focus -- if someone's already looking at Pi-NVR, the
    // in-page toast is enough and a duplicate OS popup is just noise.
    if (document.visibilityState === "visible") return;
    try {
      new Notification(title, { body });
    } catch (_) { /* not fatal -- toast already covers this event */ }
  }

  function connectWebSocket(onEvent) {
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${window.location.host}/api/ws`);
    ws.onmessage = (msg) => {
      try {
        const parsed = JSON.parse(msg.data);
        const messageFn = EVENT_MESSAGES[parsed.type];
        if (messageFn) {
          const text = messageFn(parsed.data || {});
          const isProblem = parsed.type === "camera_offline" || parsed.type === "storage_full" || parsed.type === "low_disk_space";
          toast(text, isProblem);
          showBrowserNotification("Pi-NVR", text);
          if (parsed.type === "camera_offline" || parsed.type === "camera_online") {
            refreshStatusRail(); // don't wait up to 10s for the next scheduled poll
          }
        }
        if (onEvent) onEvent(parsed);
      } catch (_) { /* ignore malformed frames */ }
    };
    ws.onclose = () => {
      // Reconnect with a flat backoff; a Pi 3 NVR's browser session is
      // typically a single always-open tab, so simplicity beats a fancy
      // exponential-backoff scheme here.
      setTimeout(() => connectWebSocket(onEvent), 4000);
    };
    return ws;
  }

  async function logout() {
    try {
      await api("/auth/logout", { method: "POST" });
    } finally {
      window.location.href = "/login";
    }
  }

  function init() {
    markActiveNav();
    const logoutBtn = document.getElementById("logoutBtn");
    if (logoutBtn) logoutBtn.addEventListener("click", logout);

    if (document.getElementById("statusRail")) {
      refreshStatusRail();
      setInterval(refreshStatusRail, 10000);
    }
    if (document.getElementById("topbarStats")) {
      refreshTopbarStats();
      setInterval(refreshTopbarStats, 5000);
    }
    if (document.getElementById("statusRail") || document.getElementById("topbarStats")) {
      connectWebSocket();
      maybeRequestNotificationPermission();
    }
  }

  document.addEventListener("DOMContentLoaded", init);

  return { api, toast, connectWebSocket, formatPercent };
})();
