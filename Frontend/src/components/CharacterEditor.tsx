import { Check, ImagePlus, Loader2, Pencil, Plus, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useCharacterEdits } from "../api/hooks";

const ACCEPT = "image/png,image/jpeg,image/webp";

/** The tile at the end of the character row: opens the new-character form. */
export function NewCharacterTile({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  return (
    <button onClick={onToggle} aria-expanded={open} className="group flex flex-col items-center gap-2.5 text-[12.5px]">
      <span className={`grid h-24 w-[72px] place-items-center rounded-[10px] border border-dashed transition
        ${open ? "border-lime text-lime" : "border-line text-mist group-hover:border-snow group-hover:text-snow"}`}>
        <Plus size={20} /></span>
      <span className={open ? "text-snow" : "text-mist"}>New character</span>
    </button>
  );
}

/** Name + images: saved as a new folder in AI Influencers Characters (the first image is the main one). */
export function NewCharacterForm({ onDone, onCancel }: { onDone: (slug: string) => void; onCancel: () => void }) {
  const { create } = useCharacterEdits();
  const [name, setName] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const previews = useObjectUrls(files);
  return (
    <form className="mt-6 max-w-[640px] rounded-2xl border border-line bg-panel/40 p-5"
      onSubmit={(e) => { e.preventDefault(); create.mutate({ name: name.trim(), files }, { onSuccess: (r) => onDone(r.slug) }); }}>
      <h2 className="text-[15px] font-semibold">New character</h2>
      <p className="mt-1 text-[12.5px] text-mist">Saved as a folder in AI Influencers Characters. The first image is the main one; face and body sheets help the agents.</p>
      <label className="mt-4 block text-[12.5px] text-mist" htmlFor="new-character-name">Name</label>
      <input id="new-character-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={40} autoFocus
        className="mt-1 h-9 w-full max-w-[320px] rounded-lg border border-line bg-transparent px-3 text-[13.5px] text-snow outline-none focus:border-lime" />
      <div className="mt-4 flex flex-wrap items-end gap-3">
        {previews.map((src, i) => (
          <figure key={src} className="relative">
            <img src={src} alt="" className="block h-28 w-auto rounded-lg border border-line bg-white object-contain" />
            {i === 0 && <figcaption className="absolute left-1.5 top-1.5 rounded bg-lime px-1.5 py-0.5 text-[10px] font-semibold text-night">Main</figcaption>}
            <button type="button" aria-label="Remove this image" onClick={() => setFiles(files.filter((_, j) => j !== i))}
              className="absolute right-1.5 top-1.5 grid h-5 w-5 place-items-center rounded bg-black/60 text-white"><X size={12} /></button>
          </figure>
        ))}
        <FilePicker label={files.length ? "More images" : "Choose images"} onFiles={(f) => setFiles([...files, ...f])} />
      </div>
      <div className="mt-5 flex items-center gap-3">
        <button disabled={!name.trim() || !files.length || create.isPending}
          className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-lime px-4 text-[13px] font-medium text-night disabled:opacity-40">
          {create.isPending && <Loader2 size={14} className="animate-spin" />}Add character</button>
        <button type="button" onClick={onCancel} className="text-[13px] text-mist hover:text-snow">Cancel</button>
      </div>
      {create.isError && <p role="alert" className="mt-3 text-[12.5px] text-bad">{(create.error as Error).message}</p>}
    </form>
  );
}

/** The character's name with a pencil: renames its folder (its runs, ratings and targets stay with it). */
export function CharacterName({ slug, name, onRenamed }: { slug: string; name: string; onRenamed: (slug: string) => void }) {
  const { rename } = useCharacterEdits();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(name);
  useEffect(() => { setDraft(name); setEditing(false); }, [name]);
  if (!editing) {
    return (
      <div className="flex items-center gap-2">
        <h2 className="text-[22px] font-semibold">{name}</h2>
        <button onClick={() => setEditing(true)} aria-label={`Rename ${name}`} title="Rename"
          className="grid h-7 w-7 place-items-center rounded-md text-mist hover:bg-panel hover:text-snow"><Pencil size={14} /></button>
      </div>
    );
  }
  return (
    <form className="flex flex-wrap items-center gap-2"
      onSubmit={(e) => { e.preventDefault(); rename.mutate({ slug, name: draft.trim() }, { onSuccess: (r) => onRenamed(r.slug) }); }}>
      <input value={draft} onChange={(e) => setDraft(e.target.value)} maxLength={40} autoFocus aria-label="Character name"
        className="h-9 w-[260px] rounded-lg border border-line bg-transparent px-3 text-[16px] font-semibold text-snow outline-none focus:border-lime" />
      <button disabled={!draft.trim() || rename.isPending} aria-label="Save name"
        className="grid h-9 w-9 place-items-center rounded-lg bg-lime text-night disabled:opacity-40">
        {rename.isPending ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />}</button>
      <button type="button" aria-label="Cancel" onClick={() => { setDraft(name); setEditing(false); rename.reset(); }}
        className="grid h-9 w-9 place-items-center rounded-lg text-mist hover:text-snow"><X size={15} /></button>
      {rename.isError && <p role="alert" className="w-full text-[12.5px] text-bad">{(rename.error as Error).message}</p>}
    </form>
  );
}

/** The last tile of the image list: adds images to the character's folder. */
export function AddImagesTile({ slug }: { slug: string }) {
  const { addImages } = useCharacterEdits();
  return (
    <li>
      <FilePicker tall busy={addImages.isPending} label="Add images" onFiles={(files) => addImages.mutate({ slug, files })} />
      {addImages.isError && <p role="alert" className="mt-1 max-w-[160px] text-[12px] text-bad">{(addImages.error as Error).message}</p>}
    </li>
  );
}

function FilePicker({ label, onFiles, tall, busy }: { label: string; onFiles: (f: File[]) => void; tall?: boolean; busy?: boolean }) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <label className={`flex cursor-pointer flex-col items-center justify-center gap-1.5 rounded-lg border border-dashed border-line px-4 text-[12px] text-mist transition hover:border-snow hover:text-snow
      ${tall ? "h-40 w-[110px]" : "h-28 w-[110px]"} ${busy ? "pointer-events-none opacity-60" : ""}`}>
      {busy ? <Loader2 size={18} className="animate-spin" /> : <ImagePlus size={18} />}{busy ? "Saving…" : label}
      <input ref={input} type="file" accept={ACCEPT} multiple className="sr-only"
        onChange={(e) => { const f = Array.from(e.target.files ?? []); if (f.length) onFiles(f); if (input.current) input.current.value = ""; }} />
    </label>
  );
}

function useObjectUrls(files: File[]): string[] {
  const [urls, setUrls] = useState<string[]>([]);
  useEffect(() => {
    const next = files.map((f) => URL.createObjectURL(f));
    setUrls(next);
    return () => next.forEach((u) => URL.revokeObjectURL(u));
  }, [files]);
  return urls;
}
