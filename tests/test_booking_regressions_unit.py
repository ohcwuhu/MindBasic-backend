"""不连接数据库的预约回归测试；可用 python -m unittest discover -s tests -p test_booking_regressions_unit.py 运行。"""
import unittest
from datetime import date, time, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
from sqlalchemy.exc import IntegrityError
from app.core.exceptions import AppError
from app.services import appointment_service as appointments, chat_service as chats, coach_service as coaches
from app.schemas.coach import SlotBatchIn


class BookingRegressions(unittest.IsolatedAsyncioTestCase):
    async def test_idempotent_retry_returns_existing_before_slot_check(self):
        existing = NS(id=8)
        db = NS(scalar=AsyncMock(return_value=existing))
        result = await appointments.create_appointment(db, NS(id=2), NS(), "retry")
        self.assertEqual(result, (existing, False))
        self.assertEqual(db.scalar.await_count, 1)

    def booking_db(self, slot):
        return NS(scalar=AsyncMock(side_effect=[None, NS(id=1, user_id=3),
                  NS(
                      id=4, name="测试服务", service_type="SINGLE",
                      price_in_cents=100, duration_min=60,
                  ), NS(id=8)]),
                  get=AsyncMock(return_value=slot), execute=AsyncMock(return_value=NS(rowcount=1)),
                  add=Mock(), flush=AsyncMock(side_effect=IntegrityError("insert", {}, Exception())),
                  rollback=AsyncMock())

    async def test_flush_integrity_error_rolls_back_and_returns_existing(self):
        slot = NS(
            id=5, coach_id=1, date=date.today()+timedelta(days=2),
            start_time=time(10), end_time=time(11),
        )
        db = self.booking_db(slot)
        result, created = await appointments.create_appointment(db, NS(id=2),
            NS(coach_id=1, service_id=4, slot_id=5, need_desc="test"), "retry")
        self.assertEqual(result.id, 8)
        self.assertFalse(created)
        db.rollback.assert_awaited_once()

    async def test_started_slot_cannot_be_booked(self):
        slot = NS(id=5, coach_id=1, date=date.today(), start_time=time(0), end_time=time(1))
        db = self.booking_db(slot)
        db.scalar.side_effect = [NS(id=1, user_id=3), NS(id=4, duration_min=60)]
        with self.assertRaises(AppError):
            await appointments.create_appointment(db, NS(id=2),
                NS(coach_id=1, service_id=4, slot_id=5), None)
        db.execute.assert_not_awaited()

    async def test_service_longer_than_slot_is_rejected(self):
        slot = NS(
            id=5, coach_id=1, date=date.today()+timedelta(days=2),
            start_time=time(10), end_time=time(10, 30),
        )
        db = self.booking_db(slot)
        db.scalar.side_effect = [NS(id=1, user_id=3), NS(id=4, duration_min=90)]
        with self.assertRaises(AppError) as raised:
            await appointments.create_appointment(
                db, NS(id=2),
                NS(coach_id=1, service_id=4, slot_id=5, need_desc="test"),
                None,
            )
        self.assertEqual(raised.exception.code, "SLOT_DURATION_MISMATCH")
        db.execute.assert_not_awaited()

    async def save_slots(self, spans, booked=()):
        day = (date.today()+timedelta(days=2)).isoformat()
        data = SlotBatchIn(slots=[dict(date=day, startTime=a, endTime=b) for a,b in spans])
        existing = list(booked)
        for index, row in enumerate(existing, start=1):
            if not hasattr(row, "id"):
                row.id = index
            if not hasattr(row, "status"):
                row.status = "BOOKED"
        referenced = [row.id for row in existing if getattr(row, "referenced", False)]
        db = NS(
            scalars=AsyncMock(side_effect=[existing, referenced]),
            execute=AsyncMock(),
            add=Mock(),
            delete=AsyncMock(),
            commit=AsyncMock(),
        )
        with patch.object(coaches, "list_coach_slots", AsyncMock(return_value=[])):
            await coaches.replace_coach_slots(db, 1, data)
        return db

    async def test_overlapping_submitted_slots_rejected(self):
        with self.assertRaises(AppError):
            await self.save_slots([("09:00", "10:00"), ("09:30", "10:30")])

    async def test_adjacent_slots_allowed(self):
        db = await self.save_slots([("09:00", "10:00"), ("10:00", "11:00")])
        self.assertEqual(db.add.call_count, 2)
        db.commit.assert_awaited_once()

    async def test_booked_slot_overlap_rejected(self):
        booked = NS(date=date.today()+timedelta(days=2), start_time=time(9), end_time=time(10))
        with self.assertRaises(AppError):
            await self.save_slots([("09:30", "10:30")], [booked])

    async def test_unchanged_booked_slot_preserved(self):
        booked = NS(date=date.today()+timedelta(days=2), start_time=time(9), end_time=time(10))
        db = await self.save_slots([("09:00", "10:00"), ("10:00", "11:00")], [booked])
        self.assertEqual(db.add.call_count, 1)

    async def test_history_referenced_slot_is_kept_but_off(self):
        historical = NS(
            date=date.today()+timedelta(days=2), start_time=time(9), end_time=time(10),
            status="AVAILABLE", referenced=True,
        )
        db = await self.save_slots([("10:00", "11:00")], [historical])
        self.assertEqual(historical.status, "OFF")
        db.delete.assert_not_awaited()
        self.assertEqual(db.add.call_count, 1)

    async def test_saved_action_is_loaded_for_its_journal(self):
        from app.api.v1.checkins import latest_action_adjustment
        db = NS(scalar=AsyncMock(return_value="行动调小：只列一个待办（来源日记：42）"))
        request = NS(state=NS(trace_id="test"))
        with patch("app.services.emotion_journal_service.get_own_journal_or_404", AsyncMock()) as own:
            result = await latest_action_adjustment(request, 42, NS(id=2), db)
        own.assert_awaited_once_with(db, 2, 42)
        self.assertEqual(result["data"]["action"], "只列一个待办")

    async def test_chat_created_after_appointment_is_unlocked(self):
        profile = NS(id=7, user_id=3, deleted_at=None, audit_status="APPROVED")
        db = NS(
            get=AsyncMock(return_value=profile),
            scalar=AsyncMock(side_effect=[None, 99]),
            add=Mock(),
            commit=AsyncMock(),
            refresh=AsyncMock(),
        )
        conv = await chats.get_or_create_conversation(db, NS(id=2), 7)
        self.assertTrue(conv.unlocked)
        db.commit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
