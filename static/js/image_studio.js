document.addEventListener('DOMContentLoaded', () => {
    const PAGE = document.body?.dataset?.page || '';
    if (PAGE !== 'image-studio') return;

    console.log("✅ Image Studio Script Loaded (Multi-Feature Support)");

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
                    };
                    reader.readAsDataURL(this.refFiles[this.refFiles.length - 1]); // Show the latest one
                    this.generateBtn.disabled = false;
                } else {
                    img.src = '';
                    this.internalPreview.classList.add('hidden');
                    if (this.uploadContent) this.uploadContent.classList.remove('hidden');
                    this.generateBtn.disabled = true;
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
            }
        }

        async generate() {
            if (this.refFiles.length === 0) return;

            this.placeholderEl?.classList.add('hidden');
            this.resultContainer?.classList.add('hidden');
            if (this.gridEl) {
                this.gridEl.innerHTML = '';
                // [NEW] If Edit or Decorate, set grid to 1 column for large single result
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

            const formData = new FormData();
            this.refFiles.forEach(f => formData.append('input_photos', f));
            
            // [NEW] Add instructions if applicable
            if (this.instructionInput) {
                formData.append('instructions', this.instructionInput.value);
            }

            try {
                // For now, all features use the same synthesis endpoint
                const res = await fetch('/generate-frontal-view', { method: 'POST', body: formData });
                const data = await res.json();

                if (res.ok && data.urls && data.urls.length > 0) {
                    this.loadingEl?.classList.add('hidden');
                    this.resultContainer?.classList.remove('hidden');

                    // [NEW] If Edit or Decorate, only show the FIRST image
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

                        downBtn.onclick = () => {
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

    function showAlert(title, msg) {
        if (!globalModal) { alert(msg); return; }
        modalTitle.textContent = title;
        modalMsg.innerHTML = msg.replace(/\n/g, '<br>');
        modalOkBtn.onclick = () => globalModal.classList.add('hidden');
        globalModal.classList.remove('hidden');
    }

    // Initialize Managers
    new WorkspaceManager('real-photo', { prefix: 'fp-' });
    new WorkspaceManager('edit-image', { prefix: 'edit-' });
    new WorkspaceManager('decorate-image', { prefix: 'decor-' });
});
