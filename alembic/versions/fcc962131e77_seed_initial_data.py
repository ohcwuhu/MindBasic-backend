"""seed initial data

Revision ID: fcc962131e77
Revises: d18a1a1347d5
Create Date: 2026-08-09 14:50:48.118652

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fcc962131e77'
down_revision: Union[str, Sequence[str], None] = 'd18a1a1347d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ---------- 标签体系 ----------
    tags = sa.table(
        "tags",
        sa.column("id", sa.BigInteger),
        sa.column("name", sa.String),
        sa.column("type", sa.String),
        sa.column("sort_order", sa.Integer),
        sa.column("is_enabled", sa.Boolean),
    )
    op.bulk_insert(tags, [
        # 擅长领域 FIELD
        {"id": 1, "name": "考前焦虑", "type": "FIELD", "sort_order": 1, "is_enabled": True},
        {"id": 2, "name": "职场压力", "type": "FIELD", "sort_order": 2, "is_enabled": True},
        {"id": 3, "name": "亲子沟通", "type": "FIELD", "sort_order": 3, "is_enabled": True},
        {"id": 4, "name": "情绪低落", "type": "FIELD", "sort_order": 4, "is_enabled": True},
        {"id": 5, "name": "目标规划", "type": "FIELD", "sort_order": 5, "is_enabled": True},
        {"id": 6, "name": "个人成长", "type": "FIELD", "sort_order": 6, "is_enabled": True},
        # 服务人群 AUDIENCE
        {"id": 7, "name": "青少年", "type": "AUDIENCE", "sort_order": 1, "is_enabled": True},
        {"id": 8, "name": "高考考研学生", "type": "AUDIENCE", "sort_order": 2, "is_enabled": True},
        {"id": 9, "name": "家长", "type": "AUDIENCE", "sort_order": 3, "is_enabled": True},
        {"id": 10, "name": "职场人士", "type": "AUDIENCE", "sort_order": 4, "is_enabled": True},
        {"id": 11, "name": "产后女性", "type": "AUDIENCE", "sort_order": 5, "is_enabled": True},
        {"id": 12, "name": "中老年", "type": "AUDIENCE", "sort_order": 6, "is_enabled": True},
    ])

    # ---------- 自我教练模板（5 套） ----------
    coaching_templates = sa.table(
        "coaching_templates",
        sa.column("id", sa.BigInteger),
        sa.column("name", sa.String),
        sa.column("scene", sa.String),
        sa.column("description", sa.String),
        sa.column("is_enabled", sa.Boolean),
        sa.column("sort_order", sa.Integer),
    )
    op.bulk_insert(coaching_templates, [
        {"id": 1, "name": "考前焦虑调节", "scene": "考前焦虑", "description": "面对考试前的紧张与压力，梳理状态、找回资源", "is_enabled": True, "sort_order": 1},
        {"id": 2, "name": "职场压力缓解", "scene": "职场压力", "description": "应对职场中的压力情境，重建从容与掌控感", "is_enabled": True, "sort_order": 2},
        {"id": 3, "name": "亲子沟通探索", "scene": "亲子沟通", "description": "改善与孩子的沟通方式，看见彼此的需要", "is_enabled": True, "sort_order": 3},
        {"id": 4, "name": "情绪低落调整", "scene": "情绪低落", "description": "接纳当下的低落，找回属于自己的力量", "is_enabled": True, "sort_order": 4},
        {"id": 5, "name": "目标规划梳理", "scene": "目标规划", "description": "把想法落成行动，找到第一步的方向", "is_enabled": True, "sort_order": 5},
    ])

    # ---------- 模板步骤（四步：现状→理想→资源→行动） ----------
    template_steps = sa.table(
        "template_steps",
        sa.column("id", sa.BigInteger),
        sa.column("template_id", sa.BigInteger),
        sa.column("step_key", sa.String),
        sa.column("step_name", sa.String),
        sa.column("question", sa.Text),
        sa.column("placeholder", sa.String),
        sa.column("sort_order", sa.Integer),
    )
    op.bulk_insert(template_steps, [
        # 模板1 考前焦虑调节
        {"id": 1, "template_id": 1, "step_key": "STATUS", "step_name": "现状描述", "question": "如果为现在的状态起个名字，你会怎么称呼它？它最影响你的是哪一部分？", "placeholder": "写下此刻的状态...", "sort_order": 1},
        {"id": 2, "template_id": 1, "step_key": "IDEAL", "step_name": "理想状态", "question": "想象考前自己状态平稳的画面，那会是一种什么感觉？你理想中的状态是什么样的？", "placeholder": "描述你理想中的状态...", "sort_order": 2},
        {"id": 3, "template_id": 1, "step_key": "RESOURCES", "step_name": "资源盘点", "question": "回顾过去，你曾经有哪些应对紧张、顺利坚持下来的经验？身边有哪些人可以支持你？", "placeholder": "列出你的资源与经验...", "sort_order": 3},
        {"id": 4, "template_id": 1, "step_key": "ACTION", "step_name": "小行动制定", "question": "从明天开始，哪一件小事能让你离理想状态更近一步？它会在什么时候发生？", "placeholder": "写下一件可执行的小行动...", "sort_order": 4},
        # 模板2 职场压力缓解
        {"id": 5, "template_id": 2, "step_key": "STATUS", "step_name": "现状描述", "question": "最近哪件事最让你感到压力？如果把它放进一个画面，你会怎么描述？", "placeholder": "写下此刻的状态...", "sort_order": 1},
        {"id": 6, "template_id": 2, "step_key": "IDEAL", "step_name": "理想状态", "question": "如果工作状态更从容，那会是什么样子？你希望自己在那里面扮演什么角色？", "placeholder": "描述你理想中的状态...", "sort_order": 2},
        {"id": 7, "template_id": 2, "step_key": "RESOURCES", "step_name": "资源盘点", "question": "过去你渡过类似压力时，靠的是什么优势或方法？同事或团队中谁是你的支持资源？", "placeholder": "列出你的资源与经验...", "sort_order": 3},
        {"id": 8, "template_id": 2, "step_key": "ACTION", "step_name": "小行动制定", "question": "本周你能做的最小的一个调整是什么？你怎么判断它有效？", "placeholder": "写下一件可执行的小行动...", "sort_order": 4},
        # 模板3 亲子沟通探索
        {"id": 9, "template_id": 3, "step_key": "STATUS", "step_name": "现状描述", "question": "最近一次和孩子的沟通中，你最在意的是什么？那一刻你感受到的是什么？", "placeholder": "写下此刻的状态...", "sort_order": 1},
        {"id": 10, "template_id": 3, "step_key": "IDEAL", "step_name": "理想状态", "question": "你理想中的亲子相处画面是什么样的？那时的你会是什么状态？", "placeholder": "描述你理想中的状态...", "sort_order": 2},
        {"id": 11, "template_id": 3, "step_key": "RESOURCES", "step_name": "资源盘点", "question": "你和孩子之间有过哪些顺畅的时刻？当时你做了什么，让孩子愿意靠近你？", "placeholder": "列出你的资源与经验...", "sort_order": 3},
        {"id": 12, "template_id": 3, "step_key": "ACTION", "step_name": "小行动制定", "question": "这周为你们安排一个\u201c不赶时间\u201d的相处时刻，你会选什么方式？", "placeholder": "写下一件可执行的小行动...", "sort_order": 4},
        # 模板4 情绪低落调整
        {"id": 13, "template_id": 4, "step_key": "STATUS", "step_name": "现状描述", "question": "如果用一幅画面形容现在的状态，它会是什么样子？你注意到了什么？", "placeholder": "写下此刻的状态...", "sort_order": 1},
        {"id": 14, "template_id": 4, "step_key": "IDEAL", "step_name": "理想状态", "question": "当你走出这段低落，你希望自己首先恢复的是什么？那时的生活会有什么不同？", "placeholder": "描述你理想中的状态...", "sort_order": 2},
        {"id": 15, "template_id": 4, "step_key": "RESOURCES", "step_name": "资源盘点", "question": "过去让你感觉好一些的事情有哪些？哪些人、哪些地方曾给过你力量？", "placeholder": "列出你的资源与经验...", "sort_order": 3},
        {"id": 16, "template_id": 4, "step_key": "ACTION", "step_name": "小行动制定", "question": "今天或明天，做一件能让自己感到\u201c我还可以\u201d的小事，你会选哪一件？", "placeholder": "写下一件可执行的小行动...", "sort_order": 4},
        # 模板5 目标规划梳理
        {"id": 17, "template_id": 5, "step_key": "STATUS", "step_name": "现状描述", "question": "你现在最想推进的目标是什么？它对你为什么重要？", "placeholder": "写下此刻的状态...", "sort_order": 1},
        {"id": 18, "template_id": 5, "step_key": "IDEAL", "step_name": "理想状态", "question": "如果三个月后这个目标有了进展，那时的你会是什么样？你会看到什么变化？", "placeholder": "描述你理想中的状态...", "sort_order": 2},
        {"id": 19, "template_id": 5, "step_key": "RESOURCES", "step_name": "资源盘点", "question": "你已经具备哪些条件、能力或人脉能帮你实现它？过去完成类似目标时你靠的是什么？", "placeholder": "列出你的资源与经验...", "sort_order": 3},
        {"id": 20, "template_id": 5, "step_key": "ACTION", "step_name": "小行动制定", "question": "把目标拆成一个 7 天内能完成的第一步，它会是什么？完成后你用什么标准判断\u201c做到了\u201d？", "placeholder": "写下一件可执行的小行动...", "sort_order": 4},
    ])

    # ---------- 情绪反馈话术库（6 类情绪 × 3 条） ----------
    emotion_feedback_lib = sa.table(
        "emotion_feedback_lib",
        sa.column("id", sa.BigInteger),
        sa.column("mood_type", sa.String),
        sa.column("content", sa.String),
        sa.column("is_enabled", sa.Boolean),
        sa.column("sort_order", sa.Integer),
    )
    op.bulk_insert(emotion_feedback_lib, [
        {"id": 1, "mood_type": "CALM", "content": "平静本身就是一种力量，愿你把这份安定带到接下来的事情里。", "is_enabled": True, "sort_order": 1},
        {"id": 2, "mood_type": "CALM", "content": "你此刻的平稳，是你在一次次经历中积累下来的能力。", "is_enabled": True, "sort_order": 2},
        {"id": 3, "mood_type": "CALM", "content": "能在纷扰中保持平静，说明你内心自有秩序。", "is_enabled": True, "sort_order": 3},
        {"id": 4, "mood_type": "HAPPY", "content": "这份开心值得被好好收藏，你值得拥有这些明亮时刻。", "is_enabled": True, "sort_order": 1},
        {"id": 5, "mood_type": "HAPPY", "content": "看见你今天的喜悦，也提醒自己：快乐是你生活的一部分。", "is_enabled": True, "sort_order": 2},
        {"id": 6, "mood_type": "HAPPY", "content": "把这份好心情记下来，它是你内心的资源之一。", "is_enabled": True, "sort_order": 3},
        {"id": 7, "mood_type": "ANXIOUS", "content": "你愿意把这份不安写下来，本身就很有勇气。想想看，过去哪次挑战前你也紧张过，最后是怎么顺利度过的？", "is_enabled": True, "sort_order": 1},
        {"id": 8, "mood_type": "ANXIOUS", "content": "焦虑常常说明你在乎。如果把\u201c在乎\u201d的力量用来做一件小事，你会做什么？", "is_enabled": True, "sort_order": 2},
        {"id": 9, "mood_type": "ANXIOUS", "content": "此刻的紧张是身体在为你准备能量。深呼吸，慢一点，你的节奏由你决定。", "is_enabled": True, "sort_order": 3},
        {"id": 10, "mood_type": "DOWN", "content": "低落的时候，你依然选择记录自己，这已经是一种照顾自己的方式。", "is_enabled": True, "sort_order": 1},
        {"id": 11, "mood_type": "DOWN", "content": "情绪有起有落，现在的低谷不会定义你。过去你是怎么慢慢走出来的？", "is_enabled": True, "sort_order": 2},
        {"id": 12, "mood_type": "DOWN", "content": "今天不需要做得很好，只做一点点就足够。你愿意为自己做哪件小事？", "is_enabled": True, "sort_order": 3},
        {"id": 13, "mood_type": "IRRITATED", "content": "感到烦躁是正常的信号，它在告诉你有些边界或需要被看见了。", "is_enabled": True, "sort_order": 1},
        {"id": 14, "mood_type": "IRRITATED", "content": "你注意到了自己的情绪，这很重要。退后一步，什么才是你真正在意的？", "is_enabled": True, "sort_order": 2},
        {"id": 15, "mood_type": "IRRITATED", "content": "先让自己舒服一点，再决定怎么回应。你有权利暂时不处理。", "is_enabled": True, "sort_order": 3},
        {"id": 16, "mood_type": "OTHER", "content": "每一种感受都值得被看见。你愿意记录它，就已经在靠近自己。", "is_enabled": True, "sort_order": 1},
        {"id": 17, "mood_type": "OTHER", "content": "你的感受是真实的，也一直在变化。此刻它想告诉你什么？", "is_enabled": True, "sort_order": 2},
        {"id": 18, "mood_type": "OTHER", "content": "给自己一点时间，也给自己一点空间。你已经在这里照顾好自己了。", "is_enabled": True, "sort_order": 3},
    ])

    # ---------- 初始管理员 ----------
    # 默认密码 Admin@123456（bcrypt），首次登录后必须修改
    users = sa.table(
        "users",
        sa.column("id", sa.BigInteger),
        sa.column("phone", sa.String),
        sa.column("password_hash", sa.String),
        sa.column("nickname", sa.String),
        sa.column("role", sa.String),
        sa.column("status", sa.String),
        sa.column("privacy_agreed", sa.Boolean),
    )
    op.bulk_insert(users, [{
        "id": 1,
        "phone": "13800138000",
        "password_hash": "$2b$12$34dfln7AtZ8tQqaMlK.aM.HqTLMq4vqMQlHrmUTL1gNFV2dh0Uwb.",
        "nickname": "平台管理员",
        "role": "ADMIN",
        "status": "ENABLED",
        "privacy_agreed": True,
    }])


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(sa.text("DELETE FROM emotion_feedback_lib WHERE id BETWEEN 1 AND 18"))
    op.execute(sa.text("DELETE FROM template_steps WHERE id BETWEEN 1 AND 20"))
    op.execute(sa.text("DELETE FROM coaching_templates WHERE id BETWEEN 1 AND 5"))
    op.execute(sa.text("DELETE FROM tags WHERE id BETWEEN 1 AND 12"))
    op.execute(sa.text("DELETE FROM users WHERE phone = '13800138000'"))
