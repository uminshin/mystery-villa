"""그날 밤, 별장에서 — Streamlit 진입점.

턴 진행 → 상태 갱신 → 엔딩 분기까지 도는 최소 버전.
"""

from __future__ import annotations

import base64
from pathlib import Path

import anthropic
import streamlit as st

import game_state as gs
from gm import GMError, call_gm

ART_DIR = Path(__file__).parent / "assets" / "locations"
AUDIO_DIR = Path(__file__).parent / "assets" / "audio"

# 행동별 효과음. 없는 파일은 조용히 건너뛴다.
ACTION_SOUNDS = {"이동": "move.wav", "조사": "search.wav", "심문": "interrogate.wav"}

st.set_page_config(page_title="그날 밤, 별장에서", page_icon="🕯️")

OPENING_PROMPT = (
    "게임을 시작한다. 도입부 나레이션(2~4문장)과 첫 선택지 3개를 제시하라. "
    "플레이어는 거실에 있다."
)

# 발견·변화를 알리는 강조색. 촛불 불꽃색으로 잡았다. 주색상(오xblood)은
# '되돌릴 수 없는 결정'에 이미 쓰고 있어서 구분되는 색이 필요했고,
# 배경 대비 11.2:1로 WCAG AA를 크게 넘긴다.
HIGHLIGHT = "#f2c14e"

# 행동 종류를 한눈에 구분하기 위한 아이콘. 이동만 턴을 소모하지 않는다.
ACTION_ICONS = {
    "이동": ":material/directions_walk:",
    "조사": ":material/search:",
    "심문": ":material/record_voice_over:",
}


@st.cache_data(show_spinner=False)
def load_location_art(location: str) -> str | None:
    """장소 삽화 SVG를 읽어 온다. 파일이 없으면 None(삽화 없이 진행).

    location이 "__map__"이면 평면도를 읽는다.
    """
    slug = "map" if location == "__map__" else gs.LOCATION_ART.get(location)
    if not slug:
        return None
    path = ART_DIR / f"{slug}.svg"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _art_data_uri(location: str) -> str | None:
    svg = load_location_art(location)
    if svg is None:
        return None
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def render_map(location: str) -> None:
    """별장 평면도. 지금 있는 방을 강조색 테두리로 표시한다.

    배경 이미지로 넣으면 외부 CSS가 SVG 내부에 닿지 않으므로,
    SVG 문서 안에 <style>을 끼워 넣어서 현재 방만 강조한다.
    """
    svg = load_location_art("__map__")
    if svg is None:
        return
    slug = gs.LOCATION_ART.get(location)
    if slug:
        svg = svg.replace(
            "</svg>",
            f"<style>#room-{slug}{{fill:#241f1a;stroke:{HIGHLIGHT};stroke-width:3}}</style></svg>",
        )
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    st.html(
        f"""<style>
        .villa-map {{
            width: 100%;
            aspect-ratio: 420 / 300;
            background-image: url('data:image/svg+xml;base64,{encoded}');
            background-size: contain;
            background-repeat: no-repeat;
            background-position: center;
        }}
        </style>
        <div class="villa-map"></div>"""
    )


def render_location_art(location: str) -> None:
    """장소 삽화를 배경 이미지로 깐다.

    st.html은 <svg>를 살균 과정에서 제거하므로 인라인 SVG는 쓸 수 없다.
    data URI 배경으로 넣으면 <div>와 <style>만 쓰게 되어 그대로 살아남는다.
    """
    uri = _art_data_uri(location)
    if uri is None:
        return
    st.html(
        f"""<style>
        .villa-art {{
            width: 100%;
            aspect-ratio: 800 / 240;
            background-image: url('{uri}');
            background-size: cover;
            background-position: center;
            border: 1px solid #3a322a;
            border-radius: 4px;
        }}
        </style>
        <div class="villa-art"></div>"""
    )


def inject_clue_background(location: str) -> None:
    """단서 카드 배경에 그 장소의 삽화를 깔아 준다.

    본문을 읽을 수 있어야 하므로 어두운 레이어를 한 겹 덮는다.
    """
    uri = _art_data_uri(location)
    if uri is None:
        return
    st.html(
        f"""<style>
        .st-key-clue-card {{
            background-image:
                linear-gradient(rgba(20, 17, 15, 0.9), rgba(20, 17, 15, 0.9)),
                url('{uri}');
            background-size: cover;
            background-position: center;
        }}
        </style>"""
    )


@st.cache_data(show_spinner=False)
def load_audio(name: str) -> bytes | None:
    path = AUDIO_DIR / name
    return path.read_bytes() if path.exists() else None


def render_bgm() -> None:
    """폭우 보량음. 게임이 시작된 뒤에만 재생한다.

    브라우저는 사용자 조작 전에는 자동재생을 막는다. 메인 화면의
    '수사를 시작한다'를 누른 뒤에 이 요소가 처음 등장하므로 그 제약을 넘긴다.
    """
    if not st.session_state.get("sound_on"):
        return
    data = load_audio("rain.wav")
    if data is None:
        return
    # 컨테이너 키를 고정해 두면 턴이 바뀌어도 같은 요소로 취급되어
    # 재생이 처음부터 다시 시작되지 않는다.
    with st.container(key="bgm"):
        st.audio(data, format="audio/wav", loop=True, autoplay=True)


def render_action_sound() -> None:
    """직전 행동의 효과음을 한 번 재생한다.

    키에 턴과 행동을 넣어, 행동이 바뀔 때만 새 요소가 만들어지며 재생된다.
    """
    if not st.session_state.get("sound_on"):
        return
    last = st.session_state.get("last_sound")
    if not last:
        return
    data = load_audio(ACTION_SOUNDS.get(last["action"], ""))
    if data is None:
        return
    with st.container(key=f"sfx-{last['seq']}"):
        st.audio(data, format="audio/wav", autoplay=True)


def inject_css() -> None:
    """나레이션 본문을 키우고 강조 요소에 색을 넣는다.

    config.toml은 요소 단위 크기를 지정할 수 없어서 이 부분만 CSS로 처리한다.
    st.container(key=...)가 만들어 주는 .st-key-<key> 클래스에만 붙인다.
    """
    st.html(
        f"""<style>
        .st-key-narration p {{
            font-size: 1.2rem;
            line-height: 1.9;
            letter-spacing: 0.01em;
        }}
        .st-key-location h1 {{
            margin-bottom: 0.1rem;
        }}


        /* 단서 카드: 글자는 움직이지 않는다. 테두리 글로우만 두 번 번쩍인다. */
        .st-key-clue-card {{
            border-color: {HIGHLIGHT} !important;
            animation: clue-glow 1.0s ease-in-out 0.15s 2;
        }}
        @keyframes clue-glow {{
            0%, 100% {{ box-shadow: 0 0 0 0 rgba(242, 193, 78, 0); }}
            45%      {{ box-shadow: 0 0 0 5px rgba(242, 193, 78, 0.32); }}
        }}

        /* 사이드바 단서 개수: 새 단서가 들어온 턴에만 강조색으로 점멸.
           키에 턴 번호가 들어가 있어서 턴마다 애니메이션이 다시 걸린다. */
        [class*="st-key-clue-count-hit"] p {{
            color: {HIGHLIGHT} !important;
            font-weight: 600;
            animation: chip-pop 0.9s ease-out;
        }}

        @keyframes chip-pop {{
            0%   {{ transform: scale(1); }}
            25%  {{ transform: scale(1.18); }}
            55%  {{ transform: scale(1.04); }}
            100% {{ transform: scale(1); }}
        }}
        [class*="st-key-clue-count-hit"] {{
            transform-origin: left center;
        }}

        /* 남은 턴이 얼마 없을 때 강조 */
        [class*="st-key-turns-low"] p {{
            color: {HIGHLIGHT} !important;
            font-weight: 600;
        }}

        @media (prefers-reduced-motion: reduce) {{
            .st-key-clue-card,
            [class*="st-key-clue-count-hit"] p {{
                animation: none;
            }}
        }}
        </style>"""
    )


@st.cache_resource
def get_client() -> anthropic.Anthropic:
    # ANTHROPIC_API_KEY 환경변수 또는 `ant auth login` 프로필을 자동으로 사용.
    #
    # 재시도 횟수는 일부러 낮게 잡았다. 실측 기준 정상 응답은 7초 안팎인데,
    # 과부하 구간에서 max_retries를 크게 두면 지수 백오프가 누적되어 한 턴이
    # 몇 분씩 걸린다(그동안 화면은 멈춘 것처럼 보인다). 빨리 실패하고
    # '다시 시도'를 누르게 하는 편이 낫다.
    return anthropic.Anthropic(max_retries=2, timeout=60.0)


def start_new_game() -> None:
    st.session_state.state = gs.new_state()
    st.session_state.history = []
    st.session_state.phase = "start"
    st.session_state.gm = None
    st.session_state.error = None
    st.session_state.found = None
    st.session_state.ending = None
    st.session_state.pending_input = None
    st.session_state.log = []
    # 용의자별로 메모를 나눈다. 알리바이를 인물 단위로 적어야 대조가 된다.
    for key in gs.SUSPECTS:
        st.session_state[f"notes_{key}"] = ""
    st.session_state.last_sound = None
    st.session_state.sound_seq = 0
    # sound_on은 위젯 키라서 여기서 건드리지 않는다. 위젯이 이미 그려진 뒤에
    # 대입하면 Streamlit이 예외를 던지고, 소리 설정은 판을 넘겨 유지되는 편이 낫다.


def request_gm(player_input: str, narration_slot=None) -> None:
    # 실패 시 이 프롬프트를 그대로 재전송해야 한다. call_gm은 성공할 때만 히스토리를
    # 돌려주므로 히스토리에서 되짚을 수 없다(그러면 오프닝이 재전송된다).
    st.session_state.pending_input = player_input

    on_narration = None
    if narration_slot is not None:
        # 도착하는 대로 같은 자리에 덮어써서 타이핑되는 것처럼 보이게 한다.
        on_narration = lambda text: narration_slot.markdown(text)  # noqa: E731

    try:
        gm, history = call_gm(
            get_client(),
            st.session_state.history,
            st.session_state.state,
            player_input,
            on_narration=on_narration,
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


def take_action(choice: dict[str, str], narration_slot=None) -> None:
    state = st.session_state.state
    found = gs.resolve_action(state, choice)
    spent = gs.costs_turn(choice["action"])
    if spent:
        gs.advance_turn(state)
    st.session_state.found = found

    st.session_state.sound_seq += 1
    st.session_state.last_sound = {
        "action": choice["action"],
        "seq": st.session_state.sound_seq,
    }

    outcome = f"발견한 단서: {found['name']} — {found['detail']}" if found else "새로 발견한 단서: 없음"
    cost = "1턴 소모" if spent else "턴 소모 없음(이동)"
    request_gm(
        f"[턴 {state['turn']}/{state['max_turns']}] 플레이어 행동: {choice['label']} "
        f"(action={choice['action']}, target={choice['target'] or '현재 장소'}, {cost})\n"
        f"판정 결과: {outcome}\n"
        f"이 결과를 반영한 나레이션과 다음 선택지 3개를 제시하라.",
        narration_slot=narration_slot,
    )

    # 탐정 노트에 자동으로 한 줄 남긴다. 플레이어가 직접 적는 메모와 별개로,
    # "몇 턴에 어디서 무엇을 했는지"는 기계가 기억해 주는 편이 낫다.
    if st.session_state.error is None:
        st.session_state.log.append(
            {
                "turn": state["turn"],
                "location": state["location"],
                "action": choice["action"],
                "label": choice["label"],
                "found": found["name"] if found else None,
            }
        )

    # 턴이 끝나도 화면을 자동으로 넘기지 않는다. 마지막 나레이션을 읽고
    # 메모를 정리할 시간을 준 뒤, 플레이어가 직접 지목 화면으로 들어가야 한다.


def render_case_file() -> None:
    """수사 기록 — 사이드바를 없애고 본문 아래 탭으로 옮겼다.

    용의자 수치 / 탐정 노트 / 단서 목록을 나란히 두어, 진술의 앞뒤를 맞춰볼 때
    한 화면에서 오갈 수 있게 했다.
    """
    state = st.session_state.state
    # 순서: 용의자 > 단서 > 탐정 노트. 메모가 길어질 수 있어 노트를 맨 뒤로 둔다.
    suspects_tab, clues_tab, notes_tab = st.tabs(
        [
            "용의자",
            f"단서 {len(state['clues_found'])} / {len(gs.CLUES)}",
            "탐정 노트",
        ]
    )

    with suspects_tab:
        # progress bar 6개를 용의자 1행씩 3행 표로 묶었다. 같은 인물의 의심도와
        # 신뢰도를 나란히 봐야 심문 판단이 되는데, 이전 배치는 두 값이 떨어져 있었다.
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

    with notes_tab:
        # 자동 기록 + 직접 메모. 알리바이의 앞뒤를 맞추려면 "누가 몇 시에 뭘 했다"를
        # 어딘가에 적어둬야 하는데, 그걸 게임 밖 종이에 적게 만들 이유가 없다.
        if st.session_state.log:
            rows = []
            for entry in st.session_state.log:
                rows.append(
                    {
                        "턴": entry["turn"],
                        "장소": entry["location"],
                        "행동": entry["action"],
                        "내용": entry["label"],
                        "단서": entry["found"] or "",
                    }
                )
            st.dataframe(rows, hide_index=True, width="stretch")
        else:
            st.caption("아직 기록이 없습니다.")

        st.caption("인물별 메모")
        for key, info in gs.SUSPECTS.items():
            st.text_area(
                info["name"],
                key=f"notes_{key}",
                height=110,
                placeholder=f"{info['short']}의 진술과 모순을 적어두세요.\n예) 23:00 취침 주장",
            )

    with clues_tab:
        found_this_turn = st.session_state.found is not None
        count_key = (
            f"clue-count-hit-{state['turn']}" if found_this_turn else "clue-count"
        )
        with st.container(key=count_key):
            st.caption(f"단서 {len(state['clues_found'])} / {len(gs.CLUES)}")

        if state["clues_found"]:
            for index, cid in enumerate(state["clues_found"]):
                clue = gs.CLUES[cid]
                newest = found_this_turn and index == len(state["clues_found"]) - 1
                with st.container(border=True):
                    mark = " :orange[**← 방금**]" if newest else ""
                    st.markdown(f"**{clue['name']}**{mark}")
                    st.caption(f"{clue['location']} · {clue['detail']}")
        else:
            st.caption("아직 없음")


def render_start() -> None:
    """사건 브리핑. 추리에 필요한 전제는 전부 여기서 밝힌다.

    플레이어가 모르는 정보(인물의 성별·관계 등)를 전제로 단서를 해석하게 하면
    안 되므로, 인물 정보를 처음부터 표로 공개한다.
    """
    st.markdown("# 🕯️ 그날 밤, 별장에서")
    st.markdown(":gray[사건에 들어가며]")
    st.divider()

    with st.container(key="narration"):
        st.markdown(
            "폭풍으로 뱃길이 끊긴 외딴 섬의 별장. 당신은 이곳의 손님이 아니라, "
            "의뢰를 받고 건너온 **탐정**이다. 어젯밤 별장의 주인이 서재에서 숨졌고, "
            "배가 다시 들어오는 아침까지 섬을 나갈 수 있는 사람은 아무도 없다. "
            "지금 이 건물 안에 범인이 있다."
        )

    st.write("")
    left, right = st.columns([1, 1], gap="medium")

    with left:
        st.markdown("###### 피해자")
        with st.container(border=True):
            st.markdown(f"**{gs.VICTIM}**")
            st.markdown(
                f":gray[사인] {gs.CAUSE_OF_DEATH}  \n"
                f":gray[사망 추정] **{gs.TIME_OF_DEATH}**  \n"
                f":gray[발견] {gs.DISCOVERY}"
            )

    with right:
        st.markdown("###### 그날 밤의 시각")
        with st.container(border=True):
            st.markdown(
                "  \n".join(
                    f"**{when}** · {what}" if when == gs.TIME_OF_DEATH
                    else f":gray[{when}] {what}"
                    for when, what in gs.TIMELINE
                )
            )

    st.write("")
    st.markdown("###### 별장에 남은 세 사람")
    st.dataframe(
        [
            {
                "인물": info["name"],
                "성별": info["gender"],
                "나이": info["age"],
                "피해자와의 관계": info["relation"],
            }
            for info in gs.SUSPECTS.values()
        ],
        hide_index=True,
        width="stretch",
        column_config={
            "인물": st.column_config.TextColumn(width="small"),
            "성별": st.column_config.TextColumn(width="small"),
            "나이": st.column_config.TextColumn(width="small"),
            "피해자와의 관계": st.column_config.TextColumn(width="large"),
        },
    )

    st.write("")
    st.markdown("###### 수사 규칙")
    rule_left, rule_right = st.columns([1, 1], gap="medium")
    with rule_left:
        with st.container(border=True):
            st.markdown(
                "**턴**  \n"
                f":gray[· 주어진 시간은 **{gs.MAX_TURNS}턴**]  \n"
                ":gray[· 장소 이동은 턴을 쓰지 않는다]  \n"
                ":gray[· 조사와 심문은 1턴]"
            )
    with rule_right:
        with st.container(border=True):
            st.markdown(
                "**지목**  \n"
                ":gray[· 언제든 지목할 수 있다]  \n"
                f":gray[· 결정적 단서 **{gs.REQUIRED_CLUES}개** 이상이 필요하다]  \n"
                ":gray[· 심증만으로는 사건이 미제로 남는다]"
            )

    st.write("")
    st.toggle(
        "소리 켜기",
        key="sound_on",
        help="폭우 보량음과 행동 효과음. 게임 중에도 끌 수 있습니다.",
    )

    if st.button(
        "수사를 시작한다",
        type="primary",
        icon=":material/play_arrow:",
        width="stretch",
    ):
        st.session_state.phase = "play"
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

    # 화면이 넓으면 좌우 2단, 좁아지면 Streamlit이 알아서 위아래로 쌓는다.
    # 수사 기록을 스크롤 없이 옆에서 보게 하려는 배치다.
    stage, panel = st.columns([3, 2], gap="medium")

    with panel:
        render_map(state["location"])
        render_case_file()
        st.toggle("소리 켜기", key="sound_on")

    with stage:
        # 제목을 없앤 자리를 장소가 채운다. 지금 어디에 있는지가 화면의 머리글이다.
        with st.container(key="location"):
            st.markdown(f"# {state['location']}")
        render_location_art(state["location"])

        remaining = state["max_turns"] - state["turn"]
        with st.container(key="turns-low" if remaining <= 3 else "turns"):
            st.caption(
                f"남은 턴 {remaining}"
                + ("  · 시간이 얼마 없다" if 0 < remaining <= 3 else "")
            )

        # 이 자리에 다음 턴 나레이션이 스트리밍으로 덮어쓰인다.
        with st.container(key="narration"):
            narration_slot = st.empty()
            narration_slot.markdown(gm["narration"])

        if st.session_state.found:
            found = st.session_state.found
            inject_clue_background(found["location"])
            with st.container(border=True, key="clue-card"):
                st.badge("단서 획득", icon=":material/bookmark:", color="orange")
                st.markdown(f"**{found['name']}**")
                st.caption(found["detail"])

        # 심증만 앞서갈 때 물증을 챙기라고 알린다. 진범 정보를 보지 않으므로
        # 이 경고로 범인을 역산할 수 없다 (A/B/C에 같은 규칙이 걸린다).
        for key, count in gs.weak_evidence_warnings(state):
            st.warning(
                f"**{gs.SUSPECTS[key]['name']}** 쪽으로 의심이 쏠려 있지만, "
                f"그를 가리키는 물증은 {count}개입니다. "
                f"지목이 받아들여지려면 결정적 단서 {gs.REQUIRED_CLUES}개가 필요합니다.",
                icon=":material/warning:",
            )

        st.divider()

        if gs.must_accuse(state):
            # 시간이 끝났다. 화면을 강제로 넘기지 않고, 기록을 정리한 뒤
            # 직접 들어가도록 버튼만 남긴다.
            st.info(
                "주어진 시간이 끝났습니다. 기록을 정리한 뒤 지목 화면으로 넘어가세요.",
                icon=":material/hourglass_bottom:",
            )
        else:
            st.caption("이동은 턴을 소모하지 않습니다. 조사와 심문만 1턴.")
            for index, choice in enumerate(gm["choices"]):
                if st.button(
                    choice["label"],
                    icon=ACTION_ICONS.get(choice["action"]),
                    key=f"choice-{state['turn']}-{index}-{choice['action']}-{choice['target']}",
                    width="stretch",
                ):
                    narration_slot.markdown(":gray[…]")
                    take_action(choice, narration_slot=narration_slot)
                    st.rerun()

        # 턴이 남았어도 눈치챘으면 바로 지목할 수 있게 한다.
        st.write("")
        _render_accuse_entry()


def _render_accuse_entry() -> None:
    """지목 화면으로 들어가는 버튼. 시간이 끝났으면 이게 유일한 다음 행동이다."""
    final = gs.must_accuse(st.session_state.state)
    if st.button(
        "범인을 지목한다" if final else "지금 범인을 지목한다",
        icon=":material/gavel:",
        type="primary" if final else "tertiary",
        width="stretch",
    ):
        st.session_state.phase = "accuse"
        st.rerun()


def render_accuse() -> None:
    state = st.session_state.state
    early = not gs.must_accuse(state)

    st.markdown("# 지목")
    if early:
        st.write(
            f"아직 {state['max_turns'] - state['turn']}턴이 남아 있다. "
            "그래도 지금 결론을 내리겠다면, 지목한 순간 밤은 끝난다."
        )
        if st.button("아직 아니다 — 수사를 계속한다", icon=":material/arrow_back:"):
            st.session_state.phase = "play"
            st.rerun()
    else:
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
    st.markdown(f"# {ending['title']}")
    with st.container(key="narration"):
        st.markdown(ending["text"])
    st.divider()
    if st.button(
        "다시 플레이", icon=":material/restart_alt:", type="primary", width="stretch"
    ):
        start_new_game()
        st.rerun()
    render_case_file()


if "state" not in st.session_state:
    start_new_game()

# 위젯 키의 초기값은 위젯이 그려지기 전에 딱 한 번만 넣는다.
st.session_state.setdefault("sound_on", True)

inject_css()

phase = st.session_state.phase

# 오디오 요소는 컬럼 밖 고정 위치에 둔다. 위치가 흔들리면 요소가 새로 만들어져
# 보량음이 매 턴 처음부터 다시 재생된다.
if phase != "start":
    render_bgm()
    render_action_sound()

if phase == "start":
    render_start()
elif phase == "play":
    render_play()
elif phase == "accuse":
    render_accuse()
else:
    render_ending()
