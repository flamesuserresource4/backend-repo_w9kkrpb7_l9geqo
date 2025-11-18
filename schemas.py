"""
Database Schemas for Photo Search

Each Pydantic model corresponds to a MongoDB collection. The collection
name is the lowercase of the class name (e.g., Photo -> "photo").

These schemas are used for validation and to document your data model.
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime

class Catalog(BaseModel):
    """
    Catalogs collection schema
    Collection name: "catalog"
    """
    name: str = Field(..., description="Catalog name")
    source: str = Field("lightroom", description="Origin of catalog (lightroom, apple-photos, capture-one, folder)")
    path: Optional[str] = Field(None, description="Original catalog path on disk if available")
    imported_at: Optional[datetime] = Field(default_factory=datetime.utcnow, description="When this catalog was added")

class Exif(BaseModel):
    camera: Optional[str] = None
    lens: Optional[str] = None
    iso: Optional[int] = None
    shutter: Optional[str] = Field(None, description="Shutter speed, e.g., 1/125")
    aperture: Optional[float] = Field(None, description="Aperture f-number, e.g., 2.8")
    focal_length: Optional[float] = Field(None, description="Focal length in mm")

class Photo(BaseModel):
    """
    Photos collection schema
    Collection name: "photo"
    """
    filename: str = Field(..., description="File name of the image")
    path: Optional[str] = Field(None, description="Absolute or catalog-relative path")
    catalog_id: Optional[str] = Field(None, description="Reference to catalog (_id as string)")

    # Descriptive metadata
    title: Optional[str] = None
    caption: Optional[str] = None
    keywords: List[str] = Field(default_factory=list)

    # Curation
    rating: Optional[int] = Field(None, ge=0, le=5)
    label: Optional[str] = Field(None, description="Color label or tag (e.g., red, yellow, green, blue, purple)")
    flagged: bool = Field(False, description="Pick flag")

    # Dates
    capture_date: Optional[datetime] = None
    import_date: Optional[datetime] = Field(default_factory=datetime.utcnow)

    # Technical
    width: Optional[int] = None
    height: Optional[int] = None
    exif: Optional[Exif] = None

    # Thumbnails / previews
    thumbnail_url: Optional[str] = Field(None, description="URL to a thumbnail or preview")

    # Extra fields from various sources
    extra: Dict[str, Any] = Field(default_factory=dict)
