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
        
        document.querySelectorAll('.style-card.selected').forEach(el => el.classList.remove('selected'));
    }

    function loadRoomTypes() {
        fetch('/room-types')
            .then(res => res.json())
            .then(types => {
                roomGrid.innerHTML = '';
                types.forEach(type => {
                    // Room은 이름 그대로 파일명 추측 (예: Living Room -> livingroom.jpg)
                    const card = createCard(type, () => selectRoom(type, card));
                    roomGrid.appendChild(card);
                });
            });
    }

    function selectRoom(type, cardElement) {
        selectedRoom = type;
        highlightSelection(roomGrid, cardElement);
        
        fetch(`/styles/${type}`)
            .then(res => res.json())
            .then(styles => {
                styleGrid.innerHTML = '';
                styles.forEach(style => {
                    // Style도 이름 그대로 파일명 추측
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
        
        // [핵심 수정] Step 3 (Variant) 이미지 경로 생성 로직
        variantGrid.innerHTML = '';
        ['1', '2', '3'].forEach(v => {
            // 파일명 규칙: {방이름}_{스타일}_{번호}.png (로그 기반)
            // 공백제거: "Living Room" -> "livingroom"
            const safeRoom = selectedRoom.toLowerCase().replace(/ /g, '');
            // 공백->언더바: "Oriental" -> "oriental", "Modern Luxury" -> "modern_luxury" (추정)
            const safeStyle = selectedStyle.toLowerCase().replace(/ /g, '_');
            
            // 이미지 경로 직접 지정
            const customImgPath = `/static/thumbnails/${safeRoom}_${safeStyle}_${v}.png`;
            
            const vCard = createCard(`Option ${v}`, () => selectVariant(v, vCard), customImgPath);
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

    // [수정된 createCard] customImageUrl 파라미터 추가
    function createCard(text, onClick, customImageUrl = null) {
        const div = document.createElement('div');
        div.className = 'style-card';
        div.onclick = onClick;

        const img = document.createElement('img');
        
        if (customImageUrl) {
            // 1. Variant 처럼 직접 경로를 준 경우
            img.src = customImageUrl;
        } else {
            // 2. Room/Style 처럼 텍스트로 추측하는 경우
            // (기본적으로 공백 제거 후 소문자로)
            let safeName = text.toLowerCase().replace(/ /g, '');
            img.src = `/static/thumbnails/${safeName}.jpg`;
        }
        
        // 스타일 설정
        img.style.width = "100%";
        img.style.height = "120px";
        img.style.objectFit = "cover";
        img.style.borderRadius = "8px";
        img.style.marginBottom = "8px";
        img.style.display = "block";

        // 에러 처리 (이미지 없으면 숨기기 or png 재시도)
        img.onerror = function() {
            // 만약 jpg였는데 실패했으면 png로, png였으면 jpg로 한번씩 교차 시도 가능하지만
            // 여기선 간단히 jpg -> png 시도 로직만 유지
            if (this.src.endsWith('.jpg')) {
                this.src = this.src.replace('.jpg', '.png');
            } else if (!this.src.includes('_retry')) {
                 // 무한루프 방지하며 숨김 처리
                 this.style.display = 'none';
            }
        };

        const span = document.createElement('div');
        span.textContent = text;
        span.style.fontWeight = "600";
        span.style.fontSize = "1rem";
        span.style.textAlign = "center";

        div.appendChild(img);
        div.appendChild(span);
        
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

            const resultAfter = document.getElementById('result-after');
            const resultBefore = document.getElementById('result-before');
            
            resultBefore.src = data.empty_room_url;
            
            const results = data.result_urls || [];
            const thumbBox = document.getElementById('thumbnailContainer');
            thumbBox.innerHTML = "";

            if (results.length > 0) {
                resultAfter.src = results[0]; 

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
                        resultAfter.src = url;
                        Array.from(thumbBox.children).forEach(c => c.style.border = "3px solid transparent");
                        thumb.style.border = "3px solid #007AFF";
                    };
                    thumbBox.appendChild(thumb);
                });
            }

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

    // --- 5. 업스케일 & 다운로드 기능 ---
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
