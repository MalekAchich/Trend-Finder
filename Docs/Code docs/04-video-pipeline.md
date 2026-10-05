# Code/04: Video Pipeline (deterministic)

**Status:** Draft v1, awaiting review

## Purpose

Turn every candidate (and seed) video into objective, cheap evidence **before** any model judges it (D-15). The output feeds the feasibility filter, the Analyst (D-16) and clustering (D-19).

## Steps

| # | Step | Tool | Output |
|---|---|---|---|
| 1 | Download (≤ 720p mp4, audio kept) | yt-dlp via `get_video` | `media/videos/<canonical_id>.mp4` |
| 2 | Probe | ffprobe | duration, width, height, fps, has_audio |
| 3 | Scene cuts | ffmpeg `select='gt(scene,0.3)'` | cut timestamps, `cut_rate` (cuts per 10 s) |
| 4 | Frame sampling | ffmpeg | 2 fps analysis frames (downscaled to 360 px) plus 12 key frames spread across scenes |
| 5 | Contact sheet | Pillow | 3×4 grid of the 12 key frames, with timestamps, ~1024 px wide → `media/sheets/<id>.jpg` |
| 6 | Transcript | faster-whisper `small`, int8, CPU, VAD on | text, segments, language, `speech_ratio` (speech vs. music) |
| 7 | Pose | MediaPipe Pose Landmarker (`num_poses=3`) on the 2 fps frames | per-frame person count, landmark visibility, head box |
| 8 | Camera motion | OpenCV global optical flow on 2 fps frames | `camera_motion` (normalized 0–1) |
| 9 | Clean segments | from steps 3, 7, 8 | windows ≥ 3 s with exactly one person, no cut, low motion |
| 10 | Fingerprint | perceptual hashes of key frames + audio fingerprint (Chromaprint) | clustering keys (D-19) |

Steps 6–8 run in a **process pool** (max 2 jobs, Q-12: CPU-only). Transcripts are skipped when `has_audio` is false.

## Feasibility metrics (0–1 unless noted)

| Metric | Definition |
|---|---|
| `single_person_ratio` | share of sampled frames with exactly one detected person |
| `body_visibility` | mean share of key landmarks visible (head, shoulders, elbows, wrists, hips, knees, ankles; visibility > 0.5) in single-person frames |
| `hands_visibility` | share of single-person frames with both wrists visible |
| `face_size_ok` | 1 if the median head-box height ≥ 8% of frame height, else scaled down |
| `camera_motion` | normalized mean global motion (0 = static) |
| `cut_rate` | cuts per 10 s |
| `best_clean_segment` | longest clean window `{start_s, end_s}` |

```
feasibility = 100 × ( 0.30·single_person_ratio
                    + 0.30·body_visibility
                    + 0.10·hands_visibility
                    + 0.10·face_size_ok
                    + 0.10·(1 − camera_motion)
                    + 0.10·(1 − min(cut_rate / 3, 1)) )
```

**Hard filter** (skip the Analyst call; the finding is stored as `filtered_feasibility` and stays visible in a "filtered" tab): `single_person_ratio < 0.5` **or** `body_visibility < 0.4` **or** no clean segment ≥ 3 s. Thresholds are configurable.

⚡ The **best clean segment** is passed into the production brief as the exact time window to use as the Kling motion reference. Many viral videos have unusable intros but a perfect 6-second core.

## Storage and retention (D-26)

- `media/videos/`: full videos. Deleted after analysis unless the finding is 👍, a seed, or the best source of a top-20 trend card in the latest run.
- `media/frames/`: 2 fps analysis frames. Deleted right after analysis.
- `media/sheets/`: contact sheets (~150 KB each). Kept.
- Quota (default 5 GB): least-recently-used eviction of deletable media; at 95% new downloads pause and the UI warns.

## Output record (`video_analyses`)

```json
{
  "canonical_id": "tiktok:7412…",
  "probe": {"duration_s": 14.2, "width": 720, "height": 1280, "fps": 30, "has_audio": true},
  "cuts": [3.1, 9.8], "cut_rate": 1.4,
  "transcript": {"language": "en", "text": "…", "speech_ratio": 0.1},
  "pose": {"single_person_ratio": 0.92, "body_visibility": 0.81, "hands_visibility": 0.7, "face_size_ok": 1.0},
  "camera_motion": 0.12,
  "best_clean_segment": {"start_s": 3.2, "end_s": 9.7},
  "feasibility": 84.5,
  "fingerprint": {"frame_hashes": ["…"], "audio_fp": "…"},
  "contact_sheet": "media/sheets/tiktok_7412….jpg",
  "pipeline_version": "1"
}
```
