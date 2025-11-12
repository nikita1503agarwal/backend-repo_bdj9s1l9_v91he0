import os
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from database import db, create_document, get_documents
from schemas import User as UserSchema, Article as ArticleSchema, Interaction as InteractionSchema, Preference as PreferenceSchema, Session as SessionSchema

app = FastAPI(title="AI News Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------
# Models for requests/responses
# -----------------------------
class AuthAnonymousResponse(BaseModel):
    user_id: str
    token: str

class PreferenceRequest(BaseModel):
    language: Optional[str] = None
    region: Optional[str] = None
    categories: List[str] = []

class ArticleCreateRequest(BaseModel):
    title: str
    content: str
    language: str = "en"
    region: Optional[str] = None
    categories: List[str] = []
    media_urls: List[str] = []
    source: Optional[str] = None

class InteractionCreateRequest(BaseModel):
    article_id: str
    action: str
    reading_time_sec: Optional[int] = 0
    engagement: float = 0.0

# -----------------------------
# Utility helpers
# -----------------------------
BANNED_KEYWORDS = {"fake", "terror", "hate"}


def simple_moderation(title: str, content: str) -> Dict[str, Any]:
    text = f"{title}\n{content}".lower()
    matched = [w for w in BANNED_KEYWORDS if w in text]
    if matched:
        return {"status": "rejected", "notes": f"Contains flagged terms: {', '.join(matched)}"}
    return {"status": "approved", "notes": "Auto-approved"}


def simple_translate(text: str, target_lang: str) -> str:
    # Placeholder translation: in production integrate with an external service
    if target_lang == "en":
        return text
    return f"[{target_lang} translation] " + text


# -----------------------------
# Health and root
# -----------------------------
@app.get("/")
def read_root():
    return {"message": "AI News Backend running"}


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
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = db.name
            response["connection_status"] = "Connected"
            try:
                response["collections"] = db.list_collection_names()[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️ Connected but Error: {str(e)[:80]}"
        else:
            response["database"] = "⚠️ Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:80]}"
    return response


# -----------------------------
# Auth
# -----------------------------
@app.post("/auth/anonymous", response_model=AuthAnonymousResponse)
def auth_anonymous():
    # Create a user with anonymous provider and a session token
    uid = str(uuid4())
    token = str(uuid4())
    user = UserSchema(auth_provider="anonymous")
    user_dict = user.model_dump()
    user_dict["_id"] = uid
    user_dict["created_at"] = datetime.now(timezone.utc)
    user_dict["updated_at"] = datetime.now(timezone.utc)
    db.user.insert_one(user_dict)

    session = SessionSchema(user_id=uid, token=token)
    sess = session.model_dump()
    sess["created_at"] = datetime.now(timezone.utc)
    db.session.insert_one(sess)
    return {"user_id": uid, "token": token}


# -----------------------------
# Preferences
# -----------------------------
@app.get("/users/{user_id}/preferences", response_model=PreferenceRequest)
def get_preferences(user_id: str):
    pref = db.preference.find_one({"user_id": user_id})
    if not pref:
        # return defaults
        return PreferenceRequest(language=None, region=None, categories=[])
    return PreferenceRequest(language=pref.get("language"), region=pref.get("region"), categories=pref.get("categories", []))


@app.post("/users/{user_id}/preferences")
def set_preferences(user_id: str, payload: PreferenceRequest):
    db.preference.update_one({"user_id": user_id}, {"$set": payload.model_dump()}, upsert=True)
    # also reflect on user profile for convenience
    db.user.update_one({"_id": user_id}, {"$set": {"language": payload.language, "region": payload.region, "categories": payload.categories}})
    return {"status": "ok"}


# -----------------------------
# Articles (create, translate, list)
# -----------------------------
@app.post("/articles")
def create_article(user_id: str, payload: ArticleCreateRequest):
    user = db.user.find_one({"_id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.get("is_verified", False):
        # allow upload but mark pending moderation
        moderation = {"status": "pending", "notes": "Awaiting manual review"}
    else:
        moderation = simple_moderation(payload.title, payload.content)

    doc = ArticleSchema(
        title=payload.title,
        content=payload.content,
        author_id=user_id,
        language=payload.language,
        region=payload.region,
        categories=payload.categories,
        source=payload.source,
        media_urls=payload.media_urls,
        is_published=True,
        moderation_status=moderation["status"],
        moderation_notes=moderation["notes"],
    ).model_dump()

    doc["created_at"] = datetime.now(timezone.utc)
    doc["updated_at"] = datetime.now(timezone.utc)

    inserted_id = db.article.insert_one(doc).inserted_id
    return {"id": str(inserted_id), "moderation_status": doc["moderation_status"], "moderation_notes": doc["moderation_notes"]}


@app.get("/articles/feed")
def get_feed(
    user_id: str,
    language: Optional[str] = None,
    region: Optional[str] = None,
    limit: int = Query(20, ge=1, le=100),
):
    # Load preferences
    pref = db.preference.find_one({"user_id": user_id}) or {}
    lang = language or pref.get("language")
    reg = region or pref.get("region")
    cats = pref.get("categories", [])

    # Base query: published and approved or pending (to allow testing) but prioritize approved
    base_filter: Dict[str, Any] = {"is_published": True}
    if lang:
        base_filter["language"] = lang
    if reg:
        base_filter["region"] = reg

    articles = list(db.article.find(base_filter))

    # Build simple engagement signals from interactions
    interactions = list(db.interaction.find({"user_id": user_id}))
    liked = {it["article_id"] for it in interactions if it.get("action") == "like"}

    def score(article: Dict[str, Any]) -> float:
        s = 0.0
        # approved gets bonus
        if article.get("moderation_status") == "approved":
            s += 5
        # category overlap
        if cats:
            overlap = len(set(article.get("categories", [])) & set(cats))
            s += overlap * 2
        # recency
        created = article.get("created_at")
        if created:
            age_hours = max(1.0, (datetime.now(timezone.utc) - created).total_seconds() / 3600.0)
            s += 10.0 / age_hours
        # if user liked before similar category
        if article.get("_id") in liked:
            s += 3
        return s

    articles.sort(key=score, reverse=True)

    # map to response and handle translation if needed
    feed = []
    for a in articles[:limit]:
        item = {
            "id": str(a.get("_id")),
            "title": a.get("title"),
            "content": a.get("content"),
            "language": a.get("language"),
            "region": a.get("region"),
            "categories": a.get("categories", []),
            "moderation_status": a.get("moderation_status"),
            "created_at": a.get("created_at"),
        }
        # Auto-translate if preference language differs and translation available/needed
        if lang and a.get("language") != lang:
            translated = a.get("translated", {}).get(lang)
            if not translated:
                # create on the fly and store
                tr_title = simple_translate(a.get("title", ""), lang)
                tr_content = simple_translate(a.get("content", ""), lang)
                db.article.update_one({"_id": a["_id"]}, {"$set": {f"translated.{lang}": {"title": tr_title, "content": tr_content}}})
                translated = {"title": tr_title, "content": tr_content}
            item["title"] = translated.get("title")
            item["content"] = translated.get("content")
            item["language"] = lang
        feed.append(item)

    return {"items": feed}


@app.post("/articles/{article_id}/translate")
def translate_article(article_id: str, target_lang: str = Query(...)):
    a = db.article.find_one({"_id": db.article._Database__client.get_database().codec_options.document_class()(article_id)})
    # The above is tricky due to ObjectId; simpler approach:
    from bson import ObjectId
    try:
        oid = ObjectId(article_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid article id")
    a = db.article.find_one({"_id": oid})
    if not a:
        raise HTTPException(status_code=404, detail="Article not found")
    tr_title = simple_translate(a.get("title", ""), target_lang)
    tr_content = simple_translate(a.get("content", ""), target_lang)
    db.article.update_one({"_id": oid}, {"$set": {f"translated.{target_lang}": {"title": tr_title, "content": tr_content}}})
    return {"title": tr_title, "content": tr_content}


# -----------------------------
# Interactions
# -----------------------------
@app.post("/interactions")
def create_interaction(user_id: str, payload: InteractionCreateRequest):
    inter = InteractionSchema(
        user_id=user_id,
        article_id=payload.article_id,
        action=payload.action,
        reading_time_sec=payload.reading_time_sec or 0,
        engagement=payload.engagement or 0.0,
        created_at=datetime.now(timezone.utc),
    ).model_dump()
    db.interaction.insert_one(inter)
    return {"status": "ok"}


# -----------------------------
# Admin helpers
# -----------------------------
@app.post("/admin/verify-user/{user_id}")
def verify_user(user_id: str, secret: str):
    admin_secret = os.getenv("ADMIN_SECRET", "secret")
    if secret != admin_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")
    db.user.update_one({"_id": user_id}, {"$set": {"is_verified": True}})
    return {"status": "ok", "user_id": user_id}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
