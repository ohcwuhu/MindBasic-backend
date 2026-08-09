from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_optional_user
from app.api.response import ok
from app.models.user import User
from app.schemas.home import BannerOut, CoachBriefOut, HomeOut, QuickEntryOut
from app.schemas.article import ArticleListOut
from app.services.home_service import (
    QUICK_ENTRIES,
    article_to_out,
    get_banners,
    get_favorite_ids,
    get_featured_articles,
    get_recommended_coaches,
)

router = APIRouter(prefix="/home", tags=["home"])


def banner_to_out(banner) -> BannerOut:
    return BannerOut(
        id=banner.id,
        title=banner.title,
        image_url=banner.image_url,
        link_type=banner.link_type,
        link_value=banner.link_value,
        sort_order=banner.sort_order,
    )


@router.get("")
def get_home(
    request: Request,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
) -> dict:
    banners = get_banners(db)
    articles = get_featured_articles(db)
    favorite_ids = get_favorite_ids(db, user.id, [a.id for a in articles]) if user else set()

    home = HomeOut(
        banners=[banner_to_out(b) for b in banners],
        quick_entries=[QuickEntryOut(**entry) for entry in QUICK_ENTRIES],
        featured_articles=[ArticleListOut(**article_to_out(a, favorite_ids)) for a in articles],
        recommended_coaches=[CoachBriefOut(**c) for c in get_recommended_coaches(db)],
    )
    return ok(home.model_dump(by_alias=True), trace_id=request.state.trace_id)


@router.get("/banners")
def get_banners_endpoint(request: Request, db: Session = Depends(get_db)) -> dict:
    items = [banner_to_out(b) for b in get_banners(db)]
    return ok({"items": items}, trace_id=request.state.trace_id)
