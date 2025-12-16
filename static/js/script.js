document.addEventListener('DOMContentLoaded', () => {
    // 요소 선택
    const dropZone = document.querySelector('.drop-zone');
    const fileInput = document.getElementById('file-input');
    const previewContainer = document.getElementById('preview-container');
    const imagePreview = document.getElementById('image-preview');
    const removeBtn = document.getElementById('remove-image');
    
    const roomSection = document.getElementById('room-section');
    const styleSection = document.getElementById('style-section');
    const variantSection = document.getElementById('variant-section');
    
    const roomGrid = document.getElementById('room-grid');
    const styleGrid = document.getElementById('style-grid');
    const variantGrid = document.getElementById('variant-grid');
    
    const renderBtn = document.getElementById('render-btn');
    const loadingOverlay = document.getElementById('loading-overlay');
    const resultSection = document.getElementById('result-section');
    const timerElement = document.getElementById('timer');

    // 상태 변수
    let selectedFile = null;
    let selectedRoom = null;
    let selectedStyle = null;
    let selectedVariant = null;

    // --- 1. 파일 업로드 핸들링 ---
    dropZone.addEventListener('click', () => fileInput.click());

    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.style.borderColor = '#007AFF';
    });

    dropZone.addEventListener('dragleave', () => {
        dropZone.style.borderColor = '#ccc';
    });

    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.style.borderColor = '#ccc';
        if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
    });

    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length) handleFile(e.target.files[0]);
    });

    function handleFile(file) {
        selectedFile = file;
        const reader = new FileReader();
        reader.onload = (e) => {
            imagePreview.src = e.target.result;
            dropZone.classList.add('hidden');
            previewContainer.classList.remove('hidden');
            loadRoomTypes(); // 파일 업로드 후 Step 1 로드
        };
        reader.readAsDataURL(file);
    }

    removeBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        selectedFile = null;
        fileInput.value = '';
        previewContainer.classList.add('hidden');
        dropZone.classList.remove('hidden');
        resetSelections();
    });

    // --- 2. 데이터 로드 및 UI 생성 ---
    function resetSelections() {
        selectedRoom = null;
        selectedStyle = null;
        selectedVariant = null;
        styleSection.classList.add('hidden');
        variantSection.classList.add('hidden');
        renderBtn.disabled = true;
        
        // 기존 선택 제거
        document.querySelectorAll('.style-card.selected').forEach(el => el.classList.remove('selected'));
    }

    function loadRoomTypes() {
        fetch('/room-types')
            .then(res => res.json())
            .then(types => {
                roomGrid.innerHTML = '';
                types.forEach(type => {
                    const card = createCard(type, () => selectRoom(type, card));
                    roomGrid.appendChild(card);
                });
            });
    }

    function selectRoom(type, cardElement) {
        selectedRoom = type;
        highlightSelection(roomGrid, cardElement);
        
        // 다음 단계 로드
        fetch(`/styles/${type}`)
            .then(res => res.json())
            .then(styles => {
                styleGrid.innerHTML = '';
                styles.forEach(style => {
                    const sCard = createCard(style, () => selectStyle(style, sCard));
                    styleGrid.appendChild(sCard);
                });
                styleSection.classList.remove('hidden');
                styleSection.scrollIntoView({ behavior: 'smooth' });
            });
    }

    function selectStyle(style, cardElement) {
        selectedStyle = style;
        highlightSelection(styleGrid, cardElement);
        
        // Step 3 (Variant) 생성
        variantGrid.innerHTML = '';
        ['1', '2', '3'].forEach(v => {
            const vCard = createCard(`Option ${v}`, () => selectVariant(v, vCard));
            variantGrid.appendChild(vCard);
        });
        variantSection.classList.remove('hidden');
        variantSection.scrollIntoView({ behavior: 'smooth' });
    }

    function selectVariant(variant, cardElement) {
        selectedVariant = variant;
        highlightSelection(variantGrid, cardElement);
        renderBtn.disabled = false;
        renderBtn.scrollIntoView({ behavior: 'smooth' });
    }

    // [복구 완료] 원래 주셨던 코드대로 텍스트만 들어가는 버전입니다.
    function createCard(text, onClick) {
        const div = document.createElement('div');
        div.className = 'style-card';
        div.textContent = text;
        div.onclick = onClick;
        return div;
    }

    function highlightSelection(grid, activeCard) {
        Array.from(grid.children).forEach(c => c.classList.remove('selected'));
        activeCard.classList.add('selected');
    }

    // --- 3. 렌더링 로직 ---
    renderBtn.addEventListener('click', () => {
        if (!selectedFile || !selectedRoom || !selectedStyle || !selectedVariant) return;

        loadingOverlay.classList.remove('hidden');
        resultSection.classList.add('hidden');
        
        // 타이머 시작
        let startTime = Date.now();
        const timerInterval = setInterval(() => {
            let elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
            timerElement.textContent = `${elapsed}s`;
        }, 100);

        const formData = new FormData();
        formData.append('file', selectedFile);
        formData.append('room', selectedRoom);
        formData.append('style', selectedStyle);
        formData.append('variant', selectedVariant);

        fetch('/render', {
            method: 'POST',
            body: formData
        })
        .then(response => response.json())
        .then(data => {
            clearInterval(timerInterval);
            loadingOverlay.classList.add('hidden');
            resultSection.classList.remove('hidden');

            // [Before & After 이미지 설정]
            const resultAfter = document.getElementById('result-after');
            const resultBefore = document.getElementById('result-before');
            
            resultBefore.src = data.empty_room_url; // 빈 방
            
            // 결과 리스트 처리
            const results = data.result_urls || [];
            const thumbBox = document.getElementById('thumbnailContainer');
            thumbBox.innerHTML = "";

            if (results.length > 0) {
                // 첫 번째 결과 기본 표시
                resultAfter.src = results[0]; 

                // 썸네일 생성
                results.forEach((url, index) => {
                    const thumb = document.createElement("img");
                    thumb.src = url;
                    thumb.style.width = "80px";
                    thumb.style.height = "80px";
                    thumb.style.objectFit = "cover";
                    thumb.style.cursor = "pointer";
                    thumb.style.borderRadius = "8px";
                    thumb.style.border = index === 0 ? "3px solid #007AFF" : "3px solid transparent";
                    
                    thumb.onclick = () => {
                        // 메인 이미지 변경
                        resultAfter.src = url;
                        // 썸네일 스타일 업데이트
                        Array.from(thumbBox.children).forEach(c => c.style.border = "3px solid transparent");
                        thumb.style.border = "3px solid #007AFF";
                    };
                    thumbBox.appendChild(thumb);
                });
            }

            // 슬라이더 초기화
            initSlider(); 
            resultSection.scrollIntoView({ behavior: 'smooth' });
        })
        .catch(err => {
            clearInterval(timerInterval);
            loadingOverlay.classList.add('hidden');
            alert("Error rendering image: " + err);
        });
    });

    // --- 4. 슬라이더 기능 ---
    function initSlider() {
        const slider = document.getElementById('compare-slider');
        const afterWrapper = document.querySelector('.image-wrapper.after');
        
        slider.oninput = function() {
            afterWrapper.style.width = this.value + "%";
        };
        
        slider.value = 50;
        afterWrapper.style.width = "50%";
    }

    // --- 5. 업스케일 & 다운로드 기능 (이건 유지해야 기능이 작동합니다) ---
    const upscaleBtn = document.getElementById("upscaleBtn");
    if(upscaleBtn) {
        upscaleBtn.onclick = function() {
            const currentImgUrl = document.getElementById("result-after").src;
            const statusText = document.getElementById("upscaleStatus");
            
            if (!currentImgUrl) return alert("No image to upscale.");

            upscaleBtn.disabled = true;
            upscaleBtn.innerText = "⏳ Processing...";
            upscaleBtn.style.opacity = "0.7";
            statusText.style.display = "block";

            fetch("/upscale", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ image_url: currentImgUrl })
            })
            .then(res => res.json())
            .then(data => {
                if (data.upscaled_url) {
                    document.getElementById("result-after").src = data.upscaled_url;
                    
                    const link = document.createElement("a");
                    link.href = data.upscaled_url;
                    link.download = "HQ_Interior_Result.jpg";
                    document.body.appendChild(link);
                    link.click();
                    document.body.removeChild(link);
                    
                    alert("✨ Upscale Complete! Image downloaded.");
                } else {
                    alert("Upscale failed: " + (data.error || "Unknown error"));
                }
            })
            .catch(err => alert("Server Error: " + err))
            .finally(() => {
                upscaleBtn.disabled = false;
                upscaleBtn.innerText = "✨ Upscale & Download";
                upscaleBtn.style.opacity = "1";
                statusText.style.display = "none";
            });
        };
    }
});
