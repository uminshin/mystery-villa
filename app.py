"""그날 밤, 별장에서 — Streamlit 진입점.

턴 진행 → 상태 갱신 → 엔딩 분기까지 도는 최소 버전.
"""

from __future__ import annotations

import base64
from pathlib import Path

import anthropic
import streamlit as st
import streamlit.components.v1 as components

import game_state as gs
from gm import GMError, call_gm

ART_DIR = Path(__file__).parent / "assets" / "locations"
AUDIO_DIR = Path(__file__).parent / "assets" / "audio"

# 행동별 효과음. 없는 파일은 조용히 건너뛴다.
ACTION_SOUNDS = {"이동": "move.wav", "조사": "search.wav", "심문": "interrogate.wav"}

# 배경음(빗소리) 재생 볼륨(0~1). 효과음을 덮지 않도록 낮춰 둔다. 효과음은
# 별도 <audio>라 이 값의 영향을 받지 않는다. render_bgm의 JS에서 사용한다.
BGM_VOLUME = 0.28

st.set_page_config(
    page_title="그날 밤, 별장에서",
    page_icon="🕯️",
    # 기본 레이아웃은 본문을 700px 남짓으로 묶어서, 2단으로 나누면 양쪽이 다 좁아진다.
    # 나레이션 자체는 CSS의 max-width로 읽기 좋은 폭을 따로 유지한다.
    layout="wide",
)

OPENING_PROMPT = (
    "게임을 시작한다. 도입부 나레이션(2~4문장)과 첫 선택지 3개를 제시하라. "
    "플레이어는 거실에 있다."
)

# 발견·변화를 알리는 강조색. 촛불 불꽃색으로 잡았다. 주색상(오xblood)은
# '되돌릴 수 없는 결정'에 이미 쓰고 있어서 구분되는 색이 필요했고,
# 배경 대비 11.2:1로 WCAG AA를 크게 넘긴다.
HIGHLIGHT = "#f2c14e"

# 사건 식별자. 2화가 붙으면 이 키로 결과를 구분한다.
CASE_ID = "case-1"

# 행동 종류를 한눈에 구분하기 위한 아이콘. 이동만 턴을 소모하지 않는다.
ACTION_ICONS = {
    "이동": ":material/directions_walk:",
    "조사": ":material/search:",
    "심문": ":material/record_voice_over:",
}


@st.cache_data(show_spinner=False)
def load_svg(slug: str) -> str | None:
    """assets/locations/<slug>.svg를 읽어 온다. 없으면 None(삽화 없이 진행)."""
    path = ART_DIR / f"{slug}.svg"
    return path.read_text(encoding="utf-8") if path.exists() else None


def load_location_art(location: str) -> str | None:
    slug = gs.LOCATION_ART.get(location)
    return load_svg(slug) if slug else None


def svg_block(
    svg: str,
    class_name: str,
    ratio: str,
    fit: str = "cover",
    max_width: str | None = None,
) -> None:
    """SVG를 data URI 배경으로 깐 div 하나를 그린다.

    st.html이 <svg>를 살균 과정에서 제거하기 때문에 인라인 SVG는 쓸 수 없다.
    <style>과 <div>는 통과하므로 배경 이미지로 우회한다.
    """
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    cap = f"max-width: {max_width};" if max_width else ""
    st.html(
        f"""<style>
        .{class_name} {{
            width: 100%;
            {cap}
            aspect-ratio: {ratio};
            background-image: url('data:image/svg+xml;base64,{encoded}');
            background-size: {fit};
            background-repeat: no-repeat;
            background-position: center;
        }}
        </style>
        <div class="{class_name}"></div>"""
    )


def _art_data_uri(location: str) -> str | None:
    svg = load_location_art(location)
    if svg is None:
        return None
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def render_map(location: str) -> None:
    """별장 평면도. 지금 있는 방을 강조색으로 칠한다.

    배경 이미지로 쓰면 외부 CSS가 SVG 내부에 닿지 않으므로,
    SVG 문서 안에 <style>을 끼워 넣어 현재 방만 강조한다.
    """
    svg = load_svg("map")
    if svg is None:
        return
    slug = gs.LOCATION_ART.get(location)
    if slug:
        svg = svg.replace(
            "</svg>",
            f"<style>#room-{slug}{{fill:#2a231d;stroke:{HIGHLIGHT}}}</style></svg>",
        )
    svg_block(svg, "villa-map", "440 / 340", fit="contain", max_width="360px")


def render_crime_scene() -> None:
    """사건 현장 도면. 어디서 어떤 자세로 쓰러졌는지 보여준다."""
    svg = load_svg("crime-scene")
    if svg is not None:
        svg_block(svg, "villa-scene", "420 / 300", fit="contain", max_width="420px")


def render_ending_art(slug: str | None) -> None:
    if not slug:
        return
    svg = load_svg(slug)
    if svg is not None:
        svg_block(svg, f"villa-{slug}", "800 / 260", max_width="46rem")


def render_location_art(location: str) -> None:
    svg = load_location_art(location)
    if svg is not None:
        svg_block(svg, "villa-art", "800 / 240", max_width="46rem")


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
    """폭우 배경음(빗소리). 게임이 시작된 뒤에만 재생한다.

    이 토글은 **배경음만** 제어한다 — 행동 효과음은 토글과 무관하게 항상 난다.

    구현 노트: st.audio에는 볼륨/음소거 파라미터가 없고, 컨테이너 키를 바꿔도
    이미 loop 재생 중인 <audio>는 멈추지 않는다(그래서 게임 중 토글이 안 먹혔다).
    그래서 오디오는 키를 고정해 **항상 한 번만** 만들어 두고(턴마다 리셋 안 됨),
    실제 on/off는 매 rerun마다 JS로 이 <audio>의 재생/음소거를 직접 제어한다.
    부모 문서에서 배경음만 콕 집으려고 st-key-bgm 컨테이너 안의 audio를 고른다.
    """
    data = load_audio("rain.wav")
    if data is None:
        return
    enabled = st.session_state.get("sound_enabled", True)
    with st.container(key="bgm"):
        st.audio(data, format="audio/wav", loop=True, autoplay=True)
    # height=0 컴포넌트로 JS만 주입한다. 컴포넌트 iframe은 같은 오리진이라
    # window.parent.document로 앱 본문의 <audio>에 접근할 수 있다.
    # 배경음은 계속 깔리는 소리라, 짧게 튀는 효과음을 덮지 않게 볼륨을 낮춘다.
    # (효과음은 별도 <audio>라 이 볼륨의 영향을 받지 않는다.)
    components.html(
        f"""
        <script>
        const enabled = {str(enabled).lower()};
        const volume = {BGM_VOLUME};
        const doc = window.parent.document;
        const apply = () => {{
            const el = doc.querySelector('.st-key-bgm audio');
            if (!el) return false;
            if (enabled) {{
                el.muted = false;
                el.volume = volume;
                if (el.paused) el.play().catch(() => {{}});
            }} else {{
                el.pause();
            }}
            return true;
        }};
        // 오디오가 아직 DOM에 없을 수 있으니, 없으면 잠깐 지켜보다 적용한다.
        if (!apply()) {{
            const obs = new MutationObserver(() => {{ if (apply()) obs.disconnect(); }});
            obs.observe(doc.body, {{childList: true, subtree: true}});
            setTimeout(() => obs.disconnect(), 4000);
        }}
        </script>
        """,
        height=0,
    )


def render_clue_chime() -> None:
    """단서 발견음. 나레이션이 다 나온 뒤에 울린다.

    클릭 즉시 울리게 했더니 조사음을 덮어버렸다. 이 함수는 스트리밍이 끝나고
    다시 그려지는 회차에서 호출되므로 자연히 나레이션 뒤에 온다.
    키에 턴을 넣어 한 번만 재생된다.

    효과음이므로 배경음 토글(sound_enabled)과 무관하게 항상 재생한다.
    """
    if not st.session_state.get("found"):
        return
    data = load_audio("clue.wav")
    if data is None:
        return
    with st.container(key=f"chime-{st.session_state.state['turn']}"):
        st.audio(data, format="audio/wav", autoplay=True)


def _sync_sound_enabled(where: str) -> None:
    """토글 변경을 rerun 시작 시점(스크립트 본문 실행 전)에 sound_enabled로 반영한다.

    이 콜백이 없으면, 스크립트 맨 위에서 도는 render_bgm()이 토글보다 먼저
    실행되면서 '한 박자 이전' 값을 읽는다 — 그래서 켜면 멈추고 끄면 나는 것처럼
    반대로 동작했다. on_change 콜백은 본문 실행 전에 먼저 돌므로 최신 값이 보장된다.
    """
    st.session_state.sound_enabled = st.session_state[f"sound_toggle_{where}"]


def sound_toggle(where: str, help_text: str | None = None) -> None:
    """배경음(빗소리) on/off 토글. 효과음은 이 토글과 무관하게 항상 난다.

    값을 위젯 키에만 두면 안 된다. 토글이 없는 화면(지목·엔딩)으로 넘어가면
    Streamlit이 그 위젯 상태를 버리고, 다음 회차에 기본값(켜짐)으로 되살아난다.
    즉 소리를 끄고 지목 화면에 가면 다시 켜졌다. 그래서 실제 값은 위젯이 아닌
    sound_enabled에 두고, 위젯에는 value로 넣어 준다. 변경 즉시 반영은
    on_change 콜백이 담당한다(위 설명 참고).
    """
    st.session_state.sound_enabled = st.toggle(
        "배경음",
        value=st.session_state.get("sound_enabled", True),
        key=f"sound_toggle_{where}",
        help=help_text,
        on_change=_sync_sound_enabled,
        args=(where,),
    )


def play_sound(slot, name: str) -> None:
    """버튼을 누른 즉시 효과음을 재생한다.

    이전에는 다음 rerun에서 오디오 요소를 만들었기 때문에 나레이션이 다 나온
    뒤에야 소리가 났다. 클릭 시점에 넘겨받은 슬롯에 바로 그려서 붙는 즉시
    재생되게 한다.

    효과음이므로 배경음 토글(sound_enabled)과 무관하게 항상 재생한다.
    """
    data = load_audio(name)
    if data is None:
        return
    slot.audio(data, format="audio/wav", autoplay=True)


def scroll_to_top_on_phase_change() -> None:
    """화면이 바뀔 때 맨 위로 올린다.

    Streamlit은 다시 그려도 스크롤 위치를 유지한다. 그래서 브리핑 아래쪽에서
    '수사를 시작한다'를 누르면 새 화면의 중간부터 보이고, 도입 나레이션을
    놓친다. 스크롤 API가 없어서 한 줄 스크립트로 처리한다.
    """
    phase = st.session_state.phase
    if st.session_state.get("scrolled_phase") == phase:
        return
    st.session_state.scrolled_phase = phase
    # 한 번만 올리면 소용이 없다. 스크립트가 도는 시점에는 새 화면의 배치가
    # 끝나지 않았고, Streamlit이 그 뒤에 이전 스크롤 위치를 복원한다.
    # 그래서 배치가 안정될 때까지 몇 번 더 올린다.
    st.html(
        """<script>
        const target = window.parent || window;
        const doc = target.document;
        const toTop = () => {
            const panes = [
                doc.querySelector('[data-testid="stMain"]'),
                doc.scrollingElement,
                doc.body,
            ];
            for (const pane of panes) { if (pane) pane.scrollTop = 0; }
            target.scrollTo(0, 0);
        };
        toTop();
        target.requestAnimationFrame(toTop);
        for (const delay of [50, 150, 350, 700]) {
            target.setTimeout(toTop, delay);
        }
        </script>""",
        unsafe_allow_javascript=True,
    )


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
        /* 브리핑 도입부는 이 게임의 첫 화면이다. 나레이션보다 한 단 크게. */
        .st-key-briefing p {{
            font-size: 1.42rem;
            line-height: 1.85;
            letter-spacing: 0.005em;
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
        [class*="st-key-turns-low"] p,
        [class*="st-key-turn-low"] [data-testid="stMetricValue"] {{
            color: {HIGHLIGHT} !important;
        }}

        /* 오디오 플레이어는 전부 숨긴다. 재생/루프는 계속 동작한다.
           효과음은 키 없는 슬롯에 그려지므로 요소 자체를 잡아야 한다. */
        [data-testid="stAudio"],
        [data-testid="stAudioInput"] {{
            display: none !important;
        }}
        [class*="st-key-bgm"] {{
            display: none !important;
        }}

        /* 나레이션 한 줄이 너무 길어지면 눈이 되돌아올 자리를 잃는다.
           한글 본문은 한 줄 40자 안쪽이 읽기 편하다. */
        .st-key-narration, .st-key-briefing {{
            max-width: 46rem;
        }}
        [class*="st-key-story-text-"] p {{
            font-size: 1.08rem;
            line-height: 1.9;
        }}

        /* 브리핑 인물표: 캔버스 기반 dataframe과 달리 글자 크기를 키울 수 있다 */
        .st-key-people-table table {{
            font-size: 1.02rem;
        }}
        .st-key-people-table th {{
            font-size: 0.94rem;
            color: #8a7f70;
        }}
        .st-key-people-table td {{
            padding-top: 0.55rem;
            padding-bottom: 0.55rem;
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
    st.session_state.phase = "home"
    st.session_state.gm = None
    st.session_state.error = None
    st.session_state.found = None
    st.session_state.ending = None
    st.session_state.pending_input = None
    st.session_state.log = []
    # 메모 본체는 위젯이 아닌 이 딕셔너리다. 위젯 키에 직접 대입하면
    # "cannot be modified after the widget is instantiated" 예외가 나므로,
    # 판 번호를 올려 위젯 키 자체를 새로 만들고 값은 여기서 비운다.
    st.session_state.notes = {key: "" for key in gs.SUSPECTS}
    st.session_state.game_id = st.session_state.get("game_id", 0) + 1
    # 소리 설정(sound_enabled)과 평판은 판을 넘겨 유지되므로 건드리지 않는다.


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

    outcome = f"발견한 단서: {found['name']} — {found['detail']}" if found else "새로 발견한 단서: 없음"
    cost = "1턴 소모" if spent else "턴 소모 없음(이동)"
    request_gm(
        f"[턴 {state['turn']}/{state['max_turns']}] 플레이어 행동: {choice['label']} "
        f"(action={choice['action']}, target={choice['target'] or '현재 장소'}, {cost})\n"
        f"판정 결과: {outcome}\n"
        f"이 결과를 반영한 나레이션과 다음 선택지 3개를 제시하라.",
        narration_slot=narration_slot,
    )

    # 단서를 찾았으면 수사 기록을 단서 면으로 데려간다. 여기서 panel_view에
    # 바로 대입하면 이번 회차에 이미 그려진 위젯이라 예외가 난다 — 그래서
    # 플래그만 남기고, 다음 회차에서 위젯이 그려지기 전에 소비한다.
    if found:
        st.session_state.jump_to_clues = True

    # 탐정 노트에 자동으로 한 줄 남긴다. 플레이어가 직접 적는 메모와 별개로,
    # "몇 턴에 어디서 무엇을 했는지"는 기계가 기억해 주는 편이 낫다.
    if st.session_state.error is None:
        gm_data = st.session_state.gm or {}
        st.session_state.log.append(
            {
                "turn": state["turn"],
                "location": state["location"],
                "action": choice["action"],
                "label": choice["label"],
                "found": found["name"] if found else None,
                # 의심도·신뢰도가 왜 움직였는지. 이게 없으면 나중에
                # "이 숫자는 무엇 때문이었나"를 되짚을 수 없다.
                "note": (gm_data.get("change_note") or "").strip(),
            }
        )

    # 턴이 끝나도 화면을 자동으로 넘기지 않는다. 마지막 나레이션을 읽고
    # 메모를 정리할 시간을 준 뒤, 플레이어가 직접 지목 화면으로 들어가야 한다.


PANEL_VIEWS = ("용의자", "단서", "탐정 노트")


def record_case_result(ending: dict) -> None:
    """지목 결과를 탐정 기록에 남긴다.

    평판은 판을 넘겨 누적되고, 해결 여부는 사건별로 기억한다(홈 화면에서
    '사건 해결'로 표시하기 위해). 판 초기화(start_new_game)로는 지워지지 않는다.
    """
    st.session_state.reputation = st.session_state.get("reputation", 0) + ending[
        "score"
    ]["total"]
    if ending["solved"]:
        st.session_state.solved = st.session_state.get("solved", 0) + 1
    results = st.session_state.setdefault("case_results", {})
    previous = results.get(CASE_ID)
    # 같은 사건을 여러 번 풀면 가장 좋은 결과를 남긴다.
    if previous is None or ending["score"]["total"] > previous["score"]:
        results[CASE_ID] = {
            "ending": ending["ending"],
            "title": ending["title"],
            "score": ending["score"]["total"],
            "solved": ending["solved"],
        }


def render_case_file() -> None:
    """수사 기록 — 용의자 / 단서 / 탐정 노트를 한 면씩 보여준다.

    st.tabs는 다시 그릴 때마다 첫 탭으로 돌아가고, 어느 면을 볼지 코드에서
    정할 수도 없다. 단서를 찾은 순간 단서 면으로 데려가려면 선택 상태를
    직접 들고 있어야 해서 segmented_control로 바꿨다.
    """
    state = st.session_state.state
    labels = {
        "용의자": "용의자",
        "단서": f"단서 {len(state['clues_found'])} / {len(gs.CLUES)}",
        "탐정 노트": "탐정 노트",
    }

    st.segmented_control(
        "수사 기록",
        PANEL_VIEWS,
        key="panel_view",
        format_func=lambda value: labels[value],
        label_visibility="collapsed",
        width="stretch",
    )

    view = st.session_state.get("panel_view") or "용의자"
    if view == "용의자":
        _render_suspects(state)
    elif view == "단서":
        _render_clues(state)

    # 노트는 다른 면을 보는 동안에도 항상 그린다. 그려지지 않은 회차가 생기면
    # Streamlit이 그 위젯 상태를 버려서 적어둔 메모가 사라진다. 값을 딕셔너리에
    # 옮겨 담아도 위젯이 사라졌다 되살아나는 사이에 비는 순간이 있어서,
    # 아예 계속 존재하게 두고 보이기만 CSS로 감춘다.
    if view != "탐정 노트":
        st.html(
            "<style>.st-key-notes-pane{display:none !important;}</style>",
        )
    with st.container(key="notes-pane"):
        _render_notes()


def _render_suspects(state: dict) -> None:
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
            "용의자": st.column_config.TextColumn(width=76),
            "의심도": st.column_config.ProgressColumn(
                min_value=0,
                max_value=100,
                format="%d",
                width=90,
                help="이 인물이 범인이라는 심증. 조사·심문 결과로 움직인다.",
            ),
            "신뢰도": st.column_config.ProgressColumn(
                min_value=0,
                max_value=100,
                format="%d",
                width=90,
                help="높으면 심문에서 더 많이 털어놓는다. 70 이상이면 숨긴 것까지 흘린다.",
            ),
        },
    )

    # 인상착의는 단서를 인물과 잇는 유일한 통로다. 브리핑에서만 보여주고
    # 게임 중에 감추면 "굽이 얇은 구두"를 누구와도 연결할 수 없다.
    st.caption("인물 정보")
    for key, info in gs.SUSPECTS.items():
        with st.expander(f"{info['name']} · {info['gender']} {info['age']}"):
            st.markdown(
                f":gray[관계] {info['relation']}  \n"
                f":gray[인상착의] {info['appearance']}  \n"
                f":gray[태도] {info['habit']}  \n"
                f":gray[알려진 동기] {info['motive']}"
            )


def _render_clues(state: dict) -> None:
    found_this_turn = st.session_state.found is not None
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


def _render_notes() -> None:
    # 자동 기록 + 인물별 메모. 알리바이의 앞뒤를 맞추려면 "누가 몇 시에 뭘 했다"를
    # 어딘가에 적어둬야 하는데, 그걸 게임 밖 종이에 적게 만들 이유가 없다.
    if st.session_state.log:
        st.dataframe(
            [
                {
                    "턴": entry["turn"],
                    "장소": entry["location"],
                    "행동": entry["action"],
                    "단서": entry["found"] or "",
                    "비고": entry.get("note", ""),
                }
                for entry in st.session_state.log
            ],
            hide_index=True,
            width="stretch",
            column_config={
                "턴": st.column_config.TextColumn(width=40),
                "장소": st.column_config.TextColumn(width=60),
                "행동": st.column_config.TextColumn(width=50),
                "단서": st.column_config.TextColumn(width=140),
                "비고": st.column_config.TextColumn(
                    width=170, help="의심도·신뢰도가 움직인 이유"
                ),
            },
        )
    else:
        st.caption("아직 기록이 없습니다.")

    # 메모를 위젯 키에만 두면 사라진다. 수사 기록을 한 면씩 보여주게 바꾼 뒤로는
    # 노트 면이 안 그려지는 회차가 생기고, Streamlit은 그 회차에 만들어지지 않은
    # 위젯의 상태를 버린다(단서를 찾아 단서 면으로 넘어가는 순간이 정확히 그 경우다).
    # 그래서 값은 위젯이 아닌 세션 딕셔너리에 보관하고, 위젯에는 value로 되돌려준다.
    notes = st.session_state.notes
    game_id = st.session_state.get("game_id", 0)
    for key, info in gs.SUSPECTS.items():
        notes[key] = st.text_area(
            info["name"],
            value=notes.get(key, ""),
            key=f"notes_{game_id}_{key}",
            height=104,
            placeholder=f"{info['short']}의 진술과 모순을 적어두세요.\n예) 23:00 취침 주장",
        )


def render_start() -> None:
    """사건 브리핑. 추리에 필요한 전제는 전부 여기서 밝힌다.

    플레이어가 모르는 정보(인물의 성별·관계 등)를 전제로 단서를 해석하게 하면
    안 되므로, 인물 정보를 처음부터 표로 공개한다.
    """
    st.markdown("## 사건에 들어가며")

    # 한 문장씩 줄을 끊는다. 통짜 문단은 어디까지 읽었는지 놓치기 쉽다.
    with st.container(key="briefing"):
        st.markdown(
            "폭풍으로 뱃길이 끊긴 외딴 섬의 별장.  \n"
            "당신은 이곳의 손님이 아니라, 의뢰를 받고 건너온 **탐정**이다.  \n"
            "어젯밤 별장의 주인이 서재에서 숨졌다.  \n"
            "아침 배가 들어오기 전까지 섬을 나갈 수 있는 사람은 아무도 없다.  \n"
            ":gray[지금 이 건물 안에 범인이 있다.]"
        )

    st.write("")
    facts, scene = st.columns([2, 3], gap="large", vertical_alignment="top")

    with facts:
        st.markdown("###### 피해자")
        # width="content"로 두면 테두리가 글자 폭까지만 감싼다.
        with st.container(border=True, width="content"):
            st.markdown(f"**{gs.VICTIM}**")
            st.markdown(
                f":gray[사인] {gs.CAUSE_OF_DEATH}  \n"
                f":gray[사망 추정] **{gs.TIME_OF_DEATH}**  \n"
                f":gray[발견] {gs.DISCOVERY}"
            )

        st.markdown("###### 그날 밤의 시각")
        with st.container(border=True, width="content"):
            st.markdown(
                "  \n".join(
                    f"**{when}** · {what}"
                    if when == gs.TIME_OF_DEATH
                    else f":gray[{when}] {what}"
                    for when, what in gs.TIMELINE
                )
            )

    with scene:
        st.markdown("###### 사건 현장")
        render_crime_scene()

    st.write("")
    st.markdown("###### 별장에 남은 세 사람")
    # dataframe은 캔버스로 그려서 글자 크기를 CSS로 못 키운다.
    # 진행에 따라 값이 바뀌지 않는 표라서 마크다운으로 그리고 CSS로 확대한다.
    with st.container(key="people-table"):
        rows = "\n".join(
            f"| {info['name']}<br>:gray[{info['gender']} · {info['age']}] "
            f"| {info['relation']} "
            f"| {info['appearance']}<br>:gray[{info['habit']}] |"
            for info in gs.SUSPECTS.values()
        )
        st.markdown(
            "| 인물 | 피해자와의 관계 | 인상착의 · 태도 |\n"
            "| --- | --- | --- |\n" + rows,
            unsafe_allow_html=True,
        )
        st.caption(
            "인상착의는 단서를 인물과 잇는 실마리입니다. 수사 중에도 "
            "수사 기록 › 용의자에서 다시 볼 수 있습니다."
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
    back, go = st.columns([1, 3], gap="small")
    with back:
        if st.button("돌아가기", icon=":material/arrow_back:", width="stretch"):
            st.session_state.phase = "home"
            st.rerun()
    with go:
        if st.button(
            "수사를 시작한다",
            type="primary",
            icon=":material/play_arrow:",
            width="stretch",
        ):
            st.session_state.phase = "play"
            st.rerun()


def render_home() -> None:
    """타이틀 화면. 사건을 고르고 설정을 만지는 자리.

    지금은 사건이 하나뿐이지만, 여러 사건을 이어서 하려면 이 층이 필요하다.
    탐정 등급은 아직 표시만 한다 — 평판 계산은 다음 단계다.
    """
    st.write("")
    st.markdown("# 🕯️ 그날 밤, 별장에서")
    st.markdown(":gray[미스터리 텍스트 어드벤처]")
    st.write("")

    left, right = st.columns([3, 2], gap="large")

    result = st.session_state.get("case_results", {}).get(CASE_ID)

    with left:
        st.markdown("###### 사건 파일")
        with st.container(border=True):
            st.markdown("**제1화 · 그날 밤, 별장에서**")
            st.caption(
                f"폭풍으로 갇힌 외딴 섬의 별장. "
                f"용의자 {len(gs.SUSPECTS)}명, 장소 {len(gs.LOCATIONS)}곳, {gs.MAX_TURNS}턴."
            )
            if result:
                if result["solved"]:
                    st.badge(
                        f"사건 해결 · {result['score']}점",
                        icon=":material/verified:",
                        color="green",
                    )
                else:
                    st.badge(
                        f"{result['title']} · {result['score']}점",
                        icon=":material/history:",
                        color="orange",
                    )
            label = "다시 수사한다" if result else "사건을 맡는다"
            if st.button(
                label,
                type="primary",
                icon=":material/play_arrow:",
                width="stretch",
            ):
                st.session_state.phase = "briefing"
                st.rerun()

        with st.container(border=True):
            st.markdown(":gray[**제2화 · 준비 중**]")
            st.caption("다음 사건은 아직 열리지 않았습니다.")

    with right:
        reputation = st.session_state.get("reputation", 0)
        st.markdown("###### 탐정")
        with st.container(border=True):
            st.metric("등급", gs.rank_for(reputation), border=False)
            st.markdown(
                f":gray[해결한 사건] {st.session_state.get('solved', 0)}건  \n"
                f":gray[평판] {reputation}"
            )
            next_rank = next(
                ((need, name) for need, name in gs.RANKS if need > reputation), None
            )
            if next_rank:
                st.caption(
                    f"{next_rank[1]}까지 {next_rank[0] - reputation}점 "
                    ":gray[· 증거로 입증할수록 많이 오릅니다]"
                )
            else:
                st.caption("최고 등급입니다.")

        st.markdown("###### 설정")
        with st.container(border=True):
            sound_toggle(
                "home",
                help_text="빗소리 배경음을 켜고 끕니다. 게임 중에도 바뀝니다. "
                "(행동 효과음은 항상 재생됩니다.)",
            )


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

    # 단서를 찾은 직후라면 수사 기록을 단서 면으로 옮긴다.
    # 위젯이 그려지기 전인 지금이 대입할 수 있는 유일한 시점이다.
    if st.session_state.pop("jump_to_clues", False):
        st.session_state.panel_view = "단서"

    # 화면이 넓으면 좌우 2단, 좁아지면 Streamlit이 알아서 위아래로 쌓는다.
    # 수사 기록을 스크롤 없이 옆에서 보게 하려는 배치다.
    stage, panel = st.columns([3, 2], gap="medium")

    with panel:
        # 오른편 위: 설정과 남은 시간. 그 아래 평면도, 그리고 수사 기록.
        sound_toggle("play")
        remaining = state["max_turns"] - state["turn"]
        with st.container(key="turn-low" if remaining <= 3 else "turn-normal"):
            st.metric(
                "남은 턴",
                f"{remaining}",
                delta=f"{state['turn']} / {state['max_turns']} 사용",
                delta_color="off",
                border=True,
            )
        render_map(state["location"])
        st.divider()
        render_case_file()

        st.divider()
        if st.button(
            "처음부터 다시",
            icon=":material/restart_alt:",
            type="tertiary",
            width="stretch",
        ):
            start_new_game()
            st.rerun()

    with stage:
        # 제목을 없앤 자리를 장소가 채운다. 지금 어디에 있는지가 화면의 머리글이다.
        with st.container(key="location"):
            st.markdown(f"# {state['location']}")
        render_location_art(state["location"])

        # 효과음이 붙을 자리. 클릭 시점에 여기에 바로 그려서 즉시 재생한다.
        sound_slot = st.empty()
        # 단서 발견음은 스트리밍이 끝난 회차에서 울린다(조사음과 겹치지 않게).
        render_clue_chime()

        # 이 자리에 다음 턴 나레이션이 스트리밍으로 덮어쓰인다.
        with st.container(key="narration"):
            narration_slot = st.empty()
            narration_slot.markdown(gm["narration"])

        # 단서 카드는 별도 슬롯에 둔다. 다음 행동을 고르는 순간 비워야
        # 스트리밍되는 새 나레이션 옆에 지난 턴 단서가 남아 있지 않다.
        clue_slot = st.empty()
        if st.session_state.found:
            found = st.session_state.found
            inject_clue_background(found["location"])
            with clue_slot.container(border=True, key="clue-card"):
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
            # 선택지 전체를 한 슬롯에 담아 두고, 클릭 순간 그 슬롯을 비운다.
            # 그러지 않으면 새 나레이션이 흘러나오는 동안 지난 턴 버튼이 남아
            # 두 번 눌릴 수 있다.
            choice_slot = st.empty()
            with choice_slot.container():
                st.caption("이동은 턴을 소모하지 않습니다. 조사와 심문만 1턴.")
                for index, choice in enumerate(gm["choices"]):
                    if st.button(
                        choice["label"],
                        icon=ACTION_ICONS.get(choice["action"]),
                        key=f"choice-{state['turn']}-{index}-{choice['action']}-{choice['target']}",
                        width="stretch",
                    ):
                        # 순서가 중요하다: 소리를 먼저 붙여 즉시 재생시키고,
                        # 지난 턴 흔적(단서·선택지)을 걷어낸 뒤 스트리밍을 시작한다.
                        # 소리 선택은 state가 바뀌기 전에 해야 한다.
                        play_sound(
                            sound_slot, ACTION_SOUNDS.get(choice["action"], "")
                        )
                        clue_slot.empty()
                        choice_slot.empty()
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
            record_case_result(st.session_state.ending)
            st.session_state.phase = "ending"
            st.rerun()

    if st.button("지목을 포기한다", key="accuse-none", type="tertiary", width="stretch"):
        st.session_state.ending = gs.accuse(st.session_state.state, None)
        record_case_result(st.session_state.ending)
        st.session_state.phase = "ending"
        st.rerun()


def render_ending() -> None:
    ending = st.session_state.ending
    st.markdown(f"# {ending['title']}")
    render_ending_art(ending.get("art"))
    with st.container(key="narration"):
        st.markdown(ending["text"])

    score = ending.get("score")
    if score:
        st.write("")
        st.markdown("###### 평판")
        with st.container(border=True, width="content"):
            earned = "  \n".join(
                f":gray[{label}] {value:+d}" for label, value in score["parts"] if value
            )
            st.markdown(
                (earned + "  \n" if earned else "")
                + f"**이번 사건 {score['total']}점**  \n"
                f":gray[누적 평판] {st.session_state.get('reputation', 0)} "
                f":gray[· {gs.rank_for(st.session_state.get('reputation', 0))}]"
            )

    # 동기는 정답 엔딩에서만 온다. 절마다 그날 밤의 장면을 옆에 붙여서
    # 긴 글이 밋밋해지지 않게 한다(사후 상황이 아니라 사건 당시 장면이다).
    if ending.get("story"):
        st.write("")
        st.markdown("## 그날 밤, 무슨 일이 있었나")
        for index, section in enumerate(ending["story"]):
            st.write("")
            st.markdown(f"###### {section['title']}")
            text_col, art_col = st.columns([3, 2], gap="large", vertical_alignment="top")
            with text_col:
                with st.container(key=f"story-text-{index}"):
                    st.markdown(section["body"])
            with art_col:
                svg = load_svg(section["art"]) if section.get("art") else None
                if svg is not None:
                    svg_block(
                        svg,
                        f"villa-{section['art']}",
                        "520 / 200",
                        fit="contain",
                        max_width="420px",
                    )

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
st.session_state.setdefault("sound_enabled", True)
st.session_state.setdefault("panel_view", "용의자")

inject_css()
scroll_to_top_on_phase_change()

phase = st.session_state.phase

# 오디오 요소는 컬럼 밖 고정 위치에 둔다. 위치가 흔들리면 요소가 새로 만들어져
# 보량음이 매 턴 처음부터 다시 재생된다. 플레이어 조작 전에는 브라우저가
# 자동재생을 막으므로 홈 화면에서는 아예 만들지 않는다.
if phase not in ("home", "briefing"):
    render_bgm()

if phase == "home":
    render_home()
elif phase == "briefing":
    render_start()
elif phase == "play":
    render_play()
elif phase == "accuse":
    render_accuse()
else:
    render_ending()
