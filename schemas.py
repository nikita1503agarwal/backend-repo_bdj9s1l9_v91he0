"""
Database Schemas for Personalized News App

Each Pydantic model represents a collection in MongoDB. The collection name is the lowercase class name.
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict
from datetime import datetime

class User(BaseModel):
    email: Optional[str] = Field(None, description="Email address")
    name: Optional[str] = Field(None, description="Display name")
    auth_provider: str = Field("anonymous", description="anonymous | email | google | apple | github")
    is_verified: bool = Field(False, description="Whether user is verified for content upload")
    language: Optional[str] = Field(None, description="Preferred language code, e.g., en, zh, es")
    region: Optional[str] = Field(None, description="Preferred region code, e.g., US, CN, IN")
    categories: List[str] = Field(default_factory=list, description="Interested categories")

class Article(BaseModel):
    title: str
    content: str
    author_id: str
    language: str = Field("en")
    region: Optional[str] = None
    categories: List[str] = Field(default_factory=list)
    source: Optional[str] = None
    media_urls: List[str] = Field(default_factory=list)
    is_published: bool = Field(True)
    moderation_status: str = Field("pending", description="pending | approved | rejected")
    moderation_notes: Optional[str] = None
    translated: Dict[str, Dict[str, str]] = Field(default_factory=dict, description="lang -> {title, content}")

class Interaction(BaseModel):
    user_id: str
    article_id: str
    action: str = Field(..., description="view | like | share")
    reading_time_sec: Optional[int] = 0
    engagement: float = 0.0
    created_at: Optional[datetime] = None

class Preference(BaseModel):
    user_id: str
    language: Optional[str] = None
    region: Optional[str] = None
    categories: List[str] = Field(default_factory=list)

class Session(BaseModel):
    user_id: str
    token: str
    created_at: Optional[datetime] = None

# Admin and Billing models could be added later for subscriptions and ads placement
