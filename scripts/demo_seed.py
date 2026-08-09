"""开发环境演示数据（UTF-8 安全，可重复执行）。

注意：请勿通过 PowerShell 管道向本脚本传中文，直接运行：
    python scripts/demo_seed.py
"""

import bcrypt
import os
import sys
from datetime import date, time, timedelta

from sqlalchemy import delete, select

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from app.db.session import SessionLocal
from app.models.coach import CoachProfile, CoachSlot, CoachTag, Service
from app.models.content import Article, Banner
from app.models.user import User
from app.utils.time import utcnow_naive


def main() -> None:
    db = SessionLocal()
    try:
        # 清理历史损坏/测试数据
        db.execute(delete(Article).where(Article.title.like("%?%")))
        db.execute(delete(Article).where(Article.deleted_at.is_not(None)))
        db.execute(delete(Banner).where(Banner.title.like("%?%")))
        for phone in ("13900000001", "13900000002"):
            user = db.scalar(select(User).where(User.phone == phone))
            if user is not None:
                db.delete(user)
        db.commit()

        # 演示教练
        coach_user = User(
            phone="13900000001",
            password_hash=bcrypt.hashpw(b"Demo123456", bcrypt.gensalt()).decode(),
            nickname="林老师",
            role="USER",
            status="ENABLED",
            privacy_agreed=True,
        )
        db.add(coach_user)
        db.flush()
        profile = CoachProfile(
            user_id=coach_user.id,
            real_name="林老师",
            bio="专注高考陪伴与亲子沟通，十年青少年成长工作经验。",
            training_exp="心理教练认证培训 120 学时；家庭教育指导师。",
            service_concept="赋能、陪伴、资源导向。陪你看见自己已经拥有的力量。",
            years_of_experience=8,
            credential_urls=[],
            id_card_url=None,
            audit_status="APPROVED",
            rating=4.9,
            review_count=12,
        )
        db.add(profile)
        db.flush()
        db.add_all(
            [
                CoachTag(coach_id=profile.id, tag_id=1),
                CoachTag(coach_id=profile.id, tag_id=2),
                CoachTag(coach_id=profile.id, tag_id=9),
            ]
        )
        db.add_all(
            [
                Service(
                    coach_id=profile.id,
                    name="单次咨询",
                    service_type="SINGLE",
                    duration_min=60,
                    price_in_cents=9900,
                    description="一次 60 分钟的线上对话。",
                    is_enabled=True,
                ),
                Service(
                    coach_id=profile.id,
                    name="高考 5 次陪伴卡",
                    service_type="PACKAGE",
                    duration_min=60,
                    price_in_cents=99000,
                    description="考前五周，每周一次深度陪伴。",
                    is_enabled=True,
                ),
            ]
        )
        db.flush()
        for i in range(7):
            day = date.today() + timedelta(days=i + 1)
            db.add_all(
                [
                    CoachSlot(
                        coach_id=profile.id,
                        date=day,
                        start_time=time(10, 0),
                        end_time=time(11, 0),
                        status="AVAILABLE",
                    ),
                    CoachSlot(
                        coach_id=profile.id,
                        date=day,
                        start_time=time(14, 0),
                        end_time=time(15, 0),
                        status="AVAILABLE",
                    ),
                ]
            )

        # 演示普通用户
        db.add(
            User(
                phone="13900000002",
                password_hash=bcrypt.hashpw(b"Demo123456", bcrypt.gensalt()).decode(),
                nickname="小满",
                role="USER",
                status="ENABLED",
                privacy_agreed=True,
            )
        )

        # 演示文章
        base = utcnow_naive()
        db.add_all(
            [
                Article(
                    title="考前一周，如何安顿自己的情绪",
                    summary="用资源视角看待考前紧张，把注意力放回自己已经做过的事情上。",
                    content=(
                        "<p>考前紧张不是问题，它说明你在乎。</p>"
                        "<p>试试这三件事：每天固定时间做一次深呼吸练习；把'我还没准备好'改成'我已经准备了这么久'；"
                        "睡前写下第二天最重要的一件事。</p>"
                        "<blockquote>你理想中的状态，不是没有紧张，而是紧张来了依然能往前走。</blockquote>"
                    ),
                    category_id=1,
                    status="PUBLISHED",
                    is_pinned=True,
                    published_at=base,
                    view_count=0,
                ),
                Article(
                    title="一位教练的故事：陪伴比答案更重要",
                    summary="当家长不再急着给答案，孩子的房间门反而打开了。",
                    content=(
                        "<p>很多家长来找我，第一句话都是：老师，快帮我出个主意。</p>"
                        "<p>可陪得久了你会发现，真正让孩子愿意靠近的，不是方案，而是被听见的感觉。</p>"
                    ),
                    category_id=2,
                    status="PUBLISHED",
                    published_at=base + timedelta(hours=2),
                    view_count=0,
                ),
                Article(
                    title="孩子越来越沉默，父母可以怎么做",
                    summary="沉默不是拒绝，也许是孩子还不确定说出来会怎样。",
                    content=(
                        "<p>当孩子话变少，先别急着追问。留出'不赶时间'的时刻，从一件共同的小事开始，"
                        "比如一起做饭、散步。</p>"
                    ),
                    category_id=3,
                    status="PUBLISHED",
                    published_at=base + timedelta(hours=5),
                    view_count=0,
                ),
            ]
        )

        # 演示轮播
        db.add_all(
            [
                Banner(
                    title="高考考前陪伴",
                    image_url="https://picsum.photos/seed/mindbasic1/1200/400",
                    link_type="ACTIVITY",
                    link_value="gaokao",
                    sort_order=1,
                    is_enabled=True,
                ),
                Banner(
                    title="公益体验咨询",
                    image_url="https://picsum.photos/seed/mindbasic2/1200/400",
                    link_type="NONE",
                    link_value=None,
                    sort_order=2,
                    is_enabled=True,
                ),
            ]
        )
        db.commit()
        print("demo data refreshed: coach 林老师 / user 小满 / 3 articles / 2 banners")
    finally:
        db.close()


if __name__ == "__main__":
    main()
