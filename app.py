"""그날 밤, 별장에서 — Streamlit 진입점.

턴 진행 → 상태 갱신 → 엔딩 분기까지 도는 최소 버전.
"""

from __future__ import annotations

import anthropic
import streamlit as st

import game_state as gs
from gm import GMError, call_gm

st.set_page_config(page_title="그날 밤, 별장에서", page_icon="🕯️")

OPENING_PROMPT = (
    "게임을 시작한다. 도입부 나레이션(2~4문장)과 첫 선택지 3개를 제시하라. "
    "플레이어는 거실에 있다."
)


@st.cache_resource
def get_client() -> anthropic.Anthropic:
    # ANTHROPIC_API_KEY 환경변수 또는 `ant auth login` 프로필을 자동으로 사용.
    # 529(overloaded)/429는 SDK가 지수 백오프로 자동 재시도한다. 기본 2회로는
    # 과부하 구간을 넘기지 못해서 올렸다. 게임 한 턴이 늦게 오는 것이
    # 에러 화면을 보는 것보다 낫다.
    return anthropic.Anthropic(max_retries=6, timeout=120.0)


def start_new_game() -> None:
    st.session_state.state = gs.new_state()
    st.session_state.history = []
    st.session_state.phase = "play"
    st.session_state.gm = None
    st.session_state.error = None
    st.session_state.found = None
    st.session_state.ending = None
    st.session_state.pending_input = None


def request_gm(player_input: str) -> None:
    # 실패 시 이 프롬프트를 그대로 재전송해야 한다. call_gm은 성공할 때만 히스토리를
    # 돌려주므로 히스토리에서 되짚을 수 없다(그러면 오프닝이 재전송된다).
    st.session_state.pending_input = player_input
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
    st.session_state.pending_input = None
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

        # progress bar 6개를 용의자 1행씩 3행 표로 묶었다. 같은 인물의 의심도와
        # 신뢰도를 나란히 봐야 심문 판단이 되는데, 이전 배치는 두 값이 떨어져 있었다.
        st.caption("용의자")
        st.dataframe(
            [
                {
                    "용의자": info["short"],
                    "의심도": state["suspicion"][key],
                    "신뢰도": state["npc_trust"][key],
                }
                for key, info in gs.SUSPECTS.items()
            ],
            hide_index=True,
            width="stretch",
            column_config={
                "용의자": st.column_config.TextColumn(width="small"),
                "의심도": st.column_config.ProgressColumn(
                    min_value=0, max_value=100, format="%d", width="small",
                    help="이 인물이 범인이라는 심증. 조사·심문 결과로 움직인다.",
                ),
                "신뢰도": st.column_config.ProgressColumn(
                    min_value=0, max_value=100, format="%d", width="small",
                    help="높으면 심문에서 더 많이 털어놓는다. 70 이상이면 숨긴 것까지 흘린다.",
                ),
            },
        )

        st.caption(f"단서 {len(state['clues_found'])} / {len(gs.CLUES)}")
        if state["clues_found"]:
            st.markdown(
                "\n".join(
                    f"- **{gs.CLUES[cid]['name']}**  \n  :gray[{gs.CLUES[cid]['location']}]"
                    for cid in state["clues_found"]
                )
            )
        else:
            st.caption("아직 없음")

        st.divider()
        if st.button("처음부터 다시", icon=":material/restart_alt:", width="stretch"):
            start_new_game()
            st.rerun()


def render_play() -> None:
    state = st.session_state.state

    if st.session_state.gm is None and st.session_state.error is None:
        with st.spinner("별장의 첫 밤이 시작된다…"):
            request_gm(OPENING_PROMPT)
        st.rerun()

    if st.session_state.error:
        st.error(st.session_state.error)
        st.caption(
            "턴과 단서는 이미 반영되어 있습니다. 재시도하면 같은 행동의 나레이션만 다시 받습니다."
        )
        if st.button("다시 시도", icon=":material/refresh:"):
            with st.spinner("…"):
                request_gm(st.session_state.pending_input or OPENING_PROMPT)
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

    # 마지막 턴에 GM 호출이 실패하면 그 턴의 나레이션 없이 여기로 넘어온다.
    # 조용히 삼키면 플레이어는 이유를 모르므로 남은 단서 기준으로 안내한다.
    if st.session_state.error:
        st.warning(
            f"마지막 행동의 나레이션을 받지 못했습니다 — {st.session_state.error}\n\n"
            "수집한 단서는 사이드바에 그대로 남아 있으니 그것을 근거로 지목하세요."
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
