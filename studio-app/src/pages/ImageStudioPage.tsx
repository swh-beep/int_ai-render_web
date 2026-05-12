import { useEffect, useRef, useState } from "react";

import { pollImageJob } from "../api/jobs";
import { requestImageEdit, requestRealPhoto } from "../api/imageStudio";
import { MaskCanvas, type MaskCanvasHandle } from "../components/MaskCanvas";
import { validateImageFiles, type ImageStudioMode } from "../domain/imageStudio";

type Workspace = {
  id: ImageStudioMode;
  featureId: string;
  route: string;
  title: string;
  description: string;
  cardClass: string;
  icon: string;
  placeholderTitle: string;
  placeholderCopy: string;
};

const workspaces: Workspace[] = [
  {
    id: "real-photo",
    featureId: "feature-1",
    route: "generate-real-photo",
    title: "Generate Real Photo",
    description: "Create a perfect frontal view from multiple angled photos.",
    cardClass: "card-hero",
    icon: "arrow_forward",
    placeholderTitle: "Ready to Generate Real Photo",
    placeholderCopy: "Upload Source Photos to Create a Perfect Frontal View",
  },
  {
    id: "edit-image",
    featureId: "feature-2",
    route: "edit-image",
    title: "Edit Image",
    description: "Rearrange furniture & props.",
    cardClass: "card-sub-1",
    icon: "edit",
    placeholderTitle: "Ready to Edit Image",
    placeholderCopy: "Upload Source Photos to Rearrange Furniture",
  },
  {
    id: "decorate-image",
    featureId: "feature-3",
    route: "decorate-image",
    title: "Decorate Image",
    description: "Add decorations & Lighting.",
    cardClass: "card-sub-2",
    icon: "edit",
    placeholderTitle: "Ready to Decorate Image",
    placeholderCopy: "Upload Source Photos to Add Decorations",
  },
];

const workspaceDescriptions: Record<ImageStudioMode, string> = {
  "real-photo": "Create a perfect frontal view from multiple angled photos with AI precision and accuracy.",
  "edit-image": "Rearrange furniture & props in your room image with AI precision.",
  "decorate-image": "Add decorations & Lighting to your room image for a perfect atmosphere.",
};

function useStudioBody(page: string) {
  useEffect(() => {
    document.body.dataset.page = page;
    return () => {
      if (document.body.dataset.page === page) delete document.body.dataset.page;
    };
  }, [page]);
}

function fileKey(file: File, index: number) {
  return `${file.name}-${file.size}-${index}`;
}

function ImagePreviewList({ files, onRemove, single = false }: { files: File[]; onRemove: (index: number) => void; single?: boolean }) {
  if (!files.length) return null;

  return (
    <div className={`is-file-list ${single ? "single-mode" : ""}`}>
      {files.map((file, index) => (
        <div className="is-file-item" key={fileKey(file, index)}>
          <img src={URL.createObjectURL(file)} alt={file.name} />
          <button type="button" className="remove-btn" title="Remove" onClick={() => onRemove(index)}>
            ×
          </button>
        </div>
      ))}
    </div>
  );
}

function Placeholder({ title, copy }: { title: string; copy: string }) {
  return (
    <div className="is-placeholder-modern">
      <div className="placeholder-preview-box">
        <div className="preview-image-slot">
          <span className="material-symbols-outlined">image</span>
        </div>
        <span className="preview-arrow">→</span>
        <div className="preview-image-slot">
          <span className="material-symbols-outlined">image</span>
        </div>
      </div>
      <h3 className="placeholder-title">{title}</h3>
      <p className="placeholder-desc">{copy}</p>
    </div>
  );
}

function ResultGrid({ urls }: { urls: string[] }) {
  if (!urls.length) return null;

  return (
    <div className="result-container">
      <div className="result-grid">
        {urls.map((url, index) => (
          <article className="result-card" key={`${url}-${index}`}>
            <img src={url} alt={`Generated result ${index + 1}`} />
            <a className="download-btn" href={`/download?url=${encodeURIComponent(url)}`} download>
              Download
            </a>
          </article>
        ))}
      </div>
    </div>
  );
}

export function ImageStudioPage() {
  useStudioBody("image-studio");

  function getWorkspaceFromPath() {
    const route = window.location.pathname.split("/").filter(Boolean).at(-1);
    return workspaces.find((workspace) => workspace.route === route)?.id ?? null;
  }

  const [activeWorkspace, setActiveWorkspace] = useState<ImageStudioMode | null>(() => getWorkspaceFromPath());
  const [sourceFiles, setSourceFiles] = useState<File[]>([]);
  const [referenceFiles, setReferenceFiles] = useState<File[]>([]);
  const [instructions, setInstructions] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [resultUrls, setResultUrls] = useState<string[]>([]);
  const maskRef = useRef<MaskCanvasHandle | null>(null);

  const active = workspaces.find((workspace) => workspace.id === activeWorkspace);

  useEffect(() => {
    const onPopState = () => setActiveWorkspace(getWorkspaceFromPath());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  function showWorkspace(nextWorkspace: ImageStudioMode) {
    const route = workspaces.find((workspace) => workspace.id === nextWorkspace)?.route;
    if (route) window.history.pushState({}, "", `/app/image-studio/${route}`);
    setActiveWorkspace(nextWorkspace);
    setSourceFiles([]);
    setReferenceFiles([]);
    setInstructions("");
    setResultUrls([]);
    setStatus("");
  }

  function showMenu() {
    window.history.pushState({}, "", "/app/image-studio");
    setActiveWorkspace(null);
    setStatus("");
  }

  async function runGeneration() {
    if (!activeWorkspace) return;

    try {
      setBusy(true);
      setResultUrls([]);
      validateImageFiles(sourceFiles, activeWorkspace === "real-photo" ? "Source photo" : "Source image");
      setStatus("Queueing image job...");

      const jobId =
        activeWorkspace === "real-photo"
          ? await requestRealPhoto(sourceFiles, instructions)
          : await requestImageEdit({
              sourceFiles,
              referenceFiles,
              instructions,
              mode: activeWorkspace === "edit-image" ? "edit" : "decorate",
              maskBlob: activeWorkspace === "edit-image" ? await maskRef.current?.exportMaskBlob() : null,
            });

      const state = await pollImageJob(jobId, setStatus);
      if (!state.urls?.length) throw new Error("완료된 job에 결과 이미지 URL이 없습니다.");
      setResultUrls(activeWorkspace === "real-photo" ? state.urls : state.urls.slice(0, 1));
      setStatus("Image result is ready.");
    } catch (error) {
      setStatus(`Failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  if (!active) {
    return (
      <main id="menu-screen" className="is-main-layout">
        <div className="is-branding">
          <img src="/static/logo.png" alt="Logo" className="is-branding-logo" />
          <img src="/static/TIOR STUDIO.png" alt="TIOR STUDIO" className="is-branding-wordmark" />
          <h1>Image Studio</h1>
        </div>
        <div className="is-menu-grid">
          {workspaces.map((workspace) => (
            <button
              key={workspace.id}
              id={`btn-${workspace.featureId}`}
              type="button"
              aria-label={workspace.title}
              className={`is-card-btn ${workspace.cardClass}`}
              onClick={() => showWorkspace(workspace.id)}
            >
              <div className="card-content">
                <h2>{workspace.title}</h2>
                <p>{workspace.description}</p>
                <div className="card-action">
                  <span className="material-symbols-outlined">{workspace.icon}</span>
                </div>
              </div>
            </button>
          ))}
        </div>
      </main>
    );
  }

  const selectedWorkspace = active.id;
  const prefix = activeWorkspace === "real-photo" ? "fp-" : activeWorkspace === "edit-image" ? "edit-" : "decor-";
  const isEdit = activeWorkspace === "edit-image";
  const isDecorate = activeWorkspace === "decorate-image";

  return (
    <main id={`workspace-${active.featureId}`} className="is-workspace">
      <div className="workspace-breadcrumb">
        <button type="button" className="breadcrumb-btn" onClick={showMenu}>
          <span className="material-symbols-outlined">arrow_back</span> Back
        </button>
        <span className="breadcrumb-divider">/</span>
        <span className="breadcrumb-current">Image Studio</span>
        <span className="breadcrumb-divider">/</span>
        <span className="breadcrumb-current">{active.title}</span>
      </div>

      <div className="is-workspace-card">
        <section className="is-panel-left">
          <h2 className="is-section-title">{active.title}</h2>
          <p className="is-section-desc">{workspaceDescriptions[selectedWorkspace]}</p>

          <label className="is-upload-box" htmlFor={`${prefix}ref-input`}>
            <span className="material-symbols-outlined is-upload-icon">cloud_upload</span>
            <span className="is-upload-label">{activeWorkspace === "real-photo" ? "Upload Source Photos *" : "Upload Image"}</span>
            <span className="is-upload-sub">png, jpg, jpeg & webp (up to 30mb)</span>
            <input
              id={`${prefix}ref-input`}
              type="file"
              accept="image/*"
              multiple={activeWorkspace === "real-photo"}
              className="hidden"
              onChange={(event) => setSourceFiles(Array.from(event.currentTarget.files ?? []))}
            />
          </label>
          <ImagePreviewList files={sourceFiles} single={activeWorkspace !== "real-photo"} onRemove={(index) => setSourceFiles(sourceFiles.filter((_, fileIndex) => fileIndex !== index))} />

          {sourceFiles.length ? (
            <button type="button" className="clear-all-btn" onClick={() => setSourceFiles([])}>
              Clear All
            </button>
          ) : null}

          {activeWorkspace !== "real-photo" ? (
            <>
              <div className="is-instruction-box">
                <label className="input-label" htmlFor={`${prefix}instructions`}>
                  {isEdit ? "Edit Instructions" : "Decorate Instructions"}
                </label>
                <textarea
                  id={`${prefix}instructions`}
                  className="dark-input"
                  rows={4}
                  value={instructions}
                  onChange={(event) => setInstructions(event.target.value)}
                  placeholder={isEdit ? "eg. 소파와 TV 콘솔 위치를 바꿔줘." : "eg. 따뜻한 조명과 식물을 추가해줘."}
                />
              </div>

              {isEdit ? (
                <div className="is-instruction-box">
                  <label className="input-label">Edit Mask (Optional)</label>
                  <div className="mask-controls">
                    <button className="mask-toggle-btn is-off" type="button" aria-label="Toggle mask">
                      <span className="material-symbols-outlined">toggle_off</span>
                    </button>
                    <div className="mask-range">
                      <span>Brush</span>
                      <input type="range" min="8" max="80" defaultValue="32" />
                    </div>
                  </div>
                  <MaskCanvas ref={maskRef} />
                </div>
              ) : null}

              <div className="is-instruction-box">
                <label className="is-upload-box is-instruction-upload is-reference-upload">
                  <input
                    type="file"
                    className="reference-input hidden"
                    accept="image/png,image/jpeg,image/webp"
                    multiple
                    onChange={(event) => setReferenceFiles(Array.from(event.currentTarget.files ?? []))}
                  />
                  <span className="material-symbols-outlined is-upload-icon">cloud_upload</span>
                  <span className="upload-title">Upload Reference Image</span>
                  <small>Optional reference images</small>
                </label>
                <ImagePreviewList files={referenceFiles} onRemove={(index) => setReferenceFiles(referenceFiles.filter((_, fileIndex) => fileIndex !== index))} />
              </div>
            </>
          ) : null}

          <button
            type="button"
            className="is-generate-btn"
            disabled={busy}
            aria-label={activeWorkspace === "real-photo" ? "GENERATE REAL PHOTO" : isDecorate ? "GENERATE DECORATE IMAGE" : "GENERATE EDIT IMAGE"}
            onClick={runGeneration}
          >
            <span className="material-symbols-outlined">auto_fix_high</span>
            {activeWorkspace === "real-photo" ? "Generate Real Photo" : isDecorate ? "Generate Decorate Image" : "Generate Edit Image"}
          </button>
          <div className="vs-status-text">{status}</div>
        </section>

        <section className="is-panel-right">
          {busy ? (
            <div className="loading-container">
              <div className="spinner" />
              <p>Generating image...</p>
            </div>
          ) : resultUrls.length ? (
            <ResultGrid urls={resultUrls} />
          ) : (
            <Placeholder title={active.placeholderTitle} copy={active.placeholderCopy} />
          )}
        </section>
      </div>
    </main>
  );
}
