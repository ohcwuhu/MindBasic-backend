"""主题社群：公开浏览、加入/退出、帖子、评论、点赞。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_async_db,
    get_current_coach,
    get_current_user,
    get_optional_user,
)
from app.api.response import ok, paginated
from app.models.coach import CoachProfile
from app.models.user import User
from app.schemas.community import (
    CommunityBriefOut,
    CommunityCommentIn,
    CommunityCommentOut,
    CommunityCreateIn,
    CommunityDetailOut,
    CommunityPatchIn,
    CommunityPostIn,
    CommunityPostOut,
)
from app.services.community_service import (
    create_comment,
    create_community,
    create_post,
    delete_comment,
    delete_post,
    get_community_detail,
    get_post_with_comments,
    join_community,
    leave_community,
    like_post,
    list_communities,
    list_my_communities,
    list_posts,
    toggle_pin,
    update_community,
)

router = APIRouter(prefix="/communities", tags=["communities"])


@router.get("")
async def community_list(
    request: Request,
    keyword: str | None = Query(default=None, max_length=32),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=50, alias="pageSize"),
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    rows, total = await list_communities(db, keyword, page, pageSize, user.id if user else None)
    items = [CommunityBriefOut(**item).model_dump(by_alias=True) for item in rows]
    return ok(paginated(items, total, page, pageSize), trace_id=request.state.trace_id)


@router.get("/mine")
async def my_communities(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    items = [CommunityBriefOut(**item).model_dump(by_alias=True) for item in await list_my_communities(db, user.id)]
    return ok({"items": items}, trace_id=request.state.trace_id)


@router.post("", status_code=201)
async def community_create(
    body: CommunityCreateIn,
    request: Request,
    user: User = Depends(get_current_user),
    coach: CoachProfile = Depends(get_current_coach),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    return ok(
        CommunityDetailOut(**await create_community(db, coach, user, body)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@router.get("/{community_id}")
async def community_detail(
    community_id: int,
    request: Request,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    return ok(
        CommunityDetailOut(**await get_community_detail(db, community_id, user.id if user else None)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@router.patch("/{community_id}")
async def community_update(
    community_id: int,
    body: CommunityPatchIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    community = await update_community(db, user.id, community_id, body)
    return ok({"id": community.id, "name": community.name, "description": community.description}, trace_id=request.state.trace_id)


@router.post("/{community_id}/join")
async def community_join(
    community_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    return ok(await join_community(db, user.id, community_id), trace_id=request.state.trace_id)


@router.post("/{community_id}/leave")
async def community_leave(
    community_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    return ok(await leave_community(db, user.id, community_id), trace_id=request.state.trace_id)


@router.get("/{community_id}/posts")
async def post_list(
    community_id: int,
    request: Request,
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=50, alias="pageSize"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    rows, total = await list_posts(db, community_id, user.id, page, pageSize)
    items = [CommunityPostOut(**item).model_dump(by_alias=True) for item in rows]
    return ok(paginated(items, total, page, pageSize), trace_id=request.state.trace_id)


@router.post("/{community_id}/posts", status_code=201)
async def post_create(
    community_id: int,
    body: CommunityPostIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    return ok(
        CommunityPostOut(**await create_post(db, user, community_id, body)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@router.get("/{community_id}/posts/{post_id}")
async def post_detail(
    community_id: int,
    post_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    data = await get_post_with_comments(db, community_id, user.id, post_id)
    data["post"] = CommunityPostOut(**data["post"]).model_dump(by_alias=True)
    data["comments"] = [CommunityCommentOut(**c).model_dump(by_alias=True) for c in data["comments"]]
    return ok(data, trace_id=request.state.trace_id)


@router.delete("/{community_id}/posts/{post_id}", status_code=204)
async def post_delete(
    community_id: int,
    post_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> None:
    await delete_post(db, user.id, community_id, post_id)


@router.patch("/{community_id}/posts/{post_id}/pin")
async def post_pin(
    community_id: int,
    post_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    pinned = await toggle_pin(db, user.id, community_id, post_id)
    return ok({"isPinned": pinned}, trace_id=request.state.trace_id)


@router.post("/{community_id}/posts/{post_id}/like")
async def post_like(
    community_id: int,
    post_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    return ok(await like_post(db, user.id, post_id), trace_id=request.state.trace_id)


@router.post("/{community_id}/posts/{post_id}/comments", status_code=201)
async def comment_create(
    community_id: int,
    post_id: int,
    body: CommunityCommentIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    return ok(
        CommunityCommentOut(**await create_comment(db, user, post_id, body.content)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@router.delete("/{community_id}/posts/{post_id}/comments/{comment_id}", status_code=204)
async def comment_delete(
    community_id: int,
    post_id: int,
    comment_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> None:
    await delete_comment(db, user.id, comment_id)
