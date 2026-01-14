document.addEventListener('DOMContentLoaded', () => {
    const PAGE = document.body?.dataset?.page || '';
    if (PAGE !== 'image-studio') return;

    console.log("✅ Image Studio Script Loaded");

    // --- Screen Navigation Logic ---
    const menuScreen = document.getElementById('menu-screen');
    const workspace1 = document.getElementById('workspace-feature-1');

    // UI Helper Functions
    function showMenu() {
        workspace1.style.display = 'none';
        menuScreen.style.display = 'flex'; // Ensure Flex layout
        window.scrollTo(0, 0);
    }

    function showWorkspace() {
        menuScreen.style.display = 'none';
        workspace1.style.display = 'flex';
        window.scrollTo(0, 0);
    }

    // Initialize History
    // Replace current state to ensure we start with a 'menu' state if loaded fresh
    if (!history.state) {
        history.replaceState({ view: 'menu' }, '', '');
    }

    // Handle Browser Back Button
    window.addEventListener('popstate', (event) => {
        if (event.state && event.state.view === 'workspace') {
            showWorkspace();
        } else {
            // Default to menu for any other state or null
            showMenu();
        }
    });

    // Feature Buttons
    const btnFeature1 = document.getElementById('btn-feature-1'); // Real Photo
    if (btnFeature1) {
        btnFeature1.onclick = () => {
            history.pushState({ view: 'workspace' }, '', '');
            showWorkspace();
        };
    }

    const btnFeature2 = document.getElementById('btn-feature-2'); // Edit
    if (btnFeature2) {
        btnFeature2.onclick = () => showAlert("Coming Soon", "Photo Editing feature is under development.");
    }

    const btnFeature3 = document.getElementById('btn-feature-3'); // Decorate
    if (btnFeature3) {
        btnFeature3.onclick = () => showAlert("Coming Soon", "Decoration & Lighting feature is under development.");
    }

    // Back Buttons (Apps Breadcrumb)
    const btnBack1 = document.getElementById('back-to-menu-1');
    if (btnBack1) {
        btnBack1.onclick = () => {
            if (history.state && history.state.view === 'workspace') {
                history.back(); // Triggers popstate -> showMenu()
            } else {
                showMenu(); // Fallback
            }
        };
    }


    // --- 파일 처리 및 생성 로직 ---
    const dropZone = document.getElementById('fp-ref-drop-zone');
    const fileInput = document.getElementById('fp-ref-input');
    const previewContainer = document.getElementById('fp-ref-preview-container');
    const removeAllBtn = document.getElementById('fp-ref-remove-all');
    const generateBtn = document.getElementById('fp-generate-btn');

    const loadingEl = document.getElementById('fp-loading');
    const placeholderEl = document.getElementById('fp-placeholder-text');
    const resultContainer = document.getElementById('fp-result-container');
    const gridEl = document.getElementById('fp-gen-grid');


    // 모달 관련
    const globalModal = document.getElementById('global-modal');
    const modalTitle = document.getElementById('modal-title');
    const modalMsg = document.getElementById('modal-msg');
    const modalOkBtn = document.getElementById('modal-ok-btn');

    function showAlert(title, msg) {
        if (!globalModal) { alert(msg); return; }
        modalTitle.textContent = title;
        modalMsg.innerHTML = msg.replace(/\n/g, '<br>');
        modalOkBtn.onclick = () => globalModal.classList.add('hidden');
        globalModal.classList.remove('hidden');
    }

    let refFiles = [];

    if (dropZone) {
        dropZone.addEventListener('click', () => fileInput.click());
        // 드래그 앤 드롭 시각적 효과
        dropZone.addEventListener('dragover', (e) => {
            e.preventDefault();
            dropZone.classList.add('dragover');
        });
        dropZone.addEventListener('dragleave', () => {
            dropZone.classList.remove('dragover');
        });
        dropZone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropZone.classList.remove('dragover');
            if (e.dataTransfer.files.length) handleFiles(e.dataTransfer.files);
        });
        fileInput.addEventListener('change', (e) => handleFiles(e.target.files));
    }

    function handleFiles(files) {
        Array.from(files).forEach(f => {
            if (f.type.startsWith('image/')) refFiles.push(f);
        });
        updatePreviews();
    }

    function updatePreviews() {
        previewContainer.innerHTML = '';

        if (refFiles.length > 0) {
            previewContainer.style.display = 'grid'; // Grid Layout Check
            removeAllBtn.classList.remove('hidden');
            generateBtn.disabled = false;

            refFiles.forEach((file, index) => {
                const reader = new FileReader();
                const itemDiv = document.createElement('div');
                itemDiv.className = 'is-file-item';

                reader.onload = (e) => {
                    itemDiv.innerHTML = `
                        <img src="${e.target.result}" alt="${file.name}">
                        <button class="remove-btn" title="Remove">×</button>
                    `;
                    const delBtn = itemDiv.querySelector('.remove-btn');
                    delBtn.onclick = (ev) => {
                        ev.stopPropagation();
                        refFiles.splice(index, 1);
                        updatePreviews();
                    };
                    previewContainer.appendChild(itemDiv);
                };
                reader.readAsDataURL(file);
            });
        } else {
            previewContainer.style.display = 'none';
            removeAllBtn.classList.add('hidden');
            generateBtn.disabled = true;
        }
    }

    if (removeAllBtn) {
        removeAllBtn.onclick = (e) => {
            e.stopPropagation();
            refFiles = [];
            fileInput.value = '';
            updatePreviews();
        };
    }

    async function generate() {
        if (refFiles.length === 0) return;

        // 상태 초기화 - 크기 변화 없이 내용만 변경
        placeholderEl.classList.add('hidden');
        resultContainer.classList.add('hidden');
        gridEl.innerHTML = '';
        loadingEl.classList.remove('hidden');

        generateBtn.disabled = true;
        generateBtn.textContent = "GENERATING...";


        const formData = new FormData();
        refFiles.forEach(f => formData.append('input_photos', f));

        try {
            const res = await fetch('/generate-frontal-view', { method: 'POST', body: formData });
            const data = await res.json();

            if (res.ok && data.urls && data.urls.length > 0) {
                loadingEl.classList.add('hidden');
                resultContainer.classList.remove('hidden');

                data.urls.forEach((url, idx) => {
                    const card = document.createElement('div');
                    card.className = 'result-card';

                    const img = document.createElement('img');
                    img.src = url;
                    img.alt = `Generated Image ${idx + 1}`;

                    const downBtn = document.createElement('button');
                    downBtn.className = 'glow-btn burgundy detail-upscale-btn';
                    downBtn.innerHTML = '<span class="material-symbols-outlined">file_download</span> DOWNLOAD';

                    downBtn.onclick = () => {
                        const link = document.createElement('a');
                        link.href = url;
                        link.download = `Real_Photo_${Date.now()}_${idx}.png`;
                        document.body.appendChild(link);
                        link.click();
                        document.body.removeChild(link);
                    };

                    card.appendChild(img);
                    card.appendChild(downBtn);
                    gridEl.appendChild(card);
                });

            } else {
                throw new Error(data.error || "Generation failed");
            }

        } catch (e) {
            console.error(e);
            loadingEl.classList.add('hidden');
            showAlert("Error", "Failed: " + e.message);
            placeholderEl.classList.remove('hidden');
        } finally {
            generateBtn.disabled = false;
            generateBtn.innerHTML = `<span class="material-symbols-outlined" style="font-size: 18px;">auto_fix_high</span> Generate Real Photo`;

        }
    }

    if (generateBtn) generateBtn.onclick = generate;

});