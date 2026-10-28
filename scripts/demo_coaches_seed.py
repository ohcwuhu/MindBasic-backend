"""补充演示教练（UTF-8 安全，可重复执行）。

只往「真人教练」列表里补几位可展示的教练：账号、资料、擅长标签、服务与未来一周的可约时段。
不动文章 / 轮播 / 既有教练，重复执行时按手机号重建这几位。

    python scripts/demo_coaches_seed.py
"""

import os
import sys
import bcrypt
from datetime import date, time, timedelta

from sqlalchemy import select

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from app.db.session import SessionLocal
from app.models.coach import CoachProfile, CoachSlot, CoachTag, Service
from app.models.user import User

PASSWORD = "Demo123456"

# 手机号、昵称、性别、简介、培训经历、服务理念、年限、评分、评价数、标签、服务
COACHES = [
    {
        "phone": "13900000003",
        "nickname": "陈老师",
        "gender": "girl",
        "bio": "擅长陪情绪低落的人把「说不清」慢慢说清楚，找回一点力气。",
        "training": "心理教练认证培训 96 学时；情绪聚焦疗法（EFT）初级。",
        "concept": "先接住情绪，再一起找到下一步。",
        "years": 6,
        "rating": 4.80,
        "reviews": 27,
        "tags": [4, 6, 10],
        "services": [("单次咨询", "SINGLE", 60, 8800, "一次 60 分钟的线上对话。")],
    },
    {
        "phone": "13900000004",
        "nickname": "周老师",
        "gender": "boy",
        "bio": "陪职场人理清压力来源、拆解目标，把「忙到喘不过气」变成可执行的一步。",
        "training": "ICF 教练认证课程 128 学时；组织行为学硕士。",
        "concept": "把大问题拆小，让改变从今天就能开始。",
        "years": 9,
        "rating": 4.90,
        "reviews": 41,
        "tags": [2, 5, 10],
        "services": [
            ("单次咨询", "SINGLE", 60, 12800, "一次 60 分钟的线上对话。"),
            ("职场 3 次成长卡", "PACKAGE", 60, 34800, "连续三周，聚焦一个具体议题。"),
        ],
    },
    {
        "phone": "13900000005",
        "nickname": "苏老师",
        "gender": "girl",
        "bio": "十多年青少年与家庭工作经验，专注亲子沟通与青春期陪伴。",
        "training": "家庭教育指导师（高级）；萨提亚模式家庭治疗工作坊。",
        "concept": "关系先于教育，被听见的孩子才愿意开口。",
        "years": 12,
        "rating": 5.00,
        "reviews": 63,
        "tags": [3, 7, 9],
        "services": [
            ("单次咨询", "SINGLE", 60, 15800, "一次 60 分钟的线上对话。"),
            ("家庭陪伴 5 次卡", "PACKAGE", 60, 69800, "五周持续陪伴，含家长沟通复盘。"),
        ],
    },
    {
        "phone": "13900000006",
        "nickname": "顾老师",
        "gender": "boy",
        "bio": "专注考前焦虑与学习节奏，帮学生把紧张变成可以安放的能量。",
        "training": "心理教练认证培训 80 学时；正念减压（MBSR）八周课程。",
        "concept": "紧张说明你在乎，我们让它变成助力。",
        "years": 4,
        "rating": 4.60,
        "reviews": 15,
        "tags": [1, 5, 8],
        "services": [("单次咨询", "SINGLE", 50, 6800, "一次 50 分钟的线上对话。")],
    },
    {
        "phone": "13900000007",
        "nickname": "叶老师",
        "gender": "girl",
        "bio": "陪伴新手妈妈度过情绪起伏，也接住那些「不该说出口」的疲惫。",
        "training": "国家二级心理咨询师；围产期心理支持专项培训。",
        "concept": "照顾别人之前，先允许自己被照顾。",
        "years": 7,
        "rating": 4.70,
        "reviews": 22,
        "tags": [4, 6, 11],
        "services": [("单次咨询", "SINGLE", 60, 9800, "一次 60 分钟的线上对话。")],
    },
    {
        "phone": "13900000008",
        "nickname": "沈老师",
        "gender": "boy",
        "bio": "陪你在职业转型与人生规划里找到自己的节奏，不急着给标准答案。",
        "training": "ICF ACC 认证教练；生涯规划师（中级）。",
        "concept": "答案在你身上，我负责陪你把灯打开。",
        "years": 10,
        "rating": 4.80,
        "reviews": 35,
        "tags": [5, 6, 10],
        "services": [
            ("单次咨询", "SINGLE", 60, 13800, "一次 60 分钟的线上对话。"),
            ("生涯梳理 4 次卡", "PACKAGE", 60, 49800, "四次系统梳理，含行动清单。"),
        ],
    },
    {
        "phone": "13900000009",
        "nickname": "唐老师",
        "gender": "girl",
        "bio": "擅长中年家庭关系与亲子沟通，也陪长辈面对退休后的空落。",
        "training": "心理咨询师基础培训合格证书；家庭系统排列工作坊。",
        "concept": "慢一点，把话说开，关系就有回旋的余地。",
        "years": 15,
        "rating": 4.90,
        "reviews": 58,
        "tags": [3, 12],
        "services": [("单次咨询", "SINGLE", 60, 11800, "一次 60 分钟的线上对话。")],
    },
]


def main() -> None:
    db = SessionLocal()
    try:
        created = 0
        for item in COACHES:
            existing = db.scalar(select(User).where(User.phone == item["phone"]))
            if existing is not None:
                db.delete(existing)
                db.commit()

            user = User(
                phone=item["phone"],
                password_hash=bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode(),
                nickname=item["nickname"],
                gender=item["gender"],
                role="COACH",
                status="ENABLED",
                privacy_agreed=True,
            )
            db.add(user)
            db.flush()

            profile = CoachProfile(
                user_id=user.id,
                real_name=item["nickname"],
                bio=item["bio"],
                training_exp=item["training"],
                service_concept=item["concept"],
                years_of_experience=item["years"],
                credential_urls=[],
                id_card_url=None,
                audit_status="APPROVED",
                rating=item["rating"],
                review_count=item["reviews"],
            )
            db.add(profile)
            db.flush()

            db.add_all([CoachTag(coach_id=profile.id, tag_id=tag_id) for tag_id in item["tags"]])
            db.add_all(
                [
                    Service(
                        coach_id=profile.id,
                        name=name,
                        service_type=service_type,
                        duration_min=duration,
                        price_in_cents=price,
                        description=description,
                        is_enabled=True,
                    )
                    for name, service_type, duration, price, description in item["services"]
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
                            start_time=time(19, 0),
                            end_time=time(20, 0),
                            status="AVAILABLE",
                        ),
                    ]
                )
            created += 1

        db.commit()
        print(f"demo coaches refreshed: {created} 位（{COACHES[0]['nickname']} … {COACHES[-1]['nickname']}）")
    finally:
        db.close()


if __name__ == "__main__":
    main()
