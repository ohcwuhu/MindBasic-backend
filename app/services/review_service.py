"""用户评价业务逻辑。"""

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.models.coach import Appointment, CoachProfile
from app.models.user import User
from app.models.v1_1 import Review
from app.utils.time import to_iso


def get_my_review(db: Session, user_id: int, appointment_id: int) -> Review | None:
    return db.scalar(
        select(Review).where(
            Review.appointment_id == appointment_id,
            Review.user_id == user_id,
        )
    )


def create_review(db: Session, user: User, appointment_id: int, rating: int, content: str | None) -> Review:
    appointment = db.scalar(
        select(Appointment).where(Appointment.id == appointment_id, Appointment.user_id == user.id)
    )
    if appointment is None:
        raise AppError(404, "APPOINTMENT_NOT_FOUND", "预约记录不存在")
    if appointment.status != "COMPLETED":
        raise AppError(400, "INVALID_STATE_TRANSITION", "仅已完成的服务可以评价")
    if db.scalar(select(Review.id).where(Review.appointment_id == appointment_id)) is not None:
        raise AppError(409, "CONFLICT", "该预约已评价")

    review = Review(
        appointment_id=appointment.id,
        coach_id=appointment.coach_id,
        user_id=user.id,
        rating=rating,
        content=content,
    )
    db.add(review)
    db.flush()
    avg, count = db.execute(
        select(func.avg(Review.rating), func.count())
        .where(Review.coach_id == appointment.coach_id)
    ).one()
    db.execute(
        update(CoachProfile)
        .where(CoachProfile.id == appointment.coach_id)
        .values(rating=round(float(avg), 2), review_count=int(count))
    )
    db.commit()
    db.refresh(review)
    return review


async def coach_reviews(
    db: AsyncSession, coach_id: int, page: int, page_size: int
) -> tuple[list[tuple[Review, User]], int]:
    base = select(Review, User).join(User, User.id == Review.user_id).where(Review.coach_id == coach_id)
    total = await db.scalar(select(func.count()).select_from(Review).where(Review.coach_id == coach_id)) or 0
    rows = (await db.execute(
        base.order_by(Review.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )).all()
    return rows, total


def review_to_out(review: Review, nickname: str) -> dict:
    return {
        "id": review.id,
        "appointment_id": review.appointment_id,
        "coach_id": review.coach_id,
        "nickname": nickname,
        "rating": review.rating,
        "content": review.content,
        "created_at": to_iso(review.created_at),
    }
