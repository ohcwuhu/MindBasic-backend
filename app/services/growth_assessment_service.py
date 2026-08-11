"""成长测评业务逻辑（资源导向，不诊断、不贴标签）。"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.coach import Tag
from app.models.growth import (
    CoachingTemplate,
    GrowthAssessmentQuestion,
    GrowthAssessmentResult,
    GrowthAssessmentTemplate,
)
from app.utils.time import to_iso


DIMENSIONS = [
    {"key": "SELF_AWARENESS", "name": "自我觉察", "template_id": 4, "tag_id": 6},
    {"key": "RESOURCE_USE", "name": "资源运用", "template_id": 1, "tag_id": 6},
    {"key": "GOAL_CLARITY", "name": "目标清晰", "template_id": 5, "tag_id": 5},
    {"key": "ACTION", "name": "行动力", "template_id": 2, "tag_id": 2},
    {"key": "EMOTION_REGULATION", "name": "情绪调节", "template_id": 4, "tag_id": 4},
]

LEVELS = [
    {"key": "HAS_STRENGTH", "label": "已有优势", "min": 4.0},
    {"key": "GROWING", "label": "正在生长", "min": 3.0},
    {"key": "GROWTH_SPACE", "label": "成长空间", "min": 0.0},
]

INTERPRETATIONS = {
    "SELF_AWARENESS": {
        "HAS_STRENGTH": "你对自己当下的状态有清晰的觉察，能辨认情绪的来源，这是持续成长的重要基础。",
        "GROWING": "你已经开始留意自己的状态，偶尔还会被它带走。多记录、多停下来看看，觉察会越来越稳。",
        "GROWTH_SPACE": "你正在学习更细致地观察自己。从每天一次的情绪日记开始，就是很好的练习。",
    },
    "RESOURCE_USE": {
        "HAS_STRENGTH": "你善于调用过去的经验与身边的支持，遇到挑战时资源会及时到场。",
        "GROWING": "你已经有不少资源，只是有时候还没想起来用它。困难出现时先问问自己：我过去是怎么走过来的？",
        "GROWTH_SPACE": "你还在学习看见自己的资源。回想一次成功经历，把它写下来，就是盘点资源的第一步。",
    },
    "GOAL_CLARITY": {
        "HAS_STRENGTH": "你的目标清晰且对你重要，方向感会帮你做出更稳的选择。",
        "GROWING": "你大致知道自己要什么，还可以再具体一点。把目标写下来，并描述达成后的画面，会更有力量。",
        "GROWTH_SPACE": "目标还不完全清晰，这很正常。用一次自我教练的目标规划，就能找到第一步。",
    },
    "ACTION": {
        "HAS_STRENGTH": "你习惯把想法落成行动，并且会跟进调整，这是非常难得的能力。",
        "GROWING": "你已经能迈出第一步，偶尔会卡在“等准备好了再做”。试试先完成一小步，再修正。",
        "GROWTH_SPACE": "你还在找启动的方式。把目标拆成 7 天内能完成的一件小事，行动就有了入口。",
    },
    "EMOTION_REGULATION": {
        "HAS_STRENGTH": "你有自己的情绪恢复方式，能接纳低落并及时给自己充电。",
        "GROWING": "你大多数时候能稳住自己，偶尔需要更多照顾。给自己留一段恢复时间，是值得的。",
        "GROWTH_SPACE": "你正在寻找与自己情绪相处的方式。练习接纳、给自己空间，比急着处理更有帮助。",
    },
}


def _level_for(score: float) -> dict:
    for level in LEVELS:
        if score >= level["min"]:
            return level
    return LEVELS[-1]


async def get_enabled_template(db: AsyncSession) -> GrowthAssessmentTemplate:
    template = await db.scalar(
        select(GrowthAssessmentTemplate)
        .where(GrowthAssessmentTemplate.is_enabled.is_(True))
        .order_by(GrowthAssessmentTemplate.version.desc())
        .limit(1)
    )
    if template is None:
        raise AppError(404, "NOT_FOUND", "测评量表尚未发布")
    return template


async def list_template_questions(db: AsyncSession, template_id: int) -> list[GrowthAssessmentQuestion]:
    return list(
        await db.scalars(
            select(GrowthAssessmentQuestion)
            .where(GrowthAssessmentQuestion.template_id == template_id)
            .order_by(GrowthAssessmentQuestion.sort_order)
        )
    )


async def get_template_payload(db: AsyncSession) -> dict:
    template = await get_enabled_template(db)
    questions = await list_template_questions(db, template.id)
    return {
        "id": template.id,
        "name": template.name,
        "description": template.description,
        "version": template.version,
        "questions": [
            {
                "id": q.id,
                "dimension_key": q.dimension_key,
                "dimension_name": q.dimension_name,
                "question": q.question,
                "options": q.options,
                "sort_order": q.sort_order,
            }
            for q in questions
        ],
    }


def _build_report(scores: list[dict]) -> dict:
    strengths = [s["dimension_name"] for s in scores if s["level"] == "HAS_STRENGTH"]
    spaces = [s["dimension_name"] for s in scores if s["level"] != "HAS_STRENGTH"]
    if strengths and spaces:
        summary = (
            f"你在「{'、'.join(strengths)}」方面已经积累了优势，"
            f"在「{'、'.join(spaces)}」方面还有生长的空间。"
        )
    elif strengths:
        summary = f"你在「{'、'.join(strengths)}」方面都已经积累了优势，继续保持自己的节奏就好。"
    else:
        summary = "你正在生长的早期阶段，先做一次自我教练，找到第一个小行动。"
    dimensions = [
        {
            "dimension_key": s["dimension_key"],
            "dimension_name": s["dimension_name"],
            "score": s["score"],
            "level": s["level"],
            "level_label": s["level_label"],
            "interpretation": INTERPRETATIONS[s["dimension_key"]][s["level"]],
        }
        for s in scores
    ]
    return {"summary": summary, "dimensions": dimensions}


async def submit_assessment(db: AsyncSession, user_id: int, answers: dict[str, int]) -> dict:
    template = await get_enabled_template(db)
    questions = await list_template_questions(db, template.id)

    question_ids = {str(q.id) for q in questions}
    provided = set(answers.keys())
    if question_ids != provided:
        raise AppError(400, "VALIDATION_ERROR", "答案不完整或包含无效题目")
    for value in answers.values():
        if not isinstance(value, int) or value < 1 or value > 5:
            raise AppError(400, "VALIDATION_ERROR", "答案分值需在 1-5 之间")

    grouped: dict[str, list[int]] = {}
    for q in questions:
        grouped.setdefault(q.dimension_key, []).append(answers[str(q.id)])
    scores: list[dict] = []
    for dim in DIMENSIONS:
        values = grouped.get(dim["key"], [])
        score = round(sum(values) / len(values), 2) if values else 0.0
        level = _level_for(score)
        scores.append({
            "dimension_key": dim["key"],
            "dimension_name": dim["name"],
            "score": score,
            "level": level["key"],
            "level_label": level["label"],
        })

    report = _build_report(scores)
    template_ids = sorted({
        dim["template_id"]
        for dim in DIMENSIONS
        if next(s for s in scores if s["dimension_key"] == dim["key"])["level"] != "HAS_STRENGTH"
    })
    tag_ids = sorted({
        dim["tag_id"]
        for dim in DIMENSIONS
        if next(s for s in scores if s["dimension_key"] == dim["key"])["level"] == "GROWTH_SPACE"
    })
    coaching: dict[int, str] = {}
    if template_ids:
        for row in await db.scalars(
            select(CoachingTemplate).where(CoachingTemplate.id.in_(template_ids))
        ):
            coaching[row.id] = row.name
    tags: dict[int, str] = {}
    if tag_ids:
        for row in await db.scalars(select(Tag).where(Tag.id.in_(tag_ids))):
            tags[row.id] = row.name
    report["recommendations"] = {
        "selfCoaching": [
            {"id": tid, "name": coaching[tid]} for tid in template_ids if tid in coaching
        ],
        "coachTags": [
            {"id": tag_id, "name": tags[tag_id]} for tag_id in tag_ids if tag_id in tags
        ],
    }

    result = GrowthAssessmentResult(
        user_id=user_id,
        template_id=template.id,
        answers=answers,
        scores=scores,
        report=report,
    )
    db.add(result)
    await db.commit()
    await db.refresh(result)
    return {
        "id": result.id,
        "template_id": result.template_id,
        "template_name": template.name,
        "scores": scores,
        "report": report,
        "created_at": to_iso(result.created_at),
    }


async def list_results(
    db: AsyncSession, user_id: int, page: int, page_size: int
) -> tuple[list[dict], int]:
    base = (
        select(GrowthAssessmentResult, GrowthAssessmentTemplate.name)
        .join(GrowthAssessmentTemplate, GrowthAssessmentTemplate.id == GrowthAssessmentResult.template_id)
        .where(GrowthAssessmentResult.user_id == user_id)
    )
    total = (
        await db.scalar(
            select(func.count()).select_from(GrowthAssessmentResult).where(
                GrowthAssessmentResult.user_id == user_id
            )
        )
        or 0
    )
    rows = (
        await db.execute(
            base.order_by(GrowthAssessmentResult.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    items = [
        {
            "id": result.id,
            "template_name": template_name,
            "created_at": to_iso(result.created_at),
        }
        for result, template_name in rows
    ]
    return items, total


async def get_result_or_404(db: AsyncSession, user_id: int, result_id: int) -> dict:
    row = (
        await db.execute(
            select(GrowthAssessmentResult, GrowthAssessmentTemplate.name)
            .join(GrowthAssessmentTemplate, GrowthAssessmentTemplate.id == GrowthAssessmentResult.template_id)
            .where(
                GrowthAssessmentResult.id == result_id,
                GrowthAssessmentResult.user_id == user_id,
            )
        )
    ).first()
    if row is None:
        raise AppError(404, "NOT_FOUND", "测评记录不存在")
    result, template_name = row
    return {
        "id": result.id,
        "template_id": result.template_id,
        "template_name": template_name,
        "scores": result.scores,
        "report": result.report,
        "created_at": to_iso(result.created_at),
    }
