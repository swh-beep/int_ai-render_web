document.addEventListener('DOMContentLoaded', () => {
    console.log("✅ script.js 로드됨 (Custom Modal Applied)");

    // --- [1] 통합 모달 시스템 설정 ---
    const globalModal = document.getElementById('global-modal');
    const modalTitle = document.getElementById('modal-title');
    const modalMsg = document.getElementById('modal-msg');
    const modalOkBtn = document.getElementById('modal-ok-btn');
    const modalCancelBtn = document.getElementById('modal-cancel-btn');

    // 1. 단순 알림창 (Alert 대체)
    function showCustomAlert(title, message) {
        modalTitle.textContent = title;
        modalMsg.innerHTML = message.replace(/\n/g, '<br>');
        modalCancelBtn.classList.add('hidden');
        modalOkBtn.textContent = "OK";

        modalOkBtn.onclick = () => globalModal.classList.add('hidden');

        globalModal.classList.remove('hidden');
    }

    // 2. 확인/취소창 (Confirm 대체)
    function showCustomConfirm(title, message, onConfirm) {
        modalTitle.textContent = title;
        modalMsg.innerHTML = message.replace(/\n/g, '<br>');
        modalCancelBtn.classList.remove('hidden');
        modalOkBtn.textContent = "Confirm";

        modalOkBtn.onclick = () => {
            globalModal.classList.add('hidden');
            if (onConfirm) onConfirm();
        };

        modalCancelBtn.onclick = () => {
            globalModal.classList.add('hidden');
        };

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

    const THEME_COLOR = "#ffffff";

    let selectedFile = null;
    let selectedRoom = null;
    let selectedStyle = null;
    let selectedVariant = null;
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

        variantGrid.innerHTML = '';
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
        dropZone.addEventListener('dragover', (e) => {
            e.preventDefault();
            dropZone.style.borderColor = THEME_COLOR;
        });
        dropZone.addEventListener('dragleave', () => dropZone.style.borderColor = '#ccc');
        dropZone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropZone.style.borderColor = '#ccc';
            if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
        });
        fileInput.addEventListener('change', (e) => {
            if (e.target.files.length) handleFile(e.target.files[0]);
        });
    }

    function handleFile(file) {
        if (!file.type.startsWith('image/')) {
            showCustomAlert("Error", "이미지 파일만 가능합니다.");
            return;
        }
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
            e.stopPropagation();
            selectedFile = null;
            fileInput.value = '';
            previewContainer.classList.add('hidden');
            dropZone.classList.remove('hidden');
            checkReady();
        });
    }

    function checkReady() {
        if (renderBtn) {
            renderBtn.disabled = !(selectedFile && selectedRoom && selectedStyle && selectedVariant);
        }
    }

    // --- 메인 렌더링 ---
    if (renderBtn) {
        renderBtn.addEventListener('click', async () => {
            if (!selectedFile || !selectedRoom || !selectedStyle || !selectedVariant) return;

            loadingOverlay.classList.remove('hidden');
            resultSection.classList.add('hidden');

            let startTime = Date.now();
            if (timerElement) timerElement.textContent = "0s";
            if (loadingStatus) loadingStatus.textContent = "Cleaning the room...";

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
            formData.append('variant', selectedVariant);

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
                results.forEach((url, idx) => {
                    const img = document.createElement("img");
                    img.src = url;
                    img.style.width = "142px";
                    img.style.height = "80px";
                    img.style.objectFit = "cover";
                    img.style.cursor = "pointer";
                    img.style.borderRadius = "8px";
                    img.style.border = idx === 0 ? `3px solid ${THEME_COLOR}` : "3px solid transparent";

                    img.onclick = () => {
                        resultAfter.src = url;
                        Array.from(thumbnailContainer.children).forEach(c => c.style.border = "3px solid transparent");
                        img.style.border = `3px solid ${THEME_COLOR}`;
                    };
                    thumbnailContainer.appendChild(img);
                });

                initSlider();
                resultSection.scrollIntoView({ behavior: 'smooth' });

            } catch (err) {
                clearInterval(timerInterval);
                loadingOverlay.classList.add('hidden');
                showCustomAlert("Error", err.message);
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

            if (!afterUrl || !beforeUrl) {
                showCustomAlert("Warning", "이미지가 없습니다.");
                return;
            }

            upscaleBtn.disabled = true;
            upscaleBtn.innerText = "PROCESSING...";
            upscaleBtn.style.opacity = "0.7";
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
                upscaleBtn.style.opacity = "1";
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

    function hideLoading() {
        loadingOverlay.classList.add('hidden');
    }

    if (detailBtn) {
        detailBtn.onclick = async () => {
            const currentImgUrl = resultAfter ? resultAfter.src : null;
            if (!currentImgUrl) {
                showCustomAlert("Warning", "디테일 컷을 만들 이미지가 없습니다.");
                return;
            }
            currentDetailSourceUrl = currentImgUrl;

            detailSection.classList.add('hidden');
            detailGrid.innerHTML = '';

            const startTime = showLoading("Setting up Virtual Cameras...");
            const msgs = ["Setting up Virtual Cameras...", "Capturing Light & Textures...", "Developing Editorial Shots...", "Finalizing Your Portfolio..."];
            let step = 0;

            const msgInterval = setInterval(() => {
                let elapsed = Math.floor((Date.now() - startTime) / 1000);
                if (timerElement) timerElement.textContent = `${elapsed}s`;
                step = (step + 1) % msgs.length;
                if (step < msgs.length && loadingStatus) loadingStatus.textContent = msgs[step];
            }, 8000);

            try {
                const res = await fetch("/generate-details", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ image_url: currentImgUrl })
                });

                const data = await res.json();

                if (res.ok && data.details && data.details.length > 0) {
                    data.details.forEach(item => {
                        createDetailCard(item.url, item.index);
                    });
                    detailSection.classList.remove('hidden');
                    setTimeout(() => detailSection.scrollIntoView({ behavior: 'smooth' }), 100);
                } else {
                    showCustomAlert("Failed", "디테일 뷰 생성 실패");
                }
            } catch (err) {
                showCustomAlert("Error", "Error: " + err.message);
            } finally {
                clearInterval(msgInterval);
                hideLoading();
            }
        };
    }

    function createDetailCard(url, styleIndex) {
        const card = document.createElement('div');
        card.className = 'detail-card';

        const img = document.createElement('img');
        img.src = url;
        img.onclick = () => openLightbox(url);

        const retryBtn = document.createElement('button');
        retryBtn.className = 'detail-retry-btn';
        retryBtn.innerHTML = '&#x21bb;';
        retryBtn.title = "Retry this shot";

        // [수정됨] 커스텀 모달 사용
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
            upBtn.disabled = true;
            upBtn.textContent = "Processing...";
            await upscaleAndDownload(img.src, `Detail_Shot_${styleIndex}`);
            upBtn.disabled = false;
            upBtn.textContent = "UPSCALE & DOWNLOAD";
            showCustomAlert("Success", "Detail Shot Downloaded");
        };

        card.appendChild(img);
        card.appendChild(retryBtn);
        card.appendChild(upBtn);
        detailGrid.appendChild(card);
    }

    async function retrySingleDetail(cardElement, styleIndex) {
        if (!currentDetailSourceUrl) return;

        const startTime = showLoading("Regenerating single shot...");
        const imgElement = cardElement.querySelector('img');

        const timerInterval = setInterval(() => {
            let elapsed = Math.floor((Date.now() - startTime) / 1000);
            if (timerElement) timerElement.textContent = `${elapsed}s`;
        }, 1000);

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
                imgElement.src = data.url;
                imgElement.onclick = () => openLightbox(data.url);
            } else {
                showCustomAlert("Failed", "재생성 실패: " + (data.error || "Unknown error"));
            }
        } catch (e) {
            showCustomAlert("Error", "Error: " + e.message);
        } finally {
            clearInterval(timerInterval);
            hideLoading();
        }
    }

    // Lightbox
    function openLightbox(src) {
        lightboxImg.src = src;
        lightbox.classList.remove('hidden');
    }

    if (closeLightbox) {
        closeLightbox.onclick = () => lightbox.classList.add('hidden');
    }
    if (lightbox) {
        lightbox.onclick = (e) => {
            if (e.target === lightbox) lightbox.classList.add('hidden');
        };
    }
});