document.addEventListener('DOMContentLoaded', () => {
    console.log("✅ script.js 로드됨 (Global Lock Removed - Parallel Execution Allowed)");

    // --- [1] 통합 모달 시스템 설정 ---
    const globalModal = document.getElementById('global-modal');
    const modalTitle = document.getElementById('modal-title');
    const modalMsg = document.getElementById('modal-msg');
    const modalOkBtn = document.getElementById('modal-ok-btn');
    const modalCancelBtn = document.getElementById('modal-cancel-btn');

    // 1. 단순 알림창
    function showCustomAlert(title, message) {
        modalTitle.textContent = title;
        modalMsg.innerHTML = message.replace(/\n/g, '<br>');
        modalCancelBtn.classList.add('hidden');
        modalOkBtn.textContent = "OK";
        modalOkBtn.onclick = () => globalModal.classList.add('hidden');
        globalModal.classList.remove('hidden');
    }

    // 2. 확인/취소창
    function showCustomConfirm(title, message, onConfirm) {
        modalTitle.textContent = title;
        modalMsg.innerHTML = message.replace(/\n/g, '<br>');
        modalCancelBtn.classList.remove('hidden');
        modalOkBtn.textContent = "Confirm";
        modalOkBtn.onclick = () => {
            globalModal.classList.add('hidden');
            if (onConfirm) onConfirm();
        };
        modalCancelBtn.onclick = () => globalModal.classList.add('hidden');
        globalModal.classList.remove('hidden');
    }

    globalModal.onclick = (e) => {
        if (e.target === globalModal && modalCancelBtn.classList.contains('hidden')) {
            globalModal.classList.add('hidden');
        }
    };

    // --- [2] 요소 선택 및 초기화 ---
    const dropZone = document.querySelector('.drop-zone');
    const fileInput = document.getElementById('file-input');
    const previewContainer = document.getElementById('preview-container');
    const imagePreview = document.getElementById('image-preview');
    const removeBtn = document.getElementById('remove-image');

    const roomGrid = document.getElementById('room-grid');
    const styleGrid = document.getElementById('style-grid');
    const variantGrid = document.getElementById('variant-grid');

    // Moodboard Elements
    const moodboardUploadContainer = document.getElementById('moodboard-upload-container');
    const moodboardDropZone = document.getElementById('moodboard-drop-zone');
    const moodboardInput = document.getElementById('moodboard-input');
    const moodboardPreviewContainer = document.getElementById('moodboard-preview-container');
    const moodboardPreview = document.getElementById('moodboard-preview');
    const removeMoodboardBtn = document.getElementById('remove-moodboard');

    // [NEW] Moodboard Generator Elements
    const openMbGenBtn = document.getElementById('open-mb-gen-btn');
    const mbGenModal = document.getElementById('moodboard-generator-modal');
    const mbGenDropZone = document.getElementById('mb-gen-drop-zone');
    const mbGenInput = document.getElementById('mb-gen-input');
    const mbGenPreviewContainer = document.getElementById('mb-gen-preview-container');
    const mbGenPreview = document.getElementById('mb-gen-preview');
    const mbGenRemoveBtn = document.getElementById('mb-gen-remove');
    const mbGenActionBtn = document.getElementById('mb-gen-action-btn');

    // Step Elements
    const mbGenStep1 = document.getElementById('mb-gen-step1');
    const mbGenStep2 = document.getElementById('mb-gen-step2');
    const mbStep2RefImg = document.getElementById('mb-step2-ref-img'); // [NEW] Step2 Original Image
    const mbGenRetryBtn = document.getElementById('mb-gen-retry-btn'); // [NEW] Retry Button

    const mbGenGrid = document.getElementById('mb-gen-grid');
    const mbGenLoading = document.getElementById('mb-gen-loading');
    const mbGenCloseBtn = document.getElementById('mb-gen-close-btn');

    let mbGenSelectedFile = null;

    const roomSection = document.getElementById('room-section');
    const styleSection = document.getElementById('style-section');
    const variantSection = document.getElementById('variant-section');

    const renderBtn = document.getElementById('render-btn');
    const loadingOverlay = document.getElementById('loading-overlay');
    const timerElement = document.getElementById('timer');
    const loadingStatus = document.getElementById('loading-status');

    const resultSection = document.getElementById('result-section');
    const resultBefore = document.getElementById('result-before');
    const resultAfter = document.getElementById('result-after');
    const compareSlider = document.getElementById('compare-slider');
    const sliderHandle = document.querySelector('.slider-handle');
    const comparisonContainer = document.querySelector('.comparison-container');

    const thumbnailContainer = document.getElementById('thumbnailContainer');
    const upscaleBtn = document.getElementById('upscaleBtn');
    const upscaleStatus = document.getElementById('upscaleStatus');

    const detailBtn = document.getElementById('detailBtn');
    const detailStatus = document.getElementById('detailStatus');
    const detailSection = document.getElementById('detail-section');
    const detailGrid = document.getElementById('detail-grid');

    const lightbox = document.getElementById('lightbox');
    const lightboxImg = document.getElementById('lightbox-img');
    const closeLightbox = document.querySelector('.close-lightbox');

    // [NEW] Lightbox State for Keyboard Navigation
    let lightboxImages = [];
    let currentLightboxIndex = 0;

    const THEME_COLOR = "#ffffff";

    let selectedFile = null;
    let selectedRoom = null;
    let selectedStyle = null;
    let selectedVariant = null;
    let selectedMoodboardFile = null;
    let currentDetailSourceUrl = null;

    // --- 데이터 로드 ---
    fetch('/room-types')
        .then(res => res.json())
        .then(rooms => {
            roomGrid.innerHTML = '';
            rooms.forEach(room => {
                const btn = document.createElement('button');
                btn.className = 'style-btn';
                btn.textContent = room;
                btn.onclick = () => selectRoom(room, btn);
                roomGrid.appendChild(btn);
            });
        })
        .catch(err => console.error(err));

    function selectRoom(room, btn) {
        selectedRoom = room;
        selectedStyle = null;
        selectedVariant = null;
        selectedMoodboardFile = null;

        if (moodboardPreviewContainer) moodboardPreviewContainer.classList.add('hidden');
        if (moodboardUploadContainer) moodboardUploadContainer.classList.add('hidden');
        if (moodboardDropZone) moodboardDropZone.classList.remove('hidden');
        if (variantGrid) variantGrid.classList.remove('hidden');

        updateActiveButton(roomGrid, btn);

        fetch(`/styles/${room}`)
            .then(res => res.json())
            .then(styles => {
                styleGrid.innerHTML = '';
                styles.forEach(style => {
                    const styleBtn = document.createElement('button');
                    styleBtn.className = 'style-btn';
                    styleBtn.textContent = style;
                    styleBtn.onclick = () => selectStyle(style, styleBtn);
                    styleGrid.appendChild(styleBtn);
                });
                styleSection.classList.remove('hidden');
                variantSection.classList.add('hidden');
                checkReady();
            });
    }

    function selectStyle(style, btn) {
        selectedStyle = style;
        selectedVariant = null;
        updateActiveButton(styleGrid, btn);

        if (style === 'Customize') {
            variantGrid.classList.add('hidden');
            moodboardUploadContainer.classList.remove('hidden');
        } else {
            variantGrid.classList.remove('hidden');
            moodboardUploadContainer.classList.add('hidden');
            selectedMoodboardFile = null;
        }

        variantGrid.innerHTML = '';
        if (style !== 'Customize') {
            for (let i = 1; i <= 10; i++) {
                const variantBtn = document.createElement('div');
                variantBtn.className = 'variant-img-btn';

                const img = document.createElement('img');
                const safeRoom = selectedRoom.toLowerCase().replace(/ /g, '');
                const safeStyle = style.toLowerCase().replace(/ /g, '-').replace(/_/g, '-');
                const imgName = `${safeRoom}_${safeStyle}_${i}.png`;

                img.src = `/static/thumbnails/${imgName}`;
                img.alt = `Variant ${i}`;
                img.onerror = () => variantBtn.classList.add('no-image');

                const label = document.createElement('span');
                label.className = 'variant-label';
                label.textContent = i;

                variantBtn.appendChild(img);
                variantBtn.appendChild(label);

                variantBtn.onclick = () => {
                    selectedVariant = i.toString();
                    document.querySelectorAll('.variant-img-btn').forEach(b => {
                        b.classList.remove('active');
                        b.style.borderColor = 'transparent';
                    });
                    variantBtn.classList.add('active');
                    variantBtn.style.borderColor = THEME_COLOR;
                    checkReady();
                };
                variantGrid.appendChild(variantBtn);
            }
        }
        variantSection.classList.remove('hidden');
        checkReady();
    }

    function updateActiveButton(grid, activeBtn) {
        Array.from(grid.children).forEach(btn => {
            btn.classList.remove('active');
            btn.style.backgroundColor = '';
            btn.style.color = '';
        });
        activeBtn.classList.add('active');
    }

    // --- 파일 처리 ---
    if (dropZone) {
        dropZone.addEventListener('click', () => fileInput.click());
        dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.style.borderColor = THEME_COLOR; });
        dropZone.addEventListener('dragleave', () => dropZone.style.borderColor = '#ccc');
        dropZone.addEventListener('drop', (e) => {
            e.preventDefault(); dropZone.style.borderColor = '#ccc';
            if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
        });
        fileInput.addEventListener('change', (e) => { if (e.target.files.length) handleFile(e.target.files[0]); });
    }

    function handleFile(file) {
        if (!file.type.startsWith('image/')) { showCustomAlert("Error", "이미지 파일만 가능합니다."); return; }
        selectedFile = file;
        const reader = new FileReader();
        reader.onload = (e) => {
            imagePreview.src = e.target.result;
            previewContainer.classList.remove('hidden');
            dropZone.classList.add('hidden');
            checkReady();
        };
        reader.readAsDataURL(file);
    }

    if (removeBtn) {
        removeBtn.addEventListener('click', (e) => {
            e.stopPropagation(); selectedFile = null; fileInput.value = '';
            previewContainer.classList.add('hidden'); dropZone.classList.remove('hidden'); checkReady();
        });
    }

    // --- Moodboard 처리 ---
    if (moodboardDropZone) {
        moodboardDropZone.addEventListener('click', () => moodboardInput.click());
        moodboardDropZone.addEventListener('dragover', (e) => { e.preventDefault(); moodboardDropZone.style.borderColor = THEME_COLOR; });
        moodboardDropZone.addEventListener('dragleave', () => moodboardDropZone.style.borderColor = '#ccc');
        moodboardDropZone.addEventListener('drop', (e) => {
            e.preventDefault(); moodboardDropZone.style.borderColor = '#ccc';
            if (e.dataTransfer.files.length) handleMoodboardFile(e.dataTransfer.files[0]);
        });
        moodboardInput.addEventListener('change', (e) => { if (e.target.files.length) handleMoodboardFile(e.target.files[0]); });
    }

    function handleMoodboardFile(file) {
        if (!file.type.startsWith('image/')) { showCustomAlert("Error", "이미지 파일만 가능합니다."); return; }
        selectedMoodboardFile = file;
        const reader = new FileReader();
        reader.onload = (e) => {
            moodboardPreview.src = e.target.result;
            moodboardPreviewContainer.classList.remove('hidden');
            moodboardDropZone.classList.add('hidden');
            checkReady();
        };
        reader.readAsDataURL(file);
    }

    if (removeMoodboardBtn) {
        removeMoodboardBtn.addEventListener('click', (e) => {
            e.stopPropagation(); selectedMoodboardFile = null; moodboardInput.value = '';
            moodboardPreviewContainer.classList.add('hidden'); moodboardDropZone.classList.remove('hidden'); checkReady();
        });
    }

    // -----------------------------------------------------------
    // [NEW] Moodboard Generator Logic (UI Update & Keyboard Nav)
    // -----------------------------------------------------------

    // [수정] 레퍼런스 이미지 클릭 시 라이트박스 열기
    if (mbStep2RefImg) {
        mbStep2RefImg.onclick = () => {
            if (mbStep2RefImg.src) {
                openLightbox(mbStep2RefImg.src, [mbStep2RefImg.src], 0);
            }
        };
    }

    if (openMbGenBtn) {
        openMbGenBtn.onclick = () => {
            mbGenModal.classList.remove('hidden');

            // Reset States
            mbGenSelectedFile = null;
            mbGenInput.value = '';
            mbGenPreviewContainer.classList.add('hidden');
            mbGenDropZone.classList.remove('hidden');
            mbGenActionBtn.disabled = true;

            // Show Step 1, Hide Step 2
            mbGenStep1.classList.remove('hidden');
            mbGenStep2.classList.add('hidden');
            mbGenLoading.classList.add('hidden');
            mbGenGrid.innerHTML = '';
        };
    }

    if (mbGenCloseBtn) {
        mbGenCloseBtn.onclick = () => mbGenModal.classList.add('hidden');
    }

    // File Upload (Step 1)
    if (mbGenDropZone) {
        mbGenDropZone.addEventListener('click', () => mbGenInput.click());
        mbGenDropZone.addEventListener('dragover', (e) => { e.preventDefault(); mbGenDropZone.style.borderColor = THEME_COLOR; });
        mbGenDropZone.addEventListener('dragleave', () => mbGenDropZone.style.borderColor = '#ccc');
        mbGenDropZone.addEventListener('drop', (e) => {
            e.preventDefault(); mbGenDropZone.style.borderColor = '#ccc';
            if (e.dataTransfer.files.length) handleMbGenFile(e.dataTransfer.files[0]);
        });
        mbGenInput.addEventListener('change', (e) => { if (e.target.files.length) handleMbGenFile(e.target.files[0]); });
    }

    function handleMbGenFile(file) {
        if (!file.type.startsWith('image/')) { showCustomAlert("Error", "이미지 파일만 가능합니다."); return; }
        mbGenSelectedFile = file;
        const reader = new FileReader();
        reader.onload = (e) => {
            mbGenPreview.src = e.target.result;
            mbGenPreviewContainer.classList.remove('hidden');
            mbGenDropZone.classList.add('hidden');
            mbGenActionBtn.disabled = false;
        };
        reader.readAsDataURL(file);
    }

    if (mbGenRemoveBtn) {
        mbGenRemoveBtn.onclick = (e) => {
            e.stopPropagation();
            mbGenSelectedFile = null;
            mbGenInput.value = '';
            mbGenPreviewContainer.classList.add('hidden');
            mbGenDropZone.classList.remove('hidden');
            mbGenActionBtn.disabled = true;
        }
    }

    // Generate Action
    if (mbGenActionBtn) {
        mbGenActionBtn.onclick = async () => {
            await performMbGeneration();
        };
    }

    // [NEW] Retry Button Action
    if (mbGenRetryBtn) {
        mbGenRetryBtn.onclick = async () => {
            if (!mbGenSelectedFile) {
                showCustomAlert("Error", "No reference image found. Please try again.");
                return;
            }
            await performMbGeneration();
        };
    }

    async function performMbGeneration() {
        if (!mbGenSelectedFile) return;

        // [UI Update] Switch to Step 2 & Show Loading
        mbGenStep1.classList.add('hidden');
        mbGenStep2.classList.remove('hidden');
        mbGenLoading.classList.remove('hidden');
        mbGenGrid.innerHTML = '';

        // [Logic] Copy Preview Image to Step 2 Reference
        if (mbGenPreview.src) {
            mbStep2RefImg.src = mbGenPreview.src;
        }

        const formData = new FormData();
        formData.append('file', mbGenSelectedFile);

        try {
            const res = await fetch('/generate-moodboard-options', {
                method: 'POST',
                body: formData
            });
            const data = await res.json();

            mbGenLoading.classList.add('hidden');

            if (res.ok && data.moodboards && data.moodboards.length > 0) {
                const moodboardUrls = data.moodboards; // List for lightbox

                data.moodboards.forEach((url, idx) => {
                    const div = document.createElement('div');
                    div.className = 'detail-card';

                    // 1. 이미지: 클릭 시 확대 (Lightbox)
                    const img = document.createElement('img');
                    img.src = url;
                    // [FIX] 16:9 비율, contain, 중앙 정렬
                    img.style.aspectRatio = "16 / 9";
                    img.style.objectFit = "contain";
                    img.style.objectPosition = "center";
                    img.style.backgroundColor = "#000";
                    img.style.cursor = "zoom-in";
                    img.onclick = (e) => {
                        e.stopPropagation();
                        openLightbox(url, moodboardUrls, idx);
                    };

                    // 2. 선택 버튼: 클릭 시 적용 (Select)
                    const selectBtn = document.createElement('button');
                    selectBtn.className = 'detail-upscale-btn';
                    selectBtn.textContent = "SELECT THIS";
                    selectBtn.style.marginTop = "0";

                    selectBtn.onclick = async (e) => {
                        e.stopPropagation();

                        try {
                            selectBtn.textContent = "Loading...";
                            selectBtn.disabled = true;

                            const fileRes = await fetch(url);
                            const blob = await fileRes.blob();
                            const file = new File([blob], `generated_moodboard_${idx}.jpg`, { type: 'image/jpeg' });

                            handleMoodboardFile(file);
                            mbGenModal.classList.add('hidden');
                            showCustomAlert("Success", "Moodboard Applied!");
                        } catch (err) {
                            showCustomAlert("Error", "Failed to load selected moodboard.");
                            selectBtn.textContent = "SELECT THIS";
                            selectBtn.disabled = false;
                        }
                    };

                    div.appendChild(img);
                    div.appendChild(selectBtn);
                    mbGenGrid.appendChild(div);
                });
            } else {
                showCustomAlert("Error", "Failed to generate moodboards.");
                // Go back to step 1 on failure
                mbGenStep1.classList.remove('hidden');
                mbGenStep2.classList.add('hidden');
            }
        } catch (err) {
            mbGenLoading.classList.add('hidden');
            showCustomAlert("Error", "Server Error: " + err.message);
            mbGenStep1.classList.remove('hidden');
            mbGenStep2.classList.add('hidden');
        }
    }

    function checkReady() {
        if (!renderBtn) return;
        let ready = false;
        if (selectedFile && selectedRoom && selectedStyle) {
            if (selectedStyle === 'Customize') ready = !!selectedMoodboardFile;
            else ready = !!selectedVariant;
        }
        renderBtn.disabled = !ready;
    }

    // --- 메인 렌더링 ---
    if (renderBtn) {
        renderBtn.addEventListener('click', async () => {
            let ready = false;
            if (selectedFile && selectedRoom && selectedStyle) {
                if (selectedStyle === 'Customize') ready = !!selectedMoodboardFile;
                else ready = !!selectedVariant;
            }
            if (!ready) return;

            renderBtn.disabled = true;
            loadingOverlay.classList.remove('hidden');
            resultSection.classList.add('hidden');

            let startTime = Date.now();
            if (timerElement) timerElement.textContent = "0s";

            const timerInterval = setInterval(() => {
                let elapsedSeconds = Math.floor((Date.now() - startTime) / 1000);
                if (timerElement) timerElement.textContent = `${elapsedSeconds}s`;
                if (loadingStatus) {
                    if (elapsedSeconds < 10) loadingStatus.textContent = "Cleaning the room...";
                    else if (elapsedSeconds < 30) loadingStatus.textContent = "Designing Variation (1/3)...";
                    else loadingStatus.textContent = "Creating Comparison View...";
                }
            }, 500);

            const formData = new FormData();
            formData.append('file', selectedFile);
            formData.append('room', selectedRoom);
            formData.append('style', selectedStyle);
            formData.append('variant', selectedVariant || "1");
            if (selectedMoodboardFile) formData.append('moodboard', selectedMoodboardFile);

            try {
                const res = await fetch('/render', { method: 'POST', body: formData });
                if (!res.ok) throw new Error(`서버 에러 (${res.status})`);
                const data = await res.json();

                clearInterval(timerInterval);
                loadingOverlay.classList.add('hidden');
                resultSection.classList.remove('hidden');

                resultBefore.src = data.empty_room_url || data.original_url;
                const results = data.result_urls || [];
                if (results.length > 0) resultAfter.src = results[0];

                thumbnailContainer.innerHTML = "";

                // [NEW] Thumbnail Lightbox Support
                results.forEach((url, idx) => {
                    const img = document.createElement("img");
                    img.src = url;
                    img.style.width = "19%";
                    img.style.height = "auto";
                    img.style.aspectRatio = "16/9";
                    img.style.objectFit = "cover";
                    img.style.cursor = "pointer";
                    img.style.borderRadius = "8px";
                    img.style.border = idx === 0 ? `3px solid ${THEME_COLOR}` : "3px solid transparent";

                    img.onclick = () => {
                        resultAfter.src = url;
                        Array.from(thumbnailContainer.children).forEach(c => c.style.border = "3px solid transparent");
                        img.style.border = `3px solid ${THEME_COLOR}`;
                    };

                    // Double click to open lightbox with list
                    img.ondblclick = () => {
                        openLightbox(url, results, idx);
                    };

                    thumbnailContainer.appendChild(img);
                });

                initSlider();
                resultSection.scrollIntoView({ behavior: 'smooth' });

            } catch (err) {
                clearInterval(timerInterval);
                loadingOverlay.classList.add('hidden');
                showCustomAlert("Error", err.message);
            } finally {
                renderBtn.disabled = false;
                checkReady();
            }
        });
    }

    // --- 슬라이더 ---
    function initSlider() {
        if (!compareSlider || !sliderHandle || !comparisonContainer) return;
        const beforeWrapper = document.querySelector('.image-wrapper.before');
        const afterWrapper = document.querySelector('.image-wrapper.after');
        const beforeImage = document.getElementById('result-before');
        const afterImage = document.getElementById('result-after');

        const initialValue = 50;
        compareSlider.value = initialValue;
        if (beforeWrapper) beforeWrapper.style.width = `${initialValue}%`;
        if (afterWrapper) afterWrapper.style.width = "100%";
        sliderHandle.style.left = `${initialValue}%`;

        const containerWidth = comparisonContainer.offsetWidth;
        if (beforeImage) beforeImage.style.width = `${containerWidth}px`;
        if (afterImage) afterImage.style.width = `${containerWidth}px`;

        compareSlider.oninput = function () {
            const sliderValue = this.value + "%";
            if (beforeWrapper) beforeWrapper.style.width = sliderValue;
            sliderHandle.style.left = sliderValue;
        };
    }
    window.addEventListener('resize', initSlider);

    // --- 업스케일링 ---
    async function upscaleAndDownload(imgUrl, filenamePrefix) {
        try {
            const res = await fetch("/upscale", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ image_url: imgUrl })
            });
            const data = await res.json();
            if (data.upscaled_url) {
                const link = document.createElement("a");
                link.href = data.upscaled_url;
                link.download = `${filenamePrefix}_HighRes.jpg`;
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);
                return true;
            } else {
                throw new Error(data.warning || "Unknown error");
            }
        } catch (e) {
            console.error(e);
            return false;
        }
    }

    if (upscaleBtn) {
        upscaleBtn.onclick = async function () {
            const afterUrl = resultAfter ? resultAfter.src : null;
            const beforeUrl = resultBefore ? resultBefore.src : null;

            if (!afterUrl || !beforeUrl) { showCustomAlert("Warning", "이미지가 없습니다."); return; }

            // [Lock] 본인만 잠금 (동시 실행 허용)
            upscaleBtn.disabled = true;
            upscaleBtn.innerText = "PROCESSING...";
            if (upscaleStatus) upscaleStatus.style.display = "block";

            try {
                const p1 = upscaleAndDownload(afterUrl, "Result_After");
                const p2 = upscaleAndDownload(beforeUrl, "Result_Before");
                await Promise.all([p1, p2]);
                showCustomAlert("Success", "DOWNLOAD COMPLETE\n(Before & After)");
            } catch (err) {
                showCustomAlert("Error", "Server Error during upscale.");
            } finally {
                upscaleBtn.disabled = false;
                upscaleBtn.innerText = "UPSCALE & DOWNLOAD";
                if (upscaleStatus) upscaleStatus.style.display = "none";
            }
        };
    }

    // --- 디테일 뷰 ---
    function showLoading(msg) {
        loadingOverlay.classList.remove('hidden');
        if (loadingStatus) loadingStatus.textContent = msg;
        if (timerElement) timerElement.textContent = "0s";
        return Date.now();
    }

    function hideLoading() { loadingOverlay.classList.add('hidden'); }

    if (detailBtn) {
        detailBtn.onclick = async () => {
            const currentImgUrl = resultAfter ? resultAfter.src : null;
            if (!currentImgUrl) { showCustomAlert("Warning", "디테일 컷을 만들 이미지가 없습니다."); return; }
            currentDetailSourceUrl = currentImgUrl;

            // [Lock] 본인만 잠금 (동시 실행 허용)
            detailBtn.disabled = true;

            detailSection.classList.add('hidden');
            detailGrid.innerHTML = '';

            const startTime = showLoading("Setting up Virtual Cameras...");
            const msgs = ["Setting up Virtual Cameras...", "Capturing Light & Textures...", "Developing Editorial Shots...", "Finalizing Your Portfolio..."];
            let step = 0;

            const timerInterval = setInterval(() => {
                let elapsed = Math.floor((Date.now() - startTime) / 1000);
                if (timerElement) timerElement.textContent = `${elapsed}s`;
            }, 1000);

            const msgInterval = setInterval(() => {
                step = (step + 1) % msgs.length;
                if (step < msgs.length && loadingStatus) loadingStatus.textContent = msgs[step];
            }, 4000);

            try {
                const res = await fetch("/generate-details", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ image_url: currentImgUrl })
                });

                const data = await res.json();

                if (res.ok && data.details && data.details.length > 0) {
                    const detailUrls = data.details.map(d => d.url); // Extract URLs for lightbox

                    data.details.forEach(item => {
                        createDetailCard(item.url, item.index, detailUrls); // Pass full list
                    });

                    detailSection.classList.remove('hidden');
                    setTimeout(() => detailSection.scrollIntoView({ behavior: 'smooth' }), 100);
                } else {
                    showCustomAlert("Failed", "디테일 뷰 생성 실패");
                }
            } catch (err) {
                showCustomAlert("Error", "Error: " + err.message);
            } finally {
                clearInterval(timerInterval);
                clearInterval(msgInterval);
                hideLoading();
                detailBtn.disabled = false;
            }
        };
    }

    function createDetailCard(url, styleIndex, fullList = null) {
        const card = document.createElement('div');
        card.className = 'detail-card';

        const img = document.createElement('img');
        img.src = url;
        img.onclick = () => openLightbox(url, fullList, styleIndex - 1); // [NEW] Pass list & index (0-based)

        const retryBtn = document.createElement('button');
        retryBtn.className = 'detail-retry-btn';
        retryBtn.innerHTML = '&#x21bb;';
        retryBtn.title = "Retry this shot";

        retryBtn.onclick = (e) => {
            e.stopPropagation();
            showCustomConfirm("Retry", "이 컷만 다시 생성하시겠습니까?\n기존 이미지는 삭제됩니다.", async () => {
                await retrySingleDetail(card, styleIndex);
            });
        };

        const upBtn = document.createElement('button');
        upBtn.className = 'detail-upscale-btn';
        upBtn.textContent = "UPSCALE & DOWNLOAD";
        upBtn.onclick = async (e) => {
            e.stopPropagation();
            // [Lock] 카드 내부 버튼만 잠금
            upBtn.disabled = true;
            upBtn.textContent = "Processing...";

            await upscaleAndDownload(img.src, `Detail_Shot_${styleIndex}`);

            upBtn.textContent = "UPSCALE & DOWNLOAD";
            upBtn.disabled = false;
            showCustomAlert("Success", "Detail Shot Downloaded");
        };

        card.appendChild(img); card.appendChild(retryBtn); card.appendChild(upBtn);
        detailGrid.appendChild(card);
    }

    async function retrySingleDetail(cardElement, styleIndex) {
        if (!currentDetailSourceUrl) return;

        // [Lock] 카드 내부 버튼만 잠금
        const buttons = cardElement.querySelectorAll('button');
        buttons.forEach(btn => btn.disabled = true);

        const loader = document.createElement('div');
        loader.className = 'detail-card-loader';
        const spinner = document.createElement('div');
        spinner.className = 'mini-spinner';
        loader.appendChild(spinner);
        cardElement.appendChild(loader);

        try {
            const res = await fetch("/regenerate-single-detail", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    original_image_url: currentDetailSourceUrl,
                    style_index: styleIndex
                })
            });
            const data = await res.json();
            if (res.ok && data.url) {
                const imgElement = cardElement.querySelector('img');
                imgElement.src = data.url;
                imgElement.onclick = () => openLightbox(data.url, [data.url], 0); // Single retry view
            } else {
                showCustomAlert("Failed", "재생성 실패: " + (data.error || "Unknown error"));
            }
        } catch (e) {
            showCustomAlert("Error", "Error: " + e.message);
        } finally {
            loader.remove();
            buttons.forEach(btn => btn.disabled = false);
        }
    }

    // --- Lightbox with Keyboard Support ---
    function openLightbox(src, imageList = null, index = 0) {
        lightboxImg.src = src;
        lightbox.classList.remove('hidden');

        if (imageList && imageList.length > 0) {
            lightboxImages = imageList;
            currentLightboxIndex = index;
        } else {
            lightboxImages = [src];
            currentLightboxIndex = 0;
        }
    }

    // [NEW] Keyboard Event Listener
    document.addEventListener('keydown', (e) => {
        if (lightbox.classList.contains('hidden')) return;

        if (e.key === 'Escape') {
            lightbox.classList.add('hidden');
        } else if (e.key === 'ArrowLeft') {
            showPrevImage();
        } else if (e.key === 'ArrowRight') {
            showNextImage();
        }
    });

    function showPrevImage() {
        if (lightboxImages.length <= 1) return;
        currentLightboxIndex = (currentLightboxIndex - 1 + lightboxImages.length) % lightboxImages.length;
        lightboxImg.src = lightboxImages[currentLightboxIndex];
    }

    function showNextImage() {
        if (lightboxImages.length <= 1) return;
        currentLightboxIndex = (currentLightboxIndex + 1) % lightboxImages.length;
        lightboxImg.src = lightboxImages[currentLightboxIndex];
    }

    if (closeLightbox) { closeLightbox.onclick = () => lightbox.classList.add('hidden'); }
    if (lightbox) {
        lightbox.onclick = (e) => { if (e.target === lightbox) lightbox.classList.add('hidden'); };
    }
});