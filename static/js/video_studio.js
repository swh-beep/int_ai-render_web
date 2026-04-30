document.addEventListener("DOMContentLoaded", () => {
    const $ = (id) => document.getElementById(id);
    const IMAGE_ALLOWED_EXTS = new Set([".png", ".jpg", ".jpeg", ".webp"]);
    const VIDEO_ALLOWED_EXTS = new Set([".mp4", ".mov", ".webm"]);

    const menuScreen = $("menu-screen");
    const workspaces = {
        "create-clips": $("workspace-feature-1"),
        "assemble-video": $("workspace-feature-2"),
        "post-production": $("workspace-feature-3"),
    };

    const selectedSources = [];
    const selectedAssembleClips = [];

    const clipDropZone = $("clip-ref-drop-zone");
    const clipInput = $("clip-ref-input");
    const clipUploadEmptyState = $("clip-upload-empty-state");
    const clipUploadPreview = $("clip-upload-preview");
    const clipRemoveAllBtn = $("clip-ref-remove-all");
    const clipMotionSelect = $("clip-motion");
    const clipEffectSelect = $("clip-effect");
    const clipCustomMotionWrap = $("clip-custom-motion-wrap");
    const clipCustomEffectWrap = $("clip-custom-effect-wrap");
    const clipCustomMotion = $("clip-custom-motion");
    const clipCustomEffect = $("clip-custom-effect");
    const clipGenerateBtn = $("clip-generate-btn");
    const clipStatus = $("statusSource-1");
    const clipLoading = $("clip-loading");
    const clipPlaceholder = $("clip-placeholder-text");
    const clipResultContainer = $("clip-result-container");
    const clipResultGrid = $("clip-gen-grid");

    const assembleDropZone = $("full-ref-drop-zone");
    const assembleInput = $("full-ref-input");
    const assemblePreviewContainer = $("full-ref-preview-container");
    const assembleRemoveAllBtn = $("full-ref-remove-all");
    const assembleInstructions = $("full-instructions");
    const assembleGenerateBtn = $("full-generate-btn");
    const assembleStatus = $("statusSource-2");
    const assembleLoading = $("full-loading");
    const assemblePlaceholder = $("full-placeholder-text");
    const assembleClipCount = $("assemble-clip-count");
    const assembleTotalDuration = $("assemble-total-duration");
    const assembleExportMode = $("assemble-export-mode");
    const assembleTimelineSummary = $("assemble-timeline-summary");
    const assembleTimelineRuler = $("assemble-timeline-ruler");
    const assembleTimelineViewport = $("assemble-timeline-viewport");
    const assembleTimelineTrack = $("assemble-timeline-track");
    const assembleTimelinePlayhead = $("assemble-timeline-playhead");
    const assembleTimelineScrubber = $("assemble-timeline-scrubber");
    const assembleMonitorTitle = $("assemble-monitor-title");
    const assembleMonitorMeta = $("assemble-monitor-meta");
    const assembleMonitorPlayer = $("assemble-monitor-player");
    const assembleMonitorCanvas = $("assemble-monitor-canvas");
    const assembleMonitorBackdrop = $("assemble-monitor-video-backdrop");
    const assembleMonitorVideo = $("assemble-monitor-video");
    const assemblePreviewBadge = $("assemble-preview-badge");
    const assemblePlayToggle = $("assemble-play-toggle");
    const assembleZoomOutBtn = $("assemble-zoom-out");
    const assembleZoomInBtn = $("assemble-zoom-in");
    const assembleZoomReadout = $("assemble-monitor-zoom");
    const assemblePlaybackCurrent = $("assemble-playback-current");
    const assemblePlaybackTotal = $("assemble-playback-total");
    const assembleDeleteBtn = $("assemble-delete-btn");
    const assembleTrimBtn = $("assemble-trim-btn");
    const assembleReverseBtn = $("assemble-reverse-btn");
    const assembleFlipBtn = $("assemble-flip-btn");
    const assembleInspectorEmpty = $("assemble-inspector-empty");
    const assembleInspectorForm = $("assemble-inspector-form");
    const assembleInspectorName = $("assemble-inspector-name");
    const assembleInspectorMeta = $("assemble-inspector-meta");
    const assembleTrimCard = $("assemble-trim-card");
    const assembleTrimStartRange = $("assemble-trim-start-range");
    const assembleTrimStartInput = $("assemble-trim-start");
    const assembleTrimEndRange = $("assemble-trim-end-range");
    const assembleTrimEndInput = $("assemble-trim-end");
    const assembleSpeedRange = $("assemble-speed-range");
    const assembleSpeedInput = $("assemble-speed-input");
    const assembleClipOrder = $("assemble-clip-order");
    const assembleClipDuration = $("assemble-clip-duration");
    const assembleReverseState = $("assemble-reverse-state");
    const assembleFlipState = $("assemble-flip-state");
    const assembleRatioGroup = $("assemble-ratio-group");
    const assembleRatioButtons = Array.from(document.querySelectorAll("#assemble-ratio-group .assemble-ratio-btn"));
    const assembleFitModeGroup = $("assemble-fit-mode-group");
    const assembleFitModeButtons = Array.from(document.querySelectorAll("#assemble-fit-mode-group .assemble-fit-btn"));

    const ASSEMBLE_RATIO_MAP = {
        "16:9": { width: 16, height: 9 },
        "1:1": { width: 1, height: 1 },
        "4:5": { width: 4, height: 5 },
        "9:16": { width: 9, height: 16 },
    };

    let activeAssembleClipId = null;
    let assemblePreviewMode = "clip";
    let assembleFinalVideoUrl = "";
    let assembleAspectRatio = "9:16";
    let assembleAspectMode = "crop";
    let assembleMonitorZoom = 1;
    let assembleSequenceTime = 0;
    let assembleSequencePlaying = false;
    let assembleSequenceRaf = 0;
    let assembleSequenceLastFrame = 0;
    let assemblePendingClipSeek = null;
    const assembleTimelineThumbCache = new Map();
    const assembleTimelineThumbJobs = new Set();

    function setWorkspaceParam(workspaceId) {
        const url = new URL(window.location.href);
        if (workspaceId) {
            url.searchParams.set("workspace", workspaceId);
        } else {
            url.searchParams.delete("workspace");
        }
        window.history.replaceState({}, "", url);
    }

    function showWorkspace(id) {
        menuScreen?.classList.add("hidden");
        Object.values(workspaces).forEach((workspace) => workspace?.classList.add("hidden"));
        workspaces[id]?.classList.remove("hidden");
        setWorkspaceParam(id);
        window.scrollTo({ top: 0, behavior: "auto" });
    }

    function showMenu() {
        Object.values(workspaces).forEach((workspace) => workspace?.classList.add("hidden"));
        menuScreen?.classList.remove("hidden");
        setWorkspaceParam("");
        window.scrollTo({ top: 0, behavior: "auto" });
    }

    function setStatus(target, message) {
        if (target) {
            target.textContent = message || "";
        }
    }

    function setClipStatus(message) {
        setStatus(clipStatus, message);
    }

    function setAssembleStatus(message) {
        setStatus(assembleStatus, message);
    }

    function createSourceId() {
        if (window.crypto?.randomUUID) {
            return window.crypto.randomUUID();
        }
        return `source-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    }

    function setModalVisibility(visible, { wide = false } = {}) {
        const modal = $("global-modal");
        const modalContent = modal?.querySelector(".modal-content");
        if (!modal || !modalContent) {
            return;
        }
        modal.classList.toggle("hidden", !visible);
        modalContent.classList.toggle("preview-wide", Boolean(wide));
    }

    function showModal(title, bodyHtml, { wide = false } = {}) {
        const modalTitle = $("modal-title");
        const modalMsg = $("modal-msg");
        const modalOkBtn = $("modal-ok-btn");
        if (!modalTitle || !modalMsg || !modalOkBtn) {
            return;
        }
        modalTitle.textContent = title;
        modalMsg.innerHTML = bodyHtml;
        modalOkBtn.onclick = () => setModalVisibility(false);
        setModalVisibility(true, { wide });
    }

    function fileIdentity(file) {
        return [file.name, file.size, file.lastModified].join(":");
    }

    function sourceIdentity(source) {
        return fileIdentity(source.file);
    }

    function loadSourceDimensions(previewUrl) {
        return new Promise((resolve) => {
            const probe = new Image();
            probe.onload = () => resolve({ width: probe.naturalWidth, height: probe.naturalHeight });
            probe.onerror = () => resolve({ width: 0, height: 0 });
            probe.src = previewUrl;
        });
    }

    function revokeSourcePreview(source) {
        if (source?.previewUrl) {
            URL.revokeObjectURL(source.previewUrl);
        }
    }

    function syncCustomPromptFields() {
        clipCustomMotionWrap?.classList.toggle("hidden", clipMotionSelect?.value !== "custom");
        clipCustomEffectWrap?.classList.toggle("hidden", clipEffectSelect?.value !== "custom");
    }

    function resetClipDropZoneSize() {
        if (!clipDropZone) {
            return;
        }
        clipDropZone.style.removeProperty("width");
        clipDropZone.style.removeProperty("height");
    }

    function normalizeClipPlaceholder() {
        if (!clipPlaceholder) {
            return;
        }
        clipPlaceholder.innerHTML = `
            <div class="clip-placeholder-stack">
                <div class="clip-placeholder-card"><span class="material-symbols-outlined">image</span></div>
                <div class="clip-placeholder-arrow"><span class="material-symbols-outlined">arrow_forward</span></div>
                <div class="clip-placeholder-card"><span class="material-symbols-outlined">movie</span></div>
            </div>
            <h3 class="clip-placeholder-title">Preview</h3>
            <p class="clip-placeholder-desc">Upload one image, choose motion and effect, then generate its clip directly.</p>
        `;
    }

    function renderSelectedSources() {
        if (!clipUploadPreview || !clipGenerateBtn || !clipRemoveAllBtn || !clipDropZone) {
            return;
        }

        clipUploadPreview.innerHTML = "";

        const hasSources = selectedSources.length > 0;
        clipGenerateBtn.disabled = !hasSources;
        clipRemoveAllBtn.classList.toggle("hidden", !hasSources);
        clipUploadEmptyState?.classList.toggle("hidden", hasSources);
        clipUploadPreview.classList.toggle("hidden", !hasSources);
        clipDropZone.classList.toggle("has-preview", hasSources);
        resetClipDropZoneSize();

        if (!hasSources) {
            return;
        }

        const source = selectedSources[0];
        const img = document.createElement("img");
        img.src = source.previewUrl;
        img.alt = source.file.name;
        clipUploadPreview.appendChild(img);
    }

    async function addFileSources(fileList) {
        const files = Array.from(fileList || []).filter((file) => file.type.startsWith("image/"));
        const nextFile = files[0];
        if (!nextFile) {
            return;
        }

        const lowerName = (nextFile.name || "").toLowerCase();
        const ext = lowerName.includes(".") ? lowerName.slice(lowerName.lastIndexOf(".")) : "";
        if (!IMAGE_ALLOWED_EXTS.has(ext)) {
            setClipStatus("Only png, jpg, jpeg, and webp files are supported.");
            if (clipInput) {
                clipInput.value = "";
            }
            return;
        }

        const next = {
            id: createSourceId(),
            file: nextFile,
            previewUrl: URL.createObjectURL(nextFile),
            width: 0,
            height: 0,
        };

        if (selectedSources[0] && sourceIdentity(selectedSources[0]) === sourceIdentity(next)) {
            revokeSourcePreview(next);
            return;
        }

        const dims = await loadSourceDimensions(next.previewUrl);
        next.width = dims.width;
        next.height = dims.height;

        clearSelectedSources();
        selectedSources.push(next);
        renderSelectedSources();
    }

    function clearSelectedSources() {
        while (selectedSources.length) {
            revokeSourcePreview(selectedSources.pop());
        }
        if (clipInput) {
            clipInput.value = "";
        }
        renderSelectedSources();
    }

    function validateClipRequest() {
        if (!selectedSources.length) {
            setClipStatus("Upload at least one source image.");
            return false;
        }
        if (clipMotionSelect?.value === "custom" && !clipCustomMotion?.value.trim()) {
            setClipStatus("Custom motion prompt is required when Motion is set to Custom.");
            clipCustomMotion?.focus();
            return false;
        }
        if (clipEffectSelect?.value === "custom" && !clipCustomEffect?.value.trim()) {
            setClipStatus("Custom effect prompt is required when Effect is set to Custom.");
            clipCustomEffect?.focus();
            return false;
        }
        return true;
    }

    async function uploadOutputFile(file, endpoint, statusSetter, index, total, label) {
        const formData = new FormData();
        formData.append("file", file);
        statusSetter(`Uploading ${label} ${index}/${total}...`);
        const response = await fetch(endpoint, { method: "POST", body: formData });
        if (!response.ok) {
            let detail = "";
            try {
                const payload = await response.json();
                detail = payload?.error || payload?.detail || "";
            } catch {
                detail = await response.text();
            }
            throw new Error(detail || `Upload failed (${response.status})`);
        }
        const payload = await response.json();
        if (!payload?.url) {
            throw new Error("Upload completed but no output URL was returned.");
        }
        return payload.url;
    }

    async function materializeSourceUrls() {
        const urls = [];
        for (let index = 0; index < selectedSources.length; index += 1) {
            urls.push(await uploadOutputFile(selectedSources[index].file, "/api/outputs/upload", setClipStatus, index + 1, selectedSources.length, "image"));
        }
        return urls;
    }

    async function pollVideoJob(jobId, statusSetter) {
        while (true) {
            const response = await fetch(`/video-mvp/status/${jobId}`, { cache: "no-store" });
            if (!response.ok) {
                throw new Error(`Status check failed (${response.status})`);
            }
            const state = await response.json();
            const progress = typeof state.progress === "number" ? ` (${state.progress}%)` : "";
            statusSetter(`${state.message || state.status || "Working"}${progress}`);

            if (state.status === "COMPLETED") {
                return state;
            }
            if (state.status === "FAILED") {
                throw new Error(state.error || "Video generation failed.");
            }

            await new Promise((resolve) => setTimeout(resolve, 1500));
        }
    }

    function renderGeneratedResults(results, errors) {
        if (!clipResultContainer || !clipResultGrid || !clipPlaceholder) {
            return;
        }

        clipResultGrid.innerHTML = "";
        const validResults = (results || []).filter(Boolean);

        if (!validResults.length) {
            const empty = document.createElement("p");
            empty.className = "clip-helper-note";
            empty.textContent = "No clips were generated.";
            clipResultGrid.appendChild(empty);
        }

        validResults.forEach((videoUrl) => {
            const card = document.createElement("article");
            card.className = "clip-generated-card";

            const video = document.createElement("video");
            video.src = videoUrl;
            video.muted = true;
            video.loop = true;
            video.playsInline = true;
            video.preload = "metadata";

            const footer = document.createElement("div");
            footer.className = "clip-result-actions-light";

            const download = document.createElement("a");
            download.href = `/download?url=${encodeURIComponent(videoUrl)}`;
            download.className = "clip-result-link-light";
            download.textContent = "Download";
            download.addEventListener("click", (event) => event.stopPropagation());

            footer.appendChild(download);
            card.appendChild(video);
            card.appendChild(footer);

            card.addEventListener("click", () => {
                showModal("Clip Preview", `<video src="${videoUrl}" controls style="width:100%;max-height:80vh;border-radius:12px;"></video>`, { wide: true });
            });
            card.addEventListener("mouseenter", () => video.play().catch(() => {}));
            card.addEventListener("mouseleave", () => {
                video.pause();
                video.currentTime = 0;
            });

            clipResultGrid.appendChild(card);
        });

        clipPlaceholder.classList.add("hidden");
        clipResultContainer.classList.remove("hidden");
        setClipStatus(errors?.length ? `Completed with ${errors.length} failed clip(s).` : "Done.");
    }

    async function handleGenerateClips() {
        if (!validateClipRequest()) {
            return;
        }

        clipGenerateBtn.disabled = true;
        clipLoading?.classList.remove("hidden");
        clipPlaceholder?.classList.add("hidden");
        clipResultContainer?.classList.add("hidden");
        if (clipResultGrid) {
            clipResultGrid.innerHTML = "";
        }

        try {
            const urls = await materializeSourceUrls();
            const payload = {
                items: urls.map((url) => ({
                    url,
                    motion: clipMotionSelect?.value || "static",
                    effect: clipEffectSelect?.value || "none",
                    custom_motion_prompt: clipMotionSelect?.value === "custom" ? clipCustomMotion?.value.trim() : null,
                    custom_effect_prompt: clipEffectSelect?.value === "custom" ? clipCustomEffect?.value.trim() : null,
                })),
                cfg_scale: 0.5,
            };

            setClipStatus("Starting generation...");
            const response = await fetch("/video-mvp/generate-sources", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            if (!response.ok) {
                const text = await response.text();
                throw new Error(text || `Generation failed (${response.status})`);
            }

            const { job_id: jobId } = await response.json();
            if (!jobId) {
                throw new Error("Video job did not return a job id.");
            }

            const finalState = await pollVideoJob(jobId, setClipStatus);
            renderGeneratedResults(finalState.results || [], finalState.errors || []);
        } catch (error) {
            console.error(error);
            clipPlaceholder?.classList.remove("hidden");
            setClipStatus(`Failed: ${error.message}`);
        } finally {
            clipLoading?.classList.add("hidden");
            clipGenerateBtn.disabled = selectedSources.length === 0;
        }
    }

    function bindCreateClipWorkspace() {
        normalizeClipPlaceholder();

        if (clipDropZone && clipInput) {
            clipDropZone.addEventListener("click", (event) => {
                if (event.target.closest("button")) {
                    return;
                }
                clipInput.click();
            });
            clipDropZone.addEventListener("dragover", (event) => {
                event.preventDefault();
                clipDropZone.classList.add("dragover");
            });
            clipDropZone.addEventListener("dragleave", () => {
                clipDropZone.classList.remove("dragover");
            });
            clipDropZone.addEventListener("drop", (event) => {
                event.preventDefault();
                clipDropZone.classList.remove("dragover");
                addFileSources(event.dataTransfer?.files || []);
            });
            clipInput.addEventListener("change", (event) => {
                addFileSources(event.target.files || []);
                clipInput.value = "";
            });
        }

        clipRemoveAllBtn?.addEventListener("click", (event) => {
            event.preventDefault();
            event.stopPropagation();
            clearSelectedSources();
        });
        clipMotionSelect?.addEventListener("change", syncCustomPromptFields);
        clipEffectSelect?.addEventListener("change", syncCustomPromptFields);
        clipGenerateBtn?.addEventListener("click", handleGenerateClips);
        window.addEventListener("resize", resetClipDropZoneSize);
        syncCustomPromptFields();
        renderSelectedSources();
    }

    function clampNumber(value, min, max, fallback = min) {
        const numeric = Number(value);
        if (!Number.isFinite(numeric)) {
            return fallback;
        }
        return Math.min(max, Math.max(min, numeric));
    }

    function formatTimelineSeconds(value) {
        const safe = Number.isFinite(value) && value > 0 ? value : 0;
        if (safe >= 60) {
            const minutes = Math.floor(safe / 60);
            const seconds = (safe % 60).toFixed(1);
            return `${minutes}m ${seconds}s`;
        }
        return `${safe.toFixed(1)}s`;
    }

    function formatTimelineClock(value) {
        const safe = Number.isFinite(value) && value > 0 ? value : 0;
        const totalSeconds = Math.floor(safe);
        const minutes = Math.floor(totalSeconds / 60);
        const seconds = totalSeconds % 60;
        return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
    }

    function revokeAssembleClipPreview(item) {
        if (item?.previewUrl) {
            URL.revokeObjectURL(item.previewUrl);
        }
    }

    function loadVideoMetadata(previewUrl) {
        return new Promise((resolve) => {
            const probe = document.createElement("video");
            let settled = false;
            const finish = (payload) => {
                if (settled) {
                    return;
                }
                settled = true;
                resolve(payload);
            };

            probe.preload = "metadata";
            probe.muted = true;
            probe.playsInline = true;
            probe.onloadedmetadata = () => {
                finish({
                    duration: Number.isFinite(probe.duration) && probe.duration > 0 ? probe.duration : 0,
                    width: probe.videoWidth || 0,
                    height: probe.videoHeight || 0,
                });
            };
            probe.onerror = () => finish({ duration: 0, width: 0, height: 0 });
            probe.src = previewUrl;
        });
    }

    function getAssembleClipLimit(item) {
        return Math.max(0.1, Math.min(item?.duration || 5, 5));
    }

    function normalizeAssembleClip(item) {
        if (!item) {
            return;
        }
        const limit = getAssembleClipLimit(item);
        const startMax = Math.max(0, limit - 0.1);
        item.trimStart = clampNumber(item.trimStart, 0, startMax, 0);
        item.trimEnd = clampNumber(item.trimEnd, item.trimStart + 0.1, limit, limit);
        item.speed = clampNumber(item.speed, 0.25, 2.0, 1.0);
    }

    function getActiveAssembleClip() {
        const active = selectedAssembleClips.find((item) => item.id === activeAssembleClipId);
        if (active) {
            normalizeAssembleClip(active);
            return active;
        }
        if (!selectedAssembleClips.length) {
            activeAssembleClipId = null;
            return null;
        }
        activeAssembleClipId = selectedAssembleClips[0].id;
        normalizeAssembleClip(selectedAssembleClips[0]);
        return selectedAssembleClips[0];
    }

    function getAssembleClipIndex(id) {
        return selectedAssembleClips.findIndex((item) => item.id === id);
    }

    function estimateAssemblePlayback(item) {
        normalizeAssembleClip(item);
        return Math.max(0.1, (item.trimEnd - item.trimStart) / item.speed);
    }

    function getAssembleSequenceDuration() {
        return selectedAssembleClips.reduce((sum, item) => sum + estimateAssemblePlayback(item), 0);
    }

    function getAssemblePreviewDuration() {
        if (assemblePreviewMode === "final" && assembleFinalVideoUrl) {
            const actual = assembleMonitorVideo && Number.isFinite(assembleMonitorVideo.duration) && assembleMonitorVideo.duration > 0
                ? assembleMonitorVideo.duration
                : 0;
            return actual || getAssembleSequenceDuration();
        }
        return getAssembleSequenceDuration();
    }

    function getAssembleAspectValue() {
        const ratio = ASSEMBLE_RATIO_MAP[assembleAspectRatio] || ASSEMBLE_RATIO_MAP["9:16"];
        return `${ratio.width} / ${ratio.height}`;
    }

    function renderAssembleRatioButtons() {
        assembleRatioButtons.forEach((button) => {
            button.classList.toggle("is-active", button.dataset.ratio === assembleAspectRatio);
        });
    }

    function renderAssembleFitButtons() {
        assembleFitModeButtons.forEach((button) => {
            button.classList.toggle("is-active", button.dataset.fitMode === assembleAspectMode);
        });
    }

    function triggerAssembleDownload(videoUrl) {
        if (!videoUrl) {
            return;
        }
        const anchor = document.createElement("a");
        anchor.href = `/download?url=${encodeURIComponent(videoUrl)}`;
        anchor.download = "";
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
    }

    function getAssembleTimelineMetrics() {
        const visibleSeconds = 20;
        const viewportWidth = assembleTimelineViewport?.clientWidth || 0;
        const tailWidth = selectedAssembleClips.length ? 56 : 0;
        const baselineWidth = Math.max(0, viewportWidth - tailWidth);
        const pixelsPerSecond = baselineWidth > 0 ? baselineWidth / visibleSeconds : 52;
        const totalDuration = getAssemblePreviewDuration();
        const visualDuration = Math.max(visibleSeconds, totalDuration, 0);
        const playableWidth = Math.max(baselineWidth, Math.round(visualDuration * pixelsPerSecond));
        return {
            visibleSeconds,
            viewportWidth,
            baselineWidth,
            pixelsPerSecond,
            totalDuration,
            visualDuration,
            tailWidth,
            playableWidth,
            trackWidth: playableWidth + tailWidth,
        };
    }

    function getAssembleSegments() {
        let offset = 0;
        return selectedAssembleClips.map((item, index) => {
            const playback = estimateAssemblePlayback(item);
            const segment = {
                item,
                index,
                start: offset,
                end: offset + playback,
                duration: playback,
            };
            offset += playback;
            return segment;
        });
    }

    function getAssembleSegmentByTime(time) {
        const segments = getAssembleSegments();
        if (!segments.length) {
            return null;
        }
        const clamped = clampNumber(time, 0, segments[segments.length - 1].end, 0);
        return segments.find((segment) => clamped < segment.end) || segments[segments.length - 1];
    }

    function getAssembleSegmentForClip(id) {
        return getAssembleSegments().find((segment) => segment.item.id === id) || null;
    }

    function getAssembleSourceTime(segment, sequenceTime) {
        const localTime = clampNumber(sequenceTime - segment.start, 0, segment.duration, 0);
        const delta = localTime * segment.item.speed;
        if (segment.item.reverse) {
            return clampNumber(segment.item.trimEnd - delta, 0, segment.item.duration, segment.item.trimEnd);
        }
        return clampNumber(segment.item.trimStart + delta, 0, segment.item.duration, segment.item.trimStart);
    }

    function getAssembleSequenceTimeFromMonitor() {
        const active = getActiveAssembleClip();
        const segment = active ? getAssembleSegmentForClip(active.id) : null;
        if (!active || !segment || !assembleMonitorVideo?.dataset.source) {
            return clampNumber(assembleSequenceTime, 0, getAssembleSequenceDuration(), 0);
        }
        const current = Number.isFinite(assembleMonitorVideo.currentTime) ? assembleMonitorVideo.currentTime : active.trimStart;
        if (active.reverse) {
            return clampNumber(segment.start + ((active.trimEnd - current) / active.speed), segment.start, segment.end, segment.start);
        }
        return clampNumber(segment.start + ((current - active.trimStart) / active.speed), segment.start, segment.end, segment.start);
    }

    function playAssembleMonitorVideoForward() {
        const active = getActiveAssembleClip();
        if (!assembleMonitorVideo || !active) {
            return;
        }
        assembleMonitorVideo.playbackRate = active.speed;
        if (assembleMonitorBackdrop && shouldUseAssembleBackdrop()) {
            assembleMonitorBackdrop.playbackRate = active.speed;
            syncAssembleBackdropTime(assembleMonitorVideo.currentTime || active.trimStart || 0);
            assembleMonitorBackdrop.play().catch(() => {
                assembleMonitorBackdrop.muted = true;
                return assembleMonitorBackdrop.play().catch(() => {});
            });
        }
        assembleMonitorVideo.play().catch(() => {
            assembleMonitorVideo.muted = true;
            return assembleMonitorVideo.play().catch(() => {});
        });
    }

    function beginAssembleSequenceSegmentPlayback({ forceSeek = false } = {}) {
        const segment = getAssembleSegmentByTime(assembleSequenceTime);
        if (!segment) {
            renderAssembleTimelineTransport();
            return;
        }
        const clipChanged = activeAssembleClipId !== segment.item.id || assemblePreviewMode !== "clip";
        activeAssembleClipId = segment.item.id;
        assemblePreviewMode = "clip";
        if (clipChanged) {
            renderAssembleTimeline();
            renderAssembleMonitor();
            renderAssembleInspector();
        }
        const targetTime = getAssembleSourceTime(segment, assembleSequenceTime);
        const shouldAutoplay = assembleSequencePlaying && !segment.item.reverse;
        assemblePendingClipSeek = {
            clipId: segment.item.id,
            sourceTime: targetTime,
            autoplay: shouldAutoplay,
        };
        if (assembleMonitorVideo?.dataset.source !== segment.item.previewUrl) {
            setMonitorVideoSource(segment.item.previewUrl);
        } else if (assembleMonitorVideo && assembleMonitorVideo.readyState >= 1) {
            try {
                if (forceSeek || Math.abs(assembleMonitorVideo.currentTime - targetTime) > 0.04) {
                    assembleMonitorVideo.currentTime = targetTime;
                    syncAssembleBackdropTime(targetTime);
                }
                if (shouldAutoplay) {
                    playAssembleMonitorVideoForward();
                }
                assemblePendingClipSeek = null;
            } catch (_error) {
                // Ignore until metadata is ready.
            }
        }
        syncAssembleMonitorPresentation();
        renderAssembleTimelineTransport();
    }

    function stopAssembleSequencePlayback({ pauseVideo = true } = {}) {
        assembleSequencePlaying = false;
        if (assembleSequenceRaf) {
            window.cancelAnimationFrame(assembleSequenceRaf);
            assembleSequenceRaf = 0;
        }
        assembleSequenceLastFrame = 0;
        if (pauseVideo) {
            assembleMonitorVideo?.pause();
            assembleMonitorBackdrop?.pause();
        }
        updateAssemblePlayButton();
    }

    function drawFrameCover(ctx, source, width, height) {
        const sourceWidth = source.videoWidth || source.naturalWidth || width;
        const sourceHeight = source.videoHeight || source.naturalHeight || height;
        const scale = Math.max(width / sourceWidth, height / sourceHeight);
        const drawWidth = sourceWidth * scale;
        const drawHeight = sourceHeight * scale;
        const dx = (width - drawWidth) / 2;
        const dy = (height - drawHeight) / 2;
        ctx.drawImage(source, dx, dy, drawWidth, drawHeight);
    }

    async function captureAssembleTimelineFrames(item, frameCount) {
        const probe = document.createElement("video");
        probe.preload = "auto";
        probe.muted = true;
        probe.playsInline = true;
        probe.src = item.previewUrl;

        const meta = await new Promise((resolve) => {
            probe.onloadedmetadata = () => resolve({
                duration: Number.isFinite(probe.duration) && probe.duration > 0 ? probe.duration : item.duration,
                width: probe.videoWidth || 0,
                height: probe.videoHeight || 0,
            });
            probe.onerror = () => resolve({ duration: item.duration, width: 0, height: 0 });
        });

        const duration = Math.max(0.2, meta.duration || item.duration || 1);
        const canvas = document.createElement("canvas");
        canvas.width = 128;
        canvas.height = 72;
        const ctx = canvas.getContext("2d");
        if (!ctx) {
            return [];
        }

        const frames = [];
        for (let index = 0; index < frameCount; index += 1) {
            const time = Math.min(duration - 0.05, (duration / Math.max(frameCount, 1)) * index + 0.05);
            await new Promise((resolve) => {
                const handleSeeked = () => {
                    ctx.clearRect(0, 0, canvas.width, canvas.height);
                    drawFrameCover(ctx, probe, canvas.width, canvas.height);
                    frames.push(canvas.toDataURL("image/jpeg", 0.78));
                    resolve();
                };
                probe.onseeked = handleSeeked;
                try {
                    probe.currentTime = time;
                } catch (_error) {
                    handleSeeked();
                }
            });
        }
        return frames;
    }

    function queueAssembleTimelineFrames(item, frameCount) {
        if (!item || assembleTimelineThumbCache.has(item.id) || assembleTimelineThumbJobs.has(item.id)) {
            return;
        }
        assembleTimelineThumbJobs.add(item.id);
        captureAssembleTimelineFrames(item, frameCount)
            .then((frames) => {
                if (frames.length) {
                    assembleTimelineThumbCache.set(item.id, frames);
                    renderAssembleTimeline();
                }
            })
            .finally(() => {
                assembleTimelineThumbJobs.delete(item.id);
            });
    }

    function invalidateAssembleExport() {
        assembleFinalVideoUrl = "";
        if (assemblePreviewMode === "final") {
            assemblePreviewMode = "clip";
        }
    }

    function attachPreviewHover(video) {
        video.addEventListener("mouseenter", () => {
            video.play().catch(() => {});
        });
        video.addEventListener("mouseleave", () => {
            video.pause();
            video.currentTime = 0;
        });
    }

    function buildPreviewVideo(src) {
        const video = document.createElement("video");
        video.src = src;
        video.muted = true;
        video.loop = true;
        video.playsInline = true;
        video.preload = "metadata";
        attachPreviewHover(video);
        return video;
    }

    function renderAssembleSummary() {
        const sequenceDuration = getAssembleSequenceDuration();
        if (assembleClipCount) {
            assembleClipCount.textContent = `${selectedAssembleClips.length}`;
        }
        if (assembleTotalDuration) {
            assembleTotalDuration.textContent = formatTimelineSeconds(sequenceDuration);
        }
        if (assembleExportMode) {
            assembleExportMode.textContent = assembleFinalVideoUrl ? "Ready" : selectedAssembleClips.length ? "Draft" : "--";
        }
        if (assembleTimelineSummary) {
            assembleTimelineSummary.textContent = selectedAssembleClips.length
                ? `${selectedAssembleClips.length} clip(s) added to the timeline. Drag the burgundy playhead or press play to preview the sequence.`
                : "Drop clips onto the timeline to begin arranging your sequence.";
        }
        if (assembleGenerateBtn) {
            assembleGenerateBtn.disabled = selectedAssembleClips.length === 0;
        }
        assembleRemoveAllBtn?.classList.toggle("hidden", selectedAssembleClips.length === 0);
        return;
        const totalDuration = selectedAssembleClips.reduce((sum, item) => sum + estimateAssemblePlayback(item), 0);
        if (assembleClipCount) {
            assembleClipCount.textContent = `${selectedAssembleClips.length}`;
        }
        if (assembleTotalDuration) {
            assembleTotalDuration.textContent = formatTimelineSeconds(totalDuration);
        }
        if (assembleExportMode) {
            assembleExportMode.textContent = assembleFinalVideoUrl ? "Ready" : selectedAssembleClips.length ? "Draft" : "--";
        }
        if (assembleTimelineSummary) {
            assembleTimelineSummary.textContent = selectedAssembleClips.length
                ? `${selectedAssembleClips.length} shot(s) in sequence · ${formatTimelineSeconds(totalDuration)} estimated runtime. Select a card to edit trim and speed.`
                : "Upload clips to populate the timeline.";
        }
        if (assembleGenerateBtn) {
            assembleGenerateBtn.disabled = selectedAssembleClips.length === 0;
        }
        assembleRemoveAllBtn?.classList.toggle("hidden", selectedAssembleClips.length === 0);
    }

    function renderAssembleMediaList() {
        if (!assemblePreviewContainer) {
            return;
        }
        assemblePreviewContainer.innerHTML = "";

        if (!selectedAssembleClips.length) {
            const empty = document.createElement("div");
            empty.className = "assemble-timeline-empty";
            empty.textContent = "No clips imported yet.";
            assemblePreviewContainer.appendChild(empty);
            return;
        }

        const active = getActiveAssembleClip();

        selectedAssembleClips.forEach((item, index) => {
            const row = document.createElement("article");
            row.className = "assemble-media-item";
            if (active?.id === item.id) {
                row.classList.add("is-active");
            }
            row.addEventListener("click", () => selectAssembleClip(item.id));

            const thumb = document.createElement("div");
            thumb.className = "assemble-media-thumb";
            thumb.appendChild(buildPreviewVideo(item.previewUrl));

            const meta = document.createElement("div");
            meta.className = "assemble-media-meta";

            const name = document.createElement("span");
            name.className = "assemble-media-name";
            name.textContent = item.file.name;

            const sub = document.createElement("span");
            sub.className = "assemble-media-sub";
            sub.textContent = `Shot ${String(index + 1).padStart(2, "0")} · ${formatTimelineSeconds(item.duration)} source`;

            meta.appendChild(name);
            meta.appendChild(sub);

            const remove = document.createElement("button");
            remove.type = "button";
            remove.className = "assemble-timeline-btn";
            remove.textContent = "Remove";
            remove.addEventListener("click", (event) => {
                event.stopPropagation();
                removeAssembleClip(index);
            });

            row.appendChild(thumb);
            row.appendChild(meta);
            row.appendChild(remove);
            assemblePreviewContainer.appendChild(row);
        });
    }

    function renderAssembleTimelineRuler() {
        if (!assembleTimelineRuler) {
            return;
        }
        const marks = [0, 5, 10, 15, 20];
        assembleTimelineRuler.innerHTML = "";
        for (const markValue of marks) {
            const mark = document.createElement("span");
            mark.textContent = formatTimelineClock(markValue);
            assembleTimelineRuler.appendChild(mark);
        }
    }

    function renderAssembleTimelineTransport() {
        const { totalDuration, visualDuration, playableWidth } = getAssembleTimelineMetrics();
        const hasClips = totalDuration > 0 && selectedAssembleClips.length > 0;
        assembleSequenceTime = clampNumber(assembleSequenceTime, 0, totalDuration || 0, 0);
        assembleTimelinePlayhead?.classList.toggle("hidden", !hasClips);
        assembleTimelineScrubber?.classList.toggle("hidden", !hasClips);
        if (!hasClips || !assembleTimelineTrack) {
            return;
        }
        const ratio = visualDuration > 0 ? assembleSequenceTime / visualDuration : 0;
        const clampedRatio = clampNumber(ratio, 0, 1, 0);
        const maxX = Math.max(0, playableWidth - 3);
        const position = Math.round(clampedRatio * maxX);
        if (assembleTimelinePlayhead) {
            assembleTimelinePlayhead.style.transform = `translateX(${position}px)`;
        }
        if (assembleTimelineScrubber) {
            assembleTimelineScrubber.max = "1000";
            assembleTimelineScrubber.value = `${Math.round(clampedRatio * 1000)}`;
            assembleTimelineScrubber.style.width = `${playableWidth}px`;
            assembleTimelineScrubber.style.left = "0px";
            assembleTimelineScrubber.style.right = "auto";
        }
        if (assembleTimelineViewport) {
            const leftEdge = assembleTimelineViewport.scrollLeft + 40;
            const rightEdge = assembleTimelineViewport.scrollLeft + assembleTimelineViewport.clientWidth - 40;
            if (position < leftEdge || position > rightEdge) {
                assembleTimelineViewport.scrollLeft = Math.max(0, position - assembleTimelineViewport.clientWidth / 2);
            }
        }
    }

    function applyAssembleSequenceTime(nextTime, { forceSeek = false } = {}) {
        const totalDuration = getAssembleSequenceDuration();
        assembleSequenceTime = clampNumber(nextTime, 0, totalDuration || 0, 0);
        const segment = getAssembleSegmentByTime(assembleSequenceTime);
        if (!segment) {
            renderAssembleTimelineTransport();
            return;
        }
        const clipChanged = activeAssembleClipId !== segment.item.id || assemblePreviewMode !== "clip";
        activeAssembleClipId = segment.item.id;
        assemblePreviewMode = "clip";
        if (clipChanged) {
            renderAssembleTimeline();
            renderAssembleMonitor();
            renderAssembleInspector();
        }
        const targetTime = getAssembleSourceTime(segment, assembleSequenceTime);
        assemblePendingClipSeek = {
            clipId: segment.item.id,
            sourceTime: targetTime,
            autoplay: false,
        };
        if (assembleMonitorVideo?.dataset.source !== segment.item.previewUrl) {
            setMonitorVideoSource(segment.item.previewUrl);
        }
        if (assembleMonitorVideo && assembleMonitorVideo.readyState >= 1) {
            try {
                if (forceSeek || Math.abs(assembleMonitorVideo.currentTime - targetTime) > 0.04) {
                    assembleMonitorVideo.currentTime = targetTime;
                    syncAssembleBackdropTime(targetTime);
                }
                assemblePendingClipSeek = null;
            } catch (_error) {
                // Ignore until metadata is ready.
            }
        }
        syncAssembleMonitorPresentation();
        renderAssembleTimelineTransport();
    }

    function tickAssembleSequencePlayback(now) {
        if (!assembleSequencePlaying) {
            return;
        }
        if (!assembleSequenceLastFrame) {
            assembleSequenceLastFrame = now;
        }
        const delta = (now - assembleSequenceLastFrame) / 1000;
        assembleSequenceLastFrame = now;

        if (assemblePreviewMode === "final" && assembleFinalVideoUrl) {
            const totalDuration = getAssemblePreviewDuration();
            const current = Number.isFinite(assembleMonitorVideo?.currentTime) ? assembleMonitorVideo.currentTime : assembleSequenceTime;
            assembleSequenceTime = clampNumber(current, 0, totalDuration || 0, 0);
            renderAssembleTimelineTransport();
            if (current >= totalDuration - 0.02) {
                if (assembleMonitorVideo && !assembleMonitorVideo.paused) {
                    assembleMonitorVideo.pause();
                }
                stopAssembleSequencePlayback({ pauseVideo: false });
                updateAssemblePlaybackReadout();
                return;
            }
            if (assembleMonitorVideo?.paused) {
                stopAssembleSequencePlayback({ pauseVideo: false });
                return;
            }
        } else {
            const active = getActiveAssembleClip();
            if (active && !active.reverse && assembleMonitorVideo?.dataset.source) {
                const totalDuration = getAssembleSequenceDuration();
                const currentSequence = getAssembleSequenceTimeFromMonitor();
                assembleSequenceTime = clampNumber(currentSequence, 0, totalDuration || 0, 0);
                renderAssembleTimelineTransport();
                updateAssemblePlaybackReadout();

                const currentSegment = getAssembleSegmentForClip(active.id);
                const currentSourceTime = Number.isFinite(assembleMonitorVideo.currentTime)
                    ? assembleMonitorVideo.currentTime
                    : active.trimStart;
                const hitSegmentEnd = currentSourceTime >= active.trimEnd - 0.03;
                const atSequenceEnd = assembleSequenceTime >= totalDuration - 0.02;

                if (hitSegmentEnd) {
                    if (atSequenceEnd || !currentSegment || currentSegment.index >= selectedAssembleClips.length - 1) {
                        try {
                            assembleMonitorVideo.pause();
                        } catch (_error) {
                            // Ignore pause failures.
                        }
                        assembleSequenceTime = totalDuration;
                        renderAssembleTimelineTransport();
                        stopAssembleSequencePlayback({ pauseVideo: false });
                        updateAssemblePlaybackReadout();
                        return;
                    }

                    const nextSegment = getAssembleSegments()[currentSegment.index + 1];
                    assembleSequenceTime = nextSegment.start;
                    beginAssembleSequenceSegmentPlayback({ forceSeek: true });
                } else if (assembleMonitorVideo.paused) {
                    playAssembleMonitorVideoForward();
                }

                assembleSequenceRaf = window.requestAnimationFrame(tickAssembleSequencePlayback);
                return;
            }

            const totalDuration = getAssembleSequenceDuration();
            const nextTime = Math.min(totalDuration, assembleSequenceTime + delta);
            applyAssembleSequenceTime(nextTime, { forceSeek: true });
            if (nextTime >= totalDuration) {
                stopAssembleSequencePlayback();
                return;
            }
        }

        assembleSequenceRaf = window.requestAnimationFrame(tickAssembleSequencePlayback);
    }

    function startAssembleSequencePlayback() {
        if (assemblePreviewMode === "final" && assembleFinalVideoUrl) {
            if (!assembleMonitorVideo?.dataset.source) {
                return;
            }
            const totalDuration = getAssemblePreviewDuration();
            if (assembleSequenceTime >= totalDuration - 0.02) {
                assembleSequenceTime = 0;
                try {
                    assembleMonitorVideo.currentTime = 0;
                } catch (_error) {
                    // Ignore until metadata is ready.
                }
            }
            assembleSequencePlaying = true;
            assembleSequenceLastFrame = 0;
            assembleMonitorVideo.play().catch(() => {
                assembleMonitorVideo.muted = true;
                return assembleMonitorVideo.play().catch(() => {});
            });
            assembleSequenceRaf = window.requestAnimationFrame(tickAssembleSequencePlayback);
            updateAssemblePlayButton();
            return;
        }
        if (!selectedAssembleClips.length) {
            return;
        }
        const totalDuration = getAssembleSequenceDuration();
        if (assembleSequenceTime >= totalDuration - 0.01) {
            assembleSequenceTime = 0;
        }
        assembleSequencePlaying = true;
        assembleSequenceLastFrame = 0;
        const active = getActiveAssembleClip();
        if (active && !active.reverse) {
            beginAssembleSequenceSegmentPlayback({ forceSeek: true });
        } else {
            applyAssembleSequenceTime(assembleSequenceTime, { forceSeek: true });
        }
        assembleSequenceRaf = window.requestAnimationFrame(tickAssembleSequencePlayback);
        updateAssemblePlayButton();
    }

    function renderAssembleTimeline() {
        if (!assembleTimelineTrack) {
            return;
        }
        assembleTimelineTrack.innerHTML = "";
        const hasTimelineClips = selectedAssembleClips.length > 0;
        const {
            pixelsPerSecond,
            totalDuration: sequenceDuration,
            visualDuration,
            playableWidth,
            trackWidth,
        } = getAssembleTimelineMetrics();
        const activeClip = getActiveAssembleClip();
        assembleTimelineTrack.classList.toggle("is-empty", !hasTimelineClips);
        assembleTimelineViewport?.classList.toggle("is-empty", !hasTimelineClips);
        renderAssembleTimelineRuler();

        if (!hasTimelineClips) {
            assembleTimelineTrack.style.width = "100%";
            if (assembleDropZone) {
                assembleDropZone.classList.add("is-empty");
                assembleDropZone.classList.remove("is-tail");
                assembleDropZone.style.width = "100%";
                assembleTimelineTrack.appendChild(assembleDropZone);
            }
            renderAssembleTimelineTransport();
            return;
        }

        assembleDropZone?.classList.remove("is-empty");
        assembleDropZone?.classList.add("is-tail");
        assembleDropZone?.style.removeProperty("width");
        assembleTimelineTrack.style.width = `${trackWidth}px`;

        getAssembleSegments().forEach((segment) => {
            const clip = segment.item;
            const segmentEl = document.createElement("button");
            segmentEl.type = "button";
            segmentEl.className = "assemble-filmstrip-segment";
            if (activeClip?.id === clip.id) {
                segmentEl.classList.add("is-active");
            }
            const segmentWidth = Math.max(1, Math.round(segment.duration * pixelsPerSecond));
            segmentEl.style.width = `${segmentWidth}px`;
            if (segmentWidth < 108) {
                segmentEl.classList.add("is-compact");
            }
            if (segmentWidth < 40) {
                segmentEl.classList.add("is-micro");
            }
            segmentEl.setAttribute("aria-label", `Preview shot ${String(segment.index + 1).padStart(2, "0")}`);
            segmentEl.addEventListener("click", () => {
                stopAssembleSequencePlayback();
                assembleSequenceTime = segment.start;
                selectAssembleClip(clip.id);
            });

            const strip = document.createElement("div");
            strip.className = "assemble-filmstrip-strip";
            const thumbCount = Math.max(4, Math.ceil(segment.duration * 2));
            const frames = assembleTimelineThumbCache.get(clip.id);
            if (frames?.length) {
                for (let index = 0; index < thumbCount; index += 1) {
                    const frame = document.createElement("img");
                    frame.className = "assemble-filmstrip-frame";
                    frame.src = frames[index % frames.length];
                    frame.alt = "";
                    strip.appendChild(frame);
                }
            } else {
                for (let index = 0; index < thumbCount; index += 1) {
                    const placeholder = document.createElement("div");
                    placeholder.className = "assemble-filmstrip-frame is-placeholder";
                    strip.appendChild(placeholder);
                }
                queueAssembleTimelineFrames(clip, thumbCount);
            }

            const meta = document.createElement("div");
            meta.className = "assemble-filmstrip-meta";

            const badge = document.createElement("span");
            badge.className = "assemble-filmstrip-badge";
            badge.textContent = `Shot ${String(segment.index + 1).padStart(2, "0")}`;

            const duration = document.createElement("span");
            duration.className = "assemble-filmstrip-duration";
            duration.textContent = formatTimelineClock(segment.duration);

            meta.appendChild(badge);
            meta.appendChild(duration);
            segmentEl.appendChild(strip);
            segmentEl.appendChild(meta);
            assembleTimelineTrack.appendChild(segmentEl);
        });

        const fillerWidth = Math.max(0, playableWidth - Math.round(sequenceDuration * pixelsPerSecond));
        if (fillerWidth > 0) {
            const filler = document.createElement("div");
            filler.className = "assemble-timeline-filler";
            filler.style.width = `${fillerWidth}px`;
            assembleTimelineTrack.appendChild(filler);
        }

        if (assembleDropZone) {
            assembleTimelineTrack.appendChild(assembleDropZone);
        }

        if (assembleTimelinePlayhead) {
            assembleTimelineTrack.appendChild(assembleTimelinePlayhead);
        }
        if (assembleTimelineScrubber) {
            assembleTimelineTrack.appendChild(assembleTimelineScrubber);
        }
        renderAssembleTimelineTransport();
        return;
        assembleTimelineTrack.innerHTML = "";
        const hasClips = selectedAssembleClips.length > 0;
        assembleTimelineTrack.classList.toggle("is-empty", !hasClips);

        if (!hasClips) {
            if (assembleDropZone) {
                assembleDropZone.classList.add("is-empty");
                assembleTimelineTrack.appendChild(assembleDropZone);
            }
            return;
        }

        assembleDropZone?.classList.remove("is-empty");

        const active = getActiveAssembleClip();

        selectedAssembleClips.forEach((item, index) => {
            const card = document.createElement("article");
            card.className = "assemble-timeline-card";
            if (active?.id === item.id) {
                card.classList.add("is-active");
            }
            card.style.flexBasis = `${Math.max(220, Math.min(360, estimateAssemblePlayback(item) * 120 + 150))}px`;
            card.addEventListener("click", () => selectAssembleClip(item.id));

            const header = document.createElement("div");
            header.className = "assemble-timeline-header-row";

            const badge = document.createElement("span");
            badge.className = "assemble-shot-badge";
            badge.textContent = `Shot ${String(index + 1).padStart(2, "0")}`;

            const tag = document.createElement("span");
            tag.className = "assemble-shot-badge";
            tag.textContent = formatTimelineSeconds(estimateAssemblePlayback(item));

            header.appendChild(badge);
            if (item.reverse) {
                const reverseTag = document.createElement("span");
                reverseTag.className = "assemble-shot-badge is-modifier";
                reverseTag.textContent = "REV";
                header.appendChild(reverseTag);
            }
            if (item.flipHorizontal) {
                const flipTag = document.createElement("span");
                flipTag.className = "assemble-shot-badge is-modifier";
                flipTag.textContent = "FLIP";
                header.appendChild(flipTag);
            }
            header.appendChild(tag);

            const thumb = document.createElement("div");
            thumb.className = "assemble-timeline-thumb";
            const thumbVideo = buildPreviewVideo(item.previewUrl);
            thumbVideo.style.transform = item.flipHorizontal ? "scaleX(-1)" : "";
            thumb.appendChild(thumbVideo);

            const copy = document.createElement("div");
            copy.className = "assemble-timeline-card-copy";

            const name = document.createElement("span");
            name.className = "assemble-timeline-name";
            name.textContent = item.file.name;

            const sub = document.createElement("span");
            sub.className = "assemble-timeline-sub";
            sub.textContent = `${formatTimelineSeconds(item.trimStart)} to ${formatTimelineSeconds(item.trimEnd)} · ${item.speed.toFixed(2)}x`;

            copy.appendChild(name);
            copy.appendChild(sub);

            const meta = document.createElement("div");
            meta.className = "assemble-timeline-meta";

            const rawTag = document.createElement("span");
            rawTag.className = "assemble-timeline-tag";
            rawTag.textContent = `${formatTimelineSeconds(item.duration)} source`;

            const trimmedTag = document.createElement("span");
            trimmedTag.className = "assemble-timeline-tag";
            trimmedTag.textContent = `${formatTimelineSeconds(estimateAssemblePlayback(item))} export`;

            meta.appendChild(rawTag);
            meta.appendChild(trimmedTag);

            const actions = document.createElement("div");
            actions.className = "assemble-timeline-actions";

            const buildIconButton = (iconName, label, onClick) => {
                const button = document.createElement("button");
                button.type = "button";
                button.className = "assemble-timeline-btn is-icon";
                button.setAttribute("aria-label", label);
                button.innerHTML = `<span class="material-symbols-outlined">${iconName}</span>`;
                button.addEventListener("click", (event) => {
                    event.stopPropagation();
                    onClick();
                });
                return button;
            };

            const moveLeft = document.createElement("button");
            moveLeft.type = "button";
            moveLeft.className = "assemble-timeline-btn is-icon";
            moveLeft.setAttribute("aria-label", "Move clip left");
            moveLeft.innerHTML = '<span class="material-symbols-outlined">arrow_back</span>';
            moveLeft.disabled = index === 0;
            moveLeft.addEventListener("click", (event) => {
                event.stopPropagation();
                moveAssembleClip(index, -1);
            });

            const moveRight = document.createElement("button");
            moveRight.type = "button";
            moveRight.className = "assemble-timeline-btn is-icon";
            moveRight.setAttribute("aria-label", "Move clip right");
            moveRight.innerHTML = '<span class="material-symbols-outlined">arrow_forward</span>';
            moveRight.disabled = index === selectedAssembleClips.length - 1;
            moveRight.addEventListener("click", (event) => {
                event.stopPropagation();
                moveAssembleClip(index, 1);
            });

            const remove = buildIconButton("delete", "Delete clip", () => removeAssembleClip(index));

            actions.appendChild(moveLeft);
            actions.appendChild(moveRight);
            actions.appendChild(buildIconButton("content_cut", "Focus trim controls", () => {
                selectAssembleClip(item.id);
                focusAssembleTrimControls();
            }));
            actions.appendChild(buildIconButton("flip", "Flip clip horizontally", () => {
                selectAssembleClip(item.id);
                toggleActiveAssembleFlag("flipHorizontal");
            }));
            actions.appendChild(buildIconButton("replay", "Reverse clip", () => {
                selectAssembleClip(item.id);
                toggleActiveAssembleFlag("reverse");
            }));
            actions.appendChild(remove);

            card.appendChild(header);
            card.appendChild(thumb);
            card.appendChild(copy);
            card.appendChild(meta);
            card.appendChild(actions);
            assembleTimelineTrack.appendChild(card);
        });

        if (assembleDropZone) {
            assembleTimelineTrack.appendChild(assembleDropZone);
        }
    }

    function setMonitorVideoSource(url) {
        if (!assembleMonitorVideo) {
            return;
        }
        if (!url) {
            assembleMonitorVideo.pause();
            assembleMonitorVideo.removeAttribute("src");
            assembleMonitorVideo.dataset.source = "";
            assembleMonitorVideo.load();
            if (assembleMonitorBackdrop) {
                assembleMonitorBackdrop.pause();
                assembleMonitorBackdrop.removeAttribute("src");
                assembleMonitorBackdrop.dataset.source = "";
                assembleMonitorBackdrop.load();
            }
            return;
        }
        if (assembleMonitorVideo.dataset.source === url && (!assembleMonitorBackdrop || assembleMonitorBackdrop.dataset.source === url)) {
            return;
        }
        assembleMonitorVideo.pause();
        assembleMonitorVideo.src = url;
        assembleMonitorVideo.dataset.source = url;
        assembleMonitorVideo.load();
        if (assembleMonitorBackdrop) {
            assembleMonitorBackdrop.pause();
            assembleMonitorBackdrop.src = url;
            assembleMonitorBackdrop.dataset.source = url;
            assembleMonitorBackdrop.load();
        }
    }

    function shouldUseAssembleBackdrop() {
        return assemblePreviewMode === "clip" && Boolean(getActiveAssembleClip()) && assembleAspectMode === "fill";
    }

    function syncAssembleBackdropTime(targetTime) {
        if (!assembleMonitorBackdrop || !shouldUseAssembleBackdrop()) {
            return;
        }
        try {
            if (!Number.isFinite(assembleMonitorBackdrop.currentTime) || Math.abs(assembleMonitorBackdrop.currentTime - targetTime) > 0.05) {
                assembleMonitorBackdrop.currentTime = targetTime;
            }
        } catch (_error) {
            // Ignore until metadata is ready.
        }
    }

    function clampAssembleZoom(value) {
        return clampNumber(value, 0.5, 1.75, 1);
    }

    function updateAssemblePlayButton() {
        if (!assemblePlayToggle) {
            return;
        }
        const icon = assemblePlayToggle.querySelector(".material-symbols-outlined");
        if (icon) {
            const isPlaying = assemblePreviewMode === "final" && assembleFinalVideoUrl
                ? Boolean(assembleMonitorVideo && !assembleMonitorVideo.paused)
                : assembleSequencePlaying;
            icon.textContent = isPlaying ? "pause" : "play_arrow";
        }
    }

    function updateAssemblePlaybackReadout() {
        if (!assemblePlaybackCurrent || !assemblePlaybackTotal) {
            return;
        }
        const active = getActiveAssembleClip();
        const totalDuration = getAssemblePreviewDuration();
        if (assemblePreviewMode === "final" && assembleFinalVideoUrl) {
            const current = assembleMonitorVideo && Number.isFinite(assembleMonitorVideo.currentTime)
                ? assembleMonitorVideo.currentTime
                : 0;
            const clampedCurrent = clampNumber(current, 0, totalDuration || 0, 0);
            assembleSequenceTime = clampedCurrent;
            assemblePlaybackCurrent.textContent = formatTimelineClock(clampedCurrent);
            assemblePlaybackTotal.textContent = formatTimelineClock(totalDuration);
            return;
        }
        if (active) {
            assemblePlaybackCurrent.textContent = formatTimelineClock(assembleSequenceTime);
            assemblePlaybackTotal.textContent = formatTimelineClock(totalDuration);
            return;
        }
        assemblePlaybackCurrent.textContent = "00:00";
        assemblePlaybackTotal.textContent = "00:00";
    }

    function syncAssembleMonitorTimeWindow(forceSeek = false) {
        if (!assembleMonitorVideo) {
            return;
        }
        const active = getActiveAssembleClip();
        const isClipMode = assemblePreviewMode === "clip" && active;
        if (!isClipMode) {
            return;
        }

        const start = Math.max(0, active.trimStart);
        const end = Math.max(start + 0.1, active.trimEnd);
        if (forceSeek || assembleMonitorVideo.currentTime < start || assembleMonitorVideo.currentTime > end) {
            try {
                assembleMonitorVideo.currentTime = start;
                syncAssembleBackdropTime(start);
            } catch (_error) {
                // Ignore browser timing errors until metadata is ready.
            }
        }
    }

    function syncAssembleMonitorPresentation() {
        if (!assembleMonitorCanvas || !assembleMonitorVideo) {
            return;
        }
        const active = getActiveAssembleClip();
        const isClipMode = assemblePreviewMode === "clip" && active;
        const scale = clampAssembleZoom(assembleMonitorZoom);
        const flip = isClipMode && active?.flipHorizontal ? -1 : 1;
        const fitMode = isClipMode ? assembleAspectMode : "final";

        assembleMonitorCanvas.style.setProperty("--assemble-monitor-scale", `${scale}`);
        assembleMonitorCanvas.style.setProperty("--assemble-monitor-ratio", getAssembleAspectValue());
        assembleMonitorCanvas.dataset.fitMode = fitMode;
        assembleMonitorVideo.style.transform = `scale(${scale}) scaleX(${flip})`;
        assembleMonitorVideo.playbackRate = isClipMode ? active.speed : 1;
        if (assembleMonitorBackdrop) {
            const backdropScale = Math.max(1.12, scale * 1.08);
            assembleMonitorBackdrop.style.transform = `scale(${backdropScale}) scaleX(${flip})`;
            assembleMonitorBackdrop.playbackRate = isClipMode ? active.speed : 1;
            if (!shouldUseAssembleBackdrop()) {
                assembleMonitorBackdrop.pause();
            }
        }
        if (assembleZoomReadout) {
            assembleZoomReadout.textContent = `${Math.round(scale * 100)}%`;
        }
        syncAssembleMonitorTimeWindow();
        updateAssemblePlayButton();
        updateAssemblePlaybackReadout();
        renderAssembleTimelineTransport();
    }

    function focusAssembleTrimControls() {
        if (!assembleTrimCard) {
            return;
        }
        assembleTrimCard.classList.add("is-focused");
        assembleTrimCard.scrollIntoView({ block: "center", behavior: "smooth" });
        assembleTrimStartRange?.focus();
        window.setTimeout(() => assembleTrimCard.classList.remove("is-focused"), 1400);
    }

    function toggleActiveAssembleFlag(flagName) {
        updateActiveAssembleClip((item) => {
            item[flagName] = !item[flagName];
        });
    }

    function renderAssembleMonitor() {
        const active = getActiveAssembleClip();
        const canShowFinal = Boolean(assembleFinalVideoUrl);
        if (assemblePreviewMode === "final" && canShowFinal) {
            assemblePlaceholder?.classList.add("hidden");
            assembleMonitorPlayer?.classList.remove("hidden");
            if (assemblePreviewBadge) {
                assemblePreviewBadge.textContent = "Sequence Preview";
            }
            if (assembleMonitorTitle) {
                assembleMonitorTitle.textContent = "Exported Sequence";
            }
            if (assembleMonitorMeta) {
                const totalDuration = getAssemblePreviewDuration();
                assembleMonitorMeta.textContent = `${selectedAssembleClips.length} shot(s) | ${formatTimelineSeconds(totalDuration)} runtime`;
                assembleMonitorMeta.textContent = `${selectedAssembleClips.length} shot(s) | ${formatTimelineSeconds(totalDuration)} runtime`;
                assembleMonitorMeta.textContent = `${selectedAssembleClips.length} shot(s) · ${formatTimelineSeconds(totalDuration)} estimated runtime`;
            }
            if (assembleMonitorMeta) {
                const totalDuration = getAssemblePreviewDuration();
                assembleMonitorMeta.textContent = `${selectedAssembleClips.length} shot(s) | ${formatTimelineSeconds(totalDuration)} runtime`;
            }
            setMonitorVideoSource(assembleFinalVideoUrl);
            syncAssembleMonitorPresentation();
            return;
        }

        if (active) {
            assemblePreviewMode = "clip";
            assemblePlaceholder?.classList.add("hidden");
            assembleMonitorPlayer?.classList.remove("hidden");
            const index = getAssembleClipIndex(active.id);
            if (assemblePreviewBadge) {
                assemblePreviewBadge.textContent = "Preview Monitor";
            }
            if (assembleMonitorTitle) {
                assembleMonitorTitle.textContent = `Shot ${String(index + 1).padStart(2, "0")} Preview`;
            }
            if (assembleMonitorMeta) {
                const resolution = active.width && active.height ? `${active.width}x${active.height}` : "Resolution pending";
                const statusBits = [
                    resolution,
                    `${formatTimelineSeconds(active.trimStart)} to ${formatTimelineSeconds(active.trimEnd)}`,
                    `${active.speed.toFixed(2)}x`,
                ];
                if (active.reverse) {
                    statusBits.push("reverse");
                }
                if (active.flipHorizontal) {
                    statusBits.push("flip");
                }
                assembleMonitorMeta.textContent = statusBits.join(" | ");
                assembleMonitorMeta.textContent = `Shot ${String(index + 1).padStart(2, "0")} · ${resolution} · ${formatTimelineSeconds(active.trimStart)} to ${formatTimelineSeconds(active.trimEnd)} · ${active.speed.toFixed(2)}x`;
            }
            if (assembleMonitorMeta) {
                const index = getAssembleClipIndex(active.id);
                const resolution = active.width && active.height ? `${active.width}x${active.height}` : "Resolution pending";
                const statusBits = [
                    `Shot ${String(index + 1).padStart(2, "0")}`,
                    resolution,
                    `${formatTimelineSeconds(active.trimStart)} to ${formatTimelineSeconds(active.trimEnd)}`,
                    `${active.speed.toFixed(2)}x`,
                ];
                if (active.reverse) {
                    statusBits.push("reverse");
                }
                if (active.flipHorizontal) {
                    statusBits.push("flip");
                }
                assembleMonitorMeta.textContent = statusBits.join(" | ");
            }
            setMonitorVideoSource(active.previewUrl);
            syncAssembleMonitorTimeWindow(true);
            syncAssembleMonitorPresentation();
            return;
        }

        assemblePlaceholder?.classList.remove("hidden");
        assembleMonitorPlayer?.classList.add("hidden");
        if (assembleMonitorTitle) {
            assembleMonitorTitle.textContent = "No clip selected";
        }
        if (assembleMonitorMeta) {
            assembleMonitorMeta.textContent = "Upload clips and select a shot to preview it here.";
        }
        setMonitorVideoSource("");
        assembleDeleteBtn?.toggleAttribute("disabled", true);
        assembleTrimBtn?.toggleAttribute("disabled", true);
        assembleReverseBtn?.classList.remove("is-active");
        assembleFlipBtn?.classList.remove("is-active");
        syncAssembleMonitorPresentation();
    }

    function renderAssembleInspector() {
        const active = getActiveAssembleClip();
        renderAssembleRatioButtons();
        renderAssembleFitButtons();
        if (!active) {
            assembleInspectorEmpty?.classList.remove("hidden");
            assembleInspectorForm?.classList.add("hidden");
            if (assembleReverseState) {
                assembleReverseState.textContent = "Off";
            }
            if (assembleFlipState) {
                assembleFlipState.textContent = "Off";
            }
            assembleDeleteBtn?.toggleAttribute("disabled", true);
            assembleTrimBtn?.toggleAttribute("disabled", true);
            assembleReverseBtn?.toggleAttribute("disabled", true);
            assembleFlipBtn?.toggleAttribute("disabled", true);
            return;
        }

        normalizeAssembleClip(active);
        const limit = getAssembleClipLimit(active);
        const index = getAssembleClipIndex(active.id);

        assembleInspectorEmpty?.classList.add("hidden");
        assembleInspectorForm?.classList.remove("hidden");

        if (assembleInspectorName) {
            assembleInspectorName.textContent = active.file.name;
        }
        if (assembleInspectorMeta) {
            const resolution = active.width && active.height ? `${active.width}x${active.height}` : "Resolution pending";
            assembleInspectorMeta.textContent = `${resolution} · ${formatTimelineSeconds(active.duration)} source`;
        }

        if (assembleTrimStartRange) {
            assembleTrimStartRange.min = "0";
            assembleTrimStartRange.max = `${Math.max(0, limit - 0.1)}`;
            assembleTrimStartRange.value = `${active.trimStart}`;
        }
        if (assembleTrimStartInput) {
            assembleTrimStartInput.min = "0";
            assembleTrimStartInput.max = `${Math.max(0, limit - 0.1)}`;
            assembleTrimStartInput.value = active.trimStart.toFixed(1);
        }
        if (assembleTrimEndRange) {
            assembleTrimEndRange.min = `${active.trimStart + 0.1}`;
            assembleTrimEndRange.max = `${limit}`;
            assembleTrimEndRange.value = `${active.trimEnd}`;
        }
        if (assembleTrimEndInput) {
            assembleTrimEndInput.min = `${active.trimStart + 0.1}`;
            assembleTrimEndInput.max = `${limit}`;
            assembleTrimEndInput.value = active.trimEnd.toFixed(1);
        }
        if (assembleSpeedRange) {
            assembleSpeedRange.value = `${active.speed}`;
        }
        if (assembleSpeedInput) {
            assembleSpeedInput.value = active.speed.toFixed(2);
        }
        if (assembleClipOrder) {
            assembleClipOrder.textContent = `Shot ${String(index + 1).padStart(2, "0")}`;
        }
        if (assembleClipDuration) {
            assembleClipDuration.textContent = formatTimelineSeconds(estimateAssemblePlayback(active));
        }
        if (assembleReverseState) {
            assembleReverseState.textContent = active.reverse ? "On" : "Off";
        }
        if (assembleFlipState) {
            assembleFlipState.textContent = active.flipHorizontal ? "On" : "Off";
        }
        assembleReverseBtn?.classList.toggle("is-active", Boolean(active.reverse));
        assembleFlipBtn?.classList.toggle("is-active", Boolean(active.flipHorizontal));
        assembleDeleteBtn?.toggleAttribute("disabled", false);
        assembleTrimBtn?.toggleAttribute("disabled", false);
        assembleReverseBtn?.toggleAttribute("disabled", false);
        assembleFlipBtn?.toggleAttribute("disabled", false);
    }

    function renderAssembleWorkspace() {
        renderAssembleSummary();
        renderAssembleMediaList();
        renderAssembleTimeline();
        renderAssembleMonitor();
        renderAssembleInspector();
    }

    function selectAssembleClip(id) {
        if (!selectedAssembleClips.some((item) => item.id === id)) {
            return;
        }
        stopAssembleSequencePlayback();
        activeAssembleClipId = id;
        assembleSequenceTime = getAssembleSegmentForClip(id)?.start || 0;
        assemblePreviewMode = "clip";
        renderAssembleWorkspace();
    }

    function updateActiveAssembleClip(mutator) {
        const active = getActiveAssembleClip();
        if (!active) {
            return;
        }
        stopAssembleSequencePlayback();
        mutator(active);
        normalizeAssembleClip(active);
        const segment = getAssembleSegmentForClip(active.id);
        assembleSequenceTime = clampNumber(
            segment ? Math.max(segment.start, Math.min(segment.end, assembleSequenceTime)) : assembleSequenceTime,
            0,
            getAssembleSequenceDuration() || 0,
            0,
        );
        invalidateAssembleExport();
        assemblePreviewMode = "clip";
        renderAssembleWorkspace();
    }

    function moveAssembleClip(index, offset) {
        const targetIndex = index + offset;
        if (targetIndex < 0 || targetIndex >= selectedAssembleClips.length) {
            return;
        }
        stopAssembleSequencePlayback();
        const [item] = selectedAssembleClips.splice(index, 1);
        selectedAssembleClips.splice(targetIndex, 0, item);
        assembleSequenceTime = getAssembleSegmentForClip(activeAssembleClipId)?.start || 0;
        invalidateAssembleExport();
        renderAssembleWorkspace();
    }

    function removeAssembleClip(index) {
        if (index < 0 || index >= selectedAssembleClips.length) {
            return;
        }
        stopAssembleSequencePlayback();
        const [removed] = selectedAssembleClips.splice(index, 1);
        revokeAssembleClipPreview(removed);
        if (activeAssembleClipId === removed.id) {
            activeAssembleClipId = selectedAssembleClips[Math.max(0, index - 1)]?.id || selectedAssembleClips[0]?.id || null;
        }
        assembleSequenceTime = getAssembleSegmentForClip(activeAssembleClipId)?.start || 0;
        invalidateAssembleExport();
        renderAssembleWorkspace();
    }

    function clearAssembleClips() {
        stopAssembleSequencePlayback();
        while (selectedAssembleClips.length) {
            revokeAssembleClipPreview(selectedAssembleClips.pop());
        }
        activeAssembleClipId = null;
        assembleSequenceTime = 0;
        assemblePreviewMode = "clip";
        invalidateAssembleExport();
        if (assembleInput) {
            assembleInput.value = "";
        }
        renderAssembleWorkspace();
    }

    async function addAssembleFiles(fileList) {
        const candidates = Array.from(fileList || []);
        const validFiles = [];

        candidates.forEach((file) => {
            const lowerName = (file.name || "").toLowerCase();
            const ext = lowerName.includes(".") ? lowerName.slice(lowerName.lastIndexOf(".")) : "";
            if (!VIDEO_ALLOWED_EXTS.has(ext)) {
                return;
            }
            if (selectedAssembleClips.some((entry) => fileIdentity(entry.file) === fileIdentity(file))) {
                return;
            }
            validFiles.push(file);
        });

        if (!validFiles.length) {
            setAssembleStatus("Only mp4, mov, and webm clips are supported.");
            if (assembleInput) {
                assembleInput.value = "";
            }
            return;
        }

        const entries = await Promise.all(validFiles.map(async (file) => {
            const previewUrl = URL.createObjectURL(file);
            const meta = await loadVideoMetadata(previewUrl);
            const duration = meta.duration > 0 ? meta.duration : 5;
            const item = {
                id: createSourceId(),
                file,
                previewUrl,
                duration,
                width: meta.width,
                height: meta.height,
                trimStart: 0,
                trimEnd: Math.min(duration, 5),
                speed: 1,
                reverse: false,
                flipHorizontal: false,
            };
            normalizeAssembleClip(item);
            return item;
        }));

        selectedAssembleClips.push(...entries);
        activeAssembleClipId = entries[entries.length - 1]?.id || activeAssembleClipId;
        assembleSequenceTime = getAssembleSegmentForClip(activeAssembleClipId)?.start || 0;
        assemblePreviewMode = "clip";
        invalidateAssembleExport();
        renderAssembleWorkspace();
        setAssembleStatus(`${entries.length} clip(s) added to the timeline.`);
    }

    function validateAssembleRequest() {
        if (!selectedAssembleClips.length) {
            setAssembleStatus("Upload at least one clip to assemble.");
            return false;
        }
        return true;
    }

    async function materializeAssembleUrls() {
        const urls = [];
        for (let index = 0; index < selectedAssembleClips.length; index += 1) {
            urls.push(await uploadOutputFile(
                selectedAssembleClips[index].file,
                "/api/outputs/upload-video",
                setAssembleStatus,
                index + 1,
                selectedAssembleClips.length,
                "clip",
            ));
        }
        return urls;
    }

    function renderAssembleResult(videoUrl) {
        if (!videoUrl) {
            return;
        }

        assembleFinalVideoUrl = videoUrl;
        assemblePreviewMode = "final";
        assembleSequenceTime = 0;
        setMonitorVideoSource(videoUrl);
        renderAssembleWorkspace();
        triggerAssembleDownload(videoUrl);
        setAssembleStatus("Final video exported.");
    }

    async function handleGenerateAssembleVideo() {
        if (!validateAssembleRequest()) {
            return;
        }
        if (assembleFinalVideoUrl) {
            assemblePreviewMode = "final";
            renderAssembleWorkspace();
            triggerAssembleDownload(assembleFinalVideoUrl);
            setAssembleStatus("Final video downloaded.");
            return;
        }

        assembleGenerateBtn.disabled = true;
        assembleLoading?.classList.remove("hidden");
        invalidateAssembleExport();
        renderAssembleWorkspace();

        try {
            const urls = await materializeAssembleUrls();
            const payload = {
                clips: urls.map((videoUrl, index) => ({
                    video_url: videoUrl,
                    speed: selectedAssembleClips[index].speed,
                    trim_start: selectedAssembleClips[index].trimStart,
                    trim_end: selectedAssembleClips[index].trimEnd,
                    reverse: selectedAssembleClips[index].reverse,
                    flip_horizontal: selectedAssembleClips[index].flipHorizontal,
                })),
                include_intro_outro: false,
                aspect_ratio: assembleAspectRatio,
                aspect_mode: assembleAspectMode,
            };

            setAssembleStatus("Starting assembly...");

            const response = await fetch("/video-mvp/compile", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            if (!response.ok) {
                const text = await response.text();
                throw new Error(text || `Compile failed (${response.status})`);
            }

            const { job_id: jobId } = await response.json();
            if (!jobId) {
                throw new Error("Compile job did not return a job id.");
            }

            const finalState = await pollVideoJob(jobId, setAssembleStatus);
            if (!finalState?.result_url) {
                throw new Error("Assemble completed without a final video URL.");
            }

            renderAssembleResult(finalState.result_url);
        } catch (error) {
            console.error(error);
            setAssembleStatus(`Failed: ${error.message}`);
            renderAssembleWorkspace();
        } finally {
            assembleLoading?.classList.add("hidden");
            assembleGenerateBtn.disabled = selectedAssembleClips.length === 0;
        }
    }

    function bindAssembleWorkspace() {
        if (assembleDropZone && assembleInput) {
            const triggerAssemblePicker = () => {
                try {
                    if (typeof assembleInput.showPicker === "function") {
                        assembleInput.showPicker();
                    } else {
                        assembleInput.click();
                    }
                } catch (_error) {
                    assembleInput.click();
                }
            };
            const openAssemblePicker = (event) => {
                if (event?.target?.closest?.("button")) {
                    return;
                }
                if (event?.type === "keydown") {
                    event.preventDefault();
                }
                triggerAssemblePicker();
            };
            const onDragOver = (event) => {
                event.preventDefault();
                assembleDropZone.classList.add("dragover");
                assembleTimelineViewport?.classList.add("is-drop-target");
            };
            const onDragLeave = () => {
                assembleDropZone.classList.remove("dragover");
                assembleTimelineViewport?.classList.remove("is-drop-target");
            };
            const onDrop = (event) => {
                event.preventDefault();
                onDragLeave();
                addAssembleFiles(event.dataTransfer?.files || []);
            };

            assembleDropZone.addEventListener("click", openAssemblePicker);
            assembleDropZone.addEventListener("keydown", (event) => {
                if (event.key === "Enter" || event.key === " ") {
                    openAssemblePicker(event);
                }
            });
            assembleDropZone.addEventListener("dragover", onDragOver);
            assembleDropZone.addEventListener("dragleave", onDragLeave);
            assembleDropZone.addEventListener("drop", onDrop);
            assembleTimelineViewport?.addEventListener("dragover", onDragOver);
            assembleTimelineViewport?.addEventListener("dragleave", onDragLeave);
            assembleTimelineViewport?.addEventListener("drop", onDrop);
            assembleInput.addEventListener("change", (event) => {
                addAssembleFiles(event.target.files || []);
                assembleInput.value = "";
            });
        }

        assembleRemoveAllBtn?.addEventListener("click", (event) => {
            event.preventDefault();
            event.stopPropagation();
            clearAssembleClips();
        });
        assembleGenerateBtn?.addEventListener("click", handleGenerateAssembleVideo);
        assembleRatioButtons.forEach((button) => {
            button.addEventListener("click", () => {
                const nextRatio = button.dataset.ratio || "9:16";
                if (nextRatio === assembleAspectRatio) {
                    return;
                }
                assembleAspectRatio = nextRatio;
                invalidateAssembleExport();
                renderAssembleWorkspace();
            });
        });
        assembleFitModeButtons.forEach((button) => {
            button.addEventListener("click", () => {
                const nextMode = button.dataset.fitMode || "crop";
                if (nextMode === assembleAspectMode) {
                    return;
                }
                assembleAspectMode = nextMode;
                invalidateAssembleExport();
                renderAssembleWorkspace();
            });
        });
        assembleTrimStartRange?.addEventListener("input", (event) => {
            updateActiveAssembleClip((item) => {
                item.trimStart = Number(event.target.value);
            });
        });
        assembleTrimStartInput?.addEventListener("change", (event) => {
            updateActiveAssembleClip((item) => {
                item.trimStart = Number(event.target.value);
            });
        });
        assembleTrimEndRange?.addEventListener("input", (event) => {
            updateActiveAssembleClip((item) => {
                item.trimEnd = Number(event.target.value);
            });
        });
        assembleTrimEndInput?.addEventListener("change", (event) => {
            updateActiveAssembleClip((item) => {
                item.trimEnd = Number(event.target.value);
            });
        });
        assembleSpeedRange?.addEventListener("input", (event) => {
            updateActiveAssembleClip((item) => {
                item.speed = Number(event.target.value);
            });
        });
        assembleSpeedInput?.addEventListener("change", (event) => {
            updateActiveAssembleClip((item) => {
                item.speed = Number(event.target.value);
            });
        });
        assembleTimelineScrubber?.addEventListener("input", (event) => {
            stopAssembleSequencePlayback();
            const { totalDuration, visualDuration } = getAssembleTimelineMetrics();
            const ratio = Number(event.target.value) / Number(event.target.max || 1000);
            const visualTime = visualDuration * ratio;
            applyAssembleSequenceTime(Math.min(totalDuration, visualTime), { forceSeek: true });
        });
        assemblePlayToggle?.addEventListener("click", () => {
            if (assemblePreviewMode === "final" && assembleFinalVideoUrl) {
                if (!assembleMonitorVideo?.dataset.source) {
                    return;
                }
                if (assembleSequencePlaying || !assembleMonitorVideo.paused) {
                    stopAssembleSequencePlayback();
                } else {
                    startAssembleSequencePlayback();
                }
                updateAssemblePlayButton();
                return;
            }
            if (!selectedAssembleClips.length) {
                return;
            }
            if (assembleSequencePlaying) {
                stopAssembleSequencePlayback();
                return;
            }
            startAssembleSequencePlayback();
        });
        assembleZoomOutBtn?.addEventListener("click", () => {
            assembleMonitorZoom = clampAssembleZoom(assembleMonitorZoom - 0.1);
            syncAssembleMonitorPresentation();
        });
        assembleZoomInBtn?.addEventListener("click", () => {
            assembleMonitorZoom = clampAssembleZoom(assembleMonitorZoom + 0.1);
            syncAssembleMonitorPresentation();
        });
        assembleDeleteBtn?.addEventListener("click", () => {
            const index = getAssembleClipIndex(activeAssembleClipId);
            if (index >= 0) {
                removeAssembleClip(index);
            }
        });
        assembleTrimBtn?.addEventListener("click", focusAssembleTrimControls);
        assembleReverseBtn?.addEventListener("click", () => toggleActiveAssembleFlag("reverse"));
        assembleFlipBtn?.addEventListener("click", () => toggleActiveAssembleFlag("flipHorizontal"));
        assembleMonitorVideo?.addEventListener("loadedmetadata", () => {
            if (assemblePendingClipSeek?.clipId === activeAssembleClipId) {
                const shouldAutoplay = Boolean(assemblePendingClipSeek?.autoplay);
                try {
                    assembleMonitorVideo.currentTime = assemblePendingClipSeek.sourceTime;
                    syncAssembleBackdropTime(assemblePendingClipSeek.sourceTime);
                } catch (_error) {
                    // Ignore until currentTime is assignable.
                }
                assemblePendingClipSeek = null;
                if (shouldAutoplay) {
                    playAssembleMonitorVideoForward();
                }
            } else {
                syncAssembleMonitorTimeWindow(true);
            }
            syncAssembleMonitorPresentation();
        });
        assembleMonitorVideo?.addEventListener("play", () => {
            if (shouldUseAssembleBackdrop()) {
                assembleMonitorBackdrop?.play().catch(() => {});
            }
            updateAssemblePlayButton();
        });
        assembleMonitorVideo?.addEventListener("pause", () => {
            assembleMonitorBackdrop?.pause();
            updateAssemblePlayButton();
        });
        assembleMonitorVideo?.addEventListener("ended", () => {
            assembleMonitorBackdrop?.pause();
            if (assemblePreviewMode === "final") {
                stopAssembleSequencePlayback({ pauseVideo: false });
                updateAssemblePlaybackReadout();
                return;
            }
            if (assemblePreviewMode === "clip") {
                const totalDuration = getAssembleSequenceDuration();
                const active = getActiveAssembleClip();
                const segment = active ? getAssembleSegmentForClip(active.id) : null;
                if (segment && segment.index < selectedAssembleClips.length - 1) {
                    assembleSequenceTime = segment.end;
                    beginAssembleSequenceSegmentPlayback({ forceSeek: true });
                    return;
                }
                assembleSequenceTime = totalDuration;
                stopAssembleSequencePlayback({ pauseVideo: false });
                updateAssemblePlaybackReadout();
            }
        });
        assembleMonitorVideo?.addEventListener("timeupdate", () => {
            if (shouldUseAssembleBackdrop()) {
                syncAssembleBackdropTime(assembleMonitorVideo.currentTime || 0);
            }
            if (assemblePreviewMode === "final") {
                assembleSequenceTime = Number.isFinite(assembleMonitorVideo.currentTime) ? assembleMonitorVideo.currentTime : assembleSequenceTime;
                renderAssembleTimelineTransport();
                updateAssemblePlaybackReadout();
                return;
            }
            const active = getActiveAssembleClip();
            if (assemblePreviewMode === "clip" && active && !active.reverse) {
                const currentSourceTime = Number.isFinite(assembleMonitorVideo.currentTime)
                    ? assembleMonitorVideo.currentTime
                    : active.trimStart;
                if (currentSourceTime >= active.trimEnd - 0.03) {
                    const totalDuration = getAssembleSequenceDuration();
                    const segment = getAssembleSegmentForClip(active.id);
                    if (segment && segment.index < selectedAssembleClips.length - 1) {
                        assembleSequenceTime = segment.end;
                        beginAssembleSequenceSegmentPlayback({ forceSeek: true });
                        return;
                    }
                    assembleSequenceTime = totalDuration;
                    try {
                        assembleMonitorVideo.pause();
                    } catch (_error) {
                        // Ignore pause failures.
                    }
                    renderAssembleTimelineTransport();
                    updateAssemblePlaybackReadout();
                    stopAssembleSequencePlayback({ pauseVideo: false });
                    return;
                }
                assembleSequenceTime = getAssembleSequenceTimeFromMonitor();
                renderAssembleTimelineTransport();
                updateAssemblePlaybackReadout();
            }
        });
        renderAssembleWorkspace();
    }

    $("btn-feature-1")?.addEventListener("click", () => showWorkspace("create-clips"));
    $("btn-feature-2")?.addEventListener("click", () => showWorkspace("assemble-video"));
    $("btn-feature-3")?.addEventListener("click", () => showWorkspace("post-production"));

    $("back-to-menu-1")?.addEventListener("click", showMenu);
    $("back-to-menu-2")?.addEventListener("click", showMenu);
    $("back-to-menu-3")?.addEventListener("click", showMenu);

    const globalModal = $("global-modal");
    globalModal?.addEventListener("click", (event) => {
        if (event.target === globalModal) {
            setModalVisibility(false);
        }
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            setModalVisibility(false);
        }
    });

    bindCreateClipWorkspace();
    bindAssembleWorkspace();

    const requestedWorkspace = new URL(window.location.href).searchParams.get("workspace");
    if (requestedWorkspace && workspaces[requestedWorkspace]) {
        showWorkspace(requestedWorkspace);
    }
});
