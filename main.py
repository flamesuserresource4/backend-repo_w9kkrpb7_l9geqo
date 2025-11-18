import os
from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from bson import ObjectId
import io
import json
import csv
from datetime import datetime

from database import db, create_document, get_documents
from schemas import Photo, Catalog

app = FastAPI(title="Photo Search API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"message": "Photo Search API running"}

@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"
    return response

# -------- Ingest endpoints --------
class PhotoIngest(BaseModel):
    catalog: Optional[str] = None
    source: Optional[str] = "lightroom"
    items: List[Photo]

@app.post("/api/ingest")
def ingest_photos(payload: PhotoIngest):
    # Ensure catalog exists (simple by name)
    catalog_id: Optional[str] = None
    if payload.catalog:
        existing = db["catalog"].find_one({"name": payload.catalog})
        if existing:
            catalog_id = str(existing.get("_id"))
        else:
            catalog_id = create_document("catalog", Catalog(name=payload.catalog, source=payload.source))

    inserted_ids: List[str] = []
    for item in payload.items:
        data = item.model_dump()
        if catalog_id and not data.get("catalog_id"):
            data["catalog_id"] = catalog_id
        new_id = create_document("photo", data)
        inserted_ids.append(new_id)
    return {"inserted": len(inserted_ids), "ids": inserted_ids}


def _coerce_bool(val: Any) -> Optional[bool]:
    if val is None or val == "":
        return None
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    if s in {"1", "true", "yes", "y", "picked"}:
        return True
    if s in {"0", "false", "no", "n", "unpicked"}:
        return False
    return None


def _coerce_int(val: Any) -> Optional[int]:
    try:
        return int(val) if val not in (None, "") else None
    except Exception:
        return None


def _coerce_float(val: Any) -> Optional[float]:
    try:
        return float(val) if val not in (None, "") else None
    except Exception:
        return None


def _parse_date(val: Any) -> Optional[datetime]:
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%m/%d/%Y", "%m/%d/%Y %H:%M:%S"):
        try:
            return datetime.strptime(str(val), fmt)
        except Exception:
            continue
    # Lightroom captureTime is seconds since 1/1/2001 sometimes; handle epoch-ish numbers
    try:
        num = float(val)
        # if number seems like unix timestamp
        if num > 1e9 and num < 2e10:
            return datetime.utcfromtimestamp(num)
    except Exception:
        pass
    return None


@app.post("/api/ingest/upload")
async def ingest_upload(
    file: UploadFile = File(...),
    catalog: Optional[str] = Form(None),
    source: Optional[str] = Form("upload"),
):
    content_type = file.content_type or ""
    raw = await file.read()

    def ensure_catalog_id(cat: Optional[str], src: Optional[str]) -> Optional[str]:
        if not cat:
            return None
        existing = db["catalog"].find_one({"name": cat})
        if existing:
            return str(existing.get("_id"))
        return create_document("catalog", Catalog(name=cat, source=src or "upload"))

    catalog_id = ensure_catalog_id(catalog, source)

    items: List[Photo] = []

    if "json" in content_type or file.filename.lower().endswith(".json"):
        try:
            payload = json.loads(raw.decode("utf-8"))
            if isinstance(payload, dict) and "items" in payload:
                records = payload["items"]
            elif isinstance(payload, list):
                records = payload
            else:
                raise HTTPException(status_code=400, detail="Invalid JSON structure")
            for rec in records:
                # keywords may be comma-separated string
                kws = rec.get("keywords")
                if isinstance(kws, str):
                    rec["keywords"] = [s.strip() for s in kws.split(",") if s.strip()]
                exif = rec.get("exif") or {}
                p = Photo(
                    filename=rec.get("filename") or rec.get("name") or "",
                    path=rec.get("path"),
                    catalog_id=rec.get("catalog_id") or catalog_id,
                    title=rec.get("title"),
                    caption=rec.get("caption"),
                    keywords=rec.get("keywords") or [],
                    rating=_coerce_int(rec.get("rating")),
                    label=rec.get("label"),
                    flagged=_coerce_bool(rec.get("flagged")) or False,
                    capture_date=_parse_date(rec.get("capture_date")),
                    import_date=_parse_date(rec.get("import_date")) or datetime.utcnow(),
                    width=_coerce_int(rec.get("width")),
                    height=_coerce_int(rec.get("height")),
                    exif={
                        "camera": exif.get("camera"),
                        "lens": exif.get("lens"),
                        "iso": _coerce_int(exif.get("iso")),
                        "aperture": _coerce_float(exif.get("aperture")),
                        "shutter": exif.get("shutter"),
                        "focal_length": _coerce_float(exif.get("focal_length")),
                    },
                    thumbnail_url=rec.get("thumbnail_url"),
                    extra=rec.get("extra") or {},
                )
                items.append(p)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"JSON parse error: {str(e)[:120]}")

    elif "csv" in content_type or file.filename.lower().endswith(".csv"):
        try:
            text = raw.decode("utf-8", errors="ignore")
            reader = csv.DictReader(io.StringIO(text))
            for row in reader:
                kws = row.get("keywords") or row.get("Tags") or ""
                if isinstance(kws, str):
                    keywords = [s.strip() for s in kws.replace(";", ",").split(",") if s.strip()]
                else:
                    keywords = []
                p = Photo(
                    filename=row.get("filename") or row.get("name") or row.get("FileName") or "",
                    path=row.get("path") or row.get("Path"),
                    catalog_id=catalog_id,
                    title=row.get("title") or row.get("Title"),
                    caption=row.get("caption") or row.get("Caption"),
                    keywords=keywords,
                    rating=_coerce_int(row.get("rating") or row.get("Rating")),
                    label=row.get("label") or row.get("Label"),
                    flagged=_coerce_bool(row.get("flagged") or row.get("Flagged")) or False,
                    capture_date=_parse_date(row.get("capture_date") or row.get("CaptureDate")),
                    import_date=_parse_date(row.get("import_date") or row.get("ImportDate")) or datetime.utcnow(),
                    width=_coerce_int(row.get("width") or row.get("Width")),
                    height=_coerce_int(row.get("height") or row.get("Height")),
                    exif={
                        "camera": row.get("camera") or row.get("Camera"),
                        "lens": row.get("lens") or row.get("Lens"),
                        "iso": _coerce_int(row.get("iso") or row.get("ISO")),
                        "aperture": _coerce_float(row.get("aperture") or row.get("Aperture")),
                        "shutter": row.get("shutter") or row.get("Shutter"),
                        "focal_length": _coerce_float(row.get("focal_length") or row.get("FocalLength")),
                    },
                    thumbnail_url=row.get("thumbnail_url") or row.get("ThumbnailURL"),
                    extra={},
                )
                items.append(p)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"CSV parse error: {str(e)[:120]}")
    else:
        raise HTTPException(status_code=415, detail="Unsupported file type. Use JSON or CSV.")

    if not items:
        raise HTTPException(status_code=400, detail="No items to ingest")

    inserted_ids: List[str] = []
    for item in items:
        data = item.model_dump()
        new_id = create_document("photo", data)
        inserted_ids.append(new_id)
    return {"inserted": len(inserted_ids), "ids": inserted_ids}


@app.post("/api/ingest/lrcat")
async def ingest_lrcat(file: UploadFile = File(...), catalog: Optional[str] = Form(None)):
    # Import sqlite3 lazily so environments without libsqlite don't fail at server startup
    try:
        import sqlite3  # type: ignore
        import tempfile  # type: ignore
    except Exception:
        raise HTTPException(status_code=501, detail="SQLite not available in this environment")

    if not (file.filename.lower().endswith('.lrcat') or (file.content_type or '').endswith('sqlite3')):
        raise HTTPException(status_code=415, detail="Provide a Lightroom .lrcat file")

    raw = await file.read()
    # Save to a temp file because sqlite3 requires a filename
    with tempfile.NamedTemporaryFile(delete=True, suffix=".sqlite") as tmp:
        tmp.write(raw)
        tmp.flush()
        try:
            conn = sqlite3.connect(tmp.name)
            cur = conn.cursor()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Could not open catalog: {str(e)[:120]}")

        # Create/find catalog document
        cat_name = catalog or os.path.basename(file.filename).rsplit('.', 1)[0]
        existing = db["catalog"].find_one({"name": cat_name})
        if existing:
            catalog_id = str(existing.get("_id"))
        else:
            catalog_id = create_document("catalog", Catalog(name=cat_name, source="lightroom", path=None))

        # Attempt queries across LR versions
        rows: List[Dict[str, Any]] = []
        queries = [
            # Classic fields (approximate)
            (
                "SELECT f.id_local AS file_id, f.baseName, f.extension, fo.pathFromRoot, i.rating, i.captureTime, i.pick, i.colorLabels, i.fileFormat "
                "FROM AgLibraryFile f "
                "JOIN AgLibraryFolder fo ON f.folder = fo.id_local "
                "LEFT JOIN Adobe_images i ON i.rootFile = f.id_local"
            ),
            (
                "SELECT f.id_local AS file_id, f.baseName, f.extension, fo.pathFromRoot, i.rating, i.captureTime, i.pick, i.colorLabels, NULL as fileFormat "
                "FROM AgLibraryFile f "
                "JOIN AgLibraryFolder fo ON f.folder = fo.id_local "
                "LEFT JOIN Adobe_images i ON i.rootFile = f.id_local"
            ),
        ]
        result = None
        for q in queries:
            try:
                cur.execute(q)
                result = cur.fetchall()
                colnames = [d[0] for d in cur.description]
                for r in result:
                    row = dict(zip(colnames, r))
                    rows.append(row)
                break
            except Exception:
                continue

        if not rows:
            # fallback minimal
            try:
                cur.execute("SELECT id_local AS file_id, baseName, extension FROM AgLibraryFile")
                colnames = [d[0] for d in cur.description]
                for r in cur.fetchall():
                    rows.append(dict(zip(colnames, r)))
            except Exception:
                raise HTTPException(status_code=400, detail="Unsupported Lightroom catalog structure")
        conn.close()

    def build_path(row: Dict[str, Any]) -> Optional[str]:
        base = row.get("baseName") or ""
        ext = row.get("extension") or ""
        path_from_root = row.get("pathFromRoot") or ""
        name = f"{base}.{ext}" if ext else base
        if path_from_root:
            return os.path.join(path_from_root, name)
        return name or None

    inserted_ids: List[str] = []
    for row in rows:
        filename = f"{(row.get('baseName') or '')}.{(row.get('extension') or '').strip('.')}".strip('.')
        p = Photo(
            filename=filename or (row.get('baseName') or ''),
            path=build_path(row),
            catalog_id=catalog_id,
            title=None,
            caption=None,
            keywords=[],
            rating=_coerce_int(row.get('rating')),
            label=None if row.get('colorLabels') in (None, '') else str(row.get('colorLabels')).split(',')[0].strip().lower(),
            flagged=bool(_coerce_bool(row.get('pick')) or False),
            capture_date=_parse_date(row.get('captureTime')),
            import_date=datetime.utcnow(),
            width=None,
            height=None,
            exif={
                "camera": None,
                "lens": None,
                "iso": None,
                "aperture": None,
                "shutter": None,
                "focal_length": None,
            },
            thumbnail_url=None,
            extra={"lrcat_file_id": row.get('file_id')}
        )
        new_id = create_document("photo", p.model_dump())
        inserted_ids.append(new_id)

    return {"inserted": len(inserted_ids), "ids": inserted_ids, "catalog": cat_name}

# -------- Search endpoints --------

def build_search_query(
    q: Optional[str] = None,
    rating: Optional[int] = None,
    label: Optional[str] = None,
    flagged: Optional[bool] = None,
    camera: Optional[str] = None,
    lens: Optional[str] = None,
    min_iso: Optional[int] = None,
    max_iso: Optional[int] = None,
    min_capture_date: Optional[str] = None,
    max_capture_date: Optional[str] = None,
    min_import_date: Optional[str] = None,
    max_import_date: Optional[str] = None,
) -> Dict[str, Any]:
    query: Dict[str, Any] = {}

    # Free text across common fields
    if q:
        regex = {"$regex": q, "$options": "i"}
        query["$or"] = [
            {"filename": regex},
            {"title": regex},
            {"caption": regex},
            {"keywords": regex},
        ]

    if rating is not None:
        query["rating"] = rating

    if label:
        query["label"] = {"$regex": f"^{label}$", "$options": "i"}

    if flagged is not None:
        query["flagged"] = flagged

    if camera:
        query["exif.camera"] = {"$regex": camera, "$options": "i"}

    if lens:
        query["exif.lens"] = {"$regex": lens, "$options": "i"}

    iso_clause: Dict[str, Any] = {}
    if min_iso is not None:
        iso_clause["$gte"] = min_iso
    if max_iso is not None:
        iso_clause["$lte"] = max_iso
    if iso_clause:
        query["exif.iso"] = iso_clause

    def add_date_range(field: str, min_val: Optional[str], max_val: Optional[str]):
        range_q: Dict[str, Any] = {}
        if min_val:
            try:
                range_q["$gte"] = datetime.fromisoformat(min_val)
            except Exception:
                pass
        if max_val:
            try:
                range_q["$lte"] = datetime.fromisoformat(max_val)
            except Exception:
                pass
        if range_q:
            query[field] = range_q

    add_date_range("capture_date", min_capture_date, max_capture_date)
    add_date_range("import_date", min_import_date, max_import_date)

    return query

class SearchResponse(BaseModel):
    total: int
    items: List[Dict[str, Any]]

@app.get("/api/search", response_model=SearchResponse)
def search_photos(
    q: Optional[str] = Query(None, description="Free-text search"),
    rating: Optional[int] = Query(None, ge=0, le=5),
    label: Optional[str] = None,
    flagged: Optional[bool] = None,
    camera: Optional[str] = None,
    lens: Optional[str] = None,
    min_iso: Optional[int] = None,
    max_iso: Optional[int] = None,
    min_capture_date: Optional[str] = None,
    max_capture_date: Optional[str] = None,
    min_import_date: Optional[str] = None,
    max_import_date: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(40, ge=1, le=200),
):
    query = build_search_query(q, rating, label, flagged, camera, lens, min_iso, max_iso, min_capture_date, max_capture_date, min_import_date, max_import_date)

    total = db["photo"].count_documents(query)

    cursor = (
        db["photo"]
        .find(query, {"extra": 0})
        .sort([("import_date", -1)])
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    def serialize(doc: Dict[str, Any]) -> Dict[str, Any]:
        doc["id"] = str(doc.pop("_id"))
        return doc

    items = [serialize(d) for d in cursor]
    return SearchResponse(total=total, items=items)

@app.get("/api/photos/{photo_id}")
def get_photo(photo_id: str):
    try:
        doc = db["photo"].find_one({"_id": ObjectId(photo_id)})
        if not doc:
            raise HTTPException(status_code=404, detail="Not found")
        doc["id"] = str(doc.pop("_id"))
        return doc
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")

# Simple stats endpoint for filters UI
@app.get("/api/facets")
def get_facets():
    pipeline = [
        {"$group": {
            "_id": None,
            "ratings": {"$addToSet": "$rating"},
            "labels": {"$addToSet": "$label"},
            "cameras": {"$addToSet": "$exif.camera"},
            "lenses": {"$addToSet": "$exif.lens"},
        }},
        {"$project": {
            "_id": 0,
            "ratings": 1,
            "labels": 1,
            "cameras": 1,
            "lenses": 1,
        }}
    ]
    try:
        agg = list(db["photo"].aggregate(pipeline))
        return agg[0] if agg else {"ratings": [], "labels": [], "cameras": [], "lenses": []}
    except Exception:
        return {"ratings": [], "labels": [], "cameras": [], "lenses": []}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
