document.addEventListener('DOMContentLoaded', () => {
    console.log("✅ script.js 로드됨 (Workflow Updated)");

    // 요소 선택
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

    const resultSection = document.getElementById('result-section');
    const resultBefore = document.getElementById('result-before');
    const resultAfter = document.getElementById('result-after');
    const compareSlider = document.getElementById('compare-slider');
    const sliderHandle = document.querySelector('.slider-handle');
    const comparisonContainer = document.querySelector('.comparison-container');

    const thumbnailContainer = document.getElementById('thumbnailContainer');
    const upscaleBtn = document.getElementById('upscaleBtn');
    const upscaleStatus = document.getElementById('upscaleStatus');

    const THEME_COLOR = "#ffffff";

    let selectedFile = null;
    let selectedRoom = null;
    let selectedStyle = null;
    let selectedVariant = null;

    // --- 1. 초기화 및 데이터 로드 ---
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
        if (!file.type.startsWith('image/')) return alert('이미지 파일만 가능합니다.');
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

    // --- 3. 렌더링 요청 ---
    if (renderBtn) {
        renderBtn.addEventListener('click', async () => {
            if (!selectedFile || !selectedRoom || !selectedStyle || !selectedVariant) return;

            loadingOverlay.classList.remove('hidden');
            resultSection.classList.add('hidden');

            const timerElement = document.getElementById('timer');
            const statusText = document.getElementById('loading-status');

            let startTime = Date.now();

            if (timerElement) timerElement.textContent = "0s";
            if (statusText) statusText.textContent = "Cleaning the room...";

            const timerInterval = setInterval(() => {
                let elapsedSeconds = Math.floor((Date.now() - startTime) / 1000);

                if (timerElement) {
                    timerElement.textContent = `${elapsedSeconds}s`;
                }

                // [수정] 새로운 워크플로우에 맞춘 멘트 (3단계)
                if (statusText) {
                    if (elapsedSeconds < 10) {
                        statusText.textContent = "Cleaning the room...";
                    } else if (elapsedSeconds < 30) {
                        statusText.textContent = "Designing Variation (1/3)...";
                    } else {
                        statusText.textContent = "Creating Comparison View...";
                    }
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

                // Step 3에서 만든 빈 방 이미지가 여기로 들어옵니다
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
                alert("Error: " + err.message);
            }
        });
    }

    // --- 4. 슬라이더 기능 ---
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

    // --- 5. 업스케일링 ---
    if (upscaleBtn) {
        upscaleBtn.onclick = function () {
            const currentImgUrl = resultAfter ? resultAfter.src : null;
            if (!currentImgUrl) return alert("이미지가 없습니다.");

            upscaleBtn.disabled = true;
            upscaleBtn.innerText = "PROCESSING...";
            upscaleBtn.style.opacity = "0.7";
            if (upscaleStatus) upscaleStatus.style.display = "block";

            fetch("/upscale", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ image_url: currentImgUrl })
            })
                .then(res => res.json())
                .then(data => {
                    if (data.warning) {
                        alert("⚠️ " + data.warning);
                    }

                    if (data.upscaled_url) {
                        resultAfter.src = data.upscaled_url;

                        const link = document.createElement("a");
                        link.href = data.upscaled_url;
                        link.download = data.warning ? "Original_Result.jpg" : "HQ_Interior_Result.jpg";
                        document.body.appendChild(link);
                        link.click();
                        document.body.removeChild(link);

                        if (!data.warning) {
                            alert("DOWNLOAD COMPLETE");
                        }
                    } else {
                        alert("시스템 에러: 이미지를 처리할 수 없습니다.");
                    }
                })
                .catch(err => alert("Server Error: " + err))
                .finally(() => {
                    upscaleBtn.disabled = false;
                    upscaleBtn.innerText = "UPSCALE & DOWNLOAD";
                    upscaleBtn.style.opacity = "1";
                    if (upscaleStatus) upscaleStatus.style.display = "none";
                });
        };
    }
});