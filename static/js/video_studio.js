(() => {
  const $ = (id) => document.getElementById(id);

  let recentGrid, recentEmpty, uploadZone, uploadInput, browseBtn;
  let durationSelect, selectedList, selectedEmpty, createBtn, statusEl, outputEl;
  let preview, download;

  const PRESETS = [
    { value: "ref_auto", label: "Reference Auto (Vision)" }, // ✅ 추가
    { value: "sunlight_slow_pan", label: "Sunlight • Slow Pan" },
    { value: "sunlight_gentle_parallax", label: "Sunlight • Gentle Parallax" },
    { value: "right_to_left_smooth", label: "Pan • Right → Left" },
    { value: "left_to_right_smooth", label: "Pan • Left → Right" },
    { value: "push_in_slow", label: "Zoom • Push-in (Slow)" },
    { value: "orbit_rotate", label: "Orbit • Rotation (Subtle)" },
    { value: "closeup_luxury_detail", label: "Close-up • Luxury Detail" },
    { value: "default", label: "Default" }
  ];

  let selected = [];

  function setStatus(msg) {
    if (!statusEl) return;
    statusEl.textContent = msg || "";
  }

  function setResult(url) {
    if (!url) return;
    const wrap = $("resultWrap");
    const vid = $("resultVideo");
    const dl = $("downloadLink");
    if (wrap) wrap.classList.remove("hidden");
    if (vid) vid.src = url;
    if (dl) dl.href = url;
  }

  function escapeHtml(s) {
    return (s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function buildPresetSelect(value, onChange) {
    const sel = document.createElement("select");
    sel.className = "vs-select";
    PRESETS.forEach(p => {
      const opt = document.createElement("option");
      opt.value = p.value;
      opt.textContent = p.label;
      if (p.value === value) opt.selected = true;
      sel.appendChild(opt);
    });
    sel.addEventListener("change", (e) => onChange(e.target.value));
    return sel;
  }

  function renderSelected() {
    if (!selectedList) return;
    selectedList.innerHTML = "";

    if (!selected.length) {
      if (selectedEmpty) selectedEmpty.classList.remove("hidden");
      return;
    }
    if (selectedEmpty) selectedEmpty.classList.add("hidden");

    selected.forEach((item, idx) => {
      const row = document.createElement("div");
      row.className = "vs-selected-row";

      row.innerHTML = `
        <div class="vs-selected-thumb">
          <img src="${escapeHtml(item.url)}" alt="thumb" />
        </div>
        <div class="vs-selected-meta">
          <div class="vs-selected-url muted">${escapeHtml(item.url)}</div>
        </div>
        <div class="vs-selected-actions"></div>
      `;

      const actions = row.querySelector(".vs-selected-actions");

      const presetSel = buildPresetSelect(item.preset || "ref_auto", (val) => {
        selected[idx].preset = val;
      });

      const removeBtn = document.createElement("button");
      removeBtn.className = "action-btn";
      removeBtn.textContent = "Remove";
      removeBtn.addEventListener("click", () => {
        selected.splice(idx, 1);
        renderSelected();
      });

      actions.appendChild(presetSel);
      actions.appendChild(removeBtn);

      selectedList.appendChild(row);
    });
  }

  function addSelected(url) {
    if (!url) return;
    if (selected.some(s => s.url === url)) return;
    selected.push({ url, preset: "ref_auto" }); // ✅ 기본 preset만 변경
    renderSelected();
    setStatus("");
  }

  async function uploadFiles(files) {
    if (!files || !files.length) return;

    setStatus("Uploading...");
    try {
      for (const file of files) {
        const fd = new FormData();
        fd.append("file", file);

        const res = await fetch("/api/uploads/upload", {
          method: "POST",
          body: fd,
        });
        if (!res.ok) throw new Error(`upload failed: ${res.status}`);
      }

      setStatus("Upload complete. Refreshing recent images...");
      await refreshRecent();
      setStatus("Ready.");
    } catch (e) {
      console.error(e);
      setStatus("Upload failed. Check server logs.");
    }
  }

  async function apiListRecent() {
    const res = await fetch("/api/outputs/list?limit=200", { cache: "no-store" });
    if (!res.ok) throw new Error(`list failed: ${res.status}`);
    const data = await res.json();
    const items = Array.isArray(data.items) ? data.items : [];
    return items.filter(it => it && it.url);
  }

  function renderRecent(items) {
    if (!recentGrid) return;
    recentGrid.innerHTML = "";

    if (!items.length) {
      if (recentEmpty) recentEmpty.classList.remove("hidden");
      return;
    }
    if (recentEmpty) recentEmpty.classList.add("hidden");

    items.forEach(it => {
      const btn = document.createElement("button");
      btn.className = "vs-thumb";
      btn.type = "button";
      btn.innerHTML = `
        <div class="vs-thumb-img"><img src="${escapeHtml(it.url)}" alt="recent" /></div>
        <div class="vs-thumb-label muted">${escapeHtml(it.filename || "")}</div>
      `;
      btn.addEventListener("click", () => addSelected(it.url));
      recentGrid.appendChild(btn);
    });
  }

  async function refreshRecent() {
    const items = await apiListRecent();
    renderRecent(items);
  }

  function bindUploadUI() {
    if (!uploadZone || !uploadInput || !browseBtn) return;

    browseBtn.addEventListener("click", () => uploadInput.click());
    uploadInput.addEventListener("change", (e) => {
      const files = Array.from(e.target.files || []);
      uploadFiles(files);
      uploadInput.value = "";
    });

    uploadZone.addEventListener("dragover", (e) => {
      e.preventDefault();
      uploadZone.classList.add("dragover");
    });
    uploadZone.addEventListener("dragleave", () => {
      uploadZone.classList.remove("dragover");
    });
    uploadZone.addEventListener("drop", (e) => {
      e.preventDefault();
      uploadZone.classList.remove("dragover");
      const files = Array.from(e.dataTransfer.files || []).filter(f => f.type.startsWith("image/"));
      uploadFiles(files);
    });
  }

  async function pollJob(jobId) {
    const poll = async () => {
      const res = await fetch(`/video-mvp/status/${jobId}`, { cache: "no-store" });
      if (!res.ok) throw new Error(`status failed: ${res.status}`);
      const state = await res.json();
      setStatus(state.message || state.status || "Working...");
      if (state.status === "COMPLETED" && state.result_url) return state.result_url;
      if (state.status === "FAILED") throw new Error(state.error || "job failed");
      return null;
    };

    while (true) {
      const out = await poll();
      if (out) return out;
      await new Promise(r => setTimeout(r, 1200));
    }
  }

  async function startCreateVideo() {
    if (!selected.length) {
      setStatus("Select at least one image.");
      return;
    }

    createBtn.disabled = true;

    // ✅ 문자열로 유지
    const duration = durationSelect?.value || "5";

    const payload = {
      clips: selected.map(s => ({ url: s.url, preset: s.preset || "ref_auto" })),
      duration: String(duration),  // ✅ 명시적으로 문자열 변환
      cfg_scale: 0.85,  // ✅ 0.85로 변경 (서버 기본값과 동일)

      // ✅ auto_ref 동작을 위한 필드(서버에서 backward compatible)
      mode: selected.some(s => (s.preset || "ref_auto") === "ref_auto") ? "auto_ref" : "manual",
      target_total_sec: 20.0,
      include_intro_outro: true
    };

    try {
      setStatus("Creating video (Kling)...");
      const res = await fetch("/video-mvp/create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(`create failed: ${res.status}`);

      const data = await res.json();
      const jobId = data.job_id;
      if (!jobId) throw new Error("missing job_id");

      setStatus("Job started. Generating...");
      const resultUrl = await pollJob(jobId);

      setStatus("Done.");
      setResult(resultUrl);
      createBtn.disabled = false;
    } catch (e) {
      console.error(e);
      setStatus("Failed to start video generation. Check server logs.");
      createBtn.disabled = false;
    }
  }

  function init() {
    recentGrid = $("recentGrid");
    recentEmpty = $("vsRecentEmpty");
    uploadZone = $("uploadZone");
    uploadInput = $("uploadInput");
    browseBtn = $("browseBtn");

    durationSelect = $("durationSelect");
    selectedList = $("selectedList");
    selectedEmpty = $("selectedEmpty");
    createBtn = $("createVideoBtn");
    statusEl = $("status");

    bindUploadUI();

    createBtn?.addEventListener("click", startCreateVideo);

    refreshRecent().catch(e => {
      console.error(e);
      setStatus("Failed to load recent images.");
    });
  }

  window.addEventListener("DOMContentLoaded", init);
})();
