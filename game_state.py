"""게임 상태 스키마와 결정론적 규칙.

턴 증가 / 단서 발견 / 엔딩 분기는 전부 여기서 처리한다.
LLM은 나레이션과 선택지, 의심도·신뢰도 델타만 제안하고
실제 상태 변경 권한은 이 모듈이 갖는다(엔딩 분기를 확정적으로 만들기 위함).
"""

from __future__ import annotations

import copy
from typing import Any, Optional

MAX_TURNS = 10

LOCATIONS = ["거실", "서재", "침실", "정원", "금고실", "다락방"]

SUSPECTS = {
    # short는 표처럼 좁은 자리에서 쓴다.
    # relation/gender는 시작 화면의 인물 소개에 그대로 노출한다 — 플레이어가
    # 모르는 정보를 전제로 추리를 요구하면 안 된다.
    "A": {
        "name": "A · 배다른 형제",
        "short": "A 형제",
        "motive": "유산 상속",
        "gender": "남성",
        "age": "40대",
        "relation": "피해자와 아버지가 같다. 유산 배분을 두고 오래 다퉜다.",
    },
    "B": {
        "name": "B · 전 비즈니스 파트너",
        "short": "B 파트너",
        "motive": "금전 다툼",
        "gender": "남성",
        "age": "50대",
        "relation": "피해자와 공동으로 사업을 했다. 청산 과정에서 채무가 얽혔다.",
    },
    "C": {
        "name": "C · 개인 비서",
        "short": "C 비서",
        "motive": "숨겨진 관계",
        "gender": "여성",
        "age": "30대",
        "relation": "3년간 피해자의 일정과 서류를 관리했다. 별장에 가장 익숙하다.",
    },
}

# 사건 개요. 시작 화면과 시스템 프롬프트가 같은 값을 쓴다.
# 이름을 쓰지 않는다 — 플레이어에게는 '피해자'라는 역할이 먼저 읽혀야 한다.
VICTIM = "별장 주인 · 60대 남성"
TIME_OF_DEATH = "23:40 전후"
CAUSE_OF_DEATH = "서재에서 둔기에 의한 후두부 손상"
DISCOVERY = "00:10, 비서 C가 시신을 발견하고 사람들을 불러모았다"

# 사건 당일 밤의 공용 타임라인. 심문으로 확인할 수 있는 사실만 담는다.
TIMELINE = [
    ("22:30", "저녁 식사가 끝나고 손님들이 흩어졌다"),
    ("23:20", "폭풍우가 잠시 그쳤다"),
    (TIME_OF_DEATH, "사망 추정 시각"),
    ("00:10", "시신이 발견되었다"),
]

# 진범 (플레이어에게 공개하지 않음)
CULPRIT = "C"

# 장소당 단서 1개. points_to = 이 단서가 가리키는 용의자.
CLUES: dict[str, dict[str, Any]] = {
    "c1": {
        "id": "c1",
        "location": "거실",
        "name": "엎어진 위스키 잔 두 개",
        "detail": "잔 하나에만 지문이 문질러 지워져 있다. 사망 직전 누군가와 마주 앉아 있었다.",
        "points_to": "A",
    },
    "c2": {
        "id": "c2",
        "location": "서재",
        "name": "찢겨진 유언장 초안",
        "detail": "상속 지분을 재조정한 흔적. 찢긴 조각 하나에 'A에게는 한 푼도'라는 문구가 남아 있다.",
        "points_to": "A",
    },
    "c3": {
        "id": "c3",
        "location": "침실",
        "name": "베개 밑의 편지",
        "detail": "오래 접힌 자국이 있는 연애편지. 끝에 적힌 서명은 알파벳 'C' 한 글자다.",
        "points_to": "C",
    },
    "c4": {
        "id": "c4",
        "location": "정원",
        "name": "젖은 흙에 남은 발자국",
        "detail": (
            "비가 그친 뒤에 찍혔다. 별장 안쪽에서 창고 쪽으로 갔다가 되돌아온 방향이다. "
            "이 시각 이후에 밖에 나간 사람이 있다는 뜻이다."
        ),
        "points_to": "C",
    },
    "c5": {
        "id": "c5",
        "location": "금고실",
        "name": "위조된 채무 상환 계약서",
        "detail": "서명 필체가 두 군데에서 어긋난다. 채무자 이름은 B로 적혀 있다.",
        "points_to": "B",
    },
    "c6": {
        "id": "c6",
        "location": "다락방",
        "name": "반쯤 태운 사진과 빈 수면제 병",
        "detail": "사진에는 피해자와 비서가 나란히 서 있다. 비서 쪽 얼굴만 불에 그슬렸다.",
        "points_to": "C",
    },
}

CLUE_BY_LOCATION = {clue["location"]: clue for clue in CLUES.values()}

# 장소별 삽화 파일명(assets/locations/<slug>.svg). 한글 장소명을 파일명으로
# 쓰지 않기 위해 매핑을 둔다.
LOCATION_ART = {
    "거실": "living-room",
    "서재": "study",
    "침실": "bedroom",
    "정원": "garden",
    "금고실": "vault",
    "다락방": "attic",
}

# 층 구성. 평면도(assets/locations/map.svg)와 반드시 일치해야 한다.
# 같은 층 사이를 "올라간다/내려간다"로 표현하면 공간 감각이 깨지므로
# 나레이션이 이 표를 참고한다.
FLOORS = {
    "다락방": "2층",
    "서재": "2층",
    "거실": "1층",
    "침실": "1층",
    "금고실": "지하",
    "정원": "옥외",
}

# 층 사이의 상하 관계. 이동 방향을 문장으로 고를 때 쓴다.
FLOOR_ORDER = {"2층": 2, "1층": 1, "지하": 0, "옥외": 1}


def move_direction(origin: str, destination: str) -> str:
    """두 장소 사이 이동을 어떤 동작으로 불러야 하는지 돌려준다."""
    here, there = FLOORS.get(origin), FLOORS.get(destination)
    if here is None or there is None:
        return "이동"
    if there == "옥외" and here != "옥외":
        return "밖으로 나간다"
    if here == "옥외" and there != "옥외":
        return "안으로 들어간다"
    high, low = FLOOR_ORDER.get(there, 1), FLOOR_ORDER.get(here, 1)
    if high > low:
        return "올라간다"
    if high < low:
        return "내려간다"
    return "같은 층에서 옮긴다"


def new_state() -> dict[str, Any]:
    return {
        "turn": 0,
        "max_turns": MAX_TURNS,
        "location": "거실",
        "clues_found": [],
        "suspicion": {"A": 0, "B": 0, "C": 0},
        "npc_trust": {"A": 50, "B": 50, "C": 50},
        "game_over": False,
        "ending": None,
    }


def clue_at(location: str) -> Optional[dict[str, Any]]:
    return CLUE_BY_LOCATION.get(location)


def undiscovered_clue_at(state: dict[str, Any], location: str) -> Optional[dict[str, Any]]:
    clue = clue_at(location)
    if clue and clue["id"] not in state["clues_found"]:
        return clue
    return None


def resolve_action(state: dict[str, Any], choice: dict[str, str]) -> Optional[dict[str, Any]]:
    """플레이어 행동을 상태에 반영하고, 이번 턴에 새로 발견한 단서를 돌려준다."""
    action = choice.get("action", "")
    target = choice.get("target", "")

    if action == "이동" and target in LOCATIONS:
        state["location"] = target
        return None

    if action == "조사":
        # target이 비어 있으면 현재 장소를 조사
        location = target if target in LOCATIONS else state["location"]
        state["location"] = location
        found = undiscovered_clue_at(state, location)
        if found:
            state["clues_found"].append(found["id"])
        return found

    # 심문: 나레이션과 델타로만 결과가 드러난다
    return None


# 턴을 소모하지 않는 행동. 이동만 자유이고, 조사·심문은 1턴을 쓴다.
# (10턴 안에 이동까지 턴을 먹으면 심문할 여유가 구조적으로 사라져서 이렇게 바꿨다.)
FREE_ACTIONS = frozenset({"이동"})


def costs_turn(action: str) -> bool:
    return action not in FREE_ACTIONS


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


# 한 턴에 움직일 수 있는 의심도·신뢰도의 최대 폭.
# 프롬프트로도 -15~15를 요구하지만 JSON 스키마는 정수 범위를 표현할 수 없어서
# 코드에서 막는다. 이게 없으면 모델이 +500을 보내면 한 턴에 0->100으로 튄다.
DELTA_LIMIT = 15


def _as_delta(value: Any) -> int:
    """모델이 보낸 델타를 안전한 정수로 만든다. 이상한 값은 0으로 떨군다."""
    try:
        return _clamp(int(value), -DELTA_LIMIT, DELTA_LIMIT)
    except (TypeError, ValueError):
        return 0


def apply_deltas(
    state: dict[str, Any],
    suspicion_delta: dict[str, int],
    trust_delta: dict[str, int],
) -> None:
    for key in ("A", "B", "C"):
        state["suspicion"][key] = _clamp(
            state["suspicion"][key] + _as_delta(suspicion_delta.get(key, 0)), 0, 100
        )
        state["npc_trust"][key] = _clamp(
            state["npc_trust"][key] + _as_delta(trust_delta.get(key, 0)), 0, 100
        )


def advance_turn(state: dict[str, Any]) -> None:
    state["turn"] += 1


def must_accuse(state: dict[str, Any]) -> bool:
    return state["turn"] >= state["max_turns"]


def culprit_clues(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        CLUES[cid] for cid in state["clues_found"] if CLUES[cid]["points_to"] == CULPRIT
    ]


def clues_pointing_to(state: dict[str, Any], suspect: str) -> list[dict[str, Any]]:
    """플레이어가 찾은 단서 중 해당 인물을 가리키는 것들."""
    return [
        CLUES[cid] for cid in state["clues_found"] if CLUES[cid]["points_to"] == suspect
    ]


# 물증 없이 심증만 쌓였다고 볼 의심도 기준선.
EVIDENCE_WARNING_THRESHOLD = 40

# 정답 엔딩에 필요한 결정적 단서 수. 경고 문구와 판정이 같은 값을 쓴다.
REQUIRED_CLUES = 2


def weak_evidence_warnings(state: dict[str, Any]) -> list[tuple[str, int]]:
    """의심도는 높은데 뒷받침하는 단서가 부족한 인물 목록.

    진범이 누구인지는 보지 않는다. A/B/C에 같은 규칙을 적용하므로
    이 경고로 진범을 역산할 수 없다 — 누설 없이 "단서를 더 모으라"만 전달한다.
    """
    result = []
    for key in SUSPECTS:
        if state["suspicion"][key] < EVIDENCE_WARNING_THRESHOLD:
            continue
        count = len(clues_pointing_to(state, key))
        if count < REQUIRED_CLUES:
            result.append((key, count))
    return result


ENDINGS = {
    "TRUE": "범인 검거",
    "INSUFFICIENT": "증거 불충분 · 석방",
    "WRONG": "오심",
    "COLD_CASE": "미제 사건",
}

# 엔딩별 삽화(assets/locations/<slug>.svg).
# 실패 계열 셋은 결과가 같으므로(사건이 닫히지 않는다) 한 장을 공유한다.
ENDING_ART = {
    "TRUE": "ending-arrest",
    "INSUFFICIENT": "ending-unsolved",
    "WRONG": "ending-unsolved",
    "COLD_CASE": "ending-unsolved",
}

# 진범의 동기. 정답 엔딩에서만 공개한다.
CULPRIT_STORY = (
    "3년이었다. 비서는 피해자의 일정과 서류를, 그리고 그 관계를 함께 관리했다.\n\n"
    "그날 저녁 피해자는 관계를 끝내겠다고 통보했다. 정리 조건은 침묵이었다. "
    "서재 금고에는 이미 그녀를 배제한 새 서류가 들어가 있었다.\n\n"
    "23:20, 비가 그쳤다. 그녀는 정원 창고에서 문진을 가져와 서재로 올라갔고, "
    "돌아오는 길에 젖은 흙에 발자국을 남겼다. 다락방에서 사진을 태웠지만 "
    "불은 얼굴 하나만 그슬리고 꺼졌다.\n\n"
    "00:10, 그녀는 시신을 '발견했다'. 가장 먼저 달려와 가장 오래 울었다."
)


def accuse(state: dict[str, Any], target: Optional[str]) -> dict[str, Any]:
    """지목 결과를 확정한다. target=None이면 지목 포기."""
    found = culprit_clues(state)
    names = ", ".join(clue["name"] for clue in found) or "없음"

    if target is None:
        ending = "COLD_CASE"
        text = (
            "당신은 끝내 아무도 지목하지 않았다. 아침 첫 배가 들어오고, 세 사람은 각자의 "
            "방향으로 흩어졌다. 별장은 다시 잠겼고 사건은 서류 더미 속으로 들어갔다.\n\n"
            f"수집한 결정적 단서: {names}"
        )
    elif target == CULPRIT and len(found) >= 2:
        ending = "TRUE"
        text = (
            f"당신은 {SUSPECTS[CULPRIT]['name']}를 지목했다. {names} — 흩어져 있던 조각들이 "
            "하나의 얼굴로 맞물리는 순간, 그가 더 이상 변명하지 않았다. "
            "숨기려 했던 것은 살인이 아니라 그 관계였고, 결국 둘 다 드러났다.\n\n"
            "당신은 진실에 닿았다."
        )
    elif target == CULPRIT:
        ending = "INSUFFICIENT"
        text = (
            f"당신은 {SUSPECTS[CULPRIT]['name']}를 지목했다. 그는 표정 하나 바꾸지 않고 "
            "당신을 마주 본다. 직감은 맞았지만, 그것을 증명할 물증이 손에 없었다.\n\n"
            f"수집한 결정적 단서: {names} — 최소 두 개가 필요했다.\n"
            "사건은 미제로 남았다."
        )
    else:
        ending = "WRONG"
        text = (
            f"당신은 {SUSPECTS[target]['name']}를 지목했다. 동기는 분명했고 정황도 그럴듯했다. "
            "그러나 조사가 끝난 뒤, 정말로 밤을 견뎌낸 한 사람은 아무 말 없이 별장을 떠났다.\n\n"
            f"수집한 결정적 단서: {names}\n"
            "당신은 엉뚱한 문을 두드렸다."
        )

    # 정답에 닿았을 때만 동기를 공개한다. 나머지 엔딩에서는 왜 그랬는지
    # 끝까지 알 수 없어야 다시 플레이할 이유가 생긴다.
    story = CULPRIT_STORY if ending == "TRUE" else None

    state["game_over"] = True
    state["ending"] = ending
    return {
        "ending": ending,
        "title": ENDINGS[ending],
        "text": text,
        "story": story,
        "art": ENDING_ART.get(ending),
    }


def state_for_prompt(state: dict[str, Any]) -> dict[str, Any]:
    """LLM에게 전달할 상태 스냅샷(진범 정보는 시스템 프롬프트에만 있다).

    미발견 단서가 '어느 장소에' 남았는지는 의도적으로 넣지 않는다. 전달하면
    나레이션에서 흘릴 수 있어 추리 난이도를 직접 깎는다. 선택지 생성에 실제로
    필요한 것은 '지금 있는 곳을 조사할 가치가 있는지' 하나뿐이다.
    """
    snapshot = copy.deepcopy(state)
    snapshot["clues_found"] = [
        {"id": cid, "name": CLUES[cid]["name"], "location": CLUES[cid]["location"]}
        for cid in state["clues_found"]
    ]
    snapshot["current_location_searchable"] = (
        undiscovered_clue_at(state, state["location"]) is not None
    )
    return snapshot
