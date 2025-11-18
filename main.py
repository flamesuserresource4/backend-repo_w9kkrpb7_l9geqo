import os
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from bson import ObjectId

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
    page: int = Query(1, ge=1),
    page_size: int = Query(40, ge=1, le=200),
):
    query = build_search_query(q, rating, label, flagged, camera, lens, min_iso, max_iso)

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
