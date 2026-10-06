# 04: Character Folder

**Status:** Built (Plan 5). Replaces the earlier `profile.md` format, which is no longer read.

A character is **only its images**. Each sub-folder of `AI Influencers Characters/` (set by `CHARACTERS_DIR`) that holds at least one `.png`, `.jpg`, `.jpeg` or `.webp` is a character:

```
AI Influencers Characters/
  Nicolaiz/
    Nicolaiz.png              ← main image (named like the folder)
    Nicolaiz face views.png
    Nicolaiz body views.png
  Tekashi67/
    Tekashi67.png
    Tekashi67 face.png
    Tekashi67 body.png
```

- **Name** = folder name. **Slug** = lower-case name with spaces as `-` (for example `nicolaiz`).
- **Main image**, used as the canonical image for the analyst and cross-check:
  1. the image whose file name equals the folder name;
  2. otherwise the first image whose name doesn't contain "face" or "body";
  3. otherwise the first image.
- Other files (`.md`, `.txt`…) are ignored. Folders without images are ignored.
- **Versions:** the app stores a new character version only when an image is added, removed or changed (hash over names + content).
- **Sync** is automatic: when the page loads the character list, at server start, and before every run.

## What the agents know about a character

Nothing is written by hand. Every run starts with the **Reader**, a vision role that looks at up to 4 of the images, plus the taste profile and the owner's notes, and returns:

| Field | Meaning |
|---|---|
| `look` | what's fixed and visible: face, hair, outfit, era, styling |
| `vibe` | attitude and energy |
| `performance_angle` | the contrast or tension that makes people watch |
| `possible_niches` | 3–6 niche hypotheses the agents test |
| `kling_constraints` | what Kling Motion Control can and can't do with this look |
| `avoid` | formats or moves that break the character or the motion transfer |

The read is stored on the run and rendered into the compact brief that every other role receives. The brief is extended with the taste profile, which the Learner keeps up to date from the owner's 👍/👎 and notes.
