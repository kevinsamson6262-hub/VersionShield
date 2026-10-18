import os
from typing import Any

_db = None
_init_attempted = False


def get_firestore():
    """Return Firestore client when credentials are configured; otherwise None."""
    global _db, _init_attempted
    if _init_attempted:
        return _db
    _init_attempted = True
    credentials_path = os.getenv("FIREBASE_CREDENTIALS", "").strip()
    if not credentials_path:
        return None
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore
        if not firebase_admin._apps:
            firebase_admin.initialize_app(credentials.Certificate(credentials_path))
        _db = firestore.client()
    except Exception as exc:
        raise RuntimeError(
            "Firebase was configured but could not initialize. Check FIREBASE_CREDENTIALS."
        ) from exc
    return _db


def collection_name(suffix: str) -> str:
    prefix = os.getenv("FIRESTORE_COLLECTION_PREFIX", "versionshield").strip() or "versionshield"
    return f"{prefix}_{suffix}"


def read_all(collection: str, memory_store: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    db = get_firestore()
    if db is None:
        return list(memory_store.values())
    return [doc.to_dict() for doc in db.collection(collection).stream()]


def save_doc(collection: str, doc_id: str, data: dict[str, Any], memory_store: dict[str, dict[str, Any]]) -> None:
    db = get_firestore()
    if db is None:
        memory_store[doc_id] = data
    else:
        db.collection(collection).document(doc_id).set(data)


def storage_mode() -> str:
    return "firestore" if get_firestore() is not None else "memory-demo"
