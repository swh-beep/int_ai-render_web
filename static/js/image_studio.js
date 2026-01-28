document.addEventListener('DOMContentLoaded', () => {
    const PAGE = document.body?.dataset?.page || '';
    if (PAGE !== 'image-studio') return;

    console.log("✅ Image Studio Script Loaded (Multi-Feature Support)");

    const JOB_POLL_INTERVAL = 2000;
    const JOB_TIMEOUT_MS = 15 * 60 * 1000;

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


    // --- Screen Navigation Logic ---
    const menuScreen = document.getElementById('menu-screen');
    const workspaces = {
        'feature-1': document.getElementById('workspace-feature-1'),
        'feature-2': document.getElementById('workspace-feature-2'),
        'feature-3': document.getElementById('workspace-feature-3')
    };

    function showMenu() {
        Object.values(workspaces).forEach(ws => ws.style.display = 'none');
        menuScreen.style.display = 'flex';
        window.scrollTo(0, 0);
    }

    function showWorkspace(id) {
        menuScreen.style.display = 'none';
        Object.keys(workspaces).forEach(key => {
            workspaces[key].style.display = (key === id) ? 'flex' : 'none';
        });
        window.scrollTo(0, 0);
    }

    // --- State & History ---
    if (!history.state) {
        history.replaceState({ view: 'menu' }, '', '');
    }

    window.addEventListener('popstate', (event) => {
        if (event.state && event.state.view === 'workspace') {
            showWorkspace(event.state.id);
        } else {
            showMenu();
        }
    });

    // Navigation Buttons
    ['feature-1', 'feature-2', 'feature-3'].forEach((id, idx) => {
        const btn = document.getElementById(`btn-${id}`);
        if (btn) {
            btn.onclick = () => {
                history.pushState({ view: 'workspace', id: id }, '', '');
                showWorkspace(id);
            };
        }
        
        const backBtn = document.getElementById(`back-to-menu-${idx + 1}`);
        if (backBtn) {
            backBtn.onclick = () => {
                if (history.state && history.state.view === 'workspace') {
                    history.back();
                } else {
                    showMenu();
                }
            };
        }
    });

    // --- Workspace Logic Management ---
    class WorkspaceManager {
        constructor(id, options = {}) {
            this.id = id;
            this.prefix = options.prefix || '';
            this.refFiles = [];
            
            // Elements
            this.dropZone = document.getElementById(`${this.prefix}ref-drop-zone`);
            this.fileInput = document.getElementById(`${this.prefix}ref-input`);
            this.previewContainer = document.getElementById(`${this.prefix}ref-preview-container`);
            this.removeAllBtn = document.getElementById(`${this.prefix}ref-remove-all`);
            this.generateBtn = document.getElementById(`${this.prefix}generate-btn`);
            
            // [NEW] Special inputs (like instructions)
            this.instructionInput = document.getElementById(`${this.prefix}instructions`);

            // [NEW] Internal Preview Support
            this.internalPreview = this.dropZone?.querySelector('.is-internal-preview');
            this.uploadContent = this.dropZone?.querySelector('.is-upload-content');
            
            this.loadingEl = document.getElementById(`${this.prefix}loading`);
            this.placeholderEl = document.getElementById(`${this.prefix}placeholder-text`);
            this.resultContainer = document.getElementById(`${this.prefix}result-container`);
            this.gridEl = document.getElementById(`${this.prefix}gen-grid`);
            
            this.init();
        }

        init() {
            if (this.dropZone) {
                this.dropZone.addEventListener('click', (e) => {
                    // Don't open file dialog if we clicked the remove button in preview
                    if (e.target.closest('.remove-btn')) return;
                    if (this.internalPreview && !this.internalPreview.classList.contains('hidden')) return;
                    this.fileInput.click();
                });
                this.dropZone.addEventListener('dragover', (e) => {
                    e.preventDefault();
                    this.dropZone.classList.add('dragover');
                });
                this.dropZone.addEventListener('dragleave', () => {
                    this.dropZone.classList.remove('dragover');
                });
                this.dropZone.addEventListener('drop', (e) => {
                    e.preventDefault();
                    this.dropZone.classList.remove('dragover');
                    if (e.dataTransfer.files.length) this.handleFiles(e.dataTransfer.files);
                });
                this.fileInput.addEventListener('change', (e) => this.handleFiles(e.target.files));
            }

            if (this.removeAllBtn) {
                this.removeAllBtn.onclick = (e) => {
                    e.stopPropagation();
                    this.clearFiles();
                };
            }

            // [NEW] Internal Preview Remove Button
            const internalRemoveBtn = this.internalPreview?.querySelector('.remove-btn');
            if (internalRemoveBtn) {
                internalRemoveBtn.onclick = (e) => {
                    e.stopPropagation();
                    this.clearFiles();
                };
            }

            if (this.generateBtn) {
                this.generateBtn.onclick = () => this.generate();
            }
        }

        clearFiles() {
            this.refFiles = [];
            if (this.fileInput) this.fileInput.value = '';
            if (this.instructionInput) this.instructionInput.value = '';
            this.updatePreviews();
        }

        handleFiles(files) {
            Array.from(files).forEach(f => {
                if (f.type.startsWith('image/')) this.refFiles.push(f);
            });
            this.updatePreviews();
        }

        updatePreviews() {
            // Priority 1: Internal Preview (Drop Zone)
            if (this.internalPreview) {
                const img = this.internalPreview.querySelector('img');
                if (this.refFiles.length > 0) {
                    const reader = new FileReader();
                    reader.onload = (e) => {
                        img.src = e.target.result;
                        this.internalPreview.classList.remove('hidden');
                        if (this.uploadContent) this.uploadContent.classList.add('hidden');
                        this.dropZone?.classList.add('has-preview');
                    };
                    reader.readAsDataURL(this.refFiles[this.refFiles.length - 1]); // Show the latest one
                    this.generateBtn.disabled = false;
                } else {
                    img.src = '';
                    this.internalPreview.classList.add('hidden');
                    if (this.uploadContent) this.uploadContent.classList.remove('hidden');
                    this.generateBtn.disabled = true;
                    this.dropZone?.classList.remove('has-preview');
                    if (this.id === 'edit-image' && window.__editMaskManager) {
                        window.__editMaskManager.reset();
                    }
                }
                // Hide external preview container if it exists
                if (this.previewContainer) this.previewContainer.style.display = 'none';
                return;
            }

            // Priority 2: External Preview List
            if (!this.previewContainer) return;
            this.previewContainer.innerHTML = '';

            if (this.refFiles.length > 0) {
                this.previewContainer.style.display = 'grid';
                if (this.id === 'edit-image' || this.id === 'decorate-image') {
                    this.previewContainer.classList.add('single-mode');
                } else {
                    this.previewContainer.classList.remove('single-mode');
                }
                this.removeAllBtn?.classList.remove('hidden');
                this.generateBtn.disabled = false;

                this.refFiles.forEach((file, index) => {
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
                            this.refFiles.splice(index, 1);
                            this.updatePreviews();
                        };
                        this.previewContainer.appendChild(itemDiv);
                    };
                    reader.readAsDataURL(file);
                });
            } else {
                this.previewContainer.style.display = 'none';
                this.removeAllBtn?.classList.add('hidden');
                this.generateBtn.disabled = true;
                if (this.id === 'edit-image' && window.__editMaskManager) {
                    window.__editMaskManager.reset();
                }
            }
        }

// image_studio.js 파일 내부의 WorkspaceManager 클래스 안의 generate() 메서드를 이것으로 교체하세요.

        async generate() {
            if (this.refFiles.length === 0) return;

            // 1. UI 초기화
            this.placeholderEl?.classList.add('hidden');
            this.resultContainer?.classList.add('hidden');
            if (this.gridEl) {
                this.gridEl.innerHTML = '';
                // Edit/Decorate는 1장만 크게 보여주기
                if (this.id === 'edit-image' || this.id === 'decorate-image') {
                    this.gridEl.style.gridTemplateColumns = '1fr';
                    this.gridEl.style.maxWidth = '1000px';
                } else {
                    this.gridEl.style.gridTemplateColumns = 'repeat(auto-fit, minmax(300px, 1fr))';
                    this.gridEl.style.maxWidth = '100%';
                }
            }
            this.loadingEl?.classList.remove('hidden');

            const originalBtnText = this.generateBtn.innerHTML;
            this.generateBtn.disabled = true;
            this.generateBtn.textContent = "GENERATING...";

            // 2. FormData 생성
            const formData = new FormData();
            this.refFiles.forEach(f => formData.append('input_photos', f));
            if (this.id === 'edit-image' || this.id === 'decorate-image') {
                const refBox = document.querySelector(`.is-reference-upload[data-ref-for="${this.id}"]`);
                const refInput = refBox ? refBox.querySelector('.reference-input') : null;
                const refFiles = (refInput && Array.isArray(refInput._refFiles)) ? refInput._refFiles : [];
                refFiles.forEach(f => formData.append('input_photos', f));
            }
            if (this.id === 'edit-image' && window.__editMaskManager) {
                const maskBlob = await window.__editMaskManager.exportMaskBlob();
                if (maskBlob) {
                    formData.append('mask', maskBlob, 'edit_mask.png');
                }
            }
            
            // [핵심 수정] 엔드포인트 및 지시사항(Instructions) 처리 통합
            // 기존에 위쪽에 있던 'if (this.instructionInput)...' 코드를 삭제하고 여기서 한 번에 처리합니다.
            
            let endpoint = '/async/generate-frontal-view'; // 기본값 (Real Photo)
            
            // 현재 입력된 텍스트 값을 실시간으로 가져옴 (참조 오류 방지)
            const currentInstructions = this.instructionInput ? this.instructionInput.value.trim() : "";

            console.log(`[Generate] Mode: ${this.id}, Instructions: "${currentInstructions}"`); // 디버깅용 로그

            if (this.id === 'edit-image') {
                endpoint = '/async/generate-image-edit';
                formData.append('mode', 'edit');
                // 입력값이 없으면 기본값 사용
                formData.append('instructions', currentInstructions || "Rearrange furniture for better flow.");
            } 
            else if (this.id === 'decorate-image') {
                endpoint = '/async/generate-image-edit';
                formData.append('mode', 'decorate');
                // 입력값이 없으면 기본값 사용
                formData.append('instructions', currentInstructions || "Make it cozy and stylish.");
            }
            // Real Photo 모드일 때도 instructions가 있다면 보낼 수 있음 (선택 사항)
            else if (currentInstructions) {
                formData.append('instructions', currentInstructions);
            }

            try {
                // 3. 서버 요청
                const res = await fetch(endpoint, { method: 'POST', body: formData });
                const job = await res.json();
                if (!res.ok) throw new Error(job.error || "Generation failed");
                if (!job.job_id) throw new Error("Job queue failed");

                const data = await pollJob(job.job_id);

                if (data.urls && data.urls.length > 0) {
                    this.loadingEl?.classList.add('hidden');
                    this.resultContainer?.classList.remove('hidden');

                    const urlsToShow = (this.id === 'edit-image' || this.id === 'decorate-image') ? [data.urls[0]] : data.urls;

                    urlsToShow.forEach((url, idx) => {
                        const card = document.createElement('div');
                        card.className = 'result-card';

                        const img = document.createElement('img');
                        img.src = url;
                        img.alt = `Generated Image ${idx + 1}`;

                        const downBtn = document.createElement('button');
                        downBtn.className = 'glow-btn burgundy detail-upscale-btn';
                        downBtn.innerHTML = '<span class="material-symbols-outlined">file_download</span> DOWNLOAD';

                        downBtn.onclick = (e) => {
                            e.stopPropagation();
                            const link = document.createElement('a');
                            link.href = url;
                            link.download = `Result_${this.id}_${Date.now()}_${idx}.png`;
                            document.body.appendChild(link);
                            link.click();
                            document.body.removeChild(link);
                        };

                        card.appendChild(img);
                        card.appendChild(downBtn);
                        this.gridEl?.appendChild(card);
                    });

                } else {
                    throw new Error(data.error || "Generation failed");
                }
            } catch (e) {
                console.error(e);
                this.loadingEl?.classList.add('hidden');
                showAlert("Error", "Failed: " + e.message);
                this.placeholderEl?.classList.remove('hidden');
            } finally {
                this.generateBtn.disabled = false;
                this.generateBtn.innerHTML = originalBtnText;
            }
        }
    }

    // Modal Helper
    const globalModal = document.getElementById('global-modal');
    const modalTitle = document.getElementById('modal-title');
    const modalMsg = document.getElementById('modal-msg');
    const modalOkBtn = document.getElementById('modal-ok-btn');
    const modalContent = globalModal?.querySelector('.modal-content');

    function showAlert(title, msg) {
        if (!globalModal) { alert(msg); return; }
        modalContent?.classList.remove('preview-wide');
        modalTitle.textContent = title;
        modalMsg.innerHTML = msg.replace(/\n/g, '<br>');
        modalOkBtn.onclick = () => globalModal.classList.add('hidden');
        globalModal.classList.remove('hidden');
    }

    function showPreview(title, html) {
        if (!globalModal) return;
        modalContent?.classList.add('preview-wide');
        modalTitle.textContent = title;
        modalMsg.innerHTML = html;
        modalOkBtn.onclick = () => globalModal.classList.add('hidden');
        globalModal.classList.remove('hidden');
    }

    function initEditMask() {
        const dropZone = document.getElementById('edit-ref-drop-zone');
        if (!dropZone) return null;
        const preview = dropZone.querySelector('.is-internal-preview');
        const img = preview?.querySelector('img');
        const canvas = preview?.querySelector('.mask-canvas');
        const cursor = preview?.querySelector('.mask-cursor');
        const toggleBtn = document.getElementById('edit-mask-toggle');
        const clearBtn = document.getElementById('edit-mask-clear');
        const sizeInput = document.getElementById('edit-mask-size');
        if (!preview || !img || !canvas || !toggleBtn || !clearBtn || !sizeInput) return null;

        const dataCanvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');
        const dataCtx = dataCanvas.getContext('2d');
        let drawing = false;
        let last = null;
        let lastPointer = null;
        let hasMask = false;
        let active = false;

        function positionCanvas() {
            if (!img.naturalWidth || !img.naturalHeight) return;
            const container = preview.getBoundingClientRect();
            const cw = container.width;
            const ch = container.height;
            const ir = img.naturalWidth / img.naturalHeight;
            const cr = cw / ch;
            let dw, dh, dx, dy;
            if (ir > cr) {
                dw = cw;
                dh = cw / ir;
                dx = 0;
                dy = (ch - dh) / 2;
            } else {
                dh = ch;
                dw = ch * ir;
                dy = 0;
                dx = (cw - dw) / 2;
            }
            canvas.style.left = `${dx}px`;
            canvas.style.top = `${dy}px`;
            canvas.style.width = `${dw}px`;
            canvas.style.height = `${dh}px`;
        }

        function syncCanvas() {
            if (!img.naturalWidth || !img.naturalHeight) return;
            canvas.width = img.naturalWidth;
            canvas.height = img.naturalHeight;
            dataCanvas.width = img.naturalWidth;
            dataCanvas.height = img.naturalHeight;
            clearMask();
            positionCanvas();
        }

        function clearMask() {
            if (!ctx || !dataCtx) return;
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            dataCtx.fillStyle = 'black';
            dataCtx.fillRect(0, 0, dataCanvas.width, dataCanvas.height);
            hasMask = false;
        }

        function setActive(on) {
            active = on;
            preview.classList.toggle('mask-active', on);
            toggleBtn.classList.toggle('is-off', !on);
            toggleBtn.innerHTML = `<span class="material-symbols-outlined">${on ? 'toggle_on' : 'toggle_off'}</span>`;
            canvas.style.opacity = on ? '0.55' : '0';
            if (cursor) cursor.style.display = on ? 'block' : 'none';
        }

        function getBrushSize() {
            const rect = canvas.getBoundingClientRect();
            const scale = canvas.width / rect.width;
            return Number(sizeInput.value || 32) * scale;
        }

        function getPos(e) {
            const rect = canvas.getBoundingClientRect();
            const x = (e.clientX - rect.left) * (canvas.width / rect.width);
            const y = (e.clientY - rect.top) * (canvas.height / rect.height);
            return { x, y };
        }

        function updateCursor(e) {
            if (!cursor || !active) return;
            const rect = canvas.getBoundingClientRect();
            const previewRect = preview.getBoundingClientRect();
            const size = Number(sizeInput.value || 32);
            const drawSize = size;
            cursor.style.width = `${drawSize}px`;
            cursor.style.height = `${drawSize}px`;
            if (e && typeof e.clientX === 'number') {
                cursor.style.left = `${e.clientX - previewRect.left}px`;
                cursor.style.top = `${e.clientY - previewRect.top}px`;
                lastPointer = { x: e.clientX, y: e.clientY };
            } else if (lastPointer) {
                cursor.style.left = `${lastPointer.x - previewRect.left}px`;
                cursor.style.top = `${lastPointer.y - previewRect.top}px`;
            }
        }

        function drawLine(from, to) {
            if (!ctx || !dataCtx) return;
            const size = getBrushSize();
            ctx.lineWidth = size;
            ctx.lineCap = 'round';
            ctx.lineJoin = 'round';
            ctx.strokeStyle = 'rgba(255, 80, 120, 0.65)';

            dataCtx.lineWidth = size;
            dataCtx.lineCap = 'round';
            dataCtx.lineJoin = 'round';
            dataCtx.strokeStyle = '#ffffff';

            ctx.beginPath();
            ctx.moveTo(from.x, from.y);
            ctx.lineTo(to.x, to.y);
            ctx.stroke();

            dataCtx.beginPath();
            dataCtx.moveTo(from.x, from.y);
            dataCtx.lineTo(to.x, to.y);
            dataCtx.stroke();
            hasMask = true;
        }

        canvas.addEventListener('mousedown', (e) => {
            if (!active) return;
            drawing = true;
            last = getPos(e);
        });
        window.addEventListener('mouseup', () => {
            drawing = false;
            last = null;
        });
        canvas.addEventListener('mousemove', (e) => {
            updateCursor(e);
            if (!active || !drawing || !last) return;
            const pos = getPos(e);
            drawLine(last, pos);
            last = pos;
        });
        canvas.addEventListener('mouseenter', (e) => {
            updateCursor(e);
            if (cursor) cursor.style.display = active ? 'block' : 'none';
        });
        canvas.addEventListener('mouseleave', () => {
            if (cursor) cursor.style.display = 'none';
        });

        toggleBtn.addEventListener('click', (e) => {
            e.preventDefault();
            setActive(!active);
        });
        clearBtn.addEventListener('click', (e) => {
            e.preventDefault();
            clearMask();
        });
        sizeInput.addEventListener('input', (e) => {
            if (!cursor || !active) return;
            updateCursor(e);
        });

        img.addEventListener('load', () => {
            syncCanvas();
        });
        window.addEventListener('resize', positionCanvas);

        setActive(false);

        return {
            reset: () => {
                clearMask();
                setActive(false);
            },
            exportMaskBlob: () => new Promise((resolve) => {
                if (!hasMask) return resolve(null);
                dataCanvas.toBlob((blob) => resolve(blob), 'image/png');
            }),
        };
    }

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && globalModal && !globalModal.classList.contains('hidden')) {
            globalModal.classList.add('hidden');
        }
    });

    // Initialize Managers
    new WorkspaceManager('real-photo', { prefix: 'fp-' });
    new WorkspaceManager('edit-image', { prefix: 'edit-' });
    new WorkspaceManager('decorate-image', { prefix: 'decor-' });
    window.__editMaskManager = initEditMask();

    const refUploadBoxes = document.querySelectorAll('.is-reference-upload');
    refUploadBoxes.forEach((box) => {
        const input = box.querySelector('.reference-input');
        if (!input) return;
        const previewId = box.dataset.previewTarget;
        const removeId = box.dataset.removeTarget;
        const previewContainer = previewId ? document.getElementById(previewId) : null;
        const clearBtn = removeId ? document.getElementById(removeId) : null;

        const renderPreview = (files) => {
            if (!previewContainer) return;
            previewContainer.innerHTML = '';
            if (!files || files.length === 0) {
                previewContainer.style.display = 'none';
                if (clearBtn) clearBtn.classList.add('hidden');
                return;
            }
            files.forEach((file, index) => {
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
                        input._refFiles.splice(index, 1);
                        renderPreview(input._refFiles);
                    };
                    previewContainer.appendChild(itemDiv);
                };
                reader.readAsDataURL(file);
            });
            previewContainer.style.display = 'grid';
            if (clearBtn) clearBtn.classList.remove('hidden');
        };

        box.addEventListener('click', () => input.click());
        input._refFiles = [];
        input.addEventListener('change', (e) => {
            const selected = Array.from(e.target.files || []).filter(f => f.type.startsWith('image/'));
            if (selected.length === 0) {
                input.value = '';
                renderPreview(input._refFiles);
                return;
            }
            const combined = input._refFiles.concat(selected).slice(0, 6);
            input._refFiles = combined;
            input.value = '';
            renderPreview(input._refFiles);
        });
        if (clearBtn) {
            clearBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                input.value = '';
                input._refFiles = [];
                renderPreview(input._refFiles);
            });
        }
    });
});
