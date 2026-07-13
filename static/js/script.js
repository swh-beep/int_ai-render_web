const JOB_POLL_INTERVAL = 2000;
const JOB_TIMEOUT_MS = 10 * 60 * 1000;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function pollJob(jobId, opts = {}) {
    const interval = opts.interval || JOB_POLL_INTERVAL;
    const timeoutMs = opts.timeoutMs || JOB_TIMEOUT_MS;
    const started = Date.now();

    while (true) {
        const res = await fetch(`/jobs/${jobId}`);
        if (!res.ok) {
            throw new Error(`Job status error (${res.status})`);
        }
        const data = await res.json();
        const status = data.status;

        if (status === 'finished' || status === 'completed') {
            if (data.result && data.result.error) {
                throw new Error(data.result.error);
            }
            return data.result || {};
        }
        if (status === 'failed') {
            throw new Error(data.error || 'Job failed');
        }
        if (Date.now() - started > timeoutMs) {
            throw new Error('Job timeout');
        }
        await sleep(interval);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const PAGE = document.body?.dataset?.page || 'home';
    if (PAGE !== 'home') return;
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
    const furnitureItemsContainer = document.getElementById('furniture-items-container');
    const furnitureItemTemplate = document.getElementById('furniture-item-template');
    const addFurnitureItemBtn = document.getElementById('add-furniture-item-btn');

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

    const videoStudioBtn = document.getElementById('videoStudioBtn');
    if (videoStudioBtn) {
        videoStudioBtn.onclick = () => {
            window.location.href = '/video-studio';
        };
    }

    const detailStatus = document.getElementById('detailStatus');
    const detailSection = document.getElementById('detail-section');

    const detailGridLandscape = document.getElementById('detail-grid-landscape');
    const detailGridPortrait = document.getElementById('detail-grid-portrait');

    const closeResultBtn = document.getElementById('close-result-btn');
    if (closeResultBtn) {
        closeResultBtn.onclick = () => {
            if (resultSection) resultSection.classList.add('hidden');
            if (detailSection) detailSection.classList.add('hidden');
            window.scrollTo({ top: 0, behavior: 'smooth' });
        };
    }


    const lightbox = document.getElementById('lightbox');
    const lightboxImg = document.getElementById('lightbox-img');
    const closeLightbox = document.querySelector('.close-lightbox');

    let lightboxImages = [];
    let currentLightboxIndex = 0;

    const THEME_COLOR = "#ffffff";
    const MAX_IMAGE_UPLOAD_BYTES = 25 * 1024 * 1024;
    const ALLOWED_IMAGE_UPLOAD_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'webp']);
    const INITIAL_FURNITURE_CARD_COUNT = 5;
    const FURNITURE_BATCH_SIZE = 5;
    let persistedSliderValue = 50;

    let selectedFile = null;
    let selectedRoom = null;
    let selectedStyle = null;
    let selectedVariant = null;
    let customMoodboardFile = null;
    let currentDetailSourceUrl = null;
    let currentMoodboardUrl = null;
    let furnitureClientCounter = 0;
    let roomSelectionRequestToken = 0;
    let styleSelectionRequestToken = 0;

    function validateHomeImageUpload(file, contextLabel) {
        if (!file) return false;

        const fileName = String(file.name || '').trim();
        const extension = fileName.includes('.')
            ? fileName.split('.').pop().toLowerCase()
            : '';

        if (!ALLOWED_IMAGE_UPLOAD_EXTENSIONS.has(extension)) {
            showCustomAlert("Error", `${contextLabel} must be a PNG, JPG, JPEG, or WEBP image.`);
            return false;
        }

        if (file.size > MAX_IMAGE_UPLOAD_BYTES) {
            showCustomAlert("Error", `${contextLabel} must be 25MB or smaller.`);
            return false;
        }

        return true;
    }

    // --- 데이터 로드 ---
    fetch('/room-types')
        .then(res => res.json())
        .then(rooms => {
            roomGrid.innerHTML = '';
            if (!rooms.includes('Customize')) rooms.push('Customize');
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
        const requestToken = ++roomSelectionRequestToken;
        styleSelectionRequestToken += 1;
        selectedRoom = room;
        selectedStyle = null;
        selectedVariant = null;
        customMoodboardFile = null;
        currentMoodboardUrl = null;

        if (moodboardPreviewContainer) moodboardPreviewContainer.classList.add('hidden');
        if (moodboardUploadContainer) moodboardUploadContainer.classList.add('hidden');
        if (moodboardDropZone) moodboardDropZone.classList.remove('hidden');
        if (variantGrid) variantGrid.classList.remove('hidden');

        updateActiveButton(roomGrid, btn);

        if (room === 'Customize') {
            selectedStyle = 'Customize';
            if (styleSection) styleSection.classList.add('hidden');
            if (variantSection) variantSection.classList.remove('hidden');
            if (variantGrid) variantGrid.classList.add('hidden');
            if (moodboardUploadContainer) moodboardUploadContainer.classList.remove('hidden');
            if (styleGrid) styleGrid.innerHTML = '';
            checkReady();
            return;
        }

        fetch(`/styles/${room}`)
            .then(res => res.json())
            .then(styles => {
                if (requestToken !== roomSelectionRequestToken) return;
                styleGrid.innerHTML = '';
                styles.forEach(style => {
                    if (style === 'Customize') return;
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
        const requestToken = ++styleSelectionRequestToken;
        selectedStyle = style;
        selectedVariant = null;
        updateActiveButton(styleGrid, btn);

        if (style === 'Customize') {
            variantGrid.classList.add('hidden');
            if (moodboardUploadContainer) moodboardUploadContainer.classList.remove('hidden');
        } else {
            variantGrid.classList.remove('hidden');
            if (moodboardUploadContainer) moodboardUploadContainer.classList.add('hidden');
            customMoodboardFile = null;
        }

        variantGrid.innerHTML = '';

        if (style !== 'Customize') {
            try {
                const res = await fetch(`/api/thumbnails/${selectedRoom}/${style}`);
                if (requestToken !== styleSelectionRequestToken) return;
                if (!res.ok) throw new Error("Thumbnail list fetch failed");

                // [변경] 서버에서 {index, file} 형태의 리스트를 받음
                const validItems = await res.json();
                if (requestToken !== styleSelectionRequestToken) return;
                const thumbUrls = validItems.map(item => `/static/thumbnails/${item.file}`);

                validItems.forEach((item, idx) => {
                    // [변경] item.index와 item.file을 사용
                    const i = item.index;
                    const fileName = item.file;

                    const variantBtn = document.createElement('div');
                    variantBtn.className = 'variant-img-btn';

                    const img = document.createElement('img');
                    // [핵심 수정] 무조건 .png 붙이는 게 아니라 서버가 준 파일명 그대로 사용
                    img.src = `/static/thumbnails/${fileName}`;
                    img.alt = `Variant ${i}`;

                    const label = document.createElement('span');
                    label.className = 'variant-label';
                    label.textContent = i;

                    variantBtn.appendChild(img);
                    variantBtn.appendChild(label);

                    const selectVariant = () => {
                        selectedVariant = i.toString();
                        document.querySelectorAll('.variant-img-btn').forEach(b => {
                            b.classList.remove('active');
                            b.style.borderColor = 'transparent';
                        });
                        variantBtn.classList.add('active');
                        variantBtn.style.borderColor = THEME_COLOR;
                        checkReady();
                    };
                    variantBtn.onclick = selectVariant;
                    img.onclick = (e) => {
                        e.stopPropagation();
                        selectVariant();
                        if (typeof openLightbox === 'function') {
                            openLightbox(thumbUrls[idx], thumbUrls, idx);
                        }
                    };
                    variantGrid.appendChild(variantBtn);
                });
            } catch (err) {
                console.error("썸네일 목록 로드 실패:", err);
                // 폴백 로직이 필요하다면 여기에 작성
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

    function generateFurnitureClientId() {
        furnitureClientCounter += 1;
        return `item_${furnitureClientCounter}`;
    }

    function getFurnitureCards() {
        if (!furnitureItemsContainer) return [];
        return Array.from(furnitureItemsContainer.querySelectorAll('.furniture-item-card'));
    }

    function getFurnitureFields(card) {
        return {
            card,
            heading: card.querySelector('.furniture-item-card__header h4'),
            eyebrow: card.querySelector('.furniture-item-card__eyebrow'),
            removeBtn: card.querySelector('.furniture-item-remove-btn'),
            dropZone: card.querySelector('.furniture-item-drop-zone'),
            input: card.querySelector('.furniture-item-input'),
            preview: card.querySelector('.furniture-item-preview'),
            previewImage: card.querySelector('.furniture-item-preview-image'),
            previewRemove: card.querySelector('.furniture-item-preview-remove'),
            nameInput: card.querySelector('.furniture-item-name'),
            categorySelect: card.querySelector('.furniture-category-select'),
            qtyInput: card.querySelector('.furniture-item-qty'),
            widthInput: card.querySelector('.furniture-item-width'),
            depthInput: card.querySelector('.furniture-item-depth'),
            heightInput: card.querySelector('.furniture-item-height'),
        };
    }

    function isCurtainCategoryValue(value) {
        return String(value || '').trim() === '커튼';
    }

    function syncCurtainDimensionFields(card) {
        const fields = getFurnitureFields(card);
        const curtainSelected = isCurtainCategoryValue(fields.categorySelect?.value);
        [fields.widthInput, fields.depthInput, fields.heightInput].forEach((field) => {
            if (!field) return;
            field.disabled = curtainSelected;
            field.required = !curtainSelected;
            if (curtainSelected) field.value = '';
        });
    }

    function setFurnitureCardNumber(card, index) {
        const fields = getFurnitureFields(card);
        if (fields.heading) fields.heading.textContent = `Item ${index}`;
        if (fields.eyebrow) fields.eyebrow.textContent = `Furniture Item ${index}`;
        card.dataset.cardIndex = String(index);
    }

    function resetFurnitureCardImage(card) {
        const fields = getFurnitureFields(card);
        card._previewToken = (card._previewToken || 0) + 1;
        card._itemFile = null;
        card.dataset.hasImage = 'false';
        if (fields.input) fields.input.value = '';
        if (fields.previewImage) fields.previewImage.src = '';
        if (fields.preview) fields.preview.classList.add('hidden');
        if (fields.dropZone) fields.dropZone.classList.remove('hidden');
        checkReady();
    }

    function applyFurnitureCardImage(card, file) {
        if (!file || !file.type || !file.type.startsWith('image/')) {
            showCustomAlert("Error", "이미지 파일만 가능합니다.");
            return;
        }

        const fields = getFurnitureFields(card);
        const previewToken = (card._previewToken || 0) + 1;
        card._previewToken = previewToken;
        card._itemFile = file;
        card.dataset.hasImage = 'true';

        const reader = new FileReader();
        reader.onload = (e) => {
            if (card._previewToken !== previewToken || card._itemFile !== file) {
                return;
            }
            if (fields.previewImage) fields.previewImage.src = e.target.result;
            if (fields.preview) fields.preview.classList.remove('hidden');
            if (fields.dropZone) fields.dropZone.classList.add('hidden');
            checkReady();
        };
        reader.readAsDataURL(file);
    }

    function isFurnitureCardComplete(card) {
        const fields = getFurnitureFields(card);
        const qty = Number(fields.qtyInput?.value);
        const width = Number(fields.widthInput?.value);
        const depth = Number(fields.depthInput?.value);
        const height = Number(fields.heightInput?.value);
        const dimensionsComplete = isCurtainCategoryValue(fields.categorySelect?.value) || (
            Number.isInteger(width) && width > 0 &&
            Number.isInteger(depth) && depth > 0 &&
            Number.isInteger(height) && height > 0
        );

        return !!card._itemFile &&
            !!fields.categorySelect?.value &&
            Number.isInteger(qty) && qty >= 1 &&
            dimensionsComplete;
    }

    function hasCompleteFurnitureItems() {
        const cards = getFurnitureCards();
        return cards.length > 0 && cards.every(isFurnitureCardComplete);
    }

    function getFurnitureValidationError() {
        const cards = getFurnitureCards();
        if (!cards.length) {
            return {
                title: "Missing Furniture Items",
                message: "Add at least one furniture item before rendering.",
            };
        }

        const missingImages = [];
        const missingCategories = [];
        const invalidQuantities = [];
        const missingDimensions = [];

        cards.forEach((card, index) => {
            const fields = getFurnitureFields(card);
            const itemLabel = `Item ${index + 1}`;
            const qty = Number(fields.qtyInput?.value);
            const width = Number(fields.widthInput?.value);
            const depth = Number(fields.depthInput?.value);
            const height = Number(fields.heightInput?.value);

            if (!card._itemFile) {
                missingImages.push(itemLabel);
            }
            if (!fields.categorySelect?.value) {
                missingCategories.push(itemLabel);
            }
            if (!Number.isInteger(qty) || qty < 1) {
                invalidQuantities.push(itemLabel);
            }
            if (!isCurtainCategoryValue(fields.categorySelect?.value) && (
                !Number.isInteger(width) || width <= 0 ||
                !Number.isInteger(depth) || depth <= 0 ||
                !Number.isInteger(height) || height <= 0
            )) {
                missingDimensions.push(`Item ${index + 1}`);
            }
        });

        if (missingImages.length) {
            return {
                title: "Missing Item Images",
                message: `Upload reference images for ${missingImages.join(', ')}.`,
            };
        }

        if (missingCategories.length) {
            return {
                title: "Missing Categories",
                message: `Select a category for ${missingCategories.join(', ')}.`,
            };
        }

        if (invalidQuantities.length) {
            return {
                title: "Invalid Quantity",
                message: `Enter a quantity of 1 or more for ${invalidQuantities.join(', ')}.`,
            };
        }

        if (missingDimensions.length) {
            return {
                title: "Missing Dimensions",
                message: `Enter width, depth, and height for ${missingDimensions.join(', ')}.`,
            };
        }

        return null;
    }

    function collectFurnitureItems() {
        return getFurnitureCards()
            .filter(isFurnitureCardComplete)
            .map((card) => {
                const fields = getFurnitureFields(card);
                const qty = Number(fields.qtyInput.value);
                const width = Number(fields.widthInput.value);
                const depth = Number(fields.depthInput.value);
                const height = Number(fields.heightInput.value);
                const name = fields.nameInput?.value.trim();
                const dimsMm = isCurtainCategoryValue(fields.categorySelect?.value)
                    ? undefined
                    : {
                        width_mm: width,
                        depth_mm: depth,
                        height_mm: height,
                    };

                return {
                    client_id: card.dataset.clientId || generateFurnitureClientId(),
                    name: name || undefined,
                    category: fields.categorySelect.value,
                    qty,
                    dims_mm: dimsMm,
                    file: card._itemFile,
                };
            });
    }

    function renumberFurnitureItems() {
        getFurnitureCards().forEach((card, index) => {
            if (!card.dataset.clientId) {
                card.dataset.clientId = generateFurnitureClientId();
            }
            setFurnitureCardNumber(card, index + 1);
        });
    }

    function appendFurnitureItemCards(count) {
        if (!furnitureItemsContainer || !furnitureItemTemplate) return;

        for (let i = 0; i < count; i += 1) {
            const fragment = furnitureItemTemplate.content.cloneNode(true);
            const card = fragment.querySelector('.furniture-item-card');
            if (!card) continue;
            furnitureItemsContainer.appendChild(fragment);
            ensureFurnitureItemWiring(card);
        }
    }

    function ensureFurnitureItemWiring(card) {
        if (!card || card.dataset.furnitureBound === 'true') return;
        card.dataset.furnitureBound = 'true';
        if (!card.dataset.clientId) {
            card.dataset.clientId = generateFurnitureClientId();
        }

        const fields = getFurnitureFields(card);
        const syncAndValidate = () => checkReady();

        if (fields.categorySelect && fields.categorySelect.dataset.categoryInitialized !== 'true') {
            fields.categorySelect.selectedIndex = -1;
            fields.categorySelect.dataset.categoryInitialized = 'true';
        }
        syncCurtainDimensionFields(card);

        fields.input?.addEventListener('change', (e) => {
            if (e.target.files && e.target.files.length) {
                if (validateHomeImageUpload(e.target.files[0], "Furniture item reference")) {
                    applyFurnitureCardImage(card, e.target.files[0]);
                }
            }
        });

        fields.previewRemove?.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            resetFurnitureCardImage(card);
        });

        fields.removeBtn?.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();

            const cards = getFurnitureCards();
            if (cards.length <= INITIAL_FURNITURE_CARD_COUNT) {
                card.querySelectorAll('input, select').forEach((field) => {
                    if (field.type === 'file') {
                        field.value = '';
                    } else if (field.tagName === 'SELECT') {
                        field.selectedIndex = -1;
                    } else if (field.type === 'number') {
                        field.value = field.classList.contains('furniture-item-qty') ? '1' : '';
                    } else {
                        field.value = '';
                    }
                });
                resetFurnitureCardImage(card);
                renumberFurnitureItems();
                syncAndValidate();
                return;
            }

            card.remove();
            renumberFurnitureItems();
            syncAndValidate();
        });

        fields.dropZone?.addEventListener('dragover', (e) => {
            e.preventDefault();
            fields.dropZone.classList.add('is-dragover');
        });

        fields.dropZone?.addEventListener('dragleave', () => {
            fields.dropZone.classList.remove('is-dragover');
        });

        fields.dropZone?.addEventListener('drop', (e) => {
            e.preventDefault();
            fields.dropZone.classList.remove('is-dragover');
            if (e.dataTransfer.files && e.dataTransfer.files.length) {
                if (validateHomeImageUpload(e.dataTransfer.files[0], "Furniture item reference")) {
                    applyFurnitureCardImage(card, e.dataTransfer.files[0]);
                }
            }
        });

        [
            fields.nameInput,
            fields.categorySelect,
            fields.qtyInput,
            fields.widthInput,
            fields.depthInput,
            fields.heightInput,
        ].forEach((field) => field?.addEventListener('input', syncAndValidate));

        fields.categorySelect?.addEventListener('change', () => {
            syncCurtainDimensionFields(card);
            syncAndValidate();
        });
        fields.qtyInput?.addEventListener('change', syncAndValidate);
        fields.widthInput?.addEventListener('change', syncAndValidate);
        fields.depthInput?.addEventListener('change', syncAndValidate);
        fields.heightInput?.addEventListener('change', syncAndValidate);
    }

    function initializeFurnitureItems() {
        if (!furnitureItemsContainer) return;
        getFurnitureCards().forEach((card) => ensureFurnitureItemWiring(card));
        while (getFurnitureCards().length < INITIAL_FURNITURE_CARD_COUNT) {
            appendFurnitureItemCards(1);
        }
        renumberFurnitureItems();
        addFurnitureItemBtn?.addEventListener('click', () => {
            appendFurnitureItemCards(FURNITURE_BATCH_SIZE);
            renumberFurnitureItems();
            checkReady();
        });
    }

    initializeFurnitureItems();

    // --- 파일 처리 ---
    if (dropZone) {
        dropZone.addEventListener('click', () => fileInput.click());
        dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.style.borderColor = THEME_COLOR; });
        dropZone.addEventListener('dragleave', () => dropZone.style.borderColor = '#ccc');
        dropZone.addEventListener('drop', (e) => {
            e.preventDefault(); dropZone.style.borderColor = '#ccc';
            if (e.dataTransfer.files.length) {
                const file = e.dataTransfer.files[0];
                if (validateHomeImageUpload(file, "Main room upload")) {
                    handleFile(file);
                }
            }
        });
        fileInput.addEventListener('change', (e) => {
            if (e.target.files.length) {
                const file = e.target.files[0];
                if (validateHomeImageUpload(file, "Main room upload")) {
                    handleFile(file);
                }
            }
        });
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
            if (e.dataTransfer.files.length) {
                const file = e.dataTransfer.files[0];
                if (validateHomeImageUpload(file, "Moodboard upload")) {
                    handleMoodboardFile(file);
                }
            }
        });
        moodboardInput.addEventListener('change', (e) => {
            if (e.target.files.length) {
                const file = e.target.files[0];
                if (validateHomeImageUpload(file, "Moodboard upload")) {
                    handleMoodboardFile(file);
                }
            }
        });
    }

    function handleMoodboardFile(file) {
        if (!file.type.startsWith('image/')) { showCustomAlert("Error", "이미지 파일만 가능합니다."); return; }
        customMoodboardFile = file;
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
            e.stopPropagation(); customMoodboardFile = null; moodboardInput.value = '';
            moodboardPreviewContainer.classList.add('hidden'); moodboardDropZone.classList.remove('hidden'); checkReady();
        });
    }

    // --- [수정됨] Frontal View Generator Logic ---
    // 기존의 Floor Plan 변수들을 재활용하되, 도면(Plan) 입력은 무시합니다.

    // 버튼 텍스트 변경 (옵션)
    if (openFpGenBtn) openFpGenBtn.textContent = "Auto-Correct Perspective (Frontal View)";

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
        // fpPlanFile은 더 이상 안 씀
        fpRefFiles = [];
        if (fpRefInput) fpRefInput.value = '';

        // 미리보기 초기화
        if (fpRefPreviewContainer) {
            fpRefPreviewContainer.innerHTML = '';
            fpRefPreviewContainer.classList.add('hidden');
        }
        if (fpRefDropZone) fpRefDropZone.classList.remove('hidden');
        if (fpRefRemoveAll) fpRefRemoveAll.classList.add('hidden');

        // 결과창 초기화
        if (fpPlaceholderText) fpPlaceholderText.classList.remove('hidden');
        if (fpGenGrid) {
            fpGenGrid.innerHTML = '';
            fpGenGrid.style.display = 'none';
        }
        if (fpResultActions) fpResultActions.classList.add('hidden');
        if (fpLoading) fpLoading.classList.add('hidden');

        if (fpGenerateBtn) fpGenerateBtn.disabled = true;
    }

    // 도면 드롭존(fpPlanDropZone) 관련 코드는 지우거나 무시해도 됩니다.
    // 여기서는 "사진 업로드 드롭존(fpRefDropZone)"이 메인입니다.

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
        if (!fpRefPreviewContainer) return;
        fpRefPreviewContainer.innerHTML = '';
        if (fpRefFiles.length > 0) {
            fpRefPreviewContainer.classList.remove('hidden');
            if (fpRefRemoveAll) fpRefRemoveAll.classList.remove('hidden');
            fpRefFiles.forEach(file => {
                const reader = new FileReader();
                reader.onload = (e) => {
                    const img = document.createElement('img');
                    img.src = e.target.result;
                    img.className = 'unified-preview';
                    // 스타일 살짝 수정 (사진 여러장 보기 좋게)
                    img.style.width = "80px";
                    img.style.height = "80px";
                    img.style.objectFit = "cover";
                    fpRefPreviewContainer.appendChild(img);
                };
                reader.readAsDataURL(file);
            });
        } else {
            fpRefPreviewContainer.classList.add('hidden');
            if (fpRefRemoveAll) fpRefRemoveAll.classList.add('hidden');
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
        // 사진이 1장 이상이면 생성 가능
        if (fpGenerateBtn) fpGenerateBtn.disabled = !(fpRefFiles.length > 0);
    }

    async function performRoomGeneration() {
        if (fpRefFiles.length === 0) return;

        fpPlaceholderText.classList.add('hidden');
        fpGenGrid.innerHTML = '';
        fpGenGrid.style.display = 'none';
        fpResultActions.classList.add('hidden');
        fpLoading.classList.remove('hidden');

        fpGenerateBtn.disabled = true;
        if (fpRetryBtn) fpRetryBtn.disabled = true;

        const formData = new FormData();
        // [중요] input_photos 라는 키값으로 파일들을 보냅니다 (백엔드와 일치)
        fpRefFiles.forEach(file => {
            formData.append('input_photos', file);
        });

        try {
            // [중요] 엔드포인트 변경 (/generate-frontal-view)
            const res = await fetch('/async/generate-frontal-view', {
                method: 'POST',
                body: formData
            });
            const job = await res.json();
            if (!res.ok) throw new Error(job.error || 'Generation failed');
            if (!job.job_id) throw new Error('Job queue failed');

            const data = await pollJob(job.job_id);

            if (data.urls && data.urls.length > 0) {
                const resultUrls = data.urls;
                fpGenGrid.innerHTML = '';
                fpGenGrid.style.display = 'flex';

                resultUrls.forEach((url, idx) => {
                    const div = document.createElement('div');
                    div.className = 'detail-card';

                    const img = document.createElement('img');
                    img.src = url;
                    img.style.aspectRatio = "16 / 9";
                    img.style.objectFit = "cover";
                    img.style.backgroundColor = "transparent";
                    img.style.cursor = "zoom-in";
                    img.onclick = (e) => {
                        e.stopPropagation();
                        // openLightbox 함수가 있다면 사용
                        if (typeof openLightbox === 'function') openLightbox(url, resultUrls, idx);
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
                            const file = new File([blob], `generated_frontal_${idx}.png`, { type: 'image/png' });

                            handleFile(file); // 메인 화면으로 파일 전달
                            fpGenModal.classList.add('hidden');
                            // Silent success: no modal for setting frontal view.

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
                            // Silent success: no modal for moodboard apply.
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
        const hasBaseInputs = !!selectedFile && !!selectedRoom;
        const needsVariant = selectedRoom !== 'Customize' && selectedStyle !== 'Customize';
        const hasVariant = !needsVariant || !!selectedVariant;
        const hasFurnitureItems = getFurnitureCards().length > 0;
        renderBtn.disabled = !(hasBaseInputs && hasVariant && hasFurnitureItems);
    }
    // [추가] script.js: 메인 렌더링 로직(renderBtn) 근처에 추가

    const directUploadZone = document.getElementById('direct-upload-zone');
    const directUploadInput = document.getElementById('direct-upload-input');

    if (directUploadZone && directUploadInput) {
        directUploadZone.addEventListener('click', () => directUploadInput.click());

        directUploadZone.addEventListener('dragover', (e) => {
            e.preventDefault();
            directUploadZone.style.borderColor = THEME_COLOR;
            directUploadZone.style.backgroundColor = '#1a1a1a';
        });

        directUploadZone.addEventListener('dragleave', () => {
            directUploadZone.style.borderColor = '#444';
            directUploadZone.style.backgroundColor = '';
        });

        directUploadZone.addEventListener('drop', (e) => {
            e.preventDefault();
            directUploadZone.style.borderColor = '#444';
            directUploadZone.style.backgroundColor = '';
            if (e.dataTransfer.files.length) {
                const file = e.dataTransfer.files[0];
                if (validateHomeImageUpload(file, "Main cut upload")) {
                    handleDirectUpload(file);
                }
            }
        });

        directUploadInput.addEventListener('change', (e) => {
            if (e.target.files.length) {
                const file = e.target.files[0];
                if (validateHomeImageUpload(file, "Main cut upload")) {
                    handleDirectUpload(file);
                }
            }
        });
    }

    async function handleDirectUpload(file) {
        if (!file.type.startsWith('image/')) {
            showCustomAlert("Error", "이미지 파일만 업로드 가능합니다.");
            return;
        }

        // 로딩 표시
        loadingOverlay.classList.remove('hidden');
        if (loadingStatus) loadingStatus.textContent = "Uploading Main Cut...";
        if (timerElement) timerElement.textContent = "";

        const formData = new FormData();
        formData.append('file', file);

        try {
            // 기존에 존재하는 API 엔드포인트 활용 (/api/outputs/upload)
            const res = await fetch('/api/outputs/upload', {
                method: 'POST',
                body: formData
            });
            const data = await res.json();

            if (res.ok && data.url) {
                // 1. Result 섹션 강제 활성화
                resultSection.classList.remove('hidden');

                // 2. 이미지 매핑 (Before가 없으므로 After 이미지를 둘 다 넣어 슬라이더 오류 방지)
                resultAfter.src = data.url;
                resultBefore.src = data.url;

                // 3. 슬라이더 초기화
                // 이미지가 로드된 후 슬라이더 높이/비율을 잡기 위해 onload 사용
                resultAfter.onload = () => {
                    const isPortrait = resultAfter.naturalHeight > resultAfter.naturalWidth;
                    if (comparisonContainer) {
                        comparisonContainer.style.aspectRatio = isPortrait ? "4 / 5" : "16 / 9";
                        comparisonContainer.classList.add('direct-mode'); // 슬라이더 숨김 모드
                    }
                    initSlider();
                    resultSection.scrollIntoView({ behavior: 'smooth' });
                };

                // 4. 컨텍스트 초기화 (기존 렌더링 데이터가 섞이지 않도록)
                currentFurnitureData = null; // null이면 디테일 생성 시 백엔드가 알아서 다시 분석함
                currentMoodboardUrl = null;

                // 5. 썸네일 컨테이너 비우기
                if (thumbnailContainer) thumbnailContainer.innerHTML = '';

            } else {
                throw new Error("Upload failed");
            }
        } catch (err) {
            showCustomAlert("Error", "업로드 중 오류가 발생했습니다: " + err.message);
        } finally {
            loadingOverlay.classList.add('hidden');
        }
    }
    // --- 메인 렌더링 ---
    if (renderBtn) {
        renderBtn.addEventListener('click', async () => {
            const ready = !!selectedFile &&
                !!selectedRoom &&
                (selectedRoom === 'Customize' || selectedStyle === 'Customize' || !!selectedVariant) &&
                getFurnitureCards().length > 0;
            if (!ready) return;

            const furnitureValidationError = getFurnitureValidationError();
            if (furnitureValidationError) {
                if (furnitureValidationError.title === "Missing Dimensions") {
                    showCustomAlert("Missing Dimensions", furnitureValidationError.message);
                } else {
                    showCustomAlert(furnitureValidationError.title, furnitureValidationError.message);
                }
                return;
            }

            renderBtn.disabled = true;
            loadingOverlay.classList.remove('hidden');
            resultSection.classList.add('hidden');
            if (comparisonContainer) comparisonContainer.classList.remove('direct-mode'); // 일반 렌더링 시 슬라이더 복구

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
            const furnitureItems = collectFurnitureItems();
            formData.append('items_json', JSON.stringify(furnitureItems.map(({ file, ...item }) => item)));
            furnitureItems.forEach(({ file }) => {
                formData.append('item_images', file, file.name || 'item-image.jpg');
            });

            // [추가] 공간 수치 및 배치 지시사항 수집
            const dimensions = document.getElementById('room-dimensions')?.value || "";
            const placement = document.getElementById('placement-instructions')?.value || "";
            formData.append('dimensions', dimensions);
            formData.append('placement', placement);

            try {
                const res = await fetch('/async/render', { method: 'POST', body: formData });
                const job = await res.json();
                if (!res.ok) throw new Error(job.error || `Server error (${res.status})`);
                if (!job.job_id) throw new Error('Job queue failed');
                const data = await pollJob(job.job_id);
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
                    const card = document.createElement("div");
                    card.className = "thumb-card";

                    const img = document.createElement("img");
                    img.src = url;

                    const check = document.createElement("span");
                    check.className = "thumb-check material-symbols-outlined";
                    check.textContent = "check";

                    if (idx === 0) card.classList.add("is-selected");

                    card.onclick = () => {
                        resultAfter.src = url;
                        Array.from(thumbnailContainer.children).forEach(c => c.classList.remove("is-selected"));
                        card.classList.add("is-selected");
                    };

                    card.appendChild(img);
                    card.appendChild(check);
                    thumbnailContainer.appendChild(card);
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
            if (!url) throw new Error("Missing URL");

            // If already a blob/data URL, download directly.
            if (url.startsWith("blob:") || url.startsWith("data:")) {
                const link = document.createElement("a");
                link.href = url;
                link.download = `${prefix}_${Date.now()}.jpg`;
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);
                return;
            }

            const resolved = new URL(url, window.location.origin);
            const isLocalAsset = resolved.origin === window.location.origin
                && (resolved.pathname.startsWith("/outputs/") || resolved.pathname.startsWith("/assets/"));

            const fetchUrl = isLocalAsset
                ? resolved.toString()
                : `/download?url=${encodeURIComponent(url)}`;

            const res = await fetch(fetchUrl);
            if (!res.ok) {
                const msg = await res.text().catch(() => "");
                throw new Error(msg || `Download failed (${res.status})`);
            }
            const blob = await res.blob();
            const blobUrl = URL.createObjectURL(blob);
            const link = document.createElement("a");
            link.href = blobUrl;
            link.download = `${prefix}_${Date.now()}.jpg`;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            URL.revokeObjectURL(blobUrl);
        } catch (e) {
            console.error(e);
            showCustomAlert("Error", "다운로드 실패: " + e.message);
        }
    }

    async function generateEmptyRoom(imageUrl) {
        const res = await fetch("/async/generate-empty-room", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ image_url: imageUrl })
        });
        const job = await res.json();
        if (!res.ok) throw new Error(job.error || "Processing failed");
        if (!job.job_id) throw new Error("Job queue failed");
        const data = await pollJob(job.job_id);
        if (data.empty_room_url) return data.empty_room_url;
        throw new Error(data.error || "Empty room generation failed");
    }

    if (upscaleBtn) {
        upscaleBtn.onclick = async function () {
            const afterUrl = resultAfter ? resultAfter.src : null;
            if (!afterUrl) { showCustomAlert("Warning", "???? ????."); return; }
            const beforeUrl = resultBefore ? resultBefore.src : null;
            const isDirect = comparisonContainer && comparisonContainer.classList.contains('direct-mode');

            upscaleBtn.disabled = true;
            const originalText = upscaleBtn.innerText;
            upscaleBtn.innerText = "DOWNLOADING...";
            if (upscaleStatus) upscaleStatus.style.display = "block";

            try {
                if (isDirect) {
                    const emptyUrl = await generateEmptyRoom(afterUrl);
                    if (emptyUrl) resultBefore.src = emptyUrl;
                    await downloadFile(afterUrl, "Result_Main");
                    if (emptyUrl) {
                        setTimeout(() => {
                            downloadFile(emptyUrl, "Result_Empty");
                        }, 500);
                    }
                } else {
                    await downloadFile(afterUrl, "Result_After");
                    if (beforeUrl) {
                        setTimeout(() => {
                            downloadFile(beforeUrl, "Result_Before");
                        }, 500);
                    }
                }
            } catch (err) {
                showCustomAlert("Error", "Server Error: " + err.message);
            } finally {
                upscaleBtn.disabled = false;
                upscaleBtn.innerText = originalText;
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

    function extractFurnitureSnapshot(data) {
        if (data && Array.isArray(data.furniture_data) && data.furniture_data.length > 0) {
            return data.furniture_data;
        }
        if (data && Array.isArray(data.furniture_boxes) && data.furniture_boxes.length > 0) {
            return data.furniture_boxes;
        }
        return null;
    }

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
                    furniture_data: currentFurnitureData,
                    audience: "internal"
                };

                const res = await fetch("/generate-details", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });

                const job = await res.json();
                if (!res.ok) {
                    throw new Error(job.error || `Server Error (${res.status})`);
                }
                if (!job.job_id) throw new Error('Job queue failed');

                const data = await pollJob(job.job_id);

                if (data.details && data.details.length > 0) {
                    const furnitureSnapshot = extractFurnitureSnapshot(data);
                    if (furnitureSnapshot) {
                        currentFurnitureData = furnitureSnapshot;
                    }
                    const detailUrls = data.details.map(d => d.url);

                    data.details.forEach(item => {
                        createDetailCard(item, detailUrls);
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

    function createDetailCard(detailItem, fullList = null) {
        const card = document.createElement('div');
        card.className = 'detail-card';

        const item = (detailItem && typeof detailItem === 'object')
            ? detailItem
            : { url: detailItem };

        const url = item.url;
        const parsedIndex = Number(item.index);
        const index = Number.isFinite(parsedIndex) && parsedIndex > 0
            ? parsedIndex
            : (fullList ? (fullList.indexOf(url) + 1) : 1);
        card.dataset.detailIndex = String(index);

        const detailMeta = {
            target_key: item.target_key || null,
            target_label: item.target_label || null,
            style_name: item.style_name || null,
            target_box_source: item.target_box_source || null,
            target_box_2d: item.target_box_2d || null,
            target_source_box_2d: item.target_source_box_2d || null,
            aspect_ratio: item.aspect_ratio || null,
        };

        const img = document.createElement('img');
        img.src = url;

        const aspectRatio = String(item.aspect_ratio || '').replace(/\s/g, '');
        const styleName = String(item.style_name || '').trim();
        const isOverallShot = styleName ? !styleName.startsWith("Detail:") : index <= 3;
        const isLandscape = aspectRatio ? aspectRatio === "16:9" : isOverallShot;

        if (isLandscape) {
            img.style.aspectRatio = "16 / 9";
            card.appendChild(img);
            appendButtonsToCard(card, img, url, index, fullList, detailMeta);
            const landscapeGrid = document.getElementById('detail-grid-landscape');
            if (landscapeGrid) landscapeGrid.appendChild(card);
        } else {
            img.style.aspectRatio = "4 / 5";
            card.appendChild(img);
            appendButtonsToCard(card, img, url, index, fullList, detailMeta);
            const portraitGrid = document.getElementById('detail-grid-portrait');
            if (portraitGrid) portraitGrid.appendChild(card);
        }
    }

    function appendButtonsToCard(card, img, url, styleIndex, fullList, detailMeta = null) {
        img.onclick = () => openLightbox(url, fullList, styleIndex - 1);

        const retryBtn = document.createElement('button');
        retryBtn.className = 'detail-retry-btn';
        retryBtn.innerHTML = '&#x21bb;';
        retryBtn.title = "Retry this shot";

        retryBtn.onclick = (e) => {
            e.stopPropagation();
            const currentStyleIndex = Number(card.dataset.detailIndex || styleIndex) || styleIndex;
            retrySingleDetail(card, currentStyleIndex, detailMeta || {});
        };

        const upBtn = document.createElement('button');
        upBtn.className = 'glow-btn burgundy detail-upscale-btn';
        upBtn.textContent = 'DOWNLOAD';

        upBtn.onclick = async (e) => {
            e.stopPropagation();

            if (upBtn.disabled) return;
            upBtn.disabled = true;

            const originalText = upBtn.textContent;
            upBtn.textContent = "Downloading...";

            try {
                await downloadFile(img.src, `Detail_Shot_${styleIndex}`);
            } catch (err) {
                console.error("Critical Error:", err);
                showCustomAlert("Error", "??? ?? ??? ??????.");
            } finally {
                upBtn.textContent = originalText;
                upBtn.disabled = false;
            }
        };

        card.appendChild(retryBtn);
        card.appendChild(upBtn);
    }

    async function retrySingleDetail(cardElement, styleIndex, detailMeta = {}) {
        if (!currentDetailSourceUrl) return;

        const buttons = cardElement.querySelectorAll('button');
        buttons.forEach(btn => btn.disabled = true);

        const loader = document.createElement('div');
        loader.className = 'detail-card-loader';
        const spinner = document.createElement('div');
        spinner.className = 'mini-spinner';
        loader.appendChild(spinner);
        cardElement.appendChild(loader);

        const targetKey = (detailMeta && detailMeta.target_key) ? String(detailMeta.target_key).trim() : null;
        const targetLabel = (detailMeta && detailMeta.target_label) ? String(detailMeta.target_label).trim() : null;

        try {
            const res = await fetch("/regenerate-single-detail", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    original_image_url: currentDetailSourceUrl,
                    style_index: styleIndex,
                    style_index_mode: (targetKey || targetLabel) ? "auto" : "overall",
                    target_key: targetKey,
                    target_label: targetLabel,
                    target_box_2d: detailMeta?.target_box_2d || null,
                    target_source_box_2d: detailMeta?.target_source_box_2d || null,
                    moodboard_url: currentMoodboardUrl,
                    furniture_data: currentFurnitureData,
                    audience: "internal"
                })
            });
            const job = await res.json();
            if (!res.ok) {
                throw new Error(job.error || `Server Error (${res.status})`);
            }
            if (!job.job_id) throw new Error('Job queue failed');

            const data = await pollJob(job.job_id);
            if (data.url) {
                const imgElement = cardElement.querySelector('img');
                imgElement.src = data.url;
                imgElement.onclick = () => openLightbox(data.url, [data.url], 0);
                const furnitureSnapshot = extractFurnitureSnapshot(data);
                if (furnitureSnapshot) {
                    currentFurnitureData = furnitureSnapshot;
                }
                if (detailMeta && typeof detailMeta === 'object') {
                    if (data.target_key) detailMeta.target_key = data.target_key;
                    if (data.target_label) detailMeta.target_label = data.target_label;
                    if (data.style_name) detailMeta.style_name = data.style_name;
                    if (data.aspect_ratio) detailMeta.aspect_ratio = data.aspect_ratio;
                    if (data.target_box_source) detailMeta.target_box_source = data.target_box_source;
                    if (data.target_box_2d) detailMeta.target_box_2d = data.target_box_2d;
                    if (data.target_source_box_2d) detailMeta.target_source_box_2d = data.target_source_box_2d;
                }
                const resolvedStyleIndex = Number(data.resolved_style_index);
                if (Number.isFinite(resolvedStyleIndex) && resolvedStyleIndex > 0) {
                    cardElement.dataset.detailIndex = String(resolvedStyleIndex);
                }
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
        console.log("?? Upscaling start:", imgUrl);

        const res = await fetch("/async/upscale", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ image_url: imgUrl })
        });
        const job = await res.json();
        if (!res.ok) {
            throw new Error(job.error || `Server Error (${res.status})`);
        }
        if (!job.job_id) throw new Error('Job queue failed');

        const data = await pollJob(job.job_id);

        if (data.upscaled_url) {
            console.log("??Upscale success:", data.upscaled_url);
            const link = document.createElement("a");
            link.href = `/download?url=${encodeURIComponent(data.upscaled_url)}`;
            link.download = `${filenamePrefix}_${Date.now()}.jpg`;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            return true;
        } else {
            throw new Error(data.error || data.warning || "Unknown error during upscale");
        }
    } catch (e) {
        console.error("??Upscale failed:", e);
        return false;
    }
}

// =========================
// Video Studio Page (standalone)
// =========================
document.addEventListener('DOMContentLoaded', () => {
    const PAGE = document.body?.dataset?.page || '';
    if (PAGE !== 'video-studio') return;

    const backBtn = document.getElementById('vsBackBtn');
    const refreshBtn = document.getElementById('vsRefreshBtn');

    const dropZone = document.getElementById('vsDropZone');
    const uploadInput = document.getElementById('vsUploadInput');
    const uploadStatus = document.getElementById('vsUploadStatus');

    const gallery = document.getElementById('vsGallery');
    const clipListEl = document.getElementById('vsClipList');

    const durationSel = document.getElementById('vsDuration');
    const startBtn = document.getElementById('vsStartBtn');
    const statusEl = document.getElementById('vsStatus');

    const resultWrap = document.getElementById('vsResult');
    const preview = document.getElementById('vsPreview');
    const download = document.getElementById('vsDownload');

    const clips = []; // { url, preset, checked }

    function presetOptions(defaultPreset) {
        const opts = [
            ["main_sunlight", "Main: Sunlight Shift"],
            ["detail_pan_lr", "Detail: Pan L→R"],
            ["detail_dolly_in", "Detail: Dolly-in"],
            ["curtain_breeze", "Curtain Breeze"],
            ["static", "Almost Static"],
        ];
        return opts.map(([v, t]) => `<option value="${v}" ${v === defaultPreset ? 'selected' : ''}>${t}</option>`).join('');
    }

    function renderClips() {
        if (!clipListEl) return;
        clipListEl.innerHTML = "";

        if (clips.length === 0) {
            clipListEl.innerHTML = `<div class="vs-empty">아직 선택된 이미지가 없습니다. 왼쪽에서 클릭하거나 업로드로 추가하세요.</div>`;
            return;
        }

        clips.forEach((c, idx) => {
            const row = document.createElement('div');
            row.className = 'vs-clip-row';
            row.dataset.idx = String(idx);
            row.innerHTML = `
                <div class="vs-clip-left">
                    <input type="checkbox" class="vs-clip-check" ${c.checked ? 'checked' : ''} />
                    <div class="vs-clip-thumb"><img src="${c.url}" /></div>
                    <div class="vs-clip-meta">
                        <div class="vs-clip-title">Clip ${idx + 1}</div>
                        <div class="vs-clip-url">${c.url}</div>
                    </div>
                </div>
                <div class="vs-clip-right">
                    <select class="vs-clip-preset">${presetOptions(c.preset || "detail_pan_lr")}</select>
                    <div class="vs-clip-actions">
                        <button class="vs-mini-btn" data-act="up" title="Move up">↑</button>
                        <button class="vs-mini-btn" data-act="down" title="Move down">↓</button>
                        <button class="vs-mini-btn danger" data-act="remove" title="Remove">✕</button>
                    </div>
                </div>
            `;

            // wire
            row.querySelector('.vs-clip-check')?.addEventListener('change', (e) => {
                clips[idx].checked = !!e.target.checked;
            });
            row.querySelector('.vs-clip-preset')?.addEventListener('change', (e) => {
                clips[idx].preset = e.target.value;
            });
            row.querySelectorAll('.vs-mini-btn')?.forEach(btn => {
                btn.addEventListener('click', () => {
                    const act = btn.dataset.act;
                    if (act === "remove") {
                        clips.splice(idx, 1);
                        renderClips();
                        return;
                    }
                    if (act === "up" && idx > 0) {
                        const tmp = clips[idx - 1];
                        clips[idx - 1] = clips[idx];
                        clips[idx] = tmp;
                        renderClips();
                        return;
                    }
                    if (act === "down" && idx < clips.length - 1) {
                        const tmp = clips[idx + 1];
                        clips[idx + 1] = clips[idx];
                        clips[idx] = tmp;
                        renderClips();
                        return;
                    }
                });
            });

            clipListEl.appendChild(row);
        });
    }

    function addClip(url, preset = "detail_pan_lr") {
        if (!url) return;
        // avoid duplicates
        if (clips.some(c => c.url === url)) return;
        clips.push({ url, preset, checked: true });
        renderClips();
    }

    async function loadGallery() {
        if (!gallery) return;
        gallery.innerHTML = `<div class="vs-loading">불러오는 중...</div>`;

        try {
            const res = await fetch('/api/outputs/list?limit=200');
            const data = await res.json();
            const items = data.items || [];

            if (items.length === 0) {
                gallery.innerHTML = `<div class="vs-empty">최근 이미지가 없습니다.</div>`;
                return;
            }

            gallery.innerHTML = "";
            items.forEach(it => {
                const btn = document.createElement('button');
                btn.type = "button";
                btn.className = "vs-gallery-item";
                btn.title = "클립 목록에 추가";
                btn.innerHTML = `<img src="${it.url}" /><div class="vs-gallery-cap">${it.filename}</div>`;
                btn.addEventListener('click', () => addClip(it.url, "detail_pan_lr"));
                gallery.appendChild(btn);
            });
        } catch (e) {
            console.error(e);
            gallery.innerHTML = `<div class="vs-empty">목록을 불러오지 못했습니다.</div>`;
        }
    }

    async function uploadFiles(fileList) {
        if (!fileList || fileList.length === 0) return;
        if (uploadStatus) uploadStatus.textContent = `업로드 중... (${fileList.length}개)`;

        for (const file of Array.from(fileList)) {
            if (!file.type.startsWith('image/')) continue;
            try {
                const fd = new FormData();
                fd.append('file', file);
                const res = await fetch('/api/outputs/upload', { method: 'POST', body: fd });
                const data = await res.json();
                if (res.ok && data.url) {
                    addClip(data.url, "detail_pan_lr");
                }
            } catch (e) {
                console.error(e);
            }
        }

        if (uploadStatus) uploadStatus.textContent = "";
        await loadGallery();
    }

    async function startJob() {
        if (!startBtn || !statusEl) return;

        const selected = clips.filter(c => c.checked).map(c => ({
            url: c.url,
            preset: c.preset || "detail_pan_lr",
        }));

        if (selected.length === 0) {
            statusEl.textContent = "선택된 이미지가 없습니다.";
            return;
        }

        startBtn.disabled = true;
        statusEl.textContent = "Creating job...";

        try {
            const res = await fetch('/video-mvp/create', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    clips: selected,
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
            statusEl.textContent = `Error: ${e.message}`;
        } finally {
            startBtn.disabled = false;
        }
    }

    async function pollJob(jobId) {
        statusEl.textContent = `Running... (${jobId})`;

        return new Promise((resolve) => {
            const iv = setInterval(async () => {
                try {
                    const r = await fetch(`/video-mvp/status/${jobId}`);
                    if (!r.ok) return;

                    const st = await r.json();
                    const msg = st.message || st.status || "";
                    const prog = (st.progress != null) ? `${st.progress}%` : "";
                    statusEl.textContent = `${msg} ${prog}`;

                    if (st.status === "COMPLETED" && st.result_url) {
                        clearInterval(iv);
                        if (preview) preview.src = st.result_url;
                        if (download) download.href = `/download?url=${encodeURIComponent(st.result_url)}`;
                        if (resultWrap) resultWrap.classList.remove('hidden');
                        resolve();
                    }

                    if (st.status === "FAILED") {
                        clearInterval(iv);
                        statusEl.textContent = `FAILED: ${st.error || 'unknown error'}`;
                        resolve();
                    }
                } catch (e) {
                    console.error(e);
                }
            }, 2000);
        });
    }

    // events
    backBtn?.addEventListener('click', () => window.location.href = '/');
    refreshBtn?.addEventListener('click', loadGallery);

    if (dropZone && uploadInput) {
        dropZone.addEventListener('click', () => uploadInput.click());
        dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.style.borderColor = "#ffffff"; });
        dropZone.addEventListener('dragleave', () => { dropZone.style.borderColor = "#ccc"; });
        dropZone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropZone.style.borderColor = "#ccc";
            uploadFiles(e.dataTransfer.files);
        });
        uploadInput.addEventListener('change', (e) => uploadFiles(e.target.files));
    }

    startBtn?.addEventListener('click', startJob);

    // init
    if (resultWrap) resultWrap.classList.add('hidden');
    renderClips();
    loadGallery();
});
