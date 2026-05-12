(function () {
    const typeCards = Array.from(document.querySelectorAll("[data-reel-type]"));
    const contentType = document.getElementById("marketing-content-type");
    const imageInput = document.getElementById("marketing-image-input");
    const uploadCount = document.getElementById("marketing-upload-count");
    const imageOrderList = document.getElementById("marketing-image-order-list");
    const cutList = document.getElementById("marketing-cut-prompts");
    const addCutButton = document.getElementById("marketing-add-cut");
    const generateButton = document.getElementById("marketing-generate-brief");
    const runReelButton = document.getElementById("marketing-run-reel");
    const storyboardCutList = document.getElementById("storyboard-cut-list");
    const hookText = document.getElementById("storyboard-hook");
    const captionText = document.getElementById("storyboard-caption");
    const ctaText = document.getElementById("storyboard-cta");
    const statusText = document.getElementById("marketing-status");
    const progressBar = document.getElementById("marketing-progress");
    const resultClips = document.getElementById("marketing-result-clips");
    const finalReel = document.getElementById("marketing-final-reel");
    const finalVideo = document.getElementById("marketing-final-video");
    const downloadLink = document.getElementById("marketing-download-link");
    let selectedMarketingFiles = [];

    const typeCopy = {
        popup: {
            hook: "신제품이 공간의 첫인상을 바꾸는 순간",
            caption: "팝업 쇼룸에서 만나는 새로운 컬렉션.",
            cta: "런칭 일정과 쇼룸 정보를 확인하세요.",
            direction: "furniture popup launch reel, energetic showroom reveal, premium product presence"
        },
        cinematic: {
            hook: "3초 안에 시선을 머물게 하는 디자인",
            caption: "빛이 머무는 자리에, 봄의 라운지.",
            cta: "지금 쇼룸에서 직접 만나보세요.",
            direction: "cinematic interior reel, warm editorial lighting, slow refined camera movement"
        },
        install: {
            hook: "빈 공간이 완성되는 가장 자연스러운 흐름",
            caption: "배치, 균형, 마감까지 한 장면으로 보여드립니다.",
            cta: "공간 솔루션 상담을 시작하세요.",
            direction: "furniture installation reel, before and after flow, practical spatial transformation"
        }
    };

    function updateUploadCount() {
        if (!uploadCount) return;
        const count = selectedMarketingFiles.length;
        uploadCount.textContent = `${count} / 10 selected`;
    }

    function renderImageOrderList() {
        if (!imageOrderList) return;
        if (!selectedMarketingFiles.length) {
            imageOrderList.innerHTML = '<div class="image-order-row"><span class="cut-index">0</span><span class="image-order-name">아직 선택된 사진이 없습니다.</span><span></span></div>';
            updateUploadCount();
            return;
        }
        imageOrderList.innerHTML = selectedMarketingFiles.map((file, index) => `
            <div class="image-order-row">
                <span class="cut-index">${index + 1}</span>
                <span class="image-order-name">${escapeHtml(file.name)}</span>
                <span class="image-order-actions">
                    <button class="small-icon-button" type="button" data-image-action="up" data-image-index="${index}" aria-label="Move image up" ${index === 0 ? "disabled" : ""}>
                        <span class="material-symbols-outlined">arrow_upward</span>
                    </button>
                    <button class="small-icon-button" type="button" data-image-action="down" data-image-index="${index}" aria-label="Move image down" ${index === selectedMarketingFiles.length - 1 ? "disabled" : ""}>
                        <span class="material-symbols-outlined">arrow_downward</span>
                    </button>
                    <button class="small-icon-button" type="button" data-image-action="remove" data-image-index="${index}" aria-label="Remove image">
                        <span class="material-symbols-outlined">close</span>
                    </button>
                </span>
            </div>
        `).join("");
        updateUploadCount();
    }

    function moveSelectedFile(index, direction) {
        const nextIndex = index + direction;
        if (nextIndex < 0 || nextIndex >= selectedMarketingFiles.length) return;
        const nextFiles = selectedMarketingFiles.slice();
        const [file] = nextFiles.splice(index, 1);
        nextFiles.splice(nextIndex, 0, file);
        selectedMarketingFiles = nextFiles;
        renderImageOrderList();
    }

    function removeSelectedFile(index) {
        selectedMarketingFiles = selectedMarketingFiles.filter((_, fileIndex) => fileIndex !== index);
        renderImageOrderList();
    }

    function setStatus(message, progress) {
        if (statusText) statusText.textContent = message;
        if (progressBar && typeof progress === "number") {
            progressBar.style.width = `${Math.max(0, Math.min(100, progress))}%`;
        }
    }

    function setBusy(isBusy) {
        if (runReelButton) runReelButton.disabled = isBusy;
        if (generateButton) generateButton.disabled = isBusy;
    }

    function syncType(type) {
        typeCards.forEach((card) => {
            card.classList.toggle("is-active", card.dataset.reelType === type);
        });
        if (contentType) contentType.value = type;
        const copy = typeCopy[type] || typeCopy.popup;
        hookText.textContent = copy.hook;
        captionText.textContent = copy.caption;
        ctaText.textContent = copy.cta;
    }

    function renumberCuts() {
        const rows = Array.from(cutList.querySelectorAll(".cut-row"));
        rows.forEach((row, index) => {
            const nextIndex = index + 1;
            row.querySelector(".cut-index").textContent = String(nextIndex);
            const input = row.querySelector(".cut-prompt-input");
            input.name = `cut_prompt_${nextIndex}`;
            const removeButton = row.querySelector("button");
            removeButton.disabled = rows.length <= 3;
        });
    }

    function addCut() {
        const rows = Array.from(cutList.querySelectorAll(".cut-row"));
        const nextIndex = rows.length + 1;
        const row = document.createElement("div");
        row.className = "cut-row";
        row.innerHTML = `
            <span class="cut-index">${nextIndex}</span>
            <input class="brief-input cut-prompt-input" name="cut_prompt_${nextIndex}" value="추가 컷 - 제품과 공간 연결">
            <button class="icon-button" type="button" aria-label="Remove cut">
                <span class="material-symbols-outlined">remove</span>
            </button>
        `;
        row.querySelector("button").addEventListener("click", () => {
            row.remove();
            renumberCuts();
            updateStoryboard();
        });
        cutList.appendChild(row);
        renumberCuts();
        updateStoryboard();
    }

    function getCutPrompts() {
        return Array.from(cutList.querySelectorAll(".cut-prompt-input"))
            .map((input) => input.value.trim())
            .filter(Boolean);
    }

    function getTargetDurationSec() {
        const raw = document.getElementById("marketing-duration")?.value || "20초";
        const parsed = Number.parseInt(raw.replace(/[^0-9]/g, ""), 10);
        return Number.isFinite(parsed) ? parsed : 20;
    }

    function getBriefValue(id) {
        return (document.getElementById(id)?.value || "").trim();
    }

    function updateStoryboard() {
        const prompts = getCutPrompts();
        const safePrompts = prompts.length ? prompts : ["오프닝 - 정면 클로즈업", "와이드 컷 - 공간감 강조", "디테일 텍스처 컷"];
        const targetDuration = getTargetDurationSec();
        const secondsPerCut = Math.max(3, Math.round(targetDuration / Math.max(1, safePrompts.length)));

        storyboardCutList.innerHTML = safePrompts.map((prompt, index) => {
            const seconds = String(secondsPerCut).padStart(2, "0");
            return `
                <article class="storyboard-cut">
                    <header><span>Cut ${index + 1}</span><span>${seconds}s</span></header>
                    <p>${escapeHtml(prompt)}</p>
                </article>
            `;
        }).join("");

        const type = contentType ? contentType.value : "popup";
        const copy = typeCopy[type] || typeCopy.popup;
        hookText.textContent = copy.hook;
        captionText.textContent = copy.caption;
        ctaText.textContent = copy.cta;
    }

    function buildKlingPrompt(cutPrompt, index) {
        const type = contentType ? contentType.value : "popup";
        const copy = typeCopy[type] || typeCopy.popup;
        const parts = [
            copy.direction,
            `cut ${index + 1}: ${cutPrompt}`,
            `global direction: ${getBriefValue("marketing-global-prompt")}`,
            `tone: ${getBriefValue("marketing-tone")}`,
            `platform: ${getBriefValue("marketing-platform")}`,
            `audience: ${getBriefValue("marketing-audience")}`,
            `goal: ${getBriefValue("marketing-goal")}`,
            `language: ${getBriefValue("marketing-language")}`,
            "Keep the furniture, room layout, product shape, material, color, and perspective faithful to the source photo. Smooth professional marketing reel motion. No text overlays."
        ];
        return parts.filter(Boolean).join(". ").slice(0, 2400);
    }

    function validateReadyToGenerate() {
        const files = selectedMarketingFiles.slice();
        if (files.length < 3 || files.length > 10) {
            throw new Error("이미지는 3~10장을 선택해야 합니다.");
        }
        const invalid = files.find((file) => !file.type.startsWith("image/"));
        if (invalid) {
            throw new Error("이미지 파일만 업로드할 수 있습니다.");
        }
        return files;
    }

    async function uploadImage(file, index, total) {
        const formData = new FormData();
        formData.append("file", file);
        setStatus(`Uploading image ${index + 1}/${total}...`, Math.round((index / total) * 18));
        const response = await fetch("/api/outputs/upload", { method: "POST", body: formData });
        if (!response.ok) {
            const text = await readErrorText(response);
            throw new Error(text || `이미지 업로드 실패 (${response.status})`);
        }
        const payload = await response.json();
        if (!payload?.url) throw new Error("이미지 업로드 결과 URL이 없습니다.");
        return payload.url;
    }

    async function uploadSelectedImages(files) {
        const urls = [];
        for (let index = 0; index < files.length; index += 1) {
            urls.push(await uploadImage(files[index], index, files.length));
        }
        return urls;
    }

    async function pollVideoJob(jobId, phaseLabel, baseProgress, progressSpan) {
        while (true) {
            const response = await fetch(`/video-mvp/status/${jobId}`, { cache: "no-store" });
            if (!response.ok) {
                throw new Error(`상태 확인 실패 (${response.status})`);
            }
            const state = await response.json();
            const upstreamProgress = typeof state.progress === "number" ? state.progress : 0;
            const progress = baseProgress + Math.round((upstreamProgress / 100) * progressSpan);
            setStatus(`${phaseLabel}: ${state.message || state.status || "Working"}`, progress);
            if (state.status === "COMPLETED") return state;
            if (state.status === "FAILED") throw new Error(state.error || `${phaseLabel} 실패`);
            await new Promise((resolve) => setTimeout(resolve, 1500));
        }
    }

    async function generateSourceClips(imageUrls) {
        const prompts = getCutPrompts();
        const payload = {
            items: imageUrls.map((url, index) => ({
                url,
                motion: "custom",
                effect: "none",
                custom_motion_prompt: buildKlingPrompt(prompts[index] || prompts[prompts.length - 1] || "premium furniture reel", index),
                custom_effect_prompt: null
            })),
            cfg_scale: 0.5
        };
        setStatus("Starting Kling clip generation...", 20);
        const response = await fetch("/video-mvp/generate-sources", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        if (!response.ok) {
            const text = await readErrorText(response);
            throw new Error(text || `Kling 생성 요청 실패 (${response.status})`);
        }
        const payloadResponse = await response.json();
        if (!payloadResponse?.job_id) throw new Error("Kling 생성 job id가 없습니다.");
        return pollVideoJob(payloadResponse.job_id, "Generating clips", 20, 50);
    }

    function buildCompileClips(sourceUrls) {
        const targetDuration = getTargetDurationSec();
        const clips = [];
        let remaining = targetDuration;
        let index = 0;
        while (remaining > 0 && sourceUrls.length) {
            const videoUrl = sourceUrls[index % sourceUrls.length];
            const trimEnd = Math.min(5, remaining);
            clips.push({
                video_url: videoUrl,
                speed: 1,
                trim_start: 0,
                trim_end: trimEnd,
                reverse: false,
                flip_horizontal: false
            });
            remaining -= trimEnd;
            index += 1;
        }
        return clips;
    }

    async function compileFinalReel(sourceUrls) {
        const clips = buildCompileClips(sourceUrls);
        if (!clips.length) throw new Error("컴파일할 성공 clip이 없습니다.");
        setStatus("Starting final reel assembly...", 72);
        const response = await fetch("/video-mvp/compile", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                clips,
                include_intro_outro: false,
                aspect_ratio: "9:16",
                aspect_mode: "crop"
            })
        });
        if (!response.ok) {
            const text = await readErrorText(response);
            throw new Error(text || `최종 릴스 컴파일 실패 (${response.status})`);
        }
        const payload = await response.json();
        if (!payload?.job_id) throw new Error("컴파일 job id가 없습니다.");
        return pollVideoJob(payload.job_id, "Compiling final reel", 72, 28);
    }

    function renderClipResults(results, errors) {
        if (!resultClips) return;
        resultClips.innerHTML = "";
        (results || []).forEach((url, index) => {
            const row = document.createElement("div");
            row.className = "clip-row";
            if (url) {
                row.innerHTML = `<span>Clip ${index + 1}</span><a href="${escapeAttribute(url)}" target="_blank" rel="noreferrer">Preview</a>`;
            } else {
                row.innerHTML = `<span>Clip ${index + 1}</span><span>Failed</span>`;
            }
            resultClips.appendChild(row);
        });
        (errors || []).forEach((error) => {
            const row = document.createElement("div");
            row.className = "clip-row";
            row.innerHTML = `<span>Cut ${Number(error.index) + 1}</span><span>${escapeHtml(error.error || "Failed")}</span>`;
            resultClips.appendChild(row);
        });
    }

    function renderFinalReel(resultUrl) {
        if (!resultUrl) return;
        finalReel?.classList.remove("is-hidden");
        if (finalVideo) finalVideo.src = resultUrl;
        if (downloadLink) downloadLink.href = `/download?url=${encodeURIComponent(resultUrl)}`;
    }

    async function runMarketingReel() {
        try {
            setBusy(true);
            if (finalVideo) finalVideo.removeAttribute("src");
            finalReel?.classList.add("is-hidden");
            if (resultClips) resultClips.innerHTML = "";
            setStatus("Validating brief...", 0);
            updateStoryboard();
            const files = validateReadyToGenerate();
            const imageUrls = await uploadSelectedImages(files);
            const sourceState = await generateSourceClips(imageUrls);
            const successfulClips = (sourceState.results || []).filter(Boolean);
            renderClipResults(sourceState.results || [], sourceState.errors || []);
            if (!successfulClips.length) throw new Error("성공한 Kling clip이 없습니다.");
            const finalState = await compileFinalReel(successfulClips);
            if (!finalState?.result_url) throw new Error("최종 릴스 URL이 없습니다.");
            renderFinalReel(finalState.result_url);
            setStatus("Final reel is ready.", 100);
        } catch (error) {
            console.error(error);
            setStatus(`Failed: ${error.message}`, 0);
        } finally {
            setBusy(false);
        }
    }

    async function readErrorText(response) {
        try {
            const payload = await response.json();
            return payload?.error || payload?.detail || JSON.stringify(payload);
        } catch {
            return response.text();
        }
    }

    function escapeHtml(value) {
        return String(value).replace(/[&<>"']/g, (char) => ({
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            "\"": "&quot;",
            "'": "&#39;"
        }[char]));
    }

    function escapeAttribute(value) {
        return escapeHtml(value).replace(/`/g, "&#96;");
    }

    typeCards.forEach((card) => {
        card.addEventListener("click", () => syncType(card.dataset.reelType));
    });

    if (contentType) {
        contentType.addEventListener("change", () => syncType(contentType.value));
    }

    if (imageInput) {
        imageInput.addEventListener("change", () => {
            selectedMarketingFiles = Array.from(imageInput.files || []).slice(0, 10);
            imageInput.value = "";
            renderImageOrderList();
        });
    }

    if (imageOrderList) {
        imageOrderList.addEventListener("click", (event) => {
            const button = event.target.closest("[data-image-action]");
            if (!button) return;
            const index = Number.parseInt(button.dataset.imageIndex || "-1", 10);
            if (!Number.isFinite(index) || index < 0) return;
            if (button.dataset.imageAction === "up") {
                moveSelectedFile(index, -1);
            } else if (button.dataset.imageAction === "down") {
                moveSelectedFile(index, 1);
            } else if (button.dataset.imageAction === "remove") {
                removeSelectedFile(index);
            }
        });
    }

    if (addCutButton) {
        addCutButton.addEventListener("click", addCut);
    }

    if (cutList) {
        cutList.addEventListener("input", (event) => {
            if (event.target.classList.contains("cut-prompt-input")) {
                updateStoryboard();
            }
        });
    }

    if (generateButton) {
        generateButton.addEventListener("click", updateStoryboard);
    }

    if (runReelButton) {
        runReelButton.addEventListener("click", runMarketingReel);
    }

    renumberCuts();
    renderImageOrderList();
    updateStoryboard();
}());
