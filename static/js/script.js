document.addEventListener('DOMContentLoaded', () => {
    console.log("✅ script.js 로드됨 (수정버전)");

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
    
    // 결과창 관련 요소들
    const resultSection = document.getElementById('result-section');
    const resultBefore = document.getElementById('result-before');
    const resultAfter = document.getElementById('result-after');
    const compareSlider = document.getElementById('compare-slider');
    const comparisonContainer = document.querySelector('.comparison-container');
    
    // [NEW] 새로 추가된 요소 (썸네일 & 업스케일 버튼)
    const thumbnailContainer = document.getElementById('thumbnailContainer');
    const upscaleBtn = document.getElementById('upscaleBtn');
    const upscaleStatus = document.getElementById('upscaleStatus');

    let selectedFile = null;
    let selectedRoom = null;
    let selectedStyle = null;
    let selectedVariant = null;

    // ---------------------------------------------------------
    // [초기화] 룸 타입 불러오기 (원본 유지)
    // ---------------------------------------------------------
    fetch('/room-types')
        .then(res => {
            if (!res.ok) throw new Error(`서버 연결 실패 (${res.status})`);
            return res.json();
        })
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
        .catch(err => {
            console.error(err);
            if (roomGrid) roomGrid.innerHTML = `<p style="color:red">서버 연결 실패.</p>`;
        });

    // ---------------------------------------------------------
    // 선택 핸들러 (원본 유지)
    // ---------------------------------------------------------
    function selectRoom(room, btn) {
        selectedRoom = room;
        selectedStyle = null;
        selectedVariant = null;

        document.querySelectorAll('#room-grid .style-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');

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
                if (styleSection) styleSection.classList.remove('hidden');
                if (variantSection) variantSection.classList.add('hidden');
                checkReady();
            });
    }

    // [원본 유지] Step 3: 10개 옵션 생성 로직
    function selectStyle(style, btn) {
        selectedStyle = style;
        selectedVariant = null;

        document.querySelectorAll('#style-grid .style-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');

        variantGrid.innerHTML = '';

        // 1번부터 10번까지 반복 (기존 로직 복구)
        for (let i = 1; i <= 10; i++) {
            const variantBtn = document.createElement('div');
            variantBtn.className = 'variant-img-btn';
            variantBtn.setAttribute('data-index', i);

            const img = document.createElement('img');
            const safeRoom = selectedRoom.toLowerCase().replace(/ /g, '');
            const safeStyle = style.toLowerCase().replace(/ /g, '-').replace(/_/g, '-');
            const imgName = `${safeRoom}_${safeStyle}_${i}.png`;

            img.src = `/static/thumbnails/${imgName}`;
            img.alt = `Variant ${i}`;
            img.onerror = function () {
                variantBtn.classList.add('no-image');
            };

            const label = document.createElement('span');
            label.className = 'variant-label';
            label.textContent = i;

            variantBtn.appendChild(img);
            variantBtn.appendChild(label);

            variantBtn.onclick = () => {
                selectedVariant = i.toString();
                document.querySelectorAll('.variant-img-btn').forEach(b => b.classList.remove('active'));
                variantBtn.classList.add('active');
                checkReady();
            };

            variantGrid.appendChild(variantBtn);
        }

        if (variantSection) variantSection.classList.remove('hidden');
        checkReady();
    }

    // ---------------------------------------------------------
    // 파일 업로드 (원본 유지)
    // ---------------------------------------------------------
    if (dropZone) {
        dropZone.addEventListener('click', () => fileInput.click());
        dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('dragover'); });
        dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
        dropZone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropZone.classList.remove('dragover');
            if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
        });
        fileInput.addEventListener('change', (e) => {
            if (e.target.files.length) handleFile(e.target.files[0]);
        });
    }

    function handleFile(file) {
        if (!file.type.startsWith('image/')) {
            alert('이미지 파일만 가능합니다.');
            return;
        }
        selectedFile = file;
        const reader = new FileReader();
        reader.onload = (e) => {
            if (imagePreview) imagePreview.src = e.target.result;
            if (previewContainer) previewContainer.classList.remove('hidden');
            if (dropZone) dropZone.classList.add('hidden');
            checkReady();
        };
        reader.readAsDataURL(file);
    }

    if (removeBtn) {
        removeBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            selectedFile = null;
            fileInput.value = '';
            if (previewContainer) previewContainer.classList.add('hidden');
            if (dropZone) dropZone.classList.remove('hidden');
            checkReady();
        });
    }

    function checkReady() {
        if (renderBtn) renderBtn.disabled = !(selectedFile && selectedRoom && selectedStyle && selectedVariant);
    }

    // ---------------------------------------------------------
    // [핵심 변경] 렌더링 요청 로직 (3장 처리 + 썸네일 표시)
    // ---------------------------------------------------------
    if (renderBtn) {
        renderBtn.addEventListener('click', async () => {
            if (!selectedFile || !selectedRoom || !selectedStyle || !selectedVariant) return;

            if (loadingOverlay) loadingOverlay.classList.remove('hidden');
            
            // 타이머
            const timerElement = document.getElementById('timer');
            let startTime = Date.now();
            if (timerElement) timerElement.textContent = "0.0s";

            const timerInterval = setInterval(() => {
                if (timerElement) {
                    const elapsedTime = (Date.now() - startTime) / 1000;
                    timerElement.textContent = `${elapsedTime.toFixed(1)}s`;
                }
            }, 100);

            const formData = new FormData();
            formData.append('file', selectedFile);
            formData.append('room', selectedRoom);
            formData.append('style', selectedStyle);
            formData.append('variant', selectedVariant);

            try {
                console.log("🚀 렌더링 요청 전송...");
                const response = await fetch('/render', {
                    method: 'POST',
                    body: formData
                });

                if (!response.ok) throw new Error(`서버 에러 (${response.status})`);

                const data = await response.json();
                console.log("✅ 렌더링 완료:", data);

                // 1. Before 이미지 (빈 방)
                if (resultBefore) resultBefore.src = data.empty_room_url || data.original_url;

                // 2. After 이미지들 (3장) 처리
                const resultList = data.result_urls || [];
                
                // (1) 메인 결과 표시 (첫번째 이미지)
                if (resultList.length > 0) {
                    resultAfter.src = resultList[0];
                }

                // (2) 썸네일 UI 생성 (3개)
                if (thumbnailContainer) {
                    thumbnailContainer.innerHTML = ""; // 초기화
                    resultList.forEach((url, index) => {
                        const thumb = document.createElement("img");
                        thumb.src = url;
                        thumb.style.width = "80px";
                        thumb.style.height = "80px";
                        thumb.style.objectFit = "cover";
                        thumb.style.cursor = "pointer";
                        thumb.style.borderRadius = "8px";
                        thumb.style.border = index === 0 ? "3px solid #6f42c1" : "3px solid transparent"; // 첫번째 선택됨
                        
                        // 썸네일 클릭 시 메인 이미지 교체
                        thumb.onclick = () => {
                            resultAfter.src = url;
                            // 스타일 업데이트
                            Array.from(thumbnailContainer.children).forEach(c => c.style.border = "3px solid transparent");
                            thumb.style.border = "3px solid #6f42c1";
                        };
                        thumbnailContainer.appendChild(thumb);
                    });
                }

                // 슬라이더 및 화면 표시
                if (resultSection) {
                    resultSection.classList.remove('hidden');
                    if (compareSlider) compareSlider.value = 50;
                    const beforeWrapper = document.querySelector('.image-wrapper.before');
                    if (beforeWrapper) beforeWrapper.style.width = '50%';
                    resultSection.scrollIntoView({ behavior: 'smooth' });
                    
                    // 슬라이더 높이 조절
                    setTimeout(updateImageWidth, 100);
                }

            } catch (error) {
                console.error(error);
                alert('작업 중 오류가 발생했습니다.\n' + error.message);
            } finally {
                clearInterval(timerInterval);
                if (loadingOverlay) loadingOverlay.classList.add('hidden');
            }
        });
    }

    // ---------------------------------------------------------
    // [NEW] 업스케일 & 다운로드 버튼 기능
    // ---------------------------------------------------------
    if (upscaleBtn) {
        upscaleBtn.onclick = function() {
            const currentImgUrl = resultAfter.src;
            
            if (!currentImgUrl) return alert("이미지가 없습니다.");

            upscaleBtn.disabled = true;
            upscaleBtn.innerText = "⏳ 고화질 변환 중...";
            upscaleBtn.style.opacity = "0.7";
            if (upscaleStatus) upscaleStatus.style.display = "block";

            fetch("/upscale", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ image_url: currentImgUrl })
            })
            .then(res => res.json())
            .then(data => {
                if (data.upscaled_url) {
                    // 고화질 이미지로 교체
                    resultAfter.src = data.upscaled_url;
                    
                    // 자동 다운로드 트리거
                    const link = document.createElement("a");
                    link.href = data.upscaled_url;
                    link.download = "HQ_Interior_Result.jpg";
                    document.body.appendChild(link);
                    link.click();
                    document.body.removeChild(link);
                    
                    alert("✨ 변환 완료! 이미지가 다운로드되었습니다.");
                } else {
                    alert("업스케일링 실패: " + (data.error || "알 수 없는 오류"));
                }
            })
            .catch(err => alert("서버 통신 오류: " + err))
            .finally(() => {
                upscaleBtn.disabled = false;
                upscaleBtn.innerText = "✨ Upscale & Download";
                upscaleBtn.style.opacity = "1";
                if (upscaleStatus) upscaleStatus.style.display = "none";
            });
        };
    }

    // ---------------------------------------------------------
    // 슬라이더 기능 (원본 유지)
    // ---------------------------------------------------------
    if (compareSlider) {
        compareSlider.addEventListener('input', (e) => {
            const value = e.target.value;
            const beforeWrapper = document.querySelector('.image-wrapper.before');
            const afterWrapper = document.querySelector('.image-wrapper.after'); // after 너비 조절 추가
            if (beforeWrapper) beforeWrapper.style.width = `${value}%`; // 원본코드는 before 너비를 조절했었음
            if (afterWrapper) afterWrapper.style.width = `${value}%`; // 안전하게 추가
        });
    }

    function updateImageWidth() {
        if (comparisonContainer && comparisonContainer.offsetWidth > 0 && resultBefore) {
            resultBefore.style.width = `${comparisonContainer.offsetWidth}px`;
            if (resultAfter) resultAfter.style.width = '100%';
        }
    }
    window.addEventListener('resize', updateImageWidth);
});
