document.addEventListener('DOMContentLoaded', () => {
    console.log("✅ script.js 로드됨 (Multi Option FP Generation)");

    let currentFurnitureData = null;

    // --- [1] 통합 모달 시스템 설정 ---
    const globalModal = document.getElementById('global-modal');
    const modalTitle = document.getElementById('modal-title');
    const modalMsg = document.getElementById('modal-msg');
    const modalOkBtn = document.getElementById('modal-ok-btn');
    const modalCancelBtn = document.getElementById('modal-cancel-btn');

    function showCustomAlert(title, message) {
        modalTitle.textContent = title;
        modalMsg.innerHTML = message.replace(/\n/g, '<br>');
        modalCancelBtn.classList.add('hidden');
        modalOkBtn.textContent = "OK";
        modalOkBtn.onclick = () => globalModal.classList.add('hidden');
        globalModal.classList.remove('hidden');
    }

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

    const moodboardUploadContainer = document.getElementById('moodboard-upload-container');
    const moodboardDropZone = document.getElementById('moodboard-drop-zone');
    const moodboardInput = document.getElementById('moodboard-input');
    const moodboardPreviewContainer = document.getElementById('moodboard-preview-container');
    const moodboardPreview = document.getElementById('moodboard-preview');
    const removeMoodboardBtn = document.getElementById('remove-moodboard');

    const openFpGenBtn = document.getElementById('open-fp-gen-btn');
    const fpGenModal = document.getElementById('floorplan-generator-modal');
    const fpCloseBtn = document.getElementById('fp-close-btn');
    const fpGenerateBtn = document.getElementById('fp-generate-btn');
    const fpRetryBtn = document.getElementById('fp-retry-btn');

    const fpPlanDropZone = document.getElementById('fp-plan-drop-zone');
    const fpPlanInput = document.getElementById('fp-plan-input');
    const fpPlanPreviewContainer = document.getElementById('fp-plan-preview-container');
    const fpPlanPreview = document.getElementById('fp-plan-preview');
    const fpPlanRemove = document.getElementById('fp-plan-remove');

    const fpRefDropZone = document.getElementById('fp-ref-drop-zone');
    const fpRefInput = document.getElementById('fp-ref-input');
    const fpRefPreviewContainer = document.getElementById('fp-ref-preview-container');
    const fpRefRemoveAll = document.getElementById('fp-ref-remove-all');

    const fpLoading = document.getElementById('fp-loading');
    const fpResultActions = document.getElementById('fp-result-actions');
    const fpPlaceholderText = document.getElementById('fp-placeholder-text');
    const fpGenGrid = document.getElementById('fp-gen-grid');

    let fpPlanFile = null;
    let fpRefFiles = [];

    const openMbGenBtn = document.getElementById('open-mb-gen-btn');
    const mbGenModal = document.getElementById('moodboard-generator-modal');
    const mbGenDropZone = document.getElementById('mb-gen-drop-zone');
    const mbGenInput = document.getElementById('mb-gen-input');
    const mbGenPreviewContainer = document.getElementById('mb-gen-preview-container');
    const mbGenPreview = document.getElementById('mb-gen-preview');
    const mbGenRemoveBtn = document.getElementById('mb-gen-remove');
    const mbGenActionBtn = document.getElementById('mb-gen-action-btn');

    const mbGenStep1 = document.getElementById('mb-gen-step1');
    const mbGenStep2 = document.getElementById('mb-gen-step2');
    const mbStep2RefImg = document.getElementById('mb-step2-ref-img');
    const mbGenRetryBtn = document.getElementById('mb-gen-retry-btn');

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

    const detailGridLandscape = document.getElementById('detail-grid-landscape');
    const detailGridPortrait = document.getElementById('detail-grid-portrait');

    const lightbox = document.getElementById('lightbox');
    const lightboxImg = document.getElementById('lightbox-img');
    const closeLightbox = document.querySelector('.close-lightbox');

    let lightboxImages = [];
    let currentLightboxIndex = 0;

    const THEME_COLOR = "#ffffff";
    let persistedSliderValue = 50;

    let selectedFile = null;
    let selectedRoom = null;
    let selectedStyle = null;
    let selectedVariant = null;
    let selectedMoodboardFile = null;
    let currentDetailSourceUrl = null;
    let currentMoodboardUrl = null;

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
        currentMoodboardUrl = null;

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

    async function selectStyle(style, btn) {
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
            try {
                const res = await fetch(`/api/thumbnails/${selectedRoom}/${style}`);
                if (!res.ok) throw new Error("Thumbnail list fetch failed");

                const validNumbers = await res.json();

                const safeRoom = selectedRoom.toLowerCase().replace(/ /g, '');
                const safeStyle = style.toLowerCase().replace(/ /g, '-').replace(/_/g, '-');

                validNumbers.forEach(i => {
                    const variantBtn = document.createElement('div');
                    variantBtn.className = 'variant-img-btn';

                    const img = document.createElement('img');
                    img.src = `/static/thumbnails/${safeRoom}_${safeStyle}_${i}.png`;
                    img.alt = `Variant ${i}`;

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
                });
            } catch (err) {
                console.error("썸네일 목록 로드 실패 (기본 로직으로 폴백):", err);
                for (let i = 1; i <= 30; i++) {
                }
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

    // --- Floor Plan Logic ---
    if (openFpGenBtn) {
        openFpGenBtn.onclick = () => {
            fpGenModal.classList.remove('hidden');
            resetFpModal();
        };
    }

    if (fpCloseBtn) {
        fpCloseBtn.onclick = () => fpGenModal.classList.add('hidden');
    }

    function resetFpModal() {
        fpPlanFile = null;
        fpRefFiles = [];
        fpPlanInput.value = '';
        fpRefInput.value = '';

        fpPlanPreviewContainer.classList.add('hidden');
        fpPlanDropZone.classList.remove('hidden');

        fpRefPreviewContainer.innerHTML = '';
        fpRefPreviewContainer.classList.add('hidden');
        fpRefDropZone.classList.remove('hidden');
        fpRefRemoveAll.classList.add('hidden');

        fpPlaceholderText.classList.remove('hidden');
        fpGenGrid.innerHTML = '';
        fpGenGrid.style.display = 'none';
        fpResultActions.classList.add('hidden');
        fpLoading.classList.add('hidden');

        fpGenerateBtn.disabled = true;
    }

    if (fpPlanDropZone) {
        fpPlanDropZone.addEventListener('click', () => fpPlanInput.click());
        fpPlanDropZone.addEventListener('dragover', (e) => { e.preventDefault(); fpPlanDropZone.style.borderColor = THEME_COLOR; });
        fpPlanDropZone.addEventListener('dragleave', () => fpPlanDropZone.style.borderColor = '#ccc');
        fpPlanDropZone.addEventListener('drop', (e) => { e.preventDefault(); if (e.dataTransfer.files.length) handleFpPlan(e.dataTransfer.files[0]); });
        fpPlanInput.addEventListener('change', (e) => { if (e.target.files.length) handleFpPlan(e.target.files[0]); });
    }

    function handleFpPlan(file) {
        if (!file.type.startsWith('image/')) return;
        fpPlanFile = file;
        const reader = new FileReader();
        reader.onload = (e) => {
            fpPlanPreview.src = e.target.result;
            fpPlanPreviewContainer.classList.remove('hidden');
            fpPlanDropZone.classList.add('hidden');
            checkFpReady();
        };
        reader.readAsDataURL(file);
    }

    if (fpPlanRemove) {
        fpPlanRemove.onclick = (e) => {
            e.stopPropagation(); fpPlanFile = null; fpPlanInput.value = '';
            fpPlanPreviewContainer.classList.add('hidden'); fpPlanDropZone.classList.remove('hidden'); checkFpReady();
        };
    }

    if (fpRefDropZone) {
        fpRefDropZone.addEventListener('click', () => fpRefInput.click());
        fpRefDropZone.addEventListener('dragover', (e) => { e.preventDefault(); fpRefDropZone.style.borderColor = THEME_COLOR; });
        fpRefDropZone.addEventListener('dragleave', () => fpRefDropZone.style.borderColor = '#ccc');
        fpRefDropZone.addEventListener('drop', (e) => {
            e.preventDefault();
            if (e.dataTransfer.files.length) handleFpRefFiles(e.dataTransfer.files);
        });
        fpRefInput.addEventListener('change', (e) => {
            if (e.target.files.length) handleFpRefFiles(e.target.files);
        });
    }

    function handleFpRefFiles(files) {
        if (!files || files.length === 0) return;
        Array.from(files).forEach(file => {
            if (file.type.startsWith('image/')) {
                fpRefFiles.push(file);
            }
        });
        updateRefPreviews();
        checkFpReady();
    }

    function updateRefPreviews() {
        fpRefPreviewContainer.innerHTML = '';
        if (fpRefFiles.length > 0) {
            fpRefPreviewContainer.classList.remove('hidden');
            fpRefRemoveAll.classList.remove('hidden');
            fpRefFiles.forEach(file => {
                const reader = new FileReader();
                reader.onload = (e) => {
                    const img = document.createElement('img');
                    img.src = e.target.result;
                    img.className = 'unified-preview';
                    fpRefPreviewContainer.appendChild(img);
                };
                reader.readAsDataURL(file);
            });
        } else {
            fpRefPreviewContainer.classList.add('hidden');
            fpRefRemoveAll.classList.add('hidden');
        }
    }

    if (fpRefRemoveAll) {
        fpRefRemoveAll.onclick = (e) => {
            e.stopPropagation();
            fpRefFiles = [];
            fpRefInput.value = '';
            updateRefPreviews();
            checkFpReady();
        };
    }

    function checkFpReady() {
        fpGenerateBtn.disabled = !(fpPlanFile && fpRefFiles.length > 0);
    }

    async function performRoomGeneration() {
        if (!fpPlanFile || fpRefFiles.length === 0) return;

        fpPlaceholderText.classList.add('hidden');
        fpGenGrid.innerHTML = '';
        fpGenGrid.style.display = 'none';
        fpResultActions.classList.add('hidden');
        fpLoading.classList.remove('hidden');

        fpGenerateBtn.disabled = true;
        if (fpRetryBtn) fpRetryBtn.disabled = true;

        const formData = new FormData();
        formData.append('floor_plan', fpPlanFile);
        fpRefFiles.forEach(file => {
            formData.append('ref_photos', file);
        });

        try {
            const res = await fetch('/generate-room-from-plan', {
                method: 'POST',
                body: formData
            });
            const data = await res.json();

            if (res.ok && data.urls && data.urls.length > 0) {
                const resultUrls = data.urls;
                fpGenGrid.innerHTML = '';
                fpGenGrid.style.display = 'flex';

                resultUrls.forEach((url, idx) => {
                    const div = document.createElement('div');
                    div.className = 'detail-card';

                    const img = document.createElement('img');
                    img.src = url;
                    img.style.aspectRatio = "16 / 9";
                    img.style.objectFit = "contain";
                    img.style.backgroundColor = "#000";
                    img.style.cursor = "zoom-in";
                    img.onclick = (e) => {
                        e.stopPropagation();
                        openLightbox(url, resultUrls, idx);
                    };

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
                            const file = new File([blob], `generated_empty_room_${idx}.png`, { type: 'image/png' });

                            handleFile(file);
                            fpGenModal.classList.add('hidden');
                            showCustomAlert("Success", "Generated room set as main input!");

                        } catch (err) {
                            showCustomAlert("Error", "Failed to load selected image.");
                            selectBtn.textContent = "SELECT THIS";
                            selectBtn.disabled = false;
                        }
                    };

                    div.appendChild(img);
                    div.appendChild(selectBtn);
                    fpGenGrid.appendChild(div);
                });

                fpResultActions.classList.remove('hidden');
            } else {
                fpPlaceholderText.textContent = "Generation failed. Please try again.";
                fpPlaceholderText.classList.remove('hidden');
                showCustomAlert("Error", "Generation failed: " + (data.error || "Unknown error"));
            }
        } catch (err) {
            fpPlaceholderText.textContent = "Server error.";
            fpPlaceholderText.classList.remove('hidden');
            showCustomAlert("Error", "Server error: " + err.message);
        } finally {
            fpLoading.classList.add('hidden');
            fpGenerateBtn.disabled = false;
            if (fpRetryBtn) fpRetryBtn.disabled = false;
        }
    }

    if (fpGenerateBtn) {
        fpGenerateBtn.onclick = performRoomGeneration;
    }

    if (fpRetryBtn) {
        fpRetryBtn.onclick = performRoomGeneration;
    }

    // --- Moodboard Generator Logic ---
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
            mbGenSelectedFile = null;
            mbGenInput.value = '';
            mbGenPreviewContainer.classList.add('hidden');
            mbGenDropZone.classList.remove('hidden');
            mbGenActionBtn.disabled = true;
            mbGenStep1.classList.remove('hidden');
            mbGenStep2.classList.add('hidden');
            mbGenLoading.classList.add('hidden');
            mbGenGrid.innerHTML = '';
        };
    }

    if (mbGenCloseBtn) {
        mbGenCloseBtn.onclick = () => mbGenModal.classList.add('hidden');
    }

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

    if (mbGenActionBtn) {
        mbGenActionBtn.onclick = async () => {
            await performMbGeneration();
        };
    }

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
        mbGenStep1.classList.add('hidden');
        mbGenStep2.classList.remove('hidden');
        mbGenLoading.classList.remove('hidden');
        mbGenGrid.innerHTML = '';

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
                const moodboardUrls = data.moodboards;

                data.moodboards.forEach((url, idx) => {
                    const div = document.createElement('div');
                    div.className = 'detail-card';

                    const img = document.createElement('img');
                    img.src = url;
                    img.style.aspectRatio = "16 / 9";
                    img.style.objectFit = "contain";
                    img.style.objectPosition = "center";
                    img.style.backgroundColor = "#000";
                    img.style.cursor = "zoom-in";
                    img.onclick = (e) => {
                        e.stopPropagation();
                        openLightbox(url, moodboardUrls, idx);
                    };

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

                if (data.furniture_data) {
                    console.log("📦 가구 분석 데이터 저장 완료:", data.furniture_data.length + "개");
                    currentFurnitureData = data.furniture_data;
                }

                if (data.moodboard_url) {
                    currentMoodboardUrl = data.moodboard_url;
                    console.log("✅ Moodboard URL Saved:", currentMoodboardUrl);
                } else {
                    currentMoodboardUrl = null;
                }

                clearInterval(timerInterval);
                loadingOverlay.classList.add('hidden');
                resultSection.classList.remove('hidden');

                resultBefore.src = data.empty_room_url || data.original_url;
                const results = data.result_urls || [];
                if (results.length > 0) resultAfter.src = results[0];

                // [수정] 결과 이미지 로드 시 비율 감지 로직 추가
                resultAfter.onload = () => {
                    const isPortrait = resultAfter.naturalHeight > resultAfter.naturalWidth;
                    if (comparisonContainer) {
                        comparisonContainer.style.aspectRatio = isPortrait ? "4 / 5" : "16 / 9";
                    }
                    initSlider();
                };

                thumbnailContainer.innerHTML = "";
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

    function initSlider() {
        if (!compareSlider || !sliderHandle || !comparisonContainer) return;
        const beforeWrapper = document.querySelector('.image-wrapper.before');
        const afterWrapper = document.querySelector('.image-wrapper.after');
        const beforeImage = document.getElementById('result-before');
        const afterImage = document.getElementById('result-after');

        // ✅ 기존 50 고정 대신, 저장된 값 사용
        const initialValue = (typeof persistedSliderValue === 'number' ? persistedSliderValue : 50);
        compareSlider.value = initialValue;

        if (beforeWrapper) beforeWrapper.style.width = `${initialValue}%`;
        if (afterWrapper) afterWrapper.style.width = "100%";
        sliderHandle.style.left = `${initialValue}%`;

        const containerWidth = comparisonContainer.offsetWidth;
        if (beforeImage) beforeImage.style.width = `${containerWidth}px`;
        if (afterImage) afterImage.style.width = `${containerWidth}px`;

        compareSlider.oninput = function () {
            // ✅ 사용자가 움직일 때마다 값 저장
            persistedSliderValue = Math.max(0, Math.min(100, parseInt(this.value, 10) || 0));

            const sliderValue = this.value + "%";
            if (beforeWrapper) beforeWrapper.style.width = sliderValue;
            sliderHandle.style.left = sliderValue;
        };
    }
    window.addEventListener('resize', initSlider);

    async function downloadFile(url, prefix) {
        try {
            const link = document.createElement("a");
            link.href = url;
            link.download = `${prefix}_${Date.now()}.jpg`;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        } catch (e) {
            console.error(e);
        }
    }

    if (upscaleBtn) {
        upscaleBtn.onclick = async function () {
            const afterUrl = resultAfter ? resultAfter.src : null;
            if (!afterUrl) { showCustomAlert("Warning", "이미지가 없습니다."); return; }

            upscaleBtn.disabled = true;
            upscaleBtn.innerText = "PROCESSING (Empty Room Gen & Upscale)...";
            if (upscaleStatus) upscaleStatus.style.display = "block";

            try {
                const res = await fetch("/finalize-download", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ image_url: afterUrl })
                });

                const data = await res.json();

                if (res.ok && data.upscaled_furnished && data.upscaled_empty) {
                    await downloadFile(data.upscaled_furnished, "Result_After_HighRes");
                    setTimeout(() => {
                        downloadFile(data.upscaled_empty, "Result_Before_Empty_HighRes");
                    }, 1000);

                    showCustomAlert("Success", "Download Complete! (Before & After)");
                } else {
                    throw new Error(data.error || "Processing failed");
                }

            } catch (err) {
                showCustomAlert("Error", "Server Error: " + err.message);
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

            detailBtn.disabled = true;

            detailSection.classList.remove('hidden');
            if (detailGridLandscape) detailGridLandscape.innerHTML = '';
            if (detailGridPortrait) detailGridPortrait.innerHTML = '';

            const startTime = showLoading("Generating Dynamic Detail Views...");
            const msgs = ["Analysing Furniture...", "Setting up Angles...", "Rendering Close-ups...", "Finalizing..."];
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
                const payload = {
                    image_url: currentImgUrl,
                    moodboard_url: currentMoodboardUrl,
                    furniture_data: currentFurnitureData
                };

                const res = await fetch("/generate-details", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });

                const data = await res.json();

                if (res.ok && data.details && data.details.length > 0) {
                    const detailUrls = data.details.map(d => d.url);

                    data.details.forEach(item => {
                        createDetailCard(item.url, item.index, detailUrls);
                    });

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

        if (styleIndex < 3) {
            img.style.aspectRatio = "16 / 9";
            card.appendChild(img);
            appendButtonsToCard(card, img, url, styleIndex + 1, fullList);
            const landscapeGrid = document.getElementById('detail-grid-landscape');
            if (landscapeGrid) landscapeGrid.appendChild(card);
        } else {
            img.style.aspectRatio = "4 / 5";
            card.appendChild(img);
            appendButtonsToCard(card, img, url, styleIndex + 1, fullList);
            const portraitGrid = document.getElementById('detail-grid-portrait');
            if (portraitGrid) portraitGrid.appendChild(card);
        }
    }

    function appendButtonsToCard(card, img, url, styleIndex, fullList) {
        img.onclick = () => openLightbox(url, fullList, styleIndex - 1);

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

            if (upBtn.disabled) return;
            upBtn.disabled = true;

            const loader = document.createElement('div');
            loader.className = 'detail-card-loader';
            const spinner = document.createElement('div');
            spinner.className = 'mini-spinner';
            loader.appendChild(spinner);
            card.appendChild(loader);

            const originalText = upBtn.textContent;
            upBtn.textContent = "Processing...";

            try {
                const success = await upscaleAndDownload(img.src, `Detail_Shot_${styleIndex}`);

                if (success) {
                    showCustomAlert("Success", "다운로드가 완료되었습니다.");
                } else {
                    showCustomAlert("Error", "업스케일링에 실패했습니다.\n(잠시 후 다시 시도하거나 서버 로그를 확인하세요)");
                }
            } catch (err) {
                console.error("Critical Error:", err);
                showCustomAlert("Error", "알 수 없는 오류가 발생했습니다.");
            } finally {
                if (loader && loader.parentNode) {
                    loader.parentNode.removeChild(loader);
                }
                upBtn.textContent = originalText;
                upBtn.disabled = false;
            }
        };

        card.appendChild(retryBtn);
        card.appendChild(upBtn);
    }

    async function retrySingleDetail(cardElement, styleIndex) {
        if (!currentDetailSourceUrl) return;

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
                    style_index: styleIndex - 1,
                    moodboard_url: currentMoodboardUrl,
                    furniture_data: currentFurnitureData
                })
            });
            const data = await res.json();
            if (res.ok && data.url) {
                const imgElement = cardElement.querySelector('img');
                imgElement.src = data.url;
                imgElement.onclick = () => openLightbox(data.url, [data.url], 0);
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

async function upscaleAndDownload(imgUrl, filenamePrefix) {
    try {
        console.log("🚀 Upscaling start:", imgUrl);

        const res = await fetch("/upscale", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ image_url: imgUrl })
        });

        if (!res.ok) {
            const errText = await res.text();
            throw new Error(`Server Error (${res.status}): ${errText}`);
        }

        const data = await res.json();

        if (data.upscaled_url) {
            console.log("✅ Upscale success:", data.upscaled_url);
            const link = document.createElement("a");
            link.href = data.upscaled_url;
            link.download = `${filenamePrefix}_${Date.now()}.jpg`;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            return true;
        } else {
            throw new Error(data.error || data.warning || "Unknown error during upscale");
        }
    } catch (e) {
        console.error("❌ Upscale failed:", e);
        return false;
    }
}


// =========================
// Video MVP (Kling Image-to-Video)
// =========================
document.addEventListener('DOMContentLoaded', () => {
    const videoBtn = document.getElementById('videoMvpBtn');
    const modal = document.getElementById('video-mvp-modal');
    if (!videoBtn || !modal) return;

    const closeBtn = document.getElementById('videoMvpClose');
    const listEl = document.getElementById('videoMvpList');
    const statusEl = document.getElementById('videoMvpStatus');
    const startBtn = document.getElementById('videoMvpStart');

    const durationSel = document.getElementById('videoMvpDuration');
    const mainPresetSel = document.getElementById('videoMvpMainPreset');
    const detailPresetSel = document.getElementById('videoMvpDetailPreset');

    const resultWrap = document.getElementById('videoMvpResult');
    const preview = document.getElementById('videoMvpPreview');
    const download = document.getElementById('videoMvpDownload');

    function openModal() {
        modal.classList.remove('hidden');
        resultWrap?.classList.add('hidden');
        if (preview) preview.src = "";
        if (download) download.href = "#";
        buildClipList();
        if (statusEl) statusEl.textContent = "";
    }

    function closeModal() {
        modal.classList.add('hidden');
    }

    function getDetailUrls() {
        const urls = [];
        document.querySelectorAll('.detail-card img').forEach(img => {
            if (img && img.src) urls.push(img.src);
        });
        return Array.from(new Set(urls));
    }

    function presetSelectHtml(defaultPreset) {
        const opts = [
            ["main_sunlight", "Main: Sunlight (Dynamic)"],
            ["orbit_rotate", "Main: Orbit Rotate ↻"],
            ["orbit_rotate_ccw", "Main: Orbit Rotate ↺"],
            ["detail_pan_lr", "Detail: Pan L→R"],
            ["detail_pan_rl", "Detail: Pan R→L"],
            ["detail_dolly_in", "Detail: Dolly-in"],
            ["detail_dolly_out", "Detail: Dolly-out"],
            ["tilt_down", "Detail: Tilt Down"],
            ["static", "Almost Static"],
        ];
        return `<select class="video-mvp-preset">
            ${opts.map(([v, t]) => `<option value="${v}" ${v === defaultPreset ? 'selected' : ''}>${t}</option>`).join('')}
        </select>`;
    }

    function buildClipList() {
        if (!listEl) return;
        listEl.innerHTML = "";

        const beforeImg = document.getElementById('result-before');
        const afterImg = document.getElementById('result-after');

        const clips = [];

        if (beforeImg && beforeImg.src) {
            clips.push({
                label: "Before (Empty Room)",
                url: beforeImg.src,
                defaultPreset: "detail_dolly_in"
            });
        }

        if (afterImg && afterImg.src) {
            clips.push({
                label: "Main Shot (After)",
                url: afterImg.src,
                defaultPreset: mainPresetSel ? mainPresetSel.value : "main_sunlight"
            });
        }

        const detailUrls = getDetailUrls();
        detailUrls.forEach((u, idx) => {
            clips.push({
                label: `Detail ${idx + 1}`,
                url: u,
                defaultPreset: detailPresetSel ? detailPresetSel.value : "detail_pan_lr"
            });
        });

        if (clips.length === 0) {
            listEl.innerHTML = `<div class="video-mvp-empty">아직 결과 이미지가 없습니다. 먼저 렌더링/디테일 생성 후 열어주세요.</div>`;
            return;
        }

        clips.forEach((c) => {
            const row = document.createElement('div');
            row.className = 'video-mvp-item';
            row.innerHTML = `
                <div class="video-mvp-item-left">
                    <input type="checkbox" class="video-mvp-check" checked />
                    <div class="video-mvp-thumb"><img src="${c.url}" /></div>
                    <div class="video-mvp-label">
                        <div class="video-mvp-title">${c.label}</div>
                        <div class="video-mvp-url">${c.url}</div>
                    </div>
                </div>
                <div class="video-mvp-item-right">
                    ${presetSelectHtml(c.defaultPreset)}
                </div>
            `;
            row.dataset.url = c.url;
            listEl.appendChild(row);
        });
    }

    if (mainPresetSel) mainPresetSel.addEventListener('change', buildClipList);
    if (detailPresetSel) detailPresetSel.addEventListener('change', buildClipList);

    async function startJob() {
        if (!listEl) return;

        const items = Array.from(listEl.querySelectorAll('.video-mvp-item'));
        const clips = [];
        items.forEach(it => {
            const checked = it.querySelector('.video-mvp-check')?.checked;
            if (!checked) return;
            const url = it.dataset.url;
            const preset = it.querySelector('.video-mvp-preset')?.value || "detail_pan_lr";
            if (url) clips.push({ url, preset });
        });

        if (clips.length === 0) {
            if (statusEl) statusEl.textContent = "선택된 이미지가 없습니다.";
            return;
        }

        startBtn.disabled = true;
        if (statusEl) statusEl.textContent = "Creating job...";

        try {
            const res = await fetch('/video-mvp/create', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    clips,
                    duration: durationSel ? durationSel.value : "5",
                    cfg_scale: 0.85
                })
            });

            if (!res.ok) {
                const t = await res.text();
                throw new Error(`Create failed (${res.status}): ${t}`);
            }

            const data = await res.json();
            const jobId = data.job_id;
            if (!jobId) throw new Error("No job_id returned");

            await pollJob(jobId);
        } catch (e) {
            console.error(e);
            if (statusEl) statusEl.textContent = `Error: ${e.message}`;
        } finally {
            startBtn.disabled = false;
        }
    }

    async function pollJob(jobId) {
        if (statusEl) statusEl.textContent = `Running... (${jobId})`;

        return new Promise((resolve) => {
            const iv = setInterval(async () => {
                try {
                    const r = await fetch(`/video-mvp/status/${jobId}`);
                    if (!r.ok) return;

                    const st = await r.json();
                    const msg = st.message || st.status || "";
                    const prog = (st.progress != null) ? `${st.progress}%` : "";
                    if (statusEl) statusEl.textContent = `${msg} ${prog}`;

                    if (st.status === "COMPLETED" && st.result_url) {
                        clearInterval(iv);
                        if (preview) preview.src = st.result_url;
                        if (download) download.href = st.result_url;
                        if (resultWrap) resultWrap.classList.remove('hidden');
                        resolve();
                    }

                    if (st.status === "FAILED") {
                        clearInterval(iv);
                        if (statusEl) statusEl.textContent = `FAILED: ${st.error || 'unknown error'}`;
                        resolve();
                    }
                } catch (e) {
                    console.error(e);
                }
            }, 2000);
        });
    }

    videoBtn.addEventListener('click', openModal);
    closeBtn?.addEventListener('click', closeModal);

    modal.addEventListener('click', (e) => {
        if (e.target === modal) closeModal();
    });

    startBtn?.addEventListener('click', startJob);
});
