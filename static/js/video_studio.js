document.addEventListener('DOMContentLoaded', () => {
    const $ = (id) => document.getElementById(id);

    // --- Global State ---
    let sourceClips = []; // Generated video clips available for timeline
    let timelineClips = []; // Clips actually in the timeline
    
    // --- Navigation Logic ---
    const menuScreen = $('menu-screen');
    const workspaces = {
        'create-clips': $('workspace-feature-1'),
        'assemble-video': $('workspace-feature-2'),
        'post-production': $('workspace-feature-3')
    };

    function showWorkspace(id) {
        menuScreen.classList.add('hidden');
        Object.values(workspaces).forEach(ws => ws.classList.add('hidden'));
        workspaces[id].classList.remove('hidden');
        window.scrollTo(0, 0);
    }

    function showMenu() {
        Object.values(workspaces).forEach(ws => ws.classList.add('hidden'));
        menuScreen.classList.remove('hidden');
        window.scrollTo(0, 0);
    }

    // Menu Bindings
    $('btn-feature-1').onclick = () => showWorkspace('create-clips');
    $('btn-feature-2').onclick = () => showWorkspace('assemble-video');
    $('btn-feature-3').onclick = () => showWorkspace('post-production');

    // Breadcrumb Bindings
    $('back-to-menu-1').onclick = showMenu;
    $('back-to-menu-2').onclick = showMenu;
    $('back-to-menu-3').onclick = showMenu;


    // --- Workspace Logic Management ---
    class VideoWorkspaceManager {
        constructor(id, options = {}) {
            this.id = id;
            this.prefix = options.prefix || '';
            this.refFiles = [];
            
            // Elements
            this.dropZone = $(`${this.prefix}ref-drop-zone`);
            this.fileInput = $(`${this.prefix}ref-input`);
            this.previewContainer = $(`${this.prefix}ref-preview-container`);
            this.removeAllBtn = $(`${this.prefix}ref-remove-all`);
            this.generateBtn = $(`${this.prefix}generate-btn`);
            
            this.internalPreview = this.dropZone?.querySelector('.is-internal-preview');
            this.uploadContent = this.dropZone?.querySelector('.is-upload-content');
            
            this.loadingEl = $(`${this.prefix}loading`);
            this.placeholderEl = $(`${this.prefix}placeholder-text`);
            this.resultContainer = $(`${this.prefix}result-container`);
            this.gridEl = $(`${this.prefix}gen-grid`);
            
            this.init();
        }

        init() {
            if (this.dropZone) {
                this.dropZone.onclick = (e) => {
                    if (e.target.closest('.remove-btn')) return;
                    this.fileInput.click();
                };
                this.dropZone.ondragover = (e) => { e.preventDefault(); this.dropZone.classList.add('dragover'); };
                this.dropZone.ondragleave = () => this.dropZone.classList.remove('dragover');
                this.dropZone.ondrop = (e) => {
                    e.preventDefault();
                    this.dropZone.classList.remove('dragover');
                    if (e.dataTransfer.files.length) this.handleFiles(e.dataTransfer.files);
                };
                this.fileInput.onchange = (e) => this.handleFiles(e.target.files);
            }

            if (this.removeAllBtn) {
                this.removeAllBtn.onclick = (e) => { e.stopPropagation(); this.clearFiles(); };
            }

            const internalRemoveBtn = this.internalPreview?.querySelector('.remove-btn');
            if (internalRemoveBtn) {
                internalRemoveBtn.onclick = (e) => { e.stopPropagation(); this.clearFiles(); };
            }

            if (this.generateBtn) {
                this.generateBtn.onclick = () => this.generate();
            }
        }

        clearFiles() {
            this.refFiles = [];
            if (this.fileInput) this.fileInput.value = '';
            this.updatePreviews();
        }

        handleFiles(files) {
            Array.from(files).forEach(f => {
                if (f.type.startsWith('image/')) this.refFiles.push(f);
            });
            this.updatePreviews();
        }

        updatePreviews() {
            if (this.internalPreview) {
                const img = this.internalPreview.querySelector('img');
                if (this.refFiles.length > 0) {
                    const reader = new FileReader();
                    reader.onload = (e) => {
                        img.src = e.target.result;
                        this.internalPreview.classList.remove('hidden');
                        this.uploadContent?.classList.add('hidden');
                    };
                    reader.readAsDataURL(this.refFiles[this.refFiles.length - 1]);
                    this.generateBtn.disabled = false;
                } else {
                    img.src = '';
                    this.internalPreview.classList.add('hidden');
                    this.uploadContent?.classList.remove('hidden');
                    this.generateBtn.disabled = true;
                }
                return;
            }

            if (!this.previewContainer) return;
            this.previewContainer.innerHTML = '';
            if (this.refFiles.length > 0) {
                this.previewContainer.style.display = 'grid';
                this.removeAllBtn?.classList.remove('hidden');
                this.generateBtn.disabled = false;
                this.refFiles.forEach((file, index) => {
                    const reader = new FileReader();
                    const itemDiv = document.createElement('div');
                    itemDiv.className = 'is-file-item';
                    reader.onload = (e) => {
                        itemDiv.innerHTML = `
                            <img src="${e.target.result}">
                            <button class="remove-btn">×</button>
                        `;
                        itemDiv.querySelector('.remove-btn').onclick = (ev) => {
                            ev.stopPropagation();
                            this.refFiles.splice(index, 1);
                            this.updatePreviews();
                        };
                        this.previewContainer.appendChild(itemDiv);
                    };
                    reader.readAsDataURL(file);
                });
            } else {
                this.previewContainer.style.display = 'none';
                this.removeAllBtn?.classList.add('hidden');
                this.generateBtn.disabled = true;
            }
        }

        async generate() {
            if (this.refFiles.length === 0) return;
            
            // Logic for Step 1: Create Video Clips
            if (this.id === 'create-clips') {
                this.placeholderEl?.classList.add('hidden');
                this.loadingEl?.classList.remove('hidden');
                this.resultContainer?.classList.add('hidden');
                this.generateBtn.disabled = true;

                const motionValue = $('clip-motion').value;
                const statusText = $('statusSource-1');
                
                try {
                    // Upload files first
                    let processed = 0;
                    const items_to_gen = [];
                    
                    for (const file of this.refFiles) {
                        const fd = new FormData(); fd.append("file", file);
                        const uploadRes = await fetch("/api/outputs/upload", { method: "POST", body: fd });
                        const uploadData = await uploadRes.json();
                        items_to_gen.push({ url: uploadData.url, motion: motionValue, effect: 'none' });
                        processed++;
                        statusText.textContent = `Uploading... (${processed}/${this.refFiles.length})`;
                    }

                    statusText.textContent = "Starting generation...";
                    const genRes = await fetch("/video-mvp/generate-sources", {
                        method: "POST", headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ items: items_to_gen, cfg_scale: 0.5 })
                    });
                    const genData = await genRes.json();
                    
                    const results = await this.pollJob(genData.job_id, (p) => {
                        statusText.textContent = `Generating... (${p}%)`;
                    });

                    this.loadingEl?.classList.add('hidden');
                    this.resultContainer?.classList.remove('hidden');
                    this.gridEl.innerHTML = '';

                    results.forEach((vidUrl, idx) => {
                        const sourceItem = {
                            id: Math.random().toString(36).substr(2, 9),
                            url: items_to_gen[idx].url,
                            videoUrl: vidUrl,
                            status: 'ready'
                        };
                        sourceClips.push(sourceItem);
                        
                        const card = document.createElement('div');
                        card.className = 'result-card';
                        card.innerHTML = `
                            <video src="${vidUrl}" loop muted onmouseover="this.play()" onmouseout="this.pause()"></video>
                            <button class="glow-btn burgundy detail-upscale-btn" onclick="addToTimelineFromGlobal('${sourceItem.id}')">
                                <span class="material-symbols-outlined">add_circle</span> ADD TO TIMELINE
                            </button>
                        `;
                        this.gridEl.appendChild(card);
                    });
                    
                    statusText.textContent = "Done.";
                    // Sync to Workspace 2 list
                    syncTimelineSourceList();

                } catch (e) {
                    console.error(e);
                    statusText.textContent = "Error: " + e.message;
                    this.placeholderEl?.classList.remove('hidden');
                } finally {
                    this.generateBtn.disabled = false;
                    this.loadingEl?.classList.add('hidden');
                }
            }
        }

        async pollJob(jobId, onProgress) {
            while (true) {
                const res = await fetch(`/video-mvp/status/${jobId}`, { cache: "no-store" });
                const st = await res.json();
                if (onProgress) onProgress(st.progress || 0);
                if (st.status === "COMPLETED") return st.results || st.result_url;
                if (st.status === "FAILED") throw new Error(st.error);
                await new Promise(r => setTimeout(r, 1500));
            }
        }
    }

    // --- Timeline Logic (Workspace 2) ---
    const previewVid = $('previewVideo');
    const previewPlaceholder = $('previewPlaceholder');
    const timelineTrack = $('timelineTrack');
    const timelineRuler = $('timelineRuler');
    const playhead = $('playhead');
    const tlPropsDiv = $('timelineProperties');
    const inpTlSpeed = $('tlSpeed');
    const inpTlStart = $('tlTrimStart');
    const inpTlEnd = $('tlTrimEnd');

    let selectedTimelineIndex = -1;
    let pxPerSec = 40;
    let isPlaying = false;
    let currentPlayTime = 0;
    let animationFrameId = null;

    function syncTimelineSourceList() {
        const listContainer = $('full-ref-preview-container');
        if (!listContainer) return;
        listContainer.innerHTML = '';
        
        sourceClips.forEach(clip => {
            const item = document.createElement('div');
            item.className = 'is-file-item';
            item.innerHTML = `
                <img src="${clip.url}">
                <button class="remove-btn" style="background:rgba(0,180,0,0.8);"><span class="material-symbols-outlined">add</span></button>
            `;
            item.onclick = () => addToTimeline(clip);
            listContainer.appendChild(item);
        });
    }

    function addToTimeline(sourceItem) {
        timelineClips.push({
            sourceId: sourceItem.id, videoUrl: sourceItem.videoUrl, thumbUrl: sourceItem.url,
            speed: 1.0, trimStart: 0.0, trimEnd: 5.0
        });
        renderTimeline();
    }

    window.addToTimelineFromGlobal = (id) => {
        const item = sourceClips.find(s => s.id === id);
        if (item) addToTimeline(item);
    };

    function renderTimeline() {
        if (!timelineTrack) return;
        timelineTrack.innerHTML = "";
        timelineRuler.innerHTML = "";
        let totalDuration = 0;

        if (timelineClips.length === 0) {
            timelineTrack.innerHTML = `<div class="placeholder-desc" style="padding:20px;">Add clips to start building.</div>`;
            return;
        }

        timelineClips.forEach((clip, idx) => {
            const dur = (clip.trimEnd - clip.trimStart) / clip.speed;
            const widthPx = dur * pxPerSec;
            const block = document.createElement("div");
            block.className = `timeline-block ${idx === selectedTimelineIndex ? 'selected' : ''}`;
            block.style.width = `${widthPx}px`;
            block.onclick = (e) => { e.stopPropagation(); selectTimelineClip(idx); };

            const thumbCount = Math.max(1, Math.ceil(widthPx / 60));
            let thumbs = "";
            for (let i = 0; i < thumbCount; i++) thumbs += `<img src="${clip.thumbUrl}" draggable="false">`;

            block.innerHTML = `<div class="timeline-block-thumbs">${thumbs}</div><div class="timeline-block-info">${dur.toFixed(1)}s</div>`;
            timelineTrack.appendChild(block);
            totalDuration += dur;
        });

        // Ruler
        for (let i = 0; i < totalDuration + 5; i++) {
            const tick = document.createElement("div");
            tick.style.position = "absolute"; tick.style.left = `${i * pxPerSec}px`;
            tick.style.bottom = "0"; tick.style.borderLeft = "1px solid #555";
            tick.style.height = "40%"; tick.style.fontSize = "10px"; tick.style.color = "#777";
            if (i % 5 === 0) { tick.style.height = "100%"; tick.textContent = i + "s"; }
            timelineRuler.appendChild(tick);
        }
    }

    function selectTimelineClip(idx) {
        selectedTimelineIndex = idx;
        const clip = timelineClips[idx];
        tlPropsDiv.classList.remove("hidden");
        inpTlSpeed.value = clip.speed;
        inpTlStart.value = clip.trimStart;
        inpTlEnd.value = clip.trimEnd;

        previewPlaceholder.classList.add("hidden");
        previewVid.classList.remove("hidden");
        if (!previewVid.src.includes(clip.videoUrl)) previewVid.src = clip.videoUrl;
        previewVid.playbackRate = clip.speed;
        previewVid.currentTime = clip.trimStart;

        renderTimeline();
    }

    $('full-generate-btn').onclick = async () => {
        if (timelineClips.length === 0) return alert("Empty timeline.");
        const btn = $('full-generate-btn');
        const loading = $('full-loading');
        const status = $('statusSource-2');

        btn.disabled = true;
        loading.classList.remove('hidden');
        status.textContent = "Rendering final video...";

        try {
            const payload = {
                clips: timelineClips.map(c => ({
                    video_url: c.videoUrl, speed: c.speed, trim_start: c.trimStart, trim_end: c.trimEnd
                })),
                include_intro_outro: true
            };
            const res = await fetch("/video-mvp/compile", {
                method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
            });
            const data = await res.json();
            
            const poll = async (jobId) => {
                while (true) {
                    const r = await fetch(`/video-mvp/status/${jobId}`);
                    const s = await r.json();
                    status.textContent = `Rendering... (${s.progress || 0}%)`;
                    if (s.status === "COMPLETED") return s.results || s.result_url;
                    if (s.status === "FAILED") throw new Error(s.error);
                    await new Promise(x => setTimeout(x, 1500));
                }
            };

            const finalUrl = await poll(data.job_id);
            status.textContent = "Done!";
            
            // Show result modal
            const globalModal = $('global-modal');
            const modalMsg = $('modal-msg');
            modalMsg.innerHTML = `<video src="${finalUrl}" controls style="width:100%; border-radius:8px;"></video>
                                 <a href="${finalUrl}" download class="glow-btn burgundy" style="display:block; margin-top:15px; text-decoration:none;">DOWNLOAD FINAL VIDEO</a>`;
            $('modal-title').textContent = "Video Exported Successfully";
            $('modal-ok-btn').onclick = () => globalModal.classList.add('hidden');
            globalModal.classList.remove('hidden');

        } catch (e) {
            console.error(e);
            status.textContent = "Failed: " + e.message;
        } finally {
            btn.disabled = false;
            loading.classList.add('hidden');
        }
    };

    // --- Initialization ---
    new VideoWorkspaceManager('create-clips', { prefix: 'clip-' });
    // Note: Workspace 2 and 3 are handled via specialized logic above
    
    // Scrubber
    const container = document.querySelector('.timeline-container');
    if (container) {
        container.onmousedown = (e) => {
            const rect = container.getBoundingClientRect();
            const seek = (ev) => {
                const x = ev.clientX - rect.left + container.scrollLeft;
                currentPlayTime = x / pxPerSec;
                playhead.style.left = `${x}px`;
            };
            seek(e);
            window.onmousemove = seek;
            window.onmouseup = () => window.onmousemove = null;
        };
    }
});