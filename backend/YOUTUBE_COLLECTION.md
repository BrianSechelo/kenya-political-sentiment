# YouTube collection: first live video

This module adds POST `/youtube/ingest` to the backend at Git revision
`6defa8e144f8efec8f5c7f3a41ba95c4e5ac5fcf`. It collects public top-level
comments from one chosen video through the official YouTube Data API v3.

## Install the update

The supplied update ZIP contains the following paths. Copy each into the
matching location in your existing project; the only replacement is main.py.
Do not replace your entire backend folder.

- `backend/app/main.py` (replacement, registers the new router)
- `backend/app/routers/youtube.py` (new)
- `backend/app/schemas/youtube.py` (new)
- `backend/app/services/youtube_client.py` (new)
- `backend/app/services/youtube_ingestion.py` (new)
- `backend/tests/test_youtube_client.py` (new)
- `backend/tests/test_youtube_ingestion.py` (new)
- `backend/requirements-dev.txt` (new, adds the HTTP test client)
- `backend/.env.example` (reference configuration)
- `backend/YOUTUBE_COLLECTION.md` (this guide)

No database migration or change to models.py is required. The collector reuses
Source, Comment and Mention. Runtime HTTP uses Python's standard library.

From your existing backend PowerShell terminal:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: **101 passed** (the previous 28 plus 73 new tests). The existing
datetime.utcnow deprecation warnings do not mean tests failed. The pinned
Starlette version may also warn that its HTTPX test-client adapter is deprecated.

## Configure your own API key locally

1. Open https://console.cloud.google.com/ and create or select a project.
2. In **APIs & Services > Library**, enable **YouTube Data API v3**.
3. In **APIs & Services > Credentials**, create an **API key**.
4. In the key's API restrictions, select **YouTube Data API v3**. This collector
   makes requests from the Python backend; browser-referrer restrictions do not
   apply to that server request. Choose restrictions appropriate to the host
   where you run the backend.
5. Open your existing `backend/.env`. Keep its DATABASE_URL and append:

```dotenv
YOUTUBE_API_KEY=your_actual_key_here
```

Keep the key local. Do not paste it into chat, request JSON, screenshots or Git.
The existing .gitignore already excludes .env. The .env.example file contains
placeholders only and is safe to version. Do not copy it over your existing .env.

Restart Uvicorn after editing .env; dotenv reads environment configuration when
the backend starts:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

## Import a real video

Open http://127.0.0.1:8000/docs. Under **YouTube Collection**, expand
**POST /youtube/ingest**, click **Try it out**, and use:

```json
{
  "video_url": "https://www.youtube.com/watch?v=VIDEO_ID_HERE",
  "max_comments": 100
}
```

Replace the URL with a public Kenyan political video that has comments enabled.
The video ID in the URL must be the actual 11-character YouTube identifier;
VIDEO_ID_HERE is only a placeholder. There is no need to create a source or
manually post comments or mentions beforehand.

The request accepts standard watch URLs, youtu.be links, Shorts, live and embed
video URLs, or a raw 11-character video ID. Only fixed Google API endpoints are
fetched; the supplied URL is parsed to identify the video.

On HTTP 200, the response reports:

| Field | Meaning |
| --- | --- |
| source_id | The database source created or reused for this video |
| source_created | Whether this is a new source |
| video_id / video_title | The video identified by Google |
| fetched_count | Top-level comments examined in this request |
| created_count | New comments saved |
| existing_count | Comments already saved, including repeats within the response |
| skipped_count | Blank comments ignored |
| mentions_created | Politician mentions created automatically |
| pages_fetched | Comment pages requested; excludes the metadata request |
| next_page_token / has_more | Whether collection can continue |
| comment_scope | Always top_level in this first version |

`created_count + existing_count + skipped_count = fetched_count`.
`max_comments` limits examined comments, not the number of new rows. An import
may save comments but create zero mentions if the comments do not explicitly
contain an active politician's full name or saved alias. Add aliases you intend
to monitor through the existing Politician Aliases API. The collector does not
assign every comment to a politician merely because their video was selected.

## Continue to another page

If `has_more` is true, reuse the same video URL and put the returned token in
`page_token` for the next request. Omitting the token starts again at the newest
comments; use that behavior later to collect newly posted comments. Existing
comment IDs are skipped safely.

The request retrieves up to 500 comments and at most 10 comment pages. Google
returns at most 100 threads per page. If a bound is reached, the response carries
the next token so comments aren't silently discarded. It never slices off part
of a fetched page: the final page requests only the remaining allowed count.
Comments can change while you paginate, so this is a live collection rather than
an immutable snapshot.

## Run the existing sentiment stage

After importing, execute **POST /sentiment-analyses/analyze-pending**, limit 100
or up to 500. Repeat batches while they produce results. A comment mentioning
multiple politicians can yield more than one pending mention.

Then execute **GET /sentiment-analyses/summary** with model_version `vader-3.3.2`.
It aggregates all matching analyses, including your existing manual test records.
Those development totals are not a clean research sample.

To verify duplicates, submit the same import body a second time. It should report
existing_count for saved comments and no additional mentions for those rows.
A genuinely new YouTube comment can still produce a new row between runs.

## Failure responses

| HTTP status | Meaning and next action |
| --- | --- |
| 422 | Invalid video URL/token or comments disabled; use a valid video/token |
| 404 | Video is unavailable or was not found |
| 503 | Configure YOUTUBE_API_KEY, enable the API, check restrictions or wait for quota availability |
| 502 | Network/upstream format failure; retry the request |
| 409 | Another collection request saved overlapping rows; retry |

Network requests have a 10-second timeout each and no automatic retries. Google
requests consume the project's API quota; check your Google Cloud quota page.
Provider error bodies and the API key are not returned to Swagger. The key is
sent in the X-Goog-Api-Key header rather than placed in request URLs.

All requested pages are fetched before database writes start. If a page fails,
no portion of that batch is saved. Once fetched, source/comments/mentions commit
together; database or mention-detection failures roll back the batch. Repeating
an import skips existing records. Concurrent imports can return 409 and can be
retried safely; live PostgreSQL concurrency was not tested locally.

## Current scope and next stages

This first version is an on-demand collector for public top-level comments.
Replies require separate comments.list pagination and are not included. No
channel discovery or scheduler has been added. Existing stored comments are
skipped rather than refreshed: edited text, deleted comments, changing likes and
retention/refresh jobs need a later synchronization stage. Stored video metadata
is refreshed when the same video is imported again.

Only comment IDs, plain-text content, likes, publication time, video association
and limited provenance are saved. Author display names, avatars, channel IDs and
the full comment API response are not persisted; author_hash remains NULL.

The existing VADER baseline remains the sentiment engine. Successful collection
does not establish its accuracy for English-Swahili/Sheng or sarcasm. The model
comparison and annotated evaluation remain separate work.

## Validation completed

101 tests passed locally on Python 3.12 with the repository's pinned runtime
dependencies plus the declared HTTPX test dependency. The suite reuses the actual
repository's schemas and models. Tests use isolated SQLite databases and mocked
Google responses; they do not access your PostgreSQL database or consume Google
quota. Google credentials and a real video are needed for the remaining live
smoke test. Existing database schema and migrations were not modified.

Reference documentation checked for this implementation:

- https://developers.google.com/youtube/v3/getting-started
- https://developers.google.com/youtube/v3/docs/videos/list
- https://developers.google.com/youtube/v3/docs/commentThreads/list
- https://developers.google.com/youtube/v3/docs/commentThreads
- https://cloud.google.com/apis/docs/system-parameters
