import { useEffect, useState } from "react";

import { uploadOutputImage, uploadOutputVideo } from "../api/outputs";
import { fetchVideoJobStatus, requestCompile, requestSourceGeneration } from "../api/videoMvp";
import {
  buildCompilePayload,
  buildSourceClipPayload,
  createAssembleClip,
  moveClip,
  removeClip,
  updateClip,
  validateSourceImage,
  validateVideoFiles,
  type AspectMode,
  type AspectRatio,
  type AssembleClip,
  type VideoSourceEffect,
  type VideoSourceMotion,
} from "../domain/videoStudio";

type VideoWorkspace = "create-clips" | "assemble-video" | "post-production";

const workspaces = [
  {
    id: "create-clips",
    featureId: "feature-1",
    route: "create-video-clips",
    title: "Create Video Clips",
    description: "Transform images into AI motion clips.",
    cardClass: "card-hero",
    icon: "movie",
  },
  {
    id: "assemble-video",
    featureId: "feature-2",
    route: "assemble-full-video",
    title: "Assemble Full Video",
    description: "Combine clips into a seamless video.",
    cardClass: "card-sub-1",
    icon: "view_timeline",
  },
  {
    id: "post-production",
    featureId: "feature-3",
    route: "post-production",
    title: "Post-Production",
    description: "Add AI voiceover, music, and subtitles.",
    cardClass: "card-sub-2",
    icon: "graphic_eq",
  },
] as const;

function useStudioBody(page: string) {
  useEffect(() => {
    document.body.dataset.page = page;
    return () => {
      if (document.body.dataset.page === page) delete document.body.dataset.page;
    };
  }, [page]);
}

function clipName(file: File, index: number) {
  return `${file.name}-${file.size}-${index}`;
}

function ClipPlaceholder() {
  return (
    <div id="clip-placeholder-text" className="clip-output-placeholder">
      <div className="clip-placeholder-stack">
        <div className="clip-placeholder-card">
          <span className="material-symbols-outlined">image</span>
        </div>
        <div className="clip-placeholder-arrow">
          <span className="material-symbols-outlined">arrow_forward</span>
        </div>
        <div className="clip-placeholder-card">
          <span className="material-symbols-outlined">movie</span>
        </div>
      </div>
      <h3 className="clip-placeholder-title">Preview</h3>
      <p className="clip-placeholder-desc">Upload one image, choose motion and effect, then generate its clip directly.</p>
    </div>
  );
}

export function VideoStudioPage() {
  useStudioBody("video-studio");

  function getWorkspaceFromPath() {
    const route = window.location.pathname.split("/").filter(Boolean).at(-1);
    return workspaces.find((workspace) => workspace.route === route)?.id ?? null;
  }

  const [activeWorkspace, setActiveWorkspace] = useState<VideoWorkspace | null>(() => getWorkspaceFromPath());
  const [sourceFiles, setSourceFiles] = useState<File[]>([]);
  const [motion, setMotion] = useState<VideoSourceMotion>("static");
  const [effect, setEffect] = useState<VideoSourceEffect>("none");
  const [customMotion, setCustomMotion] = useState("");
  const [customEffect, setCustomEffect] = useState("");
  const [clipStatus, setClipStatus] = useState("");
  const [clipBusy, setClipBusy] = useState(false);
  const [generatedClips, setGeneratedClips] = useState<string[]>([]);

  const [assembleClips, setAssembleClips] = useState<AssembleClip[]>([]);
  const [activeClipId, setActiveClipId] = useState("");
  const [aspectRatio, setAspectRatio] = useState<AspectRatio>("9:16");
  const [aspectMode, setAspectMode] = useState<AspectMode>("crop");
  const [assembleStatus, setAssembleStatus] = useState("Timeline is empty. Drop video clips to start.");
  const [assembleBusy, setAssembleBusy] = useState(false);
  const [finalUrl, setFinalUrl] = useState("");

  const active = workspaces.find((workspace) => workspace.id === activeWorkspace);
  const activeClip = assembleClips.find((clip) => clip.id === activeClipId) ?? assembleClips[0];

  useEffect(() => {
    const onPopState = () => setActiveWorkspace(getWorkspaceFromPath());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  function showWorkspace(nextWorkspace: VideoWorkspace) {
    const route = workspaces.find((workspace) => workspace.id === nextWorkspace)?.route;
    if (route) window.history.pushState({}, "", `/app/video-studio/${route}`);
    setActiveWorkspace(nextWorkspace);
    setClipStatus("");
    setAssembleStatus("Timeline is empty. Drop video clips to start.");
  }

  function showMenu() {
    window.history.pushState({}, "", "/app/video-studio");
    setActiveWorkspace(null);
  }

  async function pollVideoJob(jobId: string, statusSetter: (value: string) => void) {
    for (;;) {
      const state = await fetchVideoJobStatus(jobId);
      const progress = typeof state.progress === "number" ? ` (${state.progress}%)` : "";
      statusSetter(`${state.message ?? state.status ?? "Working"}${progress}`);
      if (state.status === "COMPLETED") return state;
      if (state.status === "FAILED") throw new Error(state.error ?? "Video generation failed.");
      await new Promise((resolve) => window.setTimeout(resolve, 1500));
    }
  }

  async function generateSourceClip() {
    try {
      setClipBusy(true);
      setGeneratedClips([]);
      const file = validateSourceImage(sourceFiles);
      setClipStatus("Uploading source image...");
      const imageUrl = await uploadOutputImage(file);
      const jobId = await requestSourceGeneration(
        buildSourceClipPayload({
          imageUrl,
          motion,
          effect,
          customMotionPrompt: customMotion,
          customEffectPrompt: customEffect,
        }),
      );
      const state = await pollVideoJob(jobId, setClipStatus);
      const results = (state.results ?? []).filter(Boolean) as string[];
      if (!results.length) throw new Error("No clips were generated.");
      setGeneratedClips(results);
      setClipStatus("Generated clips are ready.");
    } catch (error) {
      setClipStatus(`Failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setClipBusy(false);
    }
  }

  async function addAssembleFiles(files: File[]) {
    try {
      validateVideoFiles(files);
      const next = files.map((file) => createAssembleClip(file, URL.createObjectURL(file)));
      setAssembleClips((current) => [...current, ...next]);
      setActiveClipId((current) => current || next[0]?.id || "");
      setAssembleStatus(`${next.length} clip(s) added to the timeline.`);
    } catch (error) {
      setAssembleStatus(`Failed: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  function updateActiveClip(patch: Partial<AssembleClip>) {
    if (!activeClip) return;
    setAssembleClips(updateClip(assembleClips, activeClip.id, patch));
  }

  async function compileSequence() {
    try {
      setAssembleBusy(true);
      setFinalUrl("");
      validateVideoFiles(assembleClips.map((clip) => clip.file));
      setAssembleStatus("Uploading clips...");
      const urls: string[] = [];
      for (const clip of assembleClips) urls.push(await uploadOutputVideo(clip.file));
      setAssembleStatus("Starting assembly...");
      const jobId = await requestCompile(buildCompilePayload(assembleClips, urls, aspectRatio, aspectMode));
      const state = await pollVideoJob(jobId, setAssembleStatus);
      if (!state.result_url) throw new Error("Assemble completed without a final video URL.");
      setFinalUrl(state.result_url);
      setAssembleStatus("Final video exported.");
    } catch (error) {
      setAssembleStatus(`Failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setAssembleBusy(false);
    }
  }

  if (!active) {
    return (
      <main id="menu-screen" className="is-main-layout">
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
        <div className="is-branding">
          <img src="/static/logo.png" alt="Logo" className="is-branding-logo" />
          <img src="/static/TIOR STUDIO(Black).png" alt="TIOR STUDIO" className="is-branding-wordmark" />
          <h1>Video Studio</h1>
        </div>
      </main>
    );
  }

  if (activeWorkspace === "create-clips") {
    return (
      <main id="workspace-feature-1" className="is-workspace">
        <div className="workspace-breadcrumb">
          <button className="breadcrumb-btn breadcrumb-back" type="button" onClick={showMenu}>
            <span className="material-symbols-outlined">arrow_back</span> Back
          </button>
          <span className="breadcrumb-divider">/</span>
          <span className="breadcrumb-current">Video Studio</span>
          <span className="breadcrumb-divider">/</span>
          <span className="breadcrumb-current">Create Clips</span>
        </div>

        <div className="clip-workspace-shell">
          <section className="clip-form-panel clip-surface">
            <div className="clip-form-top">
              <span className="clip-eyebrow">Step 1</span>
              <h2 className="clip-heading">Create Video Clips</h2>
              <p className="clip-copy">Upload source image and turn it into a short video clip.</p>
            </div>

            <div className="clip-upload-stage">
              <label id="clip-ref-drop-zone" className={`clip-upload-box ${sourceFiles.length ? "has-preview" : ""}`}>
                {sourceFiles.length ? (
                  <>
                    <button
                      id="clip-ref-remove-all"
                      className="clear-all-btn clip-clear-btn"
                      type="button"
                      onClick={(event) => {
                        event.preventDefault();
                        setSourceFiles([]);
                      }}
                    >
                      Clear
                    </button>
                    <div id="clip-upload-preview" className="clip-upload-preview">
                      <img src={URL.createObjectURL(sourceFiles[0])} alt={sourceFiles[0].name} />
                    </div>
                  </>
                ) : (
                  <div id="clip-upload-empty-state" className="clip-upload-empty">
                    <span className="material-symbols-outlined">cloud_upload</span>
                    <span className="clip-upload-title">Upload Image</span>
                    <span className="clip-upload-subtitle">
                      Drag & Drop your room photo here or <span className="clip-upload-browse">Browse</span>
                    </span>
                    <span className="clip-upload-subtitle">png, jpg, jpeg & webp (up to 25mb)</span>
                  </div>
                )}
                <input
                  type="file"
                  id="clip-ref-input"
                  accept=".png,.jpg,.jpeg,.webp"
                  className="hidden"
                  onChange={(event) => setSourceFiles(Array.from(event.currentTarget.files ?? []))}
                />
              </label>
            </div>

            <div className="clip-form-bottom">
              <div className="clip-form-grid">
                <div className="clip-field">
                  <label htmlFor="clip-motion">Motion</label>
                  <select id="clip-motion" className="clip-select" value={motion} onChange={(event) => setMotion(event.target.value as VideoSourceMotion)}>
                    <option value="static">Static</option>
                    <option value="orbit_r_slow">Orbit Right Slow</option>
                    <option value="orbit_l_slow">Orbit Left Slow</option>
                    <option value="zoom_in_slow">Zoom In Slow</option>
                    <option value="zoom_out_slow">Zoom Out Slow</option>
                    <option value="custom">Custom</option>
                  </select>
                </div>
                <div className="clip-field">
                  <label htmlFor="clip-effect">Effect</label>
                  <select id="clip-effect" className="clip-select" value={effect} onChange={(event) => setEffect(event.target.value as VideoSourceEffect)}>
                    <option value="none">No effect</option>
                    <option value="sunlight">Sunlight shift</option>
                    <option value="lights_on">Lights on</option>
                    <option value="blinds">Blinds motion</option>
                    <option value="plants">Plant motion</option>
                    <option value="door_open">Door open</option>
                    <option value="custom">Custom</option>
                  </select>
                </div>
              </div>
              {motion === "custom" ? (
                <div id="clip-custom-motion-wrap" className="clip-field">
                  <label htmlFor="clip-custom-motion">Custom Motion</label>
                  <textarea id="clip-custom-motion" className="clip-textarea" value={customMotion} onChange={(event) => setCustomMotion(event.target.value)} />
                </div>
              ) : null}
              {effect === "custom" ? (
                <div id="clip-custom-effect-wrap" className="clip-field">
                  <label htmlFor="clip-custom-effect">Custom Effect</label>
                  <textarea id="clip-custom-effect" className="clip-textarea" value={customEffect} onChange={(event) => setCustomEffect(event.target.value)} />
                </div>
              ) : null}
              <button id="clip-generate-btn" className="clip-generate-btn" disabled={clipBusy} type="button" onClick={generateSourceClip}>
                Generate Clips
              </button>
              <div id="statusSource-1" className="clip-status">
                {clipStatus}
              </div>
            </div>
          </section>

          <section className="clip-output-panel clip-surface">
            {clipBusy ? (
              <div id="clip-loading" className="loading-container">
                <div className="spinner" />
                <h3 className="clip-placeholder-title">Generating video clips...</h3>
                <p className="clip-loading-copy">The output card will appear here when the clip finishes.</p>
              </div>
            ) : generatedClips.length ? (
              <div id="clip-result-container" className="result-container">
                <div id="clip-gen-grid" className="clip-results-grid">
                  {generatedClips.map((url, index) => (
                    <article className="clip-generated-card" key={`${url}-${index}`}>
                      <video src={url} controls playsInline />
                      <a className="download-btn" href={`/download?url=${encodeURIComponent(url)}`} download>
                        Download Clip
                      </a>
                    </article>
                  ))}
                </div>
              </div>
            ) : (
              <ClipPlaceholder />
            )}
          </section>
        </div>
      </main>
    );
  }

  if (activeWorkspace === "assemble-video") {
    return (
      <main id="workspace-feature-2" className="is-workspace assemble-editor-workspace">
        <div className="workspace-breadcrumb assemble-breadcrumb">
          <button className="breadcrumb-btn" type="button" onClick={showMenu}>
            <span className="material-symbols-outlined">arrow_back</span> Back
          </button>
          <span className="breadcrumb-divider">/</span>
          <span className="breadcrumb-current">Video Studio</span>
          <span className="breadcrumb-divider">/</span>
          <span className="breadcrumb-current">Assemble Video</span>
        </div>

        <div className="assemble-editor-shell">
          <div className="assemble-workbench">
            <main className="assemble-stage-shell">
              <section className="assemble-stage-panel assemble-stage-main">
                <div className="assemble-stage-topline">
                  <div className="assemble-stage-copy">
                    <span id="assemble-monitor-title" className="assemble-stage-title">
                      {activeClip ? activeClip.name : "No clip selected"}
                    </span>
                    <p id="assemble-monitor-meta" className="assemble-stage-meta">
                      {activeClip ? `Speed ${activeClip.speed}x · ${activeClip.trimStart}s-${activeClip.trimEnd}s` : "Upload clips and select a shot to preview it here."}
                    </p>
                  </div>
                </div>

                <div className="assemble-monitor-frame">
                  <div className="assemble-monitor">
                    {!activeClip && !finalUrl ? (
                      <div id="full-placeholder-text" className="assemble-monitor-empty">
                        <span className="material-symbols-outlined">video_library</span>
                        <h3>No active shot</h3>
                        <p>Import clips to build your sequence, then review them here before export.</p>
                      </div>
                    ) : (
                      <div id="assemble-monitor-player" className="assemble-monitor-player">
                        <span id="assemble-preview-badge" className="assemble-preview-badge">
                          {finalUrl ? "Final Video" : "Selected Clip"}
                        </span>
                        <div id="assemble-monitor-canvas" className="assemble-monitor-canvas">
                          <video src={finalUrl || activeClip?.previewUrl} controls playsInline />
                        </div>
                      </div>
                    )}
                    {assembleBusy ? (
                      <div id="full-loading" className="assemble-monitor-overlay">
                        <div className="spinner" />
                      </div>
                    ) : null}
                  </div>
                </div>

                <div className="assemble-toolbar">
                  <div className="assemble-toolbar-group assemble-toolbar-primary">
                    <button id="assemble-delete-btn" className="assemble-toolbar-btn" type="button" disabled={!activeClip} onClick={() => setAssembleClips(removeClip(assembleClips, assembleClips.findIndex((clip) => clip.id === activeClip?.id)))}>
                      <span className="material-symbols-outlined">delete</span>
                    </button>
                    <button id="assemble-trim-btn" className="assemble-toolbar-btn" type="button">
                      <span className="material-symbols-outlined">content_cut</span>
                    </button>
                    <button id="assemble-reverse-btn" className={`assemble-toolbar-btn ${activeClip?.reverse ? "is-active" : ""}`} type="button" disabled={!activeClip} onClick={() => updateActiveClip({ reverse: !activeClip?.reverse })}>
                      <span className="material-symbols-outlined">replay</span>
                    </button>
                    <button id="assemble-flip-btn" className={`assemble-toolbar-btn ${activeClip?.flipHorizontal ? "is-active" : ""}`} type="button" disabled={!activeClip} onClick={() => updateActiveClip({ flipHorizontal: !activeClip?.flipHorizontal })}>
                      <span className="material-symbols-outlined">flip</span>
                    </button>
                  </div>
                  <div className="assemble-toolbar-group assemble-toolbar-center">
                    <button id="assemble-play-toggle" className="assemble-toolbar-btn is-emphasis" type="button">
                      <span className="material-symbols-outlined">play_arrow</span>
                    </button>
                    <div className="assemble-playback-readout">
                      <span>00:00</span>
                      <span>/</span>
                      <span>00:00</span>
                    </div>
                  </div>
                  <div className="assemble-toolbar-group assemble-toolbar-secondary">
                    <button id="full-ref-remove-all" className="assemble-clear-btn" type="button" onClick={() => setAssembleClips([])}>
                      Clear All
                    </button>
                    <button id="full-generate-btn" className="assemble-export-btn" disabled={assembleBusy} type="button" aria-label="Export Sequence" onClick={compileSequence}>
                      <span className="material-symbols-outlined">upload</span>
                      Export Sequence
                    </button>
                  </div>
                </div>

                <section className="assemble-timeline-shell">
                  <div id="assemble-timeline-ruler" className="assemble-timeline-ruler" aria-hidden="true">
                    <span>00:00</span>
                    <span>00:05</span>
                    <span>00:10</span>
                  </div>
                  <div id="assemble-timeline-viewport" className="assemble-timeline-viewport">
                    <div id="assemble-timeline-track" className="assemble-timeline-track">
                      {assembleClips.map((clip, index) => (
                        <article key={clip.id} className={`assemble-timeline-clip ${activeClip?.id === clip.id ? "is-active" : ""}`} onClick={() => setActiveClipId(clip.id)}>
                          <video src={clip.previewUrl} muted playsInline />
                          <div className="assemble-timeline-clip-copy">
                            <strong>{index + 1}. {clip.name}</strong>
                            <span>{clip.speed}x · {clip.reverse ? "Reverse" : "Forward"}</span>
                          </div>
                          <div className="assemble-timeline-actions">
                            <button type="button" className="assemble-timeline-btn is-icon" disabled={index === 0} onClick={(event) => { event.stopPropagation(); setAssembleClips(moveClip(assembleClips, index, -1)); }}>
                              ←
                            </button>
                            <button type="button" className="assemble-timeline-btn is-icon" disabled={index === assembleClips.length - 1} onClick={(event) => { event.stopPropagation(); setAssembleClips(moveClip(assembleClips, index, 1)); }}>
                              →
                            </button>
                          </div>
                        </article>
                      ))}
                      <label id="full-ref-drop-zone" className="assemble-timeline-drop-slot" role="button" tabIndex={0} aria-label="Add clips to timeline">
                        <span className="material-symbols-outlined">add_photo_alternate</span>
                        <div className="assemble-dropzone-content">
                          <strong className="assemble-dropzone-title">Drop Clips Here</strong>
                          <p className="assemble-dropzone-copy">Drag clips onto the timeline or click to browse.</p>
                        </div>
                        <input type="file" id="full-ref-input" accept="video/*" multiple className="assemble-file-input" onChange={(event) => addAssembleFiles(Array.from(event.currentTarget.files ?? []))} />
                      </label>
                    </div>
                  </div>
                  <p id="assemble-timeline-summary" className="assemble-timeline-summary">
                    {assembleStatus || "Upload clips to populate the timeline."}
                  </p>
                </section>
              </section>

            </main>

            <aside className="assemble-inspector-panel">
              <div className="assemble-inspector-body">
                <div className="assemble-inspector-head">
                  <div>
                    <span className="assemble-panel-eyebrow">속도</span>
                    <h3 className="assemble-panel-title">Clip Controls</h3>
                  </div>
                </div>

                {!activeClip ? (
                  <div id="assemble-inspector-empty" className="assemble-inspector-empty">
                    <h3>No shot selected</h3>
                    <p>Select a clip from the timeline to control speed and trim.</p>
                  </div>
                ) : (
                  <div id="assemble-inspector-form" className="assemble-inspector-form">
                    <div className="assemble-clip-meta-card">
                      <strong id="assemble-inspector-name">{activeClip.name}</strong>
                      <span id="assemble-inspector-meta">Timeline clip</span>
                    </div>

                    <div className="assemble-control-group assemble-control-card">
                      <div className="assemble-control-label">
                        <span>Speed</span>
                        <strong>{activeClip.speed}x</strong>
                      </div>
                      <div className="assemble-control-row">
                        <input type="range" min="0.25" max="2" step="0.05" value={activeClip.speed} onChange={(event) => updateActiveClip({ speed: Number(event.target.value) })} />
                        <input type="number" min="0.25" max="2" step="0.05" value={activeClip.speed} onChange={(event) => updateActiveClip({ speed: Number(event.target.value) })} />
                      </div>
                    </div>

                    <div className="assemble-control-group assemble-control-card">
                      <div className="assemble-control-label">
                        <span>Trim Start</span>
                        <strong>{activeClip.trimStart}s</strong>
                      </div>
                      <div className="assemble-control-row">
                        <input type="range" min="0" max="5" step="0.1" value={activeClip.trimStart} onChange={(event) => updateActiveClip({ trimStart: Number(event.target.value) })} />
                        <input type="number" min="0" max="5" step="0.1" value={activeClip.trimStart} onChange={(event) => updateActiveClip({ trimStart: Number(event.target.value) })} />
                      </div>
                    </div>

                    <div className="assemble-control-group assemble-control-card">
                      <div className="assemble-control-label">
                        <span>Trim End</span>
                        <strong>{activeClip.trimEnd}s</strong>
                      </div>
                      <div className="assemble-control-row">
                        <input type="range" min="0.1" max="5" step="0.1" value={activeClip.trimEnd} onChange={(event) => updateActiveClip({ trimEnd: Number(event.target.value) })} />
                        <input type="number" min="0.1" max="5" step="0.1" value={activeClip.trimEnd} onChange={(event) => updateActiveClip({ trimEnd: Number(event.target.value) })} />
                      </div>
                    </div>

                    <div className="assemble-inspector-toggle-grid">
                      <div className="assemble-flag-card">
                        <span>Reverse</span>
                        <strong>{activeClip.reverse ? "On" : "Off"}</strong>
                      </div>
                      <div className="assemble-flag-card">
                        <span>Flip</span>
                        <strong>{activeClip.flipHorizontal ? "On" : "Off"}</strong>
                      </div>
                    </div>
                  </div>
                )}

                <div className="assemble-control-group assemble-control-card">
                  <div className="assemble-control-label">
                    <span>Aspect Ratio</span>
                  </div>
                  <div id="assemble-ratio-group" className="assemble-ratio-grid">
                    {(["16:9", "1:1", "4:5", "9:16"] as AspectRatio[]).map((ratio) => (
                      <button key={ratio} className={`assemble-ratio-btn ${aspectRatio === ratio ? "is-active" : ""}`} type="button" onClick={() => setAspectRatio(ratio)}>
                        {ratio}
                      </button>
                    ))}
                  </div>
                  <div className="assemble-control-label assemble-fit-label">
                    <span>Source Fit</span>
                  </div>
                  <div id="assemble-fit-mode-group" className="assemble-fit-mode-grid">
                    {(["crop", "fill"] as AspectMode[]).map((mode) => (
                      <button key={mode} className={`assemble-fit-btn ${aspectMode === mode ? "is-active" : ""}`} type="button" onClick={() => setAspectMode(mode)}>
                        {mode === "crop" ? "Crop" : "Blur Fill"}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            </aside>
          </div>
        </div>
      </main>
    );
  }

  return (
    <main id="workspace-feature-3" className="is-workspace">
      <div className="workspace-breadcrumb">
        <button className="breadcrumb-btn" type="button" onClick={showMenu}>
          <span className="material-symbols-outlined">arrow_back</span> Back
        </button>
        <span className="breadcrumb-divider">/</span>
        <span className="breadcrumb-current">Video Studio</span>
        <span className="breadcrumb-divider">/</span>
        <span className="breadcrumb-current">Post-Production</span>
      </div>
      <div className="is-workspace-card">
        <section className="is-panel-left">
          <h2 className="is-section-title">Post-Production</h2>
          <p className="is-section-desc">Add voiceovers, background music, and subtitles to your video.</p>
          <label className="is-upload-box">
            <span className="material-symbols-outlined is-upload-icon">cloud_upload</span>
            <span className="is-upload-label">Upload Video</span>
            <span className="is-upload-sub">mp4, mov & webm (up to 100mb)</span>
          </label>
          <div className="is-instruction-box">
            <label className="input-label">Post-Production Instructions</label>
            <textarea className="dark-input" rows={4} placeholder="eg. 배경 음악을 추가하고, 자막을 넣어줘. 목소리 내레이션도 추가해줘." />
          </div>
          <button className="is-generate-btn" type="button" disabled>
            <span className="material-symbols-outlined">auto_fix_high</span>
            GENERATE POST-PRODUCTION VIDEO
          </button>
        </section>
        <section className="is-panel-right">
          <div className="is-placeholder-modern">
            <div className="placeholder-preview-box">
              <div className="preview-image-slot">
                <span className="material-symbols-outlined">movie</span>
              </div>
              <span className="preview-arrow">→</span>
              <div className="preview-image-slot">
                <span className="material-symbols-outlined">movie</span>
              </div>
            </div>
            <h3 className="placeholder-title">Ready for Post-Production</h3>
            <p className="placeholder-desc">Upload Video to Add Effects and Audio</p>
          </div>
        </section>
      </div>
    </main>
  );
}
