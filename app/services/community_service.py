"""主题社群业务逻辑：成员计数事务、内容合规、教练带队治理。"""

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.coach import CoachProfile
from app.models.community import (
    Community,
    CommunityComment,
    CommunityLike,
    CommunityMember,
    CommunityPost,
)
from app.models.user import User
from app.services.content_guard import check_banned_words
from app.utils.time import to_iso


# ---------- 查询助手 ----------


async def _coach_profile(db: AsyncSession, user_id: int) -> CoachProfile | None:
    return await db.scalar(select(CoachProfile).where(CoachProfile.user_id == user_id))


async def _membership_map(db: AsyncSession, user_id: int, community_ids: list[int]) -> set[int]:
    if not community_ids:
        return set()
    rows = await db.scalars(
        select(CommunityMember.community_id).where(
            CommunityMember.user_id == user_id,
            CommunityMember.community_id.in_(community_ids),
        )
    )
    return set(rows)


async def _comment_counts(db: AsyncSession, post_ids: list[int]) -> dict[int, int]:
    if not post_ids:
        return {}
    rows = (
        await db.execute(
            select(CommunityComment.post_id, func.count())
            .where(CommunityComment.post_id.in_(post_ids))
            .group_by(CommunityComment.post_id)
        )
    ).all()
    return {post_id: int(count) for post_id, count in rows}


async def _liked_set(db: AsyncSession, user_id: int, post_ids: list[int]) -> set[int]:
    if not post_ids:
        return set()
    rows = await db.scalars(
        select(CommunityLike.post_id).where(
            CommunityLike.user_id == user_id,
            CommunityLike.post_id.in_(post_ids),
        )
    )
    return set(rows)


async def get_community_or_404(db: AsyncSession, community_id: int) -> Community:
    community = await db.get(Community, community_id)
    if community is None or community.status != "ACTIVE":
        raise AppError(404, "COMMUNITY_NOT_FOUND", "社群不存在或已下线")
    return community


async def require_member(db: AsyncSession, user_id: int, community_id: int) -> None:
    membership = await db.scalar(
        select(CommunityMember.id).where(
            CommunityMember.community_id == community_id,
            CommunityMember.user_id == user_id,
        )
    )
    if membership is None:
        raise AppError(403, "COMMUNITY_JOIN_REQUIRED", "请先加入社群")


async def _is_owner(db: AsyncSession, user_id: int, community_id: int) -> bool:
    coach = await _coach_profile(db, user_id)
    if coach is None:
        return False
    community = await db.get(Community, community_id)
    return community is not None and community.coach_id == coach.id


def _brief(community: Community, coach_nickname: str | None = None, joined: bool = False) -> dict:
    return {
        "id": community.id,
        "name": community.name,
        "description": community.description,
        "cover_url": community.cover_url,
        "coach_nickname": coach_nickname,
        "member_count": community.member_count,
        "joined": joined,
    }


# ---------- 社群 ----------


async def list_communities(
    db: AsyncSession, keyword: str | None, page: int, page_size: int, user_id: int | None = None
) -> tuple[list[dict], int]:
    stmt = (
        select(Community, User.nickname)
        .outerjoin(CoachProfile, CoachProfile.id == Community.coach_id)
        .outerjoin(User, User.id == CoachProfile.user_id)
        .where(Community.status == "ACTIVE")
    )
    if keyword:
        stmt = stmt.where(Community.name.like(f"%{keyword}%"))
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (
        await db.execute(
            stmt.order_by(Community.member_count.desc(), Community.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    community_ids = [community.id for community, _ in rows]
    joined = await _membership_map(db, user_id, community_ids) if user_id else set()
    items = [_brief(community, nickname, community.id in joined) for community, nickname in rows]
    return items, total


async def get_community_detail(
    db: AsyncSession, community_id: int, user_id: int | None = None
) -> dict:
    row = (
        await db.execute(
            select(Community, User.nickname)
            .outerjoin(CoachProfile, CoachProfile.id == Community.coach_id)
            .outerjoin(User, User.id == CoachProfile.user_id)
            .where(Community.id == community_id)
        )
    ).first()
    if row is None:
        raise AppError(404, "COMMUNITY_NOT_FOUND", "社群不存在")
    community, coach_nickname = row
    if community.status != "ACTIVE":
        raise AppError(404, "COMMUNITY_NOT_FOUND", "社群不存在或已下线")
    joined = False
    can_manage = False
    if user_id:
        joined = bool(await _membership_map(db, user_id, [community.id]))
        can_manage = await _is_owner(db, user_id, community.id)
    data = _brief(community, coach_nickname, joined)
    data.update({
        "can_manage": can_manage,
        "max_members": community.max_members,
        "created_at": to_iso(community.created_at),
    })
    return data


async def create_community(db: AsyncSession, coach: CoachProfile, user: User, data) -> dict:
    check_banned_words(data.name, data.description)
    exists = await db.scalar(select(Community.id).where(Community.name == data.name.strip()))
    if exists is not None:
        raise AppError(409, "CONFLICT", "社群名称已存在")
    community = Community(
        name=data.name.strip(),
        description=data.description.strip(),
        cover_url=data.cover_url,
        coach_id=coach.id,
        member_count=1,
    )
    db.add(community)
    await db.flush()
    db.add(CommunityMember(community_id=community.id, user_id=user.id, role="OWNER"))
    await db.commit()
    await db.refresh(community)
    data = _brief(community, user.nickname, True)
    data.update({"can_manage": True, "max_members": community.max_members, "created_at": to_iso(community.created_at)})
    return data


async def update_community(db: AsyncSession, user_id: int, community_id: int, data) -> Community:
    community = await get_community_or_404(db, community_id)
    if not await _is_owner(db, user_id, community_id):
        raise AppError(403, "FORBIDDEN", "仅带队教练可管理社群")
    changes = data.model_dump(exclude_unset=True, exclude_none=True)
    if "name" in changes:
        name = changes["name"].strip()
        dup = await db.scalar(select(Community.id).where(Community.name == name, Community.id != community_id))
        if dup is not None:
            raise AppError(409, "CONFLICT", "社群名称已存在")
        changes["name"] = name
    if "description" in changes:
        changes["description"] = changes["description"].strip()
    check_banned_words(changes.get("name"), changes.get("description"))
    for field in ("name", "description", "cover_url"):
        if field in changes:
            setattr(community, field, changes[field])
    await db.commit()
    await db.refresh(community)
    return community


async def join_community(db: AsyncSession, user_id: int, community_id: int) -> dict:
    community = await get_community_or_404(db, community_id)
    membership = await db.scalar(
        select(CommunityMember.id).where(
            CommunityMember.community_id == community_id,
            CommunityMember.user_id == user_id,
        )
    )
    if membership is not None:
        raise AppError(409, "CONFLICT", "你已加入该社群")
    result = await db.execute(
        update(Community)
        .where(Community.id == community_id, Community.member_count < Community.max_members)
        .values(member_count=Community.member_count + 1)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        raise AppError(409, "COMMUNITY_FULL", "社群人数已满")
    db.add(CommunityMember(community_id=community_id, user_id=user_id, role="MEMBER"))
    await db.commit()
    return {"joined": True, "member_count": community.member_count + 1}


async def leave_community(db: AsyncSession, user_id: int, community_id: int) -> dict:
    community = await db.get(Community, community_id)
    if community is None:
        raise AppError(404, "COMMUNITY_NOT_FOUND", "社群不存在")
    membership = await db.scalar(
        select(CommunityMember).where(
            CommunityMember.community_id == community_id,
            CommunityMember.user_id == user_id,
        )
    )
    if membership is None:
        raise AppError(409, "CONFLICT", "你尚未加入该社群")
    await db.delete(membership)
    await db.execute(
        update(Community)
        .where(Community.id == community_id, Community.member_count > 0)
        .values(member_count=Community.member_count - 1)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return {"joined": False, "member_count": community.member_count - 1}


async def list_my_communities(db: AsyncSession, user_id: int) -> list[dict]:
    rows = (
        await db.execute(
            select(Community, User.nickname)
            .join(CommunityMember, CommunityMember.community_id == Community.id)
            .outerjoin(CoachProfile, CoachProfile.id == Community.coach_id)
            .outerjoin(User, User.id == CoachProfile.user_id)
            .where(CommunityMember.user_id == user_id, Community.status == "ACTIVE")
            .order_by(CommunityMember.joined_at.desc())
        )
    ).all()
    return [_brief(community, nickname, True) for community, nickname in rows]


# ---------- 帖子 ----------


async def _post_out(db: AsyncSession, post: CommunityPost, nickname: str, liked: bool, comment_count: int) -> dict:
    return {
        "id": post.id,
        "community_id": post.community_id,
        "user_id": post.user_id,
        "nickname": nickname,
        "content": post.content,
        "image_url": post.image_url,
        "is_pinned": bool(post.is_pinned),
        "like_count": post.like_count,
        "liked": liked,
        "comment_count": comment_count,
        "created_at": to_iso(post.created_at),
    }


async def list_posts(
    db: AsyncSession, community_id: int, user_id: int, page: int, page_size: int
) -> tuple[list[dict], int]:
    await require_member(db, user_id, community_id)
    stmt = (
        select(CommunityPost, User.nickname)
        .join(User, User.id == CommunityPost.user_id)
        .where(CommunityPost.community_id == community_id)
    )
    total = await db.scalar(select(func.count()).select_from(CommunityPost).where(CommunityPost.community_id == community_id)) or 0
    rows = (
        await db.execute(
            stmt.order_by(CommunityPost.is_pinned.desc(), CommunityPost.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    post_ids = [post.id for post, _ in rows]
    counts = await _comment_counts(db, post_ids)
    liked = await _liked_set(db, user_id, post_ids)
    items = [
        await _post_out(db, post, nickname, post.id in liked, counts.get(post.id, 0))
        for post, nickname in rows
    ]
    return items, total


async def create_post(db: AsyncSession, user: User, community_id: int, data) -> dict:
    await require_member(db, user.id, community_id)
    check_banned_words(data.content)
    post = CommunityPost(
        community_id=community_id,
        user_id=user.id,
        content=data.content.strip(),
        image_url=data.image_url,
    )
    db.add(post)
    await db.commit()
    await db.refresh(post)
    return await _post_out(db, post, user.nickname, False, 0)


async def get_post_with_comments(
    db: AsyncSession, community_id: int, user_id: int, post_id: int
) -> dict:
    await require_member(db, user_id, community_id)
    row = (
        await db.execute(
            select(CommunityPost, User.nickname)
            .join(User, User.id == CommunityPost.user_id)
            .where(CommunityPost.id == post_id, CommunityPost.community_id == community_id)
        )
    ).first()
    if row is None:
        raise AppError(404, "POST_NOT_FOUND", "帖子不存在")
    post, nickname = row
    liked = post.id in await _liked_set(db, user_id, [post.id])
    comment_count = (await _comment_counts(db, [post.id])).get(post.id, 0)
    comment_rows = (
        await db.execute(
            select(CommunityComment, User.nickname)
            .join(User, User.id == CommunityComment.user_id)
            .where(CommunityComment.post_id == post_id)
            .order_by(CommunityComment.created_at.asc())
        )
    ).all()
    comments = [
        {
            "id": comment.id,
            "post_id": comment.post_id,
            "user_id": comment.user_id,
            "nickname": comment_nickname,
            "content": comment.content,
            "created_at": to_iso(comment.created_at),
        }
        for comment, comment_nickname in comment_rows
    ]
    return {
        "post": await _post_out(db, post, nickname, liked, comment_count),
        "comments": comments,
    }


async def delete_post(db: AsyncSession, user_id: int, community_id: int, post_id: int) -> None:
    post = await db.scalar(
        select(CommunityPost).where(
            CommunityPost.id == post_id,
            CommunityPost.community_id == community_id,
        )
    )
    if post is None:
        raise AppError(404, "POST_NOT_FOUND", "帖子不存在")
    if post.user_id != user_id and not await _is_owner(db, user_id, community_id):
        raise AppError(403, "FORBIDDEN", "无权删除该帖子")
    await db.delete(post)
    await db.commit()


async def toggle_pin(db: AsyncSession, user_id: int, community_id: int, post_id: int) -> bool:
    if not await _is_owner(db, user_id, community_id):
        raise AppError(403, "FORBIDDEN", "仅带队教练可置顶")
    post = await db.scalar(
        select(CommunityPost).where(
            CommunityPost.id == post_id,
            CommunityPost.community_id == community_id,
        )
    )
    if post is None:
        raise AppError(404, "POST_NOT_FOUND", "帖子不存在")
    post.is_pinned = not post.is_pinned
    await db.commit()
    return bool(post.is_pinned)


async def like_post(db: AsyncSession, user_id: int, post_id: int) -> dict:
    post = await db.get(CommunityPost, post_id)
    if post is None:
        raise AppError(404, "POST_NOT_FOUND", "帖子不存在")
    await require_member(db, user_id, post.community_id)
    existing = await db.scalar(
        select(CommunityLike).where(
            CommunityLike.post_id == post_id,
            CommunityLike.user_id == user_id,
        )
    )
    if existing is not None:
        await db.delete(existing)
        await db.execute(
            update(CommunityPost)
            .where(CommunityPost.id == post_id, CommunityPost.like_count > 0)
            .values(like_count=CommunityPost.like_count - 1)
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        return {"liked": False, "like_count": max(post.like_count - 1, 0)}
    db.add(CommunityLike(post_id=post_id, user_id=user_id))
    await db.execute(
        update(CommunityPost)
        .where(CommunityPost.id == post_id)
        .values(like_count=CommunityPost.like_count + 1)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return {"liked": True, "like_count": post.like_count + 1}


# ---------- 评论 ----------


async def create_comment(db: AsyncSession, user: User, post_id: int, content: str) -> dict:
    post = await db.get(CommunityPost, post_id)
    if post is None:
        raise AppError(404, "POST_NOT_FOUND", "帖子不存在")
    await require_member(db, user.id, post.community_id)
    check_banned_words(content)
    comment = CommunityComment(post_id=post_id, user_id=user.id, content=content.strip())
    db.add(comment)
    await db.commit()
    await db.refresh(comment)
    return {
        "id": comment.id,
        "post_id": comment.post_id,
        "user_id": comment.user_id,
        "nickname": user.nickname,
        "content": comment.content,
        "created_at": to_iso(comment.created_at),
    }


async def delete_comment(db: AsyncSession, user_id: int, comment_id: int) -> None:
    comment = await db.get(CommunityComment, comment_id)
    if comment is None:
        raise AppError(404, "COMMENT_NOT_FOUND", "评论不存在")
    post = await db.get(CommunityPost, comment.post_id)
    can_manage = post is not None and await _is_owner(db, user_id, post.community_id)
    if comment.user_id != user_id and not can_manage:
        raise AppError(403, "FORBIDDEN", "无权删除该评论")
    await db.delete(comment)
    await db.commit()


# ---------- 平台治理（管理员） ----------


async def admin_list_communities(
    db: AsyncSession, keyword: str | None, status: str | None, page: int, page_size: int
) -> tuple[list[dict], int]:
    stmt = select(Community)
    if keyword:
        stmt = stmt.where(Community.name.like(f"%{keyword}%"))
    if status:
        stmt = stmt.where(Community.status == status)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        await db.scalars(
            stmt.order_by(Community.id.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    )
    coach_ids = [c.coach_id for c in rows if c.coach_id]
    nicknames: dict[int, str] = {}
    if coach_ids:
        for row in await db.execute(
            select(CoachProfile.id, User.nickname)
            .join(User, User.id == CoachProfile.user_id)
            .where(CoachProfile.id.in_(coach_ids))
        ):
            nicknames[row[0]] = row[1]
    items = [
        {
            **_brief(community, nicknames.get(community.coach_id)),
            "status": community.status,
            "created_at": to_iso(community.created_at),
        }
        for community in rows
    ]
    return items, total


async def admin_set_community_status(db: AsyncSession, community_id: int, status: str) -> Community:
    community = await db.get(Community, community_id)
    if community is None:
        raise AppError(404, "COMMUNITY_NOT_FOUND", "社群不存在")
    community.status = status
    await db.commit()
    await db.refresh(community)
    return community
