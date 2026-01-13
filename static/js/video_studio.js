(() => {
  const $ = (id) => document.getElementById(id);

  // --- Data ---
  let sourceClips = [];
  let timelineClips = [];

  // UI State
  let selectedTimelineIndex = -1;
  let pxPerSec = 40;
  let dragSrcIndex = null;
  let isScrubbing = false;
  let isPlaying = false;
  let animationFrameId = null;
  let currentPlayTime = 0;

  // --- DOM Elements ---
  let uploadInput, uploadZone, btnGenSource, btnExportFinal, btnPlay;
  let listEl, listEmptyEl, statusSourceEl, statusFinalEl;
  let timelineContainer, timelineTrack, timelineRuler, tlPropsDiv, playhead;
  let previewImg, previewVid, resultOverlay, resultVideo, previewPlaceholder;
  let inpTlSpeed, inpTlStart, inpTlEnd;

  const MOTION_OPTS = [
    { v: "static", l: "기본 (움직임 없음)" },
    { v: "orbit_r_slow", l: "회전 (우측 천천히)" },
    { v: "orbit_l_slow", l: "회전 (좌측 천천히)" },
    { v: "orbit_r_fast", l: "회전 (우측 빠르게)" },
    { v: "orbit_l_fast", l: "회전 (좌측 빠르게)" },
    { v: "zoom_in_slow", l: "줌인 (천천히)" },
    { v: "zoom_out_slow", l: "줌아웃 (천천히)" },
    { v: "zoom_in_fast", l: "줌인 (빠르게)" },
    { v: "zoom_out_fast", l: "줌아웃 (빠르게)" }
  ];

  const EFFECT_OPTS = [
    { v: "none", l: "효과 없음" },
    { v: "sunlight", l: "햇살 이동" },
    { v: "lights_on", l: "조명 켜짐" },
    { v: "blinds", l: "블라인드/커튼" },
    { v: "plants", l: "식물 흔들림" },
    { v: "door_open", l: "문 열림" }
  ];

  // -------------------------------------------------------
  // Step 1: Source Manager
  // -------------------------------------------------------
  function renderSourceList() {
    if (!listEl) return;
    listEl.innerHTML = "";

    if (sourceClips.length === 0) {
      if (listEmptyEl) listEmptyEl.classList.remove("hidden");
      return;
    }
    if (listEmptyEl) listEmptyEl.classList.add("hidden");

    sourceClips.forEach((item, idx) => {
      const row = document.createElement("div");
      row.className = "vs-source-item";

      let statusIcon = '';
      if (item.status === 'ready') {
        statusIcon = '<span style="color:#999; font-weight:bold; font-size:16px;">✔</span>';
      } else if (item.status === 'generating') {
        statusIcon = '<div class="vs-spinner"></div>';
      }

      const isDisabled = (item.status === 'ready' || item.status === 'generating') ? 'disabled' : '';

      row.innerHTML = `
            <img src="${item.url}" class="vs-source-thumb">
            <div class="vs-source-controls">
                <select class="vs-select-mini js-motion" data-idx="${idx}" ${isDisabled}>
                    ${MOTION_OPTS.map(o => `<option value="${o.v}" ${item.motion === o.v ? 'selected' : ''}>${o.l}</option>`).join('')}
                </select>
                <select class="vs-select-mini js-effect" data-idx="${idx}" ${isDisabled}>
                    ${EFFECT_OPTS.map(o => `<option value="${o.v}" ${item.effect === o.v ? 'selected' : ''}>${o.l}</option>`).join('')}
                </select>
            </div>
            <div style="width:30px; text-align:center;">${statusIcon}</div>
            <button class="icon-btn js-del" data-idx="${idx}" style="color:#777;">×</button>
        `;
      listEl.appendChild(row);
    });

    listEl.querySelectorAll('.js-motion').forEach(el => el.onchange = (e) => sourceClips[e.target.dataset.idx].motion = e.target.value);
    listEl.querySelectorAll('.js-effect').forEach(el => el.onchange = (e) => sourceClips[e.target.dataset.idx].effect = e.target.value);
    listEl.querySelectorAll('.js-del').forEach(el => el.onclick = (e) => {
      if (confirm("Remove?")) { sourceClips.splice(e.target.dataset.idx, 1); renderSourceList(); }
    });
  }

  function addSourceFiles(files) {
    if (!files || !files.length) return;
    if (statusSourceEl) statusSourceEl.textContent = "Uploading...";
    let loadedCount = 0;

    // 이미지 파일만 필터링
    const validFiles = files.filter(f => f.type.startsWith("image/"));
    if (validFiles.length === 0) {
      if (statusSourceEl) statusSourceEl.textContent = "Images only.";
      return;
    }

    validFiles.forEach(f => {
      const fd = new FormData(); fd.append("file", f);
      fetch("/api/outputs/upload", { method: "POST", body: fd })
        .then(r => r.json()).then(d => {
          sourceClips.push({
            id: Math.random().toString(36).substr(2, 9),
            url: d.url,
            motion: "static",
            effect: "none",
            status: 'idle',
            videoUrl: null
          });
          loadedCount++;
          if (loadedCount === validFiles.length) {
            if (statusSourceEl) statusSourceEl.textContent = "";
            renderSourceList();
          }
        }).catch(e => { console.error(e); if (statusSourceEl) statusSourceEl.textContent = "Upload failed."; });
    });
  }

  async function handleGenerateSources() {
    const pendingItems = sourceClips.filter(s => s.status === 'idle');

    if (pendingItems.length === 0) {
      if (sourceClips.length > 0 && sourceClips.every(s => s.status === 'ready')) {
        alert("All clips are already generated!");
      } else {
        alert("Add images first.");
      }
      return;
    }

    pendingItems.forEach(item => item.status = 'generating');
    renderSourceList();

    btnGenSource.disabled = true;
    statusSourceEl.textContent = `Generating ${pendingItems.length} clips... (0%)`;

    const payload = {
      items: pendingItems.map(s => ({ url: s.url, motion: s.motion, effect: s.effect })),
      cfg_scale: 0.5
    };

    try {
      const res = await fetch("/video-mvp/generate-sources", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
      });
      const data = await res.json();
      const results = await pollJob(data.job_id, (p) => statusSourceEl.textContent = `Generating... (${p}%)`);

      let resIdx = 0;
      sourceClips.forEach(item => {
        if (item.status === 'generating') {
          if (results[resIdx]) {
            item.videoUrl = results[resIdx];
            item.status = 'ready';
            addToTimeline(item);
          }
          resIdx++;
        }
      });

      renderSourceList();
      renderTimeline();
      statusSourceEl.textContent = "Done.";
      if (btnExportFinal) btnExportFinal.disabled = false;
    } catch (e) {
      console.error(e);
      statusSourceEl.textContent = "Error: " + e.message;
      sourceClips.forEach(item => {
        if (item.status === 'generating') item.status = 'idle';
      });
      renderSourceList();
    }
    btnGenSource.disabled = false;
  }

  // -------------------------------------------------------
  // Step 2: Timeline Logic
  // -------------------------------------------------------
  function addToTimeline(sourceItem) {
    timelineClips.push({
      sourceId: sourceItem.id, videoUrl: sourceItem.videoUrl, thumbUrl: sourceItem.url,
      speed: 1.0, trimStart: 0.0, trimEnd: 5.0
    });
  }

  function renderTimeline() {
    if (!timelineTrack) return;
    timelineTrack.innerHTML = "";
    timelineRuler.innerHTML = "";

    let totalDuration = 0;

    if (timelineClips.length === 0) {
      timelineTrack.innerHTML = `<div class="vs-empty-timeline" style="padding:20px; color:#555;">Generate source clips first.</div>`;
      return;
    }

    timelineClips.forEach((clip, idx) => {
      // 길이 계산
      const dur = (clip.trimEnd - clip.trimStart) / clip.speed;
      const widthPx = dur * pxPerSec;

      const block = document.createElement("div");
      block.className = `timeline-block ${idx === selectedTimelineIndex ? 'selected' : ''}`;
      block.style.width = `${widthPx}px`; // 정확한 너비 지정
      block.setAttribute("draggable", "true");
      block.dataset.index = idx;
      block.dataset.startTime = totalDuration;

      block.onclick = (e) => { e.stopPropagation(); selectTimelineClip(idx); };
      block.ondragstart = (e) => { dragSrcIndex = idx; e.dataTransfer.effectAllowed = 'move'; };
      block.ondragover = (e) => e.preventDefault();
      block.ondrop = (e) => handleDrop(e, idx);

      // [수정] 썸네일 채우기 로직 개선
      // 대략 60px마다 하나씩 생성하되, CSS flex가 나머지를 채움
      const thumbCount = Math.max(1, Math.ceil(widthPx / 60));
      let thumbs = "";
      for (let i = 0; i < thumbCount; i++) thumbs += `<img src="${clip.thumbUrl}" draggable="false">`;

      block.innerHTML = `
             <div class="timeline-block-thumbs">${thumbs}</div>
             <div class="timeline-block-info">${dur.toFixed(1)}s (x${clip.speed})</div>
          `;
      timelineTrack.appendChild(block);
      totalDuration += dur;
    });

    // [수정] 눈금자도 Flex 시작점에 딱 맞게 그림
    const rulerW = Math.max(timelineContainer.offsetWidth, totalDuration * pxPerSec + 200);
    timelineRuler.style.width = `${rulerW}px`;

    // 1초 단위 눈금
    for (let i = 0; i < totalDuration + 5; i++) {
      const tick = document.createElement("div");
      tick.style.position = "absolute";
      tick.style.left = `${i * pxPerSec}px`; // padding 없이 0부터 시작 (CSS timeline-track-area 패딩 제거와 매칭)
      tick.style.bottom = "0";
      tick.style.borderLeft = "1px solid #555";
      tick.style.height = "40%";
      tick.style.fontSize = "10px";
      tick.style.color = "#777";
      tick.style.paddingLeft = "2px";

      if (i % 5 === 0) {
        tick.style.height = "100%";
        tick.textContent = i + "s";
      }
      timelineRuler.appendChild(tick);
    }
  }

  function selectTimelineClip(idx) {
    selectedTimelineIndex = idx;
    const clip = timelineClips[idx];

    if (tlPropsDiv) {
      tlPropsDiv.classList.remove("hidden");
      inpTlSpeed.value = clip.speed;
      inpTlStart.value = clip.trimStart;
      inpTlEnd.value = clip.trimEnd;
    }

    if (previewPlaceholder) previewPlaceholder.classList.add("hidden");

    if (previewImg) previewImg.classList.add("hidden");
    if (previewVid) {
      previewVid.classList.remove("hidden");
      previewVid.removeAttribute("controls");
      if (!previewVid.src.includes(clip.videoUrl)) previewVid.src = clip.videoUrl;

      previewVid.playbackRate = clip.speed;
      previewVid.currentTime = clip.trimStart;
    }

    const clipStartTime = document.querySelector(`.timeline-block[data-index="${idx}"]`)?.dataset.startTime || 0;
    currentPlayTime = parseFloat(clipStartTime);
    updatePlayheadUI();

    renderTimeline();
  }

  function updatePlayheadUI() {
    if (!playhead) return;
    const x = currentPlayTime * pxPerSec;
    playhead.style.left = `${x}px`;
  }

  function seekTo(time) {
    currentPlayTime = Math.max(0, time);
    updatePlayheadUI();

    let acc = 0;
    let targetClip = null;

    for (let clip of timelineClips) {
      const dur = (clip.trimEnd - clip.trimStart) / clip.speed;
      if (currentPlayTime >= acc && currentPlayTime < acc + dur) {
        targetClip = clip;
        break;
      }
      acc += dur;
    }

    if (targetClip) {
      if (previewVid.src !== targetClip.videoUrl && !previewVid.src.includes(targetClip.videoUrl)) {
        previewVid.src = targetClip.videoUrl;
        previewVid.playbackRate = targetClip.speed;
      }
      const localTime = (currentPlayTime - acc) * targetClip.speed + targetClip.trimStart;
      previewVid.currentTime = localTime;
    }
  }

  function togglePlay() {
    const btnIcon = btnPlay.querySelector("span");
    if (isPlaying) {
      isPlaying = false;
      btnIcon.textContent = "play_arrow";
      cancelAnimationFrame(animationFrameId);
      previewVid.pause();
    } else {
      isPlaying = true;
      btnIcon.textContent = "pause";
      let lastTime = performance.now();

      function loop(now) {
        if (!isPlaying) return;
        const dt = (now - lastTime) / 1000;
        lastTime = now;

        currentPlayTime += dt;
        seekTo(currentPlayTime);
        previewVid.play().catch(() => { });

        animationFrameId = requestAnimationFrame(loop);
      }
      animationFrameId = requestAnimationFrame(loop);
    }
  }

  function setupScrubber() {
    if (!timelineContainer) return;
    const onDrag = (e) => {
      const rect = timelineContainer.getBoundingClientRect();
      const x = e.clientX - rect.left + timelineContainer.scrollLeft;
      const time = x / pxPerSec;
      seekTo(time);
    };
    timelineContainer.addEventListener("mousedown", (e) => { isScrubbing = true; onDrag(e); });
    window.addEventListener("mousemove", (e) => { if (isScrubbing) onDrag(e); });
    window.addEventListener("mouseup", () => { isScrubbing = false; });
  }

  function updateTimelineClip() {
    if (selectedTimelineIndex === -1) return;
    const clip = timelineClips[selectedTimelineIndex];
    clip.speed = parseFloat(inpTlSpeed.value);
    let s = parseFloat(inpTlStart.value);
    let e = parseFloat(inpTlEnd.value);
    if (s < 0) s = 0; if (e > 5.0) e = 5.0; if (s >= e) s = e - 0.5;

    inpTlStart.value = s; inpTlEnd.value = e;
    clip.trimStart = s; clip.trimEnd = e;

    if (previewVid) previewVid.playbackRate = clip.speed;
    renderTimeline();
  }

  function handleDrop(e, dropIndex) {
    e.stopPropagation();
    if (dragSrcIndex !== null && dragSrcIndex !== dropIndex) {
      const item = timelineClips[dragSrcIndex];
      timelineClips.splice(dragSrcIndex, 1);
      timelineClips.splice(dropIndex, 0, item);
      selectedTimelineIndex = dropIndex;
      renderTimeline();
    }
    dragSrcIndex = null;
  }

  async function handleExportFinal() {
    if (timelineClips.length === 0) return alert("Empty timeline.");
    btnExportFinal.disabled = true;
    statusFinalEl.textContent = "Rendering...";

    const payload = {
      clips: timelineClips.map(c => ({
        video_url: c.videoUrl, speed: c.speed, trim_start: c.trimStart, trim_end: c.trimEnd
      })),
      include_intro_outro: true
    };

    try {
      const res = await fetch("/video-mvp/compile", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
      });
      const data = await res.json();
      const finalUrl = await pollJob(data.job_id, (p) => statusFinalEl.textContent = `Rendering... (${p}%)`);
      statusFinalEl.textContent = "Done!";
      if (resultVideo) {
        resultVideo.src = finalUrl;
        $("downloadLink").href = finalUrl;
        resultOverlay.classList.remove("hidden");
        isPlaying = false;
      }
    } catch (e) {
      console.error(e); statusFinalEl.textContent = "Failed.";
    }
    btnExportFinal.disabled = false;
  }

  async function pollJob(jobId, onProgress) {
    while (true) {
      const res = await fetch(`/video-mvp/status/${jobId}`, { cache: "no-store" });
      const st = await res.json();
      if (onProgress) onProgress(st.progress || 0);
      if (st.status === "COMPLETED") return st.results || st.result_url;
      if (st.status === "FAILED") throw new Error(st.error);
      await new Promise(r => setTimeout(r, 1500));
    }
  }

  function init() {
    uploadInput = $("uploadInput");
    uploadZone = $("uploadZone"); // [복구] uploadZone 바인딩 추가
    listEl = $("selectedList"); listEmptyEl = $("selectedEmpty");
    btnGenSource = $("btnGenerateSource"); statusSourceEl = $("statusSource");

    timelineContainer = document.querySelector('.timeline-container');
    timelineTrack = $("timelineTrack"); timelineRuler = $("timelineRuler");
    playhead = $("playhead");
    tlPropsDiv = $("timelineProperties");

    inpTlSpeed = $("tlSpeed"); inpTlStart = $("tlTrimStart"); inpTlEnd = $("tlTrimEnd");
    btnExportFinal = $("btnExportFinal"); statusFinalEl = $("statusFinal");

    previewImg = $("previewImage"); previewVid = $("previewVideo");
    previewPlaceholder = $("previewPlaceholder");
    resultOverlay = $("resultOverlay"); resultVideo = $("resultVideo");
    btnPlay = $("btnPlayTimeline");

    // Bindings
    if ($("browseBtn")) $("browseBtn").onclick = () => uploadInput.click();
    if (uploadInput) uploadInput.onchange = (e) => { addSourceFiles(Array.from(e.target.files)); uploadInput.value = ""; };

    // [복구] 드래그 앤 드롭 이벤트 리스너 추가
    if (uploadZone) {
      uploadZone.addEventListener("dragover", (e) => {
        e.preventDefault();
        uploadZone.style.borderColor = "#999";
        uploadZone.style.background = "rgba(255,255,255,0.05)";
      });
      uploadZone.addEventListener("dragleave", () => {
        uploadZone.style.borderColor = "#555";
        uploadZone.style.background = "rgba(255,255,255,0.02)";
      });
      uploadZone.addEventListener("drop", (e) => {
        e.preventDefault();
        uploadZone.style.borderColor = "#555";
        uploadZone.style.background = "rgba(255,255,255,0.02)";
        const files = Array.from(e.dataTransfer.files).filter(f => f.type.startsWith("image/"));
        addSourceFiles(files);
      });
    }

    if (btnGenSource) btnGenSource.onclick = handleGenerateSources;
    if (btnExportFinal) btnExportFinal.onclick = handleExportFinal;
    if (btnPlay) btnPlay.onclick = togglePlay;

    if ($("closeResultBtn")) $("closeResultBtn").onclick = () => { resultOverlay.classList.add("hidden"); if (resultVideo) resultVideo.pause(); };

    [inpTlSpeed, inpTlStart, inpTlEnd].forEach(el => { if (el) el.onchange = updateTimelineClip; });

    $("btnZoomIn").onclick = () => { pxPerSec = Math.min(pxPerSec * 1.5, 200); renderTimeline(); };
    $("btnZoomOut").onclick = () => { pxPerSec = Math.max(pxPerSec / 1.5, 20); renderTimeline(); };

    setupScrubber();
  }

  window.addEventListener("DOMContentLoaded", init);
})();