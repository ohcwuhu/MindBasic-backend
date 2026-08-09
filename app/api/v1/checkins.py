"""每日打卡、勋章与排行榜。"""

from datetime import date, datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.response import ok
from app.models.user import User
from app.schemas.checkin import (
    BadgeOut,
    CheckInIn,
    CheckInOut,
    CheckInStatsOut,
    LeaderboardItemOut,
)
from app.services.checkin_service import (
    check_in,
    leaderboard,
    my_badges,
    my_checkins,
    my_stats,
)
from app.utils.time import to_iso

router = APIRouter(prefix="/check-ins", tags=["check-ins"])


def checkin_to_out(record) -> dict:
    return {
        "id": record.id,
        "check_date": record.check_date.isoformat(),
        "content": record.content,
        "created_at": to_iso(record.created_at),
    }


@router.post("", status_code=201)
def daily_check_in(
    body: CheckInIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    record, earned = check_in(db, user, body.content)
    return ok(
        {
            "record": CheckInOut(**checkin_to_out(record)).model_dump(by_alias=True),
            "earnedBadges": [
                BadgeOut(
                    id=b.id,
                    key=b.key,
                    name=b.name,
                    description=b.description,
                    icon=b.icon,
                    earned_at=datetime.now().isoformat(),
                ).model_dump(by_alias=True)
                for b in earned
            ],
        },
        trace_id=request.state.trace_id,
    )


@router.get("")
def my_checkins_endpoint(
    request: Request,
    month: str = Query(default=""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    month = month or date.today().strftime("%Y-%m")
    rows = my_checkins(db, user.id, month)
    items = [CheckInOut(**checkin_to_out(r)).model_dump(by_alias=True) for r in rows]
    return ok({"items": items}, trace_id=request.state.trace_id)


@router.get("/leaderboard")
def leaderboard_endpoint(
    request: Request,
    period: str = Query(default="month", pattern="^(week|month)$"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    items = [LeaderboardItemOut(**item).model_dump(by_alias=True) for item in leaderboard(db, period)]
    return ok({"items": items}, trace_id=request.state.trace_id)


@router.get("/stats")
def stats_endpoint(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return ok(
        CheckInStatsOut(**my_stats(db, user.id)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )
