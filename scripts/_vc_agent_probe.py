"""临时探针：把三段「用户提问」合成语音，走一遍真实视频通话管线。

流程：REST 登录 -> SocketIO 连接 -> vc_start -> 上传音频 -> vc_audio_end
      -> 等 vc_asr_result / vc_emotion_analysis / vc_llm_done
本质上是模拟前端 VideoCallPanel 的一次真实通话，不作任何 mock。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import requests
import socketio

try:
    import edge_tts
except Exception as exc:  # pragma: no cover
    print("edge_tts 不可用:", exc)
    sys.exit(1)

BASE = "http://127.0.0.1:8000"
PHONE = os.environ.get("PROBE_PHONE", "13800138000")
PASSWORD = os.environ.get("PROBE_PASSWORD", "Admin@123456")

VOICE = "zh-CN-XiaoxiaoNeural"
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "_probe_audio")

# 每题按用户标注的语气/表情调不同语速与音高，让 ASR 之外还能带上语调线索。
TURNS = [
    {
        "id": "Q1",
        "label": "考研：疑惑的语气 + 顾虑的表情",
        "text": "我报名了考研，但说不上来为什么考。家里觉得应该考，我自己也拿不准，是继续读书还是早点就业。",
        "rate": "-8%",
        "pitch": "-12Hz",
    },
    {
        "id": "Q2",
        "label": "想换方向：迟疑的语气 + 疲惫的表情",
        "text": "最近我想换个方向，但我不太确定，是因为不喜欢现在做的事，还是压力太大，只是一时想逃。",
        "rate": "-15%",
        "pitch": "-8Hz",
    },
    {
        "id": "Q3",
        "label": "坚持锻炼和复习：无奈的语气",
        "text": "我目标很清楚，就是坚持锻炼和复习，但每次都是开始几天就放弃了，我也不知道为什么。",
        "rate": "-10%",
        "pitch": "-10Hz",
    },
]


def login() -> str:
    resp = requests.post(
        f"{BASE}/api/v1/auth/login",
        json={"phone": PHONE, "password": PASSWORD},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    print(f"[login] user={data['user']['nickname']} role={data['user']['role']}")
    return data["accessToken"]


async def synth() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    for turn in TURNS:
        path = os.path.join(OUT_DIR, f"{turn['id']}.mp3")
        comm = edge_tts.Communicate(
            turn["text"], VOICE, rate=turn["rate"], pitch=turn["pitch"]
        )
        await comm.save(path)
        print(f"[tts] {turn['id']} -> {path} ({os.path.getsize(path)}B)")


class Probe:
    def __init__(self, token: str) -> None:
        self.token = token
        self.sio = socketio.AsyncClient(reconnection=False)
        self.events: list[tuple[str, dict]] = []
        self.done = asyncio.Event()
        self._register()

    def _register(self) -> None:
        for name in (
            "vc_session_started",
            "vc_state_change",
            "vc_asr_result",
            "vc_emotion_analysis",
            "vc_llm_done",
            "vc_tts_done",
            "vc_error",
            "vc_crisis_alert",
            "vc_idle_timeout",
        ):
            self.sio.on(name, self._make_handler(name))

    def _make_handler(self, name: str):
        async def handler(data):
            self.events.append((name, data if isinstance(data, dict) else {"raw": data}))
            stamp = time.strftime("%H:%M:%S")
            print(f"[{stamp}] << {name}: {json.dumps(data, ensure_ascii=False)[:400]}")
            if name in ("vc_llm_done", "vc_error"):
                self.done.set()
        return handler

    async def connect(self) -> None:
        await self.sio.connect(
            BASE,
            auth={"token": self.token},
            transports=["websocket"],
            wait_timeout=20,
        )
        print("[socket] connected, sid =", self.sio.get_sid())

    async def start_call(self) -> None:
        await self.sio.emit(
            "vc_start",
            {"consent": {"mic": True, "camera": False, "multimodal": True, "basis": "probe"}},
        )
        await asyncio.sleep(2.0)

    async def ask(self, turn: dict) -> dict:
        self.events.clear()
        self.done.clear()
        path = os.path.join(OUT_DIR, f"{turn['id']}.mp3")
        sid = self.sio.get_sid()
        with open(path, "rb") as fh:
            resp = requests.post(
                f"{BASE}/api/vc_audio_upload",
                headers={"Authorization": f"Bearer {self.token}"},
                files={"file": (f"{turn['id']}.mp3", fh, "audio/mpeg")},
                data={"sid": sid},
                timeout=60,
            )
        resp.raise_for_status()
        info = resp.json()
        print(f"\n===== {turn['id']} | {turn['label']} =====")
        print(f"[ask] {turn['text']}")
        print(f"[upload] file_id={info['file_id']} size={info['file_size']}B")
        await self.sio.emit(
            "vc_audio_end",
            {"file_id": info["file_id"], "file_size": info["file_size"]},
        )
        try:
            await asyncio.wait_for(self.done.wait(), timeout=300)
        except asyncio.TimeoutError:
            print(f"[warn] {turn['id']} 超时未拿到回复")
        await asyncio.sleep(1.0)
        return {"turn": turn, "events": list(self.events)}

    async def stop_call(self) -> None:
        await self.sio.emit("vc_stop", {})
        await asyncio.sleep(1.0)
        await self.sio.disconnect()


async def main() -> None:
    token = login()
    await synth()
    probe = Probe(token)
    await probe.connect()
    await probe.start_call()
    results = []
    for turn in TURNS:
        results.append(await probe.ask(turn))
    await probe.stop_call()

    out = os.path.join(OUT_DIR, "results.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)
    print("\n[probe] 原始事件已写入", out)


if __name__ == "__main__":
    asyncio.run(main())
