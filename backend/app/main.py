from datetime import datetime, timezone
from uuid import uuid4
import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware

from .models import AppVersionCreate, ChangeCheckRequest
from .compatibility import analyze_compatibility
from .firebase import collection_name, read_all, save_doc, storage_mode
from .analyzer import analyze_archives, MAX_ARCHIVE_BYTES

load_dotenv()

app = FastAPI(
    title="VersionShield API",
    description="Pre-deployment compatibility checks for older app versions.",
    version="0.1.0",
)

origins = [x.strip() for x in os.getenv("FRONTEND_ORIGIN", "http://localhost:5173").split(",") if x.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_versions: dict[str, dict] = {}
_reports: dict[str, dict] = {}


def get_versions():
    return read_all(collection_name("versions"), _versions)


def get_reports():
    return read_all(collection_name("reports"), _reports)


@app.get("/api/health")
def health():
    return {"status": "ok", "storage": storage_mode(), "product": "VersionShield"}


@app.get("/api/versions")
def list_versions():
    return {"items": sorted(get_versions(), key=lambda item: item.get("version", ""))}


@app.post("/api/versions", status_code=201)
def create_version(payload: AppVersionCreate):
    existing = [item for item in get_versions() if item.get("version", "").lower() == payload.version.lower()]
    if existing:
        raise HTTPException(status_code=409, detail="That app version is already registered.")
    item = payload.model_dump()
    item["id"] = str(uuid4())
    item["created_at"] = datetime.now(timezone.utc).isoformat()
    save_doc(collection_name("versions"), item["id"], item, _versions)
    return item


@app.delete("/api/versions/{version_id}")
def delete_version(version_id: str):
    db_mode = storage_mode() == "firestore"
    if db_mode:
        from .firebase import get_firestore
        db = get_firestore()
        ref = db.collection(collection_name("versions")).document(version_id)
        if not ref.get().exists:
            raise HTTPException(status_code=404, detail="Version not found.")
        ref.delete()
    else:
        if version_id not in _versions:
            raise HTTPException(status_code=404, detail="Version not found.")
        del _versions[version_id]
    return {"deleted": True, "id": version_id}


@app.post("/api/check")
def check_change(payload: ChangeCheckRequest):
    report = analyze_compatibility(
        get_versions(),
        payload.current_fields,
        payload.proposed_fields,
        payload.endpoint,
        payload.description,
    )
    report["id"] = str(uuid4())
    report["created_at"] = datetime.now(timezone.utc).isoformat()
    save_doc(collection_name("reports"), report["id"], report, _reports)
    return report


@app.get("/api/reports")
def list_reports():
    items = sorted(get_reports(), key=lambda item: item.get("created_at", ""), reverse=True)
    return {"items": items[:50]}


@app.post("/api/demo/seed")
def seed_demo():
    """Idempotently add example versions for an easy first run."""
    examples = [
        {"version": "1.0.0", "platform": "android", "expected_fields": ["user_name", "email"], "active_users": 1200},
        {"version": "1.5.0", "platform": "android", "expected_fields": ["user_name", "email", "avatar_url"], "active_users": 850},
        {"version": "2.0.0", "platform": "android", "expected_fields": ["full_name", "email", "avatar_url"], "active_users": 2100},
    ]
    existing = {item.get("version") for item in get_versions()}
    added = []
    for example in examples:
        if example["version"] in existing:
            continue
        item = dict(example)
        item["id"] = str(uuid4())
        item["created_at"] = datetime.now(timezone.utc).isoformat()
        save_doc(collection_name("versions"), item["id"], item, _versions)
        added.append(item)
    return {"added": added, "message": f"Added {len(added)} demo version(s)."}


@app.get("/")
def root():
    return {"message": "VersionShield API is running", "docs": "/docs"}


@app.post("/api/analyze-source")
async def analyze_source(old_frontend: UploadFile = File(...), old_backend: UploadFile = File(...), new_backend: UploadFile = File(...)):
    """Scan source ZIPs statically; uploaded code is never executed."""
    for upload in (old_frontend, old_backend, new_backend):
        if not upload.filename or not upload.filename.lower().endswith('.zip'):
            raise HTTPException(status_code=400, detail='All three uploads must be .zip files.')
    # Read each upload with a hard cap before allocating an unbounded request body.
    uploads = (old_frontend, old_backend, new_backend)
    contents = []
    for upload in uploads:
        raw = await upload.read(MAX_ARCHIVE_BYTES + 1)
        if len(raw) > MAX_ARCHIVE_BYTES:
            raise HTTPException(status_code=413, detail=f'{upload.filename} exceeds the 20 MB per-archive limit.')
        contents.append(raw)
    try:
        result = analyze_archives(*contents)
        result['id'] = str(uuid4())
        result['created_at'] = datetime.now(timezone.utc).isoformat()
        save_doc(collection_name('source_reports'), result['id'], result, _reports)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
