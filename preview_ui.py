"""테마/UI 프리뷰 하네스 — Claude API를 호출하지 않고 화면만 렌더한다.

    python -m streamlit run preview_ui.py

app.py를 그대로 실행하되 GM 호출을 고정 응답으로 갈아끼우고,
게임 중반 상태를 미리 넣어 사이드바까지 채워진 화면을 보여준다.
테마를 바꿔가며 비교할 때 API 비용이 들지 않는다.
"""

from __future__ import annotations

import streamlit as st

import game_state as gs
import gm

# app.py가 자기 set_page_config를 호출해도 중복 오류가 나지 않게 무력화
st.set_page_config = lambda *args, **kwargs: None

CANNED = {
    "narration": (
        "빗물에 젖은 흙이 구두 밑창에 들러붙는다. 정원 담장 아래, 굽이 얇은 발자국 한 쌍이 "
        "창고 쪽으로 향했다가 그대로 되돌아와 있다. 비가 그친 뒤에 찍힌 자국이다. "
        "촛불을 들어 올리자, 발자국이 끝나는 자리에서 젖은 천 조각 하나가 걸려 있는 것이 보인다."
    ),
    "choices": [
        {"label": "다락방으로 올라간다", "action": "이동", "target": "다락방"},
        {"label": "이곳을 더 살펴본다", "action": "조사", "target": ""},
        {"label": "비서를 추궁한다", "action": "심문", "target": "C"},
    ],
    "suspicion_delta": {"A": 0, "B": 0, "C": 0},
    "trust_delta": {"A": 0, "B": 0, "C": 0},
}

gm.call_gm = lambda client, history, state, player_input: (dict(CANNED), history)

if "state" not in st.session_state:
    state = gs.new_state()
    state.update(
        turn=4,
        location="정원",
        clues_found=["c1", "c3", "c4"],
        suspicion={"A": 35, "B": 20, "C": 48},
        npc_trust={"A": 45, "B": 30, "C": 72},
    )
    st.session_state.state = state
    st.session_state.history = []
    st.session_state.phase = "play"
    st.session_state.gm = dict(CANNED)
    st.session_state.error = None
    st.session_state.found = gs.CLUES["c4"]
    st.session_state.ending = None

with open("app.py", encoding="utf-8") as handle:
    source = handle.read()

exec(compile(source, "app.py", "exec"), {"__name__": "__main__"})
