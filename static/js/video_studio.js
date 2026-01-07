(() => {
  const $ = (id) => document.getElementById(id);

  let recentGrid, recentEmpty, uploadZone, uploadInput, browseBtn;
  let selectedList, selectedEmpty, createBtn, statusEl;

  // [유지] Motion Options
  const MOTION_OPTIONS = [
    { value: "static", label: "1. 기본 (움직임 없음)" },
    { value: "orbit_r_slow", label: "2. 회전 (우측 천천히)" },
    { value: "orbit_l_slow", label: "3. 회전 (좌측 천천히)" },
    { value: "orbit_r_fast", label: "4. 회전 (우측 빠르게)" },
    { value: "orbit_l_fast", label: "5. 회전 (좌측 빠르게)" },
    { value: "zoom_in_slow", label: "6. 줌인 (아이레벨 천천히)" },
    { value: "zoom_out_slow", label: "7. 줌아웃 (아이레벨 천천히)" },
    { value: "zoom_in_fast", label: "8. 줌인 (아이레벨 빠르게)" },
    { value: "zoom_out_fast", label: "9. 줌아웃 (아이레벨 빠르게)" }
  ];

  // [유지] Effect Options
  const EFFECT_OPTIONS = [
    { value: "none", label: "효과 없음" },
    { value: "sunlight", label: "1. 햇살 이동" },
    { value: "lights_on", label: "2. 조명 켜짐" },
    { value: "blinds", label: "3. 블라인드/커튼 움직임" },
    { value: "plants", label: "4. 식물 흔들림" },
    { value: "door_open", label: "5. 문 열림" }
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

  // [유지] 드롭다운 생성 헬퍼
  function buildSelect(options, currentValue, onChange) {
    const sel = document.createElement("select");
    sel.className = "vs-select";
    sel.style.width = "100%";
    sel.style.marginBottom = "5px";

    options.forEach(p => {
      const opt = document.createElement("option");
      opt.value = p.value;
      opt.textContent = p.label;
      if (p.value === currentValue) opt.selected = true;
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

      // [수정] 파일명 표시 제거 (깔끔하게 비워둠)
      row.innerHTML = `
        <div class="vs-selected-thumb">
          <img src="${escapeHtml(item.url)}" alt="thumb" />
        </div>
        <div class="vs-selected-meta"></div>
        <div class="vs-selected-actions"></div>
      `;

      const actions = row.querySelector(".vs-selected-actions");
      actions.style.display = "flex";
      actions.style.flexDirection = "column";
      actions.style.gap = "4px";

      // Motion Select
      const motionSel = buildSelect(MOTION_OPTIONS, item.motion || "static", (val) => {
        selected[idx].motion = val;
      });

      // Effect Select
      const effectSel = buildSelect(EFFECT_OPTIONS, item.effect || "none", (val) => {
        selected[idx].effect = val;
      });

      const removeBtn = document.createElement("button");
      removeBtn.className = "action-btn";
      removeBtn.textContent = "Remove";
      removeBtn.addEventListener("click", () => {
        selected.splice(idx, 1);
        renderSelected();
      });

      actions.appendChild(motionSel);
      actions.appendChild(effectSel);
      actions.appendChild(removeBtn);

      selectedList.appendChild(row);
    });
  }

  // [수정] 파일명 인자 제거
  function addSelected(url) {
    if (!url) return;
    if (selected.some(s => s.url === url)) return;

    // 파일명 저장 없이 url과 설정값만 저장
    selected.push({ url, motion: "static", effect: "none" });
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

        const res = await fetch("/api/outputs/upload", {
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
      // 썸네일 그리드 쪽 라벨은 유지할지 여부를 말씀 안하셔서 일단 유지하되,
      // 선택 시 addSelected에는 url만 넘깁니다.
      btn.innerHTML = `
        <div class="vs-thumb-img"><img src="${escapeHtml(it.url)}" alt="recent" /></div>
        <div class="vs-thumb-label muted">${escapeHtml(it.filename || "")}</div>
      `;
      // [수정] 파일명 전달 제거
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

    // [유지] Motion/Effect 값 전송
    const payload = {
      clips: selected.map(s => ({
        url: s.url,
        motion: s.motion || "static",
        effect: s.effect || "none"
      })),
      duration: "5",
      cfg_scale: 0.85,
      mode: "manual",
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