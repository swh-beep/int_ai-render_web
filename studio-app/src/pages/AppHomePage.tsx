import { useEffect, useMemo, useState } from "react";

type ThumbnailItem = {
  index: number;
  file: string;
};

export function AppHomePage() {
  const [rooms, setRooms] = useState<string[]>([]);
  const [styles, setStyles] = useState<string[]>([]);
  const [variants, setVariants] = useState<ThumbnailItem[]>([]);
  const [selectedRoom, setSelectedRoom] = useState("");
  const [selectedStyle, setSelectedStyle] = useState("");
  const [selectedVariant, setSelectedVariant] = useState<ThumbnailItem | null>(null);
  const [sourceFile, setSourceFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState("");
  const [status, setStatus] = useState("");

  useEffect(() => {
    fetch("/room-types")
      .then((res) => res.json())
      .then((items: string[]) => setRooms(items.includes("Customize") ? items : [...items, "Customize"]))
      .catch(() => setStatus("Failed to load room types."));
  }, []);

  useEffect(() => {
    if (!sourceFile) {
      setPreviewUrl("");
      return undefined;
    }
    const url = URL.createObjectURL(sourceFile);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [sourceFile]);

  const canGenerate = useMemo(() => Boolean(sourceFile && selectedRoom && selectedStyle && (selectedStyle === "Customize" || selectedVariant)), [selectedRoom, selectedStyle, selectedVariant, sourceFile]);

  async function selectRoom(room: string) {
    setSelectedRoom(room);
    setSelectedStyle("");
    setSelectedVariant(null);
    setVariants([]);
    if (room === "Customize") {
      setStyles([]);
      setSelectedStyle("Customize");
      return;
    }

    setStatus("");
    const res = await fetch(`/styles/${encodeURIComponent(room)}`);
    const nextStyles = (await res.json()) as string[];
    setStyles(nextStyles.filter((style) => style !== "Customize"));
  }

  async function selectStyle(style: string) {
    setSelectedStyle(style);
    setSelectedVariant(null);
    setStatus("");
    if (!selectedRoom || style === "Customize") {
      setVariants([]);
      return;
    }

    const res = await fetch(`/api/thumbnails/${encodeURIComponent(selectedRoom)}/${encodeURIComponent(style)}`);
    setVariants((await res.json()) as ThumbnailItem[]);
  }

  function validateGenerate() {
    if (!canGenerate) {
      setStatus("Upload an image, select room type, style, and variant before rendering.");
      return;
    }
    setStatus("Ready to render. Static production rendering remains available from MAIN until this home flow is fully migrated.");
  }

  return (
    <main className="app-home-shell" data-home-ready={canGenerate ? "true" : "false"}>
      <header className="app-home-header">
        <img src="/static/logo.png" alt="Company Logo" className="app-home-logo" />
        <img src="/static/TIOR STUDIO.png" alt="TIOR STUDIO" className="app-home-wordmark" />
        <h1>AI Styling Design</h1>
        <p className="subtitle-sub">Based Google Nanobanana with Gemini</p>
        <p className="subtitle-sub">해당 웹 및 앱은 사내용입니다</p>
        <p className="subtitle-sub">담당자 외 배포 금지</p>
      </header>

      <section className="app-home-upload-section" id="upload-area">
        {!previewUrl ? (
          <label className="drop-zone app-home-drop-zone">
            <span className="material-symbols-outlined is-upload-icon">cloud_upload</span>
            <div className="home-upload-title">Upload Image</div>
            <div className="home-upload-sub">
              Drag &amp; Drop your room photo here or <span className="home-upload-browse">Browse</span>
            </div>
            <small className="home-upload-sub">png, jpg, jpeg &amp; webp (up to 25mb)</small>
            <input type="file" id="file-input" accept="image/*" hidden onChange={(event) => setSourceFile(event.currentTarget.files?.[0] ?? null)} />
          </label>
        ) : (
          <div id="preview-container" className="app-home-preview">
            <img id="image-preview" src={previewUrl} alt="Room Preview" />
            <button id="remove-image" className="icon-btn" type="button" onClick={() => setSourceFile(null)}>
              ×
            </button>
          </div>
        )}
      </section>

      <section className="spatial-constraints-section app-home-constraints">
        <div className="constraints-grid">
          <div className="app-home-dimensions-field">
            <label className="input-label">Space Dimensions (W x D x H mm)</label>
            <input type="text" id="room-dimensions" className="dark-input" placeholder="e.g. 3000 x 3500 x 2400 mm" />
          </div>
          <div className="app-home-placement-field">
            <label className="input-label">Placement Instructions</label>
            <input type="text" id="placement-instructions" className="dark-input" placeholder="e.g. 왼쪽 벽면에 소파를 배치하고 오른쪽에 수납장 배치해줘" />
          </div>
        </div>
      </section>

      <section className="style-section" id="room-section">
        <h2>Select Room Type</h2>
        <div className="style-grid" id="room-grid">
          {rooms.map((room) => (
            <button key={room} className={`style-btn ${selectedRoom === room ? "active" : ""}`} type="button" onClick={() => void selectRoom(room)}>
              {room}
            </button>
          ))}
        </div>
      </section>

      <section className={`style-section ${selectedRoom && selectedRoom !== "Customize" ? "" : "hidden"}`} id="style-section">
        <h2>Select Style</h2>
        <div className="style-grid" id="style-grid">
          {styles.map((style) => (
            <button key={style} className={`style-btn ${selectedStyle === style ? "active" : ""}`} type="button" onClick={() => void selectStyle(style)}>
              {style}
            </button>
          ))}
        </div>
      </section>

      <section className={`style-section ${selectedStyle && selectedStyle !== "Customize" ? "" : "hidden"}`} id="variant-section">
        <h2>Select Variant</h2>
        <div className="style-grid variant-grid" id="variant-grid">
          {variants.map((item) => (
            <button key={item.file} className={`variant-card ${selectedVariant?.file === item.file ? "active" : ""}`} type="button" onClick={() => setSelectedVariant(item)}>
              <img src={`/static/thumbnails/${item.file}`} alt={`Variant ${item.index}`} />
              <span>Variant {item.index}</span>
            </button>
          ))}
        </div>
      </section>

      <section className="app-home-generate">
        <button id="generate-btn" className="generate-btn" type="button" disabled={!canGenerate} onClick={validateGenerate}>
          Generate Design
        </button>
        <p className="app-home-status" role="status">
          {status}
        </p>
      </section>
    </main>
  );
}
