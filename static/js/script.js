document.addEventListener('DOMContentLoaded', () => {
    console.log("✅ script.js 로드됨 (최종 수정버전)");

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
    
    // 썸네일 & 업스케일 버튼
    const thumbnailContainer = document.getElementById('thumbnailContainer');
    const upscaleBtn = document.getElementById('upscaleBtn');
    const upscaleStatus = document.getElementById('upscaleStatus');

    let selectedFile = null;
    let selectedRoom = null;
    let selectedStyle = null;
    let selectedVariant = null;

    // ---------------------------------------------------------
    // 1. 초기화 및 데이터 로드 (UI 유지)
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

    function selectStyle(style, btn) {
        selectedStyle = style;
        selectedVariant = null;

        document.querySelectorAll('#style-grid .style-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');

        variantGrid.innerHTML = '';

        // 1~10번 옵션 생성
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
    // 2. 파일 업로드 핸들링
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
    // 3. 렌더링 요청 로직 (핵심 수정)
    // ---------------------------------------------------------
    if (renderBtn) {
        renderBtn.addEventListener('click', async () => {
            if (!selectedFile || !selectedRoom || !selectedStyle || !selectedVariant) return;

            if (loadingOverlay) loadingOverlay.classList.remove('hidden');
            if (resultSection) resultSection.classList.add('hidden'); // 결과창 숨겼다가 다시 보여주기

            // 타이머 UI
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

                // [수정] Before 이미지: 생성된 '빈 방(empty_room_url)'을 우선 사용
                if (resultBefore) {
                    // 빈 방 이미지가 있으면 쓰고, 없으면 원본 사용
                    resultBefore.src = data.empty_room_url || data.original_url;
                }

                // [수정] After 이미지 및 썸네일 처리
                const resultList = data.result_urls || [];
                
                // (1) 메인 결과 표시
                if (resultList.length > 0 && resultAfter) {
                    resultAfter.src = resultList[0];
                }

                // (2) 썸네일 생성
                if (thumbnailContainer) {
                    thumbnailContainer.innerHTML = "";
                    resultList.forEach((url, index) => {
                        const thumb = document.createElement("img");
                        thumb.src = url;
                        thumb.style.width = "80px";
                        thumb.style.height = "80px";
                        thumb.style.objectFit = "cover";
                        thumb.style.cursor = "pointer";
                        thumb.style.borderRadius = "8px";
                        thumb.style.border = index === 0 ? "3px solid #6f42c1" : "3px solid transparent";
                        
                        thumb.onclick = () => {
                            if (resultAfter) resultAfter.src = url;
                            Array.from(thumbnailContainer.children).forEach(c => c.style.border = "3px solid transparent");
                            thumb.style.border = "3px solid #6f42c1";
                        };
                        thumbnailContainer.appendChild(thumb);
                    });
                }

                // [수정] 슬라이더 초기화 및 화면 표시
                if (resultSection) {
                    resultSection.classList.remove('hidden');
                    
                    // 슬라이더를 50% 위치로 강제 초기화
                    initSlider();
                    
                    resultSection.scrollIntoView({ behavior: 'smooth' });
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
    // 4. 슬라이더 기능 (UI 깨짐 수정)
    // ---------------------------------------------------------
    function initSlider() {
        if (!compareSlider) return;
        
        const afterWrapper = document.querySelector('.image-wrapper.after'); // 위쪽 이미지 (After)
        const beforeWrapper = document.querySelector('.image-wrapper.before'); // 아래쪽 이미지 (Before)
        
        // 초기값 설정 (50%)
        compareSlider.value = 50;
        
        if (afterWrapper) afterWrapper.style.width = "50%";
        if (beforeWrapper) beforeWrapper.style.width = "100%"; // [중요] 아래쪽 이미지는 항상 꽉 차있어야 함

        // 슬라이더 조작 시 이벤트
        compareSlider.oninput = function() {
            // [중요] afterWrapper의 너비만 조절해야 자연스러운 비교가 됨
            if (afterWrapper) afterWrapper.style.width = this.value + "%";
        };
    }

    // 초기 로드시에도 슬라이더 이벤트 바인딩
    initSlider();


    // ---------------------------------------------------------
    // 5. 업스케일 & 다운로드 버튼 기능
    // ---------------------------------------------------------
    if (upscaleBtn) {
        upscaleBtn.onclick = function() {
            // 현재 보고 있는 After 이미지의 URL 가져오기
            const currentImgUrl = resultAfter ? resultAfter.src : null;
            
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
                    // 1. 화면의 이미지를 고화질로 교체
                    if (resultAfter) resultAfter.src = data.upscaled_url;
                    
                    // 2. 다운로드 실행
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
            .catch(err => {
                console.error(err);
                alert("서버 통신 오류: " + err);
            })
            .finally(() => {
                upscaleBtn.disabled = false;
                upscaleBtn.innerText = "✨ Upscale & Download";
                upscaleBtn.style.opacity = "1";
                if (upscaleStatus) upscaleStatus.style.display = "none";
            });
        };
    }
});
