import type { ReactNode } from "react";

type WorkspaceMenuItem = {
  id: string;
  title: string;
  description: string;
  badge?: string;
};

export function PageShell({
  eyebrow,
  title,
  copy,
  children,
}: {
  eyebrow: string;
  title: string;
  copy: string;
  children: ReactNode;
}) {
  return (
    <main className="studio-shell">
      <section className="mx-auto max-w-[1180px] border-b border-black/10 pb-10">
        <p className="mb-3 text-xs font-bold uppercase tracking-[0.12em] text-[#7b6f61]">{eyebrow}</p>
        <h1 className="m-0 text-5xl font-semibold leading-none tracking-normal text-[#252525] md:text-7xl">{title}</h1>
        <p className="mt-5 max-w-3xl text-base leading-7 text-[#625c53]">{copy}</p>
      </section>
      {children}
    </main>
  );
}

export function WorkspaceMenu({
  items,
  onSelect,
}: {
  items: WorkspaceMenuItem[];
  onSelect: (id: string) => void;
}) {
  return (
    <section className="mx-auto mt-10 grid max-w-[1180px] gap-4 md:grid-cols-3">
      {items.map((item) => (
        <button
          key={item.id}
          type="button"
          onClick={() => onSelect(item.id)}
          className="min-h-56 cursor-pointer rounded-lg border border-black/10 bg-white/75 p-6 text-left shadow-[0_14px_34px_rgba(37,37,37,0.06)] transition duration-200 hover:-translate-y-0.5 hover:border-[#6f5c43]/40 hover:bg-white focus:outline-none focus:ring-2 focus:ring-[#6f5c43]/40"
        >
          <span className="text-xs font-bold uppercase tracking-[0.12em] text-[#9a8873]">{item.badge ?? "Workspace"}</span>
          <h2 className="mt-8 text-2xl font-medium tracking-normal text-[#252525]">{item.title}</h2>
          <p className="mt-3 text-sm leading-6 text-[#625c53]">{item.description}</p>
        </button>
      ))}
    </section>
  );
}

export function WorkspacePanel({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <section className="mx-auto mt-10 max-w-[1180px] rounded-lg border border-black/10 bg-white/75 p-5 shadow-[0_14px_34px_rgba(37,37,37,0.06)]">
      <div className="mb-5">
        <h2 className="text-2xl font-medium tracking-normal text-[#252525]">{title}</h2>
        <p className="mt-2 text-sm leading-6 text-[#625c53]">{description}</p>
      </div>
      {children}
    </section>
  );
}

export function UploadDropzone({
  label,
  help,
  accept,
  multiple = false,
  onFiles,
}: {
  label: string;
  help: string;
  accept: string;
  multiple?: boolean;
  onFiles: (files: File[]) => void;
}) {
  return (
    <label className="flex min-h-36 cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed border-[#6f5c43]/30 bg-white/70 p-5 text-center transition hover:border-[#6f5c43]/60 hover:bg-white">
      <span className="text-sm font-bold uppercase tracking-[0.08em] text-[#9a8873]">{label}</span>
      <span className="mt-2 text-sm leading-6 text-[#625c53]">{help}</span>
      <input
        className="sr-only"
        type="file"
        accept={accept}
        multiple={multiple}
        onChange={(event) => onFiles(Array.from(event.currentTarget.files ?? []))}
      />
    </label>
  );
}

export function FilePreviewList({
  files,
  onRemove,
}: {
  files: File[];
  onRemove: (index: number) => void;
}) {
  if (!files.length) {
    return <p className="rounded-lg border border-black/10 bg-white/50 p-3 text-sm text-[#625c53]">No files selected.</p>;
  }
  return (
    <div className="grid gap-2">
      {files.map((file, index) => (
        <div key={`${file.name}-${index}`} className="grid grid-cols-[1fr_auto] items-center gap-2 rounded-lg border border-black/10 bg-white/60 p-3">
          <span className="min-w-0 truncate text-sm text-[#252525]">{file.name}</span>
          <button
            type="button"
            className="h-8 rounded-lg border border-black/10 bg-white px-3 text-sm"
            onClick={() => onRemove(index)}
          >
            Remove
          </button>
        </div>
      ))}
    </div>
  );
}

export function JobProgress({ status, busy }: { status: string; busy?: boolean }) {
  return (
    <div className="rounded-lg border border-black/10 bg-white/60 p-4">
      <div className="flex items-center gap-3">
        {busy ? <span className="h-3 w-3 animate-pulse rounded-full bg-[#3d8b5f]" /> : <span className="h-3 w-3 rounded-full bg-[#aa9783]" />}
        <p className="m-0 text-sm leading-6 text-[#625c53]">{status}</p>
      </div>
    </div>
  );
}

export function DownloadButton({ url, label = "Download" }: { url: string; label?: string }) {
  return (
    <a className="inline-flex min-h-10 items-center justify-center rounded-lg border border-[#6f5c43]/30 bg-white px-4 text-sm text-[#252525] no-underline" href={`/download?url=${encodeURIComponent(url)}`} download>
      {label}
    </a>
  );
}

export function ResultGrid({ urls, type }: { urls: string[]; type: "image" | "video" }) {
  if (!urls.length) {
    return <div className="rounded-lg border border-black/10 bg-white/50 p-6 text-center text-sm text-[#625c53]">Results will appear here.</div>;
  }
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {urls.map((url, index) => (
        <article key={`${url}-${index}`} className="rounded-lg border border-black/10 bg-white/70 p-3">
          {type === "image" ? (
            <img className="aspect-video w-full rounded-lg object-cover" src={url} alt={`Generated result ${index + 1}`} />
          ) : (
            <video className="aspect-video w-full rounded-lg bg-black object-contain" src={url} controls playsInline />
          )}
          <div className="mt-3">
            <DownloadButton url={url} label={type === "image" ? "Download image" : "Download video"} />
          </div>
        </article>
      ))}
    </div>
  );
}

export function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4">
      <section className="w-full max-w-4xl rounded-lg bg-white p-5 shadow-2xl">
        <div className="mb-4 flex items-center justify-between gap-4">
          <h2 className="text-xl font-semibold">{title}</h2>
          <button type="button" className="rounded-lg border border-black/10 px-3 py-2" onClick={onClose}>
            Close
          </button>
        </div>
        {children}
      </section>
    </div>
  );
}
