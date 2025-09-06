from pydantic import BaseModel, HttpUrl
from typing import Optional, List, Literal, Dict, Any


class Profile(BaseModel):
    name: Optional[str] = None
    handle: str
    profile_url: Optional[str] = None
    avatar_url: Optional[str] = None
    bio: Optional[str] = None
    verified: Optional[bool] = None
    scraped_at: Optional[str] = None
    query: Optional[str] = None


class Classification(BaseModel):
    handle: str
    label: Literal["likely_spam", "likely_impersonation", "likely_legit", "uncertain", "rule_violation"]
    confidence: float
    reasons: List[str] = []


class SearchResponse(BaseModel):
    profiles: List[Profile]
    classifications: Optional[List[Classification]] = None


class BlockResult(BaseModel):
    handle: str
    ok: bool
    error: Optional[str] = None

