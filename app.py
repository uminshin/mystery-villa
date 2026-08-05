"""그날 밤, 별장에서 — Streamlit 진입점.

턴 진행 → 상태 갱신 → 엔딩 분기까지 도는 최소 버전.
"""

from __future__ import annotations

import anthropic
import streamlit as st

import game_state as gs
from gm import GMError, call_gm

st.set_page_config(page_title="그날 밤, 별장에서", page_icon="🕯️")


@st.cache_resource
def get_client() -> anthropic.Anthropic:
    # ANTHROPIC_API_KEY 환경변수 또는 `ant auth login` 프로필을 자동으로 사용
    return anthropic.Anthropic()


def start_new_game() -> None:
    st.session_state.state = gs.new_state()
    st.session_state.history = []
    st.session_state.phase = "play"
    st.session_state.gm = None
    st.session_state.error = None
    st.session_state.found = None
    st.session_state.ending = None


def request_gm(player_input: str) -> None:
    try:
        gm, history = call_gm(
            get_client(),
            st.session_state.history,
            st.session_state.state,
            player_input,
        )
    except GMError as exc:
        st.session_state.error = str(exc)
        return
    except TypeError as exc:
        if "authentication" in str(exc).lower():
            st.session_state.error = (
                "API 키를 찾을 수 없습니다. `ANTHROPIC_API_KEY` 환경변수를 설정하거나 "
                "`ant auth login`으로 로그인한 뒤 앱을 다시 시작하세요."
            )
        else:
            st.session_state.error = f"TypeError: {exc}"
        return
    except Exception as exc:
        st.session_state.error = f"{type(exc).__name__}: {exc}"
        return

    st.session_state.error = None
    st.session_state.history = history
    st.session_state.gm = gm
    gs.apply_deltas(st.session_state.state, gm["suspicion_delta"], gm["trust_delta"])


def take_action(choice: dict[str, str]) -> None:
    state = st.session_state.state
    found = gs.resolve_action(state, choice)
    spent = gs.costs_turn(choice["action"])
    if spent:
        gs.advance_turn(state)
    st.session_state.found = found

    outcome = f"발견한 단서: {found['name']} — {found['detail']}" if found else "새로 발견한 단서: 없음"
    cost = "1턴 소모" if spent else "턴 소모 없음(이동)"
    request_gm(
        f"[턴 {state['turn']}/{state['max_turns']}] 플레이어 행동: {choice['label']} "
        f"(action={choice['action']}, target={choice['target'] or '현재 장소'}, {cost})\n"
        f"판정 결과: {outcome}\n"
        f"이 결과를 반영한 나레이션과 다음 선택지 3개를 제시하라."
    )

    if gs.must_accuse(state):
        st.session_state.phase = "accuse"


def render_sidebar() -> None:
    state = st.session_state.state
    with st.sidebar:
        st.subheader("수사 기록")
        st.metric("턴", f"{state['turn']} / {state['max_turns']}")

        st.divider()
        st.caption("의심도")
        for key, info in gs.SUSPECTS.items():
            st.progress(state["suspicion"][key] / 100, text=f"{info['name']} — {state['suspicion'][key]}")

        st.caption("신뢰도")
        for key, info in gs.SUSPECTS.items():
            st.progress(state["npc_trust"][key] / 100, text=f"{info['name']} — {state['npc_trust'][key]}")

        st.divider()
        st.caption(f"단서 {len(state['clues_found'])} / {len(gs.CLUES)}")
        if state["clues_found"]:
            for cid in state["clues_found"]:
                clue = gs.CLUES[cid]
                st.write(f"- **{clue['name']}** ({clue['location']})")
        else:
            st.write("아직 없음")

        st.divider()
        if st.button("처음부터 다시", width="stretch"):
            start_new_game()
            st.rerun()


def render_play() -> None:
    state = st.session_state.state

    if st.session_state.gm is None and st.session_state.error is None:
        with st.spinner("별장의 첫 밤이 시작된다…"):
            request_gm(
                "게임을 시작한다. 도입부 나레이션(2~4문장)과 첫 선택지 3개를 제시하라. "
                "플레이어는 거실에 있다."
            )
        st.rerun()

    if st.session_state.error:
        st.error(st.session_state.error)
        if st.button("다시 시도"):
            last = st.session_state.history[-1] if st.session_state.history else None
            if last and last["role"] == "user":
                st.session_state.history = st.session_state.history[:-1]
                request_gm(last["content"].split("\n\n[현재 상태]")[0])
            else:
                request_gm("게임을 시작한다. 도입부 나레이션과 첫 선택지 3개를 제시하라.")
            st.rerun()
        return

    gm = st.session_state.gm
    if gm is None:
        return

    st.markdown(f"#### {state['location']}")
    st.write(gm["narration"])

    if st.session_state.found:
        found = st.session_state.found
        with st.container(border=True):
            st.badge("단서 획득", icon=":material/search:", color="orange")
            st.markdown(f"**{found['name']}**")
            st.caption(found["detail"])

    remaining = state["max_turns"] - state["turn"]
    st.caption(f"남은 턴 {remaining}")
    st.divider()

    st.caption("이동은 턴을 소모하지 않습니다. 조사와 심문만 1턴.")

    for index, choice in enumerate(gm["choices"]):
        if st.button(
            f"{choice['action']} · {choice['label']}",
            key=f"choice-{state['turn']}-{index}-{choice['action']}-{choice['target']}",
            width="stretch",
        ):
            with st.spinner("…"):
                take_action(choice)
            st.rerun()


def render_accuse() -> None:
    st.markdown("#### 지목")
    st.write(
        "밤이 끝났다. 폭풍이 잦아들고 아침 배가 들어온다. "
        "지금 한 명을 지목해야 한다."
    )
    st.divider()

    # 되돌릴 수 없는 마지막 결정이므로 세 용의자 모두 primary로 강조하고,
    # 포기는 tertiary(테두리 없는 텍스트)로 낮춰 위계를 만든다.
    for key, info in gs.SUSPECTS.items():
        if st.button(
            f"{info['name']} 을(를) 지목한다",
            key=f"accuse-{key}",
            type="primary",
            icon=":material/gavel:",
            width="stretch",
        ):
            st.session_state.ending = gs.accuse(st.session_state.state, key)
            st.session_state.phase = "ending"
            st.rerun()

    if st.button("지목을 포기한다", key="accuse-none", type="tertiary", width="stretch"):
        st.session_state.ending = gs.accuse(st.session_state.state, None)
        st.session_state.phase = "ending"
        st.rerun()


def render_ending() -> None:
    ending = st.session_state.ending
    st.markdown(f"#### {ending['title']}")
    st.write(ending["text"])
    st.divider()
    if st.button("다시 플레이", width="stretch"):
        start_new_game()
        st.rerun()


st.title("🕯️ 그날 밤, 별장에서")

if "state" not in st.session_state:
    start_new_game()

render_sidebar()

phase = st.session_state.phase
if phase == "play":
    render_play()
elif phase == "accuse":
    render_accuse()
else:
    render_ending()
