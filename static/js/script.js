document.addEventListener('DOMContentLoaded', () => {
    console.log("✅ script.js 로드됨");

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
    const downloadLink = document.getElementById('download-link');
    const compareSlider = document.getElementById('compare-slider');
    const comparisonContainer = document.querySelector('.comparison-container');

    let selectedFile = null;
    let selectedRoom = null;
    let selectedStyle = null;
    let selectedVariant = null;

    // ---------------------------------------------------------
    // [초기화] 룸 타입 불러오기
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
            if (roomGrid) roomGrid.innerHTML = `<p style="color:red">서버 연결 실패. (python main.py 실행 확인)</p>`;
        });

    // ---------------------------------------------------------
    // 선택 핸들러
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

    // [수정됨] 5개씩 2줄 정렬 + 16:9 중앙 크롭 + 하단 번호 표시
    function selectStyle(style, btn) {
        selectedStyle = style;
        selectedVariant = null;

        document.querySelectorAll('#style-grid .style-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');

        variantGrid.innerHTML = '';

        // 1번부터 10번까지 반복
        for (let i = 1; i <= 10; i++) {
            // 1. 컨테이너 (버튼 역할)
            const variantBtn = document.createElement('div');
            variantBtn.className = 'variant-img-btn';
            variantBtn.setAttribute('data-index', i); // CSS에서 no-image일 때 사용

            // 2. 이미지 태그
            const img = document.createElement('img');

            // 파일명 규칙 (소문자, 공백제거)
            const safeRoom = selectedRoom.toLowerCase().replace(/ /g, '');
            const safeStyle = style.toLowerCase().replace(/ /g, '-').replace(/_/g, '-');
            const imgName = `${safeRoom}_${safeStyle}_${i}.png`; // .jpg 인지 .png 인지 확인 필수!

            img.src = `/static/thumbnails/${imgName}`;
            img.alt = `Variant ${i}`;

            // 이미지가 없으면 "no-image" 클래스 추가 (CSS가 처리함)
            img.onerror = function () {
                variantBtn.classList.add('no-image');
            };

            // 3. 하단 번호 라벨
            const label = document.createElement('span');
            label.className = 'variant-label';
            label.textContent = i; // "1", "2"...

            // 4. 조립 (이미지 + 번호)
            variantBtn.appendChild(img);
            variantBtn.appendChild(label);

            // 5. 클릭 이벤트
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
    function selectVariant(variant, btn) {
        selectedVariant = variant;
        document.querySelectorAll('#variant-grid .style-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        checkReady();
    }

    // ---------------------------------------------------------
    // 파일 업로드
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
    // [핵심] 렌더링 요청 및 타이머 로직
    // ---------------------------------------------------------
    if (renderBtn) {
        renderBtn.addEventListener('click', async () => {
            if (!selectedFile || !selectedRoom || !selectedStyle || !selectedVariant) return;

            // 1. 로딩 화면 표시
            if (loadingOverlay) loadingOverlay.classList.remove('hidden');

            // 2. 타이머 시작
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

                // [수정 완료] Before 이미지를 서버가 준 'original_url'(빈 방)로 설정
                // main.py에서 original_url에 빈 방 이미지를 담아서 보냈기 때문입니다.
                if (resultBefore) resultBefore.src = data.original_url;

                if (resultAfter) {
                    resultAfter.src = data.result_url;
                    resultAfter.onload = () => {
                        if (comparisonContainer) {
                            const aspect = resultAfter.naturalWidth / resultAfter.naturalHeight;
                            comparisonContainer.style.aspectRatio = `${aspect}`;
                            updateImageWidth();
                        }
                    };
                }
                if (downloadLink) downloadLink.href = data.result_url;

                // [결과 화면] 슬라이더 중앙(50%) 초기화
                if (resultSection) {
                    resultSection.classList.remove('hidden');

                    if (compareSlider) compareSlider.value = 50;

                    const beforeWrapper = document.querySelector('.image-wrapper.before');
                    if (beforeWrapper) beforeWrapper.style.width = '50%';

                    resultSection.scrollIntoView({ behavior: 'smooth' });
                }

            } catch (error) {
                console.error(error);
                alert('작업 중 오류가 발생했습니다.\n' + error.message);
            } finally {
                // 3. 타이머 종료 및 로딩 끄기
                clearInterval(timerInterval);
                if (loadingOverlay) loadingOverlay.classList.add('hidden');
            }
        });
    }

    // 슬라이더 기능
    if (compareSlider) {
        compareSlider.addEventListener('input', (e) => {
            const value = e.target.value;
            const beforeWrapper = document.querySelector('.image-wrapper.before');
            if (beforeWrapper) beforeWrapper.style.width = `${value}%`;
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
