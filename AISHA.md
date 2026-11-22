# Aisha automation

Aisha Video Studio is a local-first clipping service built on PeakCut. It keeps PeakCut's web app, job queue,
speech model, FFmpeg renderer, and posting tools, then adds two things taken from AutoClip's design:

- **Platform presets** for TikTok, Reels, Shorts, Facebook Reels, Douyin, Xiaohongshu, X, custom output sizes, and
  a generic vertical format. A preset sets the aspect ratio, layout, caption style, clip length, clip count, and
  suggested hashtags.
- **Publish kits**: a ZIP with the finished MP4s, covers, captions, post text, and a `metadata.json` that tells
  an automation exactly what to upload. The schema is `aisha.publish-kit/v1`.

The web app also has campaign briefs, a schedule view, publishing kits, and platform connection status. The result
is deliberately lighter than AutoClip: no Redis, Celery, Postgres, or Tauri. PeakCut's existing FastAPI + SQLite +
FFmpeg pipeline does the work.

## Start it

```bash
./setup.sh          # once: creates .venv and installs requirements
./run-aisha.sh      # creates a token if needed, then starts on http://127.0.0.1:8766
```

On this Termux installation, if `.venv` is absent, `run-aisha.sh` reuses `../PeakCut/.venv` and
`../PeakCut/data/models` so the 480 MB speech model is not downloaded twice. Use `setup.sh` for a fully separate
install.

The token is stored in `data/aisha_token` with owner-only permissions. You can instead set
`PEAKCUT_AISHA_TOKEN` or `PEAKCUT_AISHA_TOKEN_FILE`. Delete the token file and restart to generate a new one.

## API

All routes are under `/api/aisha/v1`. Send either:

```text
Authorization: Bearer <token>
X-Aisha-Token: <token>
```

### Discover

```bash
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8766/api/aisha/v1/manifest
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8766/api/aisha/v1/health
```

### Create from a link

```bash
curl -X POST http://127.0.0.1:8766/api/aisha/v1/jobs \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "video_url": "https://www.youtube.com/watch?v=VIDEO_ID",
    "platform": "tiktok",
    "request_id": "aisha-task-42",
    "options": {"max_clips": 4, "min_seconds": 20, "max_seconds": 60}
  }'
```

### Create from an upload

```bash
curl -X POST http://127.0.0.1:8766/api/aisha/v1/jobs/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@video.mp4" \
  -F "platform=reels" \
  -F 'options={"max_clips":3}'
```

### Poll

```bash
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8766/api/aisha/v1/jobs/JOB_ID
```

The response is PeakCut's job state plus:

- `platform`: the resolved preset;
- `bundle_url` and `publish_manifest_url`;
- for every clip, `download_urls` and `post_text`.

Useful states are `queued`, `running`, `done`, and `error`. `stage` and `progress` are safe to show to a user.
When `status` is `done`, the job is finished even if it found no clips.

### Publish kit

```bash
curl -L -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8766/api/aisha/v1/jobs/JOB_ID/bundle \
  -o kit.zip
```

The ZIP contains:

```text
metadata.json                 # schema, source, platform rules, clip metadata, post text
README.txt
clips/<title>.mp4             # ready to upload
clips/<title>.jpg             # cover frame
clips/<title>.ass             # caption file
posts/<title>.txt             # title + hook + description + hashtags
```

`metadata.json` is the source of truth. Read `clips[].kit_files.video`, `clips[].kit_files.cover`, and
`clips[].kit_files.post_text`; do not reconstruct paths from titles.

### Other routes

```text
GET    /api/aisha/v1/platforms
GET    /api/aisha/v1/jobs
GET    /api/aisha/v1/jobs/{id}/publish
GET    /api/aisha/v1/jobs/{id}/clips/{clip}/video
GET    /api/aisha/v1/jobs/{id}/clips/{clip}/cover
DELETE /api/aisha/v1/jobs/{id}
```

`DELETE` refuses a video that is still queued, running, or recording.

## Aisha integration contract

1. Call `GET /health` or `GET /manifest` once. Cache the platform list.
2. Submit a job with `request_id` if you need to correlate it with an Aisha task.
3. Poll `GET /jobs/{id}` every 3-10 seconds. Respect `429` and retry with backoff.
4. On `status=done`, fetch `GET /jobs/{id}/publish` to inspect clips, then `GET /jobs/{id}/bundle` to download.
5. Keep the token server-side. Do not place it in a web page or Android log.
6. Treat `409` from `/bundle` as "no finished clips yet" and `404` as a missing or deleted job.

The API is intentionally local by default (`PEAKCUT_HOST=127.0.0.1`). If Aisha runs on another device, bind to
the LAN address and put the service behind HTTPS or a private tunnel. A long random token is mandatory; do not
expose it on the public internet without a reverse proxy and rate limiting.

## Platform presets

| id | label | layout | style | seconds | clips |
|---|---|---|---|---|---|
| `tiktok` | TikTok | crop | hype | 15-90 | 6 |
| `reels` | Instagram Reels | crop | bounce | 15-90 | 6 |
| `shorts` | YouTube Shorts | crop | bounce | 15-60 | 6 |
| `facebook` | Facebook Reels | crop | bounce | 15-90 | 6 |
| `douyin` | Douyin | blur | hype | 15-90 | 6 |
| `xiaohongshu` | Xiaohongshu | blur | clean | 20-90 | 6 |
| `vertical` | Generic vertical | crop | bounce | 15-90 | 6 |

Callers may override `max_clips`, `min_seconds`/`min_len`, `max_seconds`/`max_len`, `style`, `layout`, `quality`,
`captions`, and `range_start`/`range_end`. PeakCut clamps the result to its own safe limits.

## Limits in this first version

- Vertical 1080x1920 output only. AutoClip's landscape presets are not ported yet.
- The web UI remains PeakCut's; platform presets and publish kits are currently API-first.
- AutoClip's separate outline/scoring pipeline was not copied. PeakCut's existing `brain.find_clips` remains the
  clip picker, now constrained by platform lengths.
- The kit is a local ZIP. Direct publishing still uses PeakCut's posting tools or Aisha's own publisher.
- No Redis/Celery/Postgres/Tauri. This is intentional for Termux and small local installs.

## Attribution

PeakCut is MIT licensed. AutoClip is MIT licensed. The platform-preset idea and the one-pass clip-window design
were read from AutoClip and reimplemented in PeakCut's modules; no AutoClip runtime is bundled. See
[NOTICE.md](NOTICE.md).
