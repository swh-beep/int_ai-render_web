(() => {
  const $ = (id) => document.getElementById(id);

  let recentGrid, recentEmpty, uploadZone, uploadInput, browseBtn;
  let durationSelect, selectedList, selectedEmpty, createBtn, statusEl, outputEl;
  let preview, download;

  const PRESETS = [
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

  function escapeHtml(str) {
    return String(str).replace(/[&<>"']/g, (m) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#039;",
    }[m]));
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
      if (recentEmpty) recentEmpty.style.display = "block";
      return;
    }
    if (recentEmpty) recentEmpty.style.display = "none";

    for (const it of items) {
      const div = document.createElement("div");
      div.className = "vs-thumb"; // 기존 클래스 유지
      div.title = it.filename || it.url;
      div.dataset.url = it.url;

      // [UX Improvement] 업스케일 된 파일(magnific_)인지 확인
      const isUpscaled = (it.filename || "").toLowerCase().includes("magnific");

      // 썸네일 컨테이너 (position: relative 필요)
      div.style.position = "relative";

      const img = document.createElement("img");
      img.loading = "lazy";
      img.src = it.url;
      img.alt = it.filename || "image";
      div.appendChild(img);

      // [UX] 고화질 파일이면 'HQ' 배지 부착
      if (isUpscaled) {
        const badge = document.createElement("span");
        badge.textContent = "HQ";
        badge.style.position = "absolute";
        badge.style.top = "6px";
        badge.style.right = "6px";
        badge.style.background = "rgba(74, 18, 21, 0.9)"; // 브랜드 컬러
        badge.style.color = "#fff";
        badge.style.fontSize = "10px";
        badge.style.fontWeight = "bold";
        badge.style.padding = "2px 6px";
        badge.style.borderRadius = "4px";
        badge.style.boxShadow = "0 2px 4px rgba(0,0,0,0.5)";
        div.appendChild(badge);
      }

      // 파일명 오버레이 (선택 사항 - 원하시면 주석 해제)
      /*
      const label = document.createElement("div");
      label.textContent = isUpscaled ? "High Res" : "Raw";
      label.style.position = "absolute";
      label.style.bottom = "0";
      label.style.left = "0";
      label.style.width = "100%";
      label.style.background = "rgba(0,0,0,0.6)";
      label.style.color = "#fff";
      label.style.fontSize = "10px";
      label.style.padding = "4px";
      label.style.textAlign = "center";
      div.appendChild(label);
      */

      div.addEventListener("click", () => addSelected(it.url));
      recentGrid.appendChild(div);
    }
  }

  function presetSelectHtml(current) {
    const opts = PRESETS.map(p => {
      const sel = p.value === current ? "selected" : "";
      return `<option value="${p.value}" ${sel}>${escapeHtml(p.label)}</option>`;
    }).join("");
    return `<select class="vs-select" data-role="preset">${opts}</select>`;
  }

  function renderSelected() {
    if (!selectedList) return;

    Array.from(selectedList.children).forEach(child => {
      if (child !== selectedEmpty) {
        child.remove();
      }
    });

    if (!selected.length) {
      if (selectedEmpty) selectedEmpty.style.display = "block";
      return;
    }
    if (selectedEmpty) selectedEmpty.style.display = "none";

    selected.forEach((clip, idx) => {
      const row = document.createElement("div");
      row.className = "vs-selected-row";
      row.dataset.idx = String(idx);

      row.innerHTML = `
        <div class="thumb"><img src="${clip.url}" alt="thumb" /></div>
        <div style="min-width:0;">
          <div style="font-weight:600; font-size:0.95rem;">Clip ${idx + 1}</div>
          <div class="vs-upload-hint" style="white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${escapeHtml(clip.url)}</div>
        </div>
        ${presetSelectHtml(clip.preset)}
        <button class="vs-mini-btn" type="button" data-role="remove" title="Remove">✕</button>
      `;

      const presetSel = row.querySelector('select[data-role="preset"]');
      presetSel?.addEventListener("change", (e) => {
        const val = e.target.value;
        selected[idx].preset = val;
      });

      const removeBtn = row.querySelector('button[data-role="remove"]');
      removeBtn?.addEventListener("click", () => {
        selected.splice(idx, 1);
        renderSelected();
      });

      selectedList.appendChild(row);
    });
  }

  function addSelected(url) {
    if (!url) return;
    if (selected.some(s => s.url === url)) return;
    selected.push({ url, preset: "sunlight_slow_pan" });
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
          body: fd
        });
        if (!res.ok) {
          const txt = await res.text().catch(() => "");
          throw new Error(`upload failed (${res.status}): ${txt}`);
        }
        const data = await res.json();
        if (data && data.url) {
          addSelected(data.url);
        }
      }
      await refreshRecent();
      setStatus("");
    } catch (err) {
      console.error(err);
      setStatus("Upload failed. Check server logs.");
    }
  }

  async function refreshRecent() {
    try {
      const items = await apiListRecent();
      renderRecent(items);
    } catch (e) {
      console.error(e);
      renderRecent([]);
      setStatus("Failed to load recent images. Is the server running?");
    }
  }

  async function createVideo() {
    if (!selected.length) return;

    outputEl.innerHTML = "";
    createBtn.disabled = true;

    // ✅ 문자열로 유지
    const duration = durationSelect?.value || "5";

    const payload = {
      clips: selected.map(s => ({ url: s.url, preset: s.preset || "default" })),
      duration: String(duration),  // ✅ 명시적으로 문자열 변환
      cfg_scale: 0.85  // ✅ 0.85로 변경 (서버 기본값과 동일)
    };

    try {
      setStatus("Creating video (Kling)...");
      const res = await fetch("/video-mvp/create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });

      if (!res.ok) {
        const txt = await res.text().catch(() => "");
        throw new Error(`create failed (${res.status}): ${txt}`);
      }
      const data = await res.json();
      const jobId = data.job_id;
      if (!jobId) throw new Error("missing job_id");

      await pollJob(jobId);
    } catch (e) {
      console.error(e);
      setStatus("Failed to start video generation. Check server logs.");
      createBtn.disabled = false;
    }
  }

  async function pollJob(jobId) {
    const poll = async () => {
      try {
        const res = await fetch(`/video-mvp/status/${jobId}`, { cache: "no-store" });
        if (!res.ok) throw new Error(`status failed: ${res.status}`);

        const state = await res.json();

        // ✅ 방어적 코딩: state가 객체인지 확인
        if (typeof state !== 'object' || state === null) {
          console.error("Invalid state response:", state);
          throw new Error("Invalid response format");
        }

        const msg = state.message || state.status || "";
        const progress = state.progress ? ` (${state.progress}%)` : "";

        if (state.status === "COMPLETED") {
          setStatus("Done.");
          createBtn.disabled = false;

          const url = state.result_url;
          if (url) {
            outputEl.innerHTML = `
            <div class="vs-subtitle" style="margin-bottom:10px;">Output</div>
            <video src="${url}" controls style="width:100%; border-radius: 16px; border: 1px solid rgba(255,255,255,0.12);"></video>
            <div style="margin-top:10px;">
              <a href="${url}" download class="vs-mini-btn" style="display:inline-flex; text-decoration:none;">Download</a>
            </div>
          `;
          } else {
            outputEl.innerHTML = `<div class="vs-subtitle">Video complete, but no output URL was returned.</div>`;
          }
          refreshRecent();
          return;
        }

        if (state.status === "FAILED") {
          setStatus(`Error: ${state.error || "Unknown error"}`);
          createBtn.disabled = false;
          return;
        }

        setStatus(`${msg}${progress}`);
        setTimeout(poll, 1500);

      } catch (e) {
        console.error("Poll error:", e);
        setStatus(`Error: ${e.message}`);
        createBtn.disabled = false;
      }
    };

    await poll();
  }

  document.addEventListener("DOMContentLoaded", () => {
    recentGrid = $("recentGrid");
    recentEmpty = $("vsRecentEmpty");
    uploadZone = $("uploadDrop");
    uploadInput = $("fileInput");
    browseBtn = $("browseBtn");
    durationSelect = $("clipDuration");
    selectedList = $("vsSelectedList");
    selectedEmpty = $("vsSelectedEmpty");
    createBtn = $("createVideoBtn");
    statusEl = $("status");
    outputEl = $("resultWrap");
    preview = $("resultVideo");
    download = $("downloadLink");

    browseBtn?.addEventListener("click", () => uploadInput?.click());

    uploadInput?.addEventListener("change", (e) => {
      const files = e.target.files ? Array.from(e.target.files) : [];
      e.target.value = "";
      uploadFiles(files);
    });

    if (uploadZone) {
      const prevent = (e) => { e.preventDefault(); e.stopPropagation(); };
      ["dragenter", "dragover", "dragleave", "drop"].forEach(ev =>
        uploadZone.addEventListener(ev, prevent)
      );

      uploadZone.addEventListener("dragenter", () => uploadZone.classList.add("dragover"));
      uploadZone.addEventListener("dragover", () => uploadZone.classList.add("dragover"));
      uploadZone.addEventListener("dragleave", () => uploadZone.classList.remove("dragover"));
      uploadZone.addEventListener("drop", (e) => {
        uploadZone.classList.remove("dragover");
        const files = e.dataTransfer?.files ? Array.from(e.dataTransfer.files) : [];
        uploadFiles(files);
      });
    }

    createBtn?.addEventListener("click", createVideo);

    if (outputEl) outputEl.classList.add("hidden");
    renderSelected();
    refreshRecent();
  });
})();