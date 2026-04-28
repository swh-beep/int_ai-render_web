document.addEventListener("DOMContentLoaded", () => {
    const $ = (id) => document.getElementById(id);
    const CLIP_ALLOWED_EXTS = new Set([".png", ".jpg", ".jpeg", ".webp"]);

    const menuScreen = $("menu-screen");
    const workspaces = {
        "create-clips": $("workspace-feature-1"),
        "assemble-video": $("workspace-feature-2"),
        "post-production": $("workspace-feature-3"),
    };

    const selectedSources = [];

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

    function setClipStatus(message) {
        if (clipStatus) {
            clipStatus.textContent = message || "";
        }
    }

    function createSourceId() {
        if (window.crypto?.randomUUID) {
            return window.crypto.randomUUID();
        }
        return `source-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    }

    function showModal(title, bodyHtml) {
        const modal = $("global-modal");
        const modalTitle = $("modal-title");
        const modalMsg = $("modal-msg");
        const modalOkBtn = $("modal-ok-btn");
        if (!modal || !modalTitle || !modalMsg || !modalOkBtn) {
            return;
        }
        modalTitle.textContent = title;
        modalMsg.innerHTML = bodyHtml;
        modalOkBtn.onclick = () => modal.classList.add("hidden");
        modal.classList.remove("hidden");
    }

    function sourceIdentity(source) {
        const file = source.file;
        return [file.name, file.size, file.lastModified].join(":");
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

    function syncClipDropZoneSize(source) {
        if (!clipDropZone) {
            return;
        }

        if (!source?.width || !source?.height) {
            clipDropZone.style.removeProperty("width");
            clipDropZone.style.removeProperty("height");
            return;
        }

        const stage = clipDropZone.parentElement;
        if (!stage) {
            return;
        }

        const maxWidth = stage.clientWidth;
        const maxHeight = stage.clientHeight;
        if (!maxWidth || !maxHeight) {
            return;
        }

        const ratio = source.width / source.height;
        let nextWidth = maxWidth;
        let nextHeight = nextWidth / ratio;

        if (nextHeight > maxHeight) {
            nextHeight = maxHeight;
            nextWidth = nextHeight * ratio;
        }

        clipDropZone.style.width = `${nextWidth}px`;
        clipDropZone.style.height = `${nextHeight}px`;
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

        if (!hasSources) {
            syncClipDropZoneSize(null);
            return;
        }

        const source = selectedSources[0];
        const img = document.createElement("img");
        img.src = source.previewUrl;
        img.alt = source.file.name;
        clipUploadPreview.appendChild(img);
        syncClipDropZoneSize(source);
    }

    async function addFileSources(fileList) {
        const files = Array.from(fileList || []).filter((file) => file.type.startsWith("image/"));
        const nextFile = files[0];
        if (!nextFile) {
            return;
        }

        const lowerName = (nextFile.name || "").toLowerCase();
        const ext = lowerName.includes(".") ? lowerName.slice(lowerName.lastIndexOf(".")) : "";
        if (!CLIP_ALLOWED_EXTS.has(ext)) {
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

    async function uploadSourceFile(file, index, total) {
        const formData = new FormData();
        formData.append("file", file);
        setClipStatus(`Uploading ${index}/${total}...`);
        const response = await fetch("/api/outputs/upload", { method: "POST", body: formData });
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
            urls.push(await uploadSourceFile(selectedSources[index].file, index + 1, selectedSources.length));
        }
        return urls;
    }

    async function pollVideoJob(jobId) {
        while (true) {
            const response = await fetch(`/video-mvp/status/${jobId}`, { cache: "no-store" });
            if (!response.ok) {
                throw new Error(`Status check failed (${response.status})`);
            }
            const state = await response.json();
            const progress = typeof state.progress === "number" ? ` (${state.progress}%)` : "";
            setClipStatus(`${state.message || state.status || "Working"}${progress}`);

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
                showModal("Clip Preview", `<video src="${videoUrl}" controls style="width:100%;max-height:80vh;border-radius:12px;"></video>`);
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
        clipResultGrid.innerHTML = "";

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

            const finalState = await pollVideoJob(jobId);
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
            clipDropZone.addEventListener("click", () => clipInput.click());
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
        window.addEventListener("resize", () => {
            if (selectedSources[0]) {
                syncClipDropZoneSize(selectedSources[0]);
            }
        });
        syncCustomPromptFields();
        renderSelectedSources();
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
            globalModal.classList.add("hidden");
        }
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            globalModal?.classList.add("hidden");
        }
    });

    bindCreateClipWorkspace();

    const requestedWorkspace = new URL(window.location.href).searchParams.get("workspace");
    if (requestedWorkspace && workspaces[requestedWorkspace]) {
        showWorkspace(requestedWorkspace);
    }
});
