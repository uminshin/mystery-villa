"""Claude API 직접 호출 레이어 (GM 역할).

- anthropic SDK 직접 사용 (LangChain 없음)
- 시스템 프롬프트에 진실/단서표를 담고 prompt caching 적용
- 구조화 출력(output_config.format)으로 JSON 스키마를 강제
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional

import anthropic

from game_state import (
    CAUSE_OF_DEATH,
    CLUES,
    CULPRIT,
    DISCOVERY,
    LOCATIONS,
    MAX_TURNS,
    SUSPECTS,
    TIME_OF_DEATH,
    TIMELINE,
    VICTIM,
    costs_turn,
    state_for_prompt,
    undiscovered_clue_at,
)

MODEL = "claude-opus-5"
MAX_TOKENS = 8000  # thinking + 응답 텍스트 합산 상한


class GMError(RuntimeError):
    """GM 호출이 사용 가능한 응답을 내지 못한 경우."""


RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "narration": {
            "type": "string",
            "description": "2~4문장의 장면 묘사. 방금 행동의 결과를 포함한다.",
        },
        "choices": {
            "type": "array",
            "description": "플레이어에게 제시할 선택지 3개.",
            "items": {
                "type": "object",
                "properties": {
                    "label": {
                        "type": "string",
                        "description": "버튼에 표시할 한 문장. 25자 이내로 짧게.",
                    },
                    "action": {"type": "string", "enum": ["이동", "심문", "조사"]},
                    "target": {
                        "type": "string",
                        "description": "이동이면 장소 이름, 심문이면 A/B/C, 조사면 빈 문자열.",
                    },
                },
                "required": ["label", "action", "target"],
                "additionalProperties": False,
            },
        },
        "suspicion_delta": {
            "type": "object",
            "description": "이번 턴 의심도 변화. -15~15 사이 정수.",
            "properties": {
                "A": {"type": "integer"},
                "B": {"type": "integer"},
                "C": {"type": "integer"},
            },
            "required": ["A", "B", "C"],
            "additionalProperties": False,
        },
        "trust_delta": {
            "type": "object",
            "description": "이번 턴 NPC 신뢰도 변화. -15~15 사이 정수.",
            "properties": {
                "A": {"type": "integer"},
                "B": {"type": "integer"},
                "C": {"type": "integer"},
            },
            "required": ["A", "B", "C"],
            "additionalProperties": False,
        },
        "change_note": {
            "type": "string",
            "description": (
                "수치가 움직인 이유를 25자 이내로. 무엇을 보고/듣고 그렇게 됐는지. "
                "움직인 값이 없으면 빈 문자열."
            ),
        },
    },
    "required": [
        "narration",
        "choices",
        "suspicion_delta",
        "trust_delta",
        "change_note",
    ],
    "additionalProperties": False,
}


def build_system_prompt() -> str:
    suspect_lines = "\n".join(
        f"- {key}: {value['name']} ({value['gender']}, {value['age']}) / "
        f"동기: {value['motive']} / {value['relation']}"
        for key, value in SUSPECTS.items()
    )
    timeline_lines = "\n".join(f"{when}  {what}" for when, what in TIMELINE)
    clue_lines = "\n".join(
        f"- {clue['location']}: {clue['name']} — {clue['detail']} (가리키는 인물: {clue['points_to']})"
        for clue in CLUES.values()
    )
    return f"""당신은 미스터리 텍스트 어드벤처 "그날 밤, 별장에서"의 게임 마스터(GM)다.

[상황]
폭풍으로 뱃길이 끊긴 외딴 섬의 별장. 어젯밤 별장 주인이 서재에서 살해당했다.
플레이어는 손님이 아니라 **의뢰를 받고 섬에 건너온 탐정**이다. 별장에 머무는
세 사람을 상대로 하룻밤 동안 진범을 찾아야 한다. 플레이어를 손님으로 부르지 마라.
총 {MAX_TURNS}턴이 주어진다.

피해자: {VICTIM}
사인: {CAUSE_OF_DEATH}
사망 추정 시각: {TIME_OF_DEATH}
발견: {DISCOVERY}

[용의자]
{suspect_lines}

[공간 구조 — 평면도와 일치시켜야 한다]
2층: 다락방, 서재
1층: 거실, 침실
지하: 금고실
옥외: 정원, 창고 (창고는 정원 끝에 붙어 있다)

이동 문구는 이 층 관계에 맞춰라. 같은 층끼리는 "올라간다/내려간다"를 쓰지 말고
"건너간다 / 옆방으로 간다"처럼 쓴다. 정원으로 갈 때는 "밖으로 나간다",
정원에서 안으로 올 때는 "안으로 들어간다"로 쓴다.

[단서 배치 — 장소당 1개]
{clue_lines}

[사건 타임라인 — 플레이어에게 이미 공개된 사실]
{timeline_lines}
* 23:20에 비가 그쳤으므로, 정원 흙에 발자국이 남았다면 그 시각 이후에 나간 것이다.

[알리바이 표 — 심문 시 이 진술을 일관되게 유지하라]
같은 인물을 여러 번 심문할 수 있으므로, 인물마다 1차·2차·3차로 내줄 정보를
층으로 나눠 두었다. **같은 층을 두 번 반복하지 마라.** 이미 말한 층은 건너뛰고
다음 층을 내준다. 마지막 층까지 갔으면 새 사실 대신 태도의 변화(초조함, 침묵,
말을 돌림)를 묘사하고 수치는 거의 움직이지 않게 한다.

■ A (배다른 형제, 남성 40대)
  1차: 22:40~23:10 거실에서 피해자와 위스키를 마시며 유산 문제로 다퉜다.
       다툰 사실은 순순히 인정한다. 이후 침실에서 잤다고 한다.
  2차: 23:10 이후를 봐준 사람이 없다는 걸 인정한다. 알리바이가 없다.
       변명하듯 "형제라고 다 유산을 노리는 건 아니다"라고 덧붙인다.
  3차: 유언장이 자신에게 불리하게 고쳐진 것을 이미 알고 있었다고 실토한다.
       그걸 알고도 다투기만 했다는 게 자기 알리바이라고 주장한다.
  신뢰도 70 이상에서 추가: 23:30경 복도에서 조심스러운 발소리를 들었다고 말한다.
    누구인지는 모른다고 하고, 성별·신발 같은 식별 정보는 절대 덧붙이지 않는다.
  잔 두 개의 지문을 닦은 사람은 A다. 다툰 사실이 드러날까 두려웠을 뿐이다.
  이건 압박이 아주 심할 때만, 그것도 마지못해 인정한다.

■ B (전 비즈니스 파트너, 남성 50대)
  1차: 23:00~23:50 금고실에서 채무 서류를 검토했다. 혼자였다고 한다.
  2차: 금고실 출입 기록에 23:05 입장만 있고 퇴장 기록이 없다는 점을 지적당하면
       "그 낡은 기계가 고장 난 걸 왜 내 탓으로 돌리느냐"고 받아친다.
  3차: 23:40경 서재 쪽에서 무언가 깨지는 소리를 들었다고 실토한다.
       금고실에서는 들릴 수 없는 거리다 — 즉 그 시각 금고실에 없었다.
       어디 있었는지는 "복도에 나와 있었다"까지만 말한다.
  신뢰도 70 이상에서 추가: 채무 상환 계약서 서명을 위조했다고 인정한다.
    다만 자기 손으로 쓴 게 아니라 "대신 처리해 준 사람이 있었다"고만 말하고,
    그게 누구인지는 끝까지 말하지 않는다.
  B는 사기꾼이지만 살인범은 아니다. 의심스럽게 보이는 것이 그의 역할이다.

■ C (개인 비서, 여성 30대)
  1차: 23:00에 방에 들어가 잤고, 00:10에 물을 마시러 나왔다가 시신을 발견했다.
       발견 시각만 다른 증언과 맞는다. 23:00~00:10은 확인할 수 없다.
       별장의 살림과 열쇠 관리, 창고 정리까지 자신이 도맡아 왔다는 것은
       (물으면) 순순히 인정한다 — 대수롭지 않은 일처럼 말한다.
  2차: 그날 저녁 피해자와 단둘이 이야기했다는 것은 인정한다.
       무슨 이야기였는지는 "일정 정리"라고만 하고 넘어간다.
  3차: 관계가 있었다는 것을 인정한다. 다만 "끝난 지 오래됐다"고 말한다
       (거짓이다 — 그날 밤 끝났다). 그 이상은 침묵하거나 화제를 돌린다.
  약점 1: 비는 23:20에 그쳤다. 정원 젖은 흙에 발자국이 남으려면 그 이후에
    밖에 나갔어야 한다. "잤다"는 진술과 충돌한다.
  약점 2: 창고 정리를 맡은 사람은 그녀다. 선반의 빈 자리를 설명할 수 있는
    사람도 그녀뿐이다. 추궁받으면 "봄에 치웠다"고 하지만 먼지 자국은 최근 것이다.
  이 두 약점은 플레이어가 해당 단서(정원 / 창고)를 찾은 경우에만 지적할 수 있다.
  C는 어떤 상황에서도 살인을 자백하지 않는다.

[심문 규칙]
- 진술 일관성이 최우선이다. 기존 진술을 뒤집지 말고 다음 층을 덧붙인다.
- 같은 인물을 다시 심문하면 **반드시 새 정보를 하나 준다.** 같은 말을 반복하면
  플레이어가 턴을 버린 셈이 되므로, 최소한 태도의 변화라도 보여줘라.
- 신뢰도 구간별 태도:
  0~30   적대적. 짧게 자르거나 질문을 되받는다. 그 층의 내용만 최소한으로.
  31~69  기본. 해당 층의 내용을 말한다.
  70~100 협조적. 해당 층 + "신뢰도 70 이상에서 추가" 항목까지 흘린다.
- 플레이어가 이미 찾은 단서(현재 상태의 clues_found)를 들이대는 흐름이면,
  해당 인물이 한 층 더 나아가게 하라. 찾지 않은 단서는 언급조차 하지 마라.
- 세 사람 모두 거짓말을 한다. 거짓말한다는 사실만으로 범인을 가릴 수 없어야 한다.

[비공개 진실]
진범은 {CULPRIT}({SUSPECTS[CULPRIT]['name']})다.
이 사실을 플레이어에게 직접 말하지 마라. 확정적으로 암시하지도 마라.
마지막 턴까지 A와 B도 그럴듯한 용의자로 남겨두어야 한다.
"범인은 ~다", "~가 죽였다" 같은 단정적 서술은 금지. 단서는 해석의 여지를 남긴 채 묘사한다.

[진행 규칙]
- 플레이어의 행동 판정(이동 성공, 단서 발견 여부, 턴 수)은 시스템이 이미 처리해서 알려준다.
  당신은 그 결과를 그대로 받아들여 묘사하라. 판정을 뒤집거나 새 단서를 발명하지 마라.
- 턴 비용: 이동은 턴을 소모하지 않는다. 조사와 심문만 1턴을 쓴다.
  플레이어가 장소를 자유롭게 오갈 수 있으니 이동 선택지를 아끼지 말고 제시하되,
  선택지 3개가 전부 이동이 되지는 않게 하라. 조사나 심문이 최소 1개는 들어가야 한다.
- 위 표에 없는 단서를 만들어내지 마라. 분위기용 소품 묘사는 자유롭게 해도 된다.
- 심문은 아래 [심문 규칙]을 따른다. 어떤 경우에도 진범 정보를 직접 누설하지 않는다.
- 남은 턴이 3턴 이하가 되면 나레이션에 시간이 얼마 없다는 압박을 넣어라.

[출력 규칙]
- narration: 2~4문장. 한국어. 현재 장소와 방금 행동의 결과를 담는다.
  단서를 발견했다면 그 단서를 장면 안에서 자연스럽게 보여준다.
- choices: 정확히 3개, 서로 다른 행동이 섞이도록 구성한다.
  - action="이동" → target은 장소 이름 하나(현재 장소 제외)
  - action="심문" → target은 "A", "B", "C" 중 하나
  - action="조사" → target은 빈 문자열("")이며 현재 장소를 조사한다
- 현재 상태의 current_location_searchable이 false면 "조사" 선택지를 제시하지 마라.
- **선택지 문구는 플레이어가 이미 아는 것만으로 써라.** 브리핑에서 공개한 정보
  (피해자, 사망 추정 시각, 타임라인, 세 인물의 성별·나이·관계)와 지금까지 발견한
  단서, 그리고 이전 나레이션에서 실제로 서술한 내용만 쓸 수 있다.
  아직 밝혀지지 않은 사실을 아는 척하는 문구는 금지다.
  - 나쁜 예: "금고실의 위조 계약서를 확인한다" (아직 찾지 못한 단서를 언급)
  - 나쁜 예: "B의 채무 액수를 캐묻는다" (심문으로 나온 적 없는 정보)
  - 좋은 예: "금고실로 내려가 본다", "B에게 어젯밤 행적을 묻는다"
  확신이 서지 않으면 "둘러본다 / 말을 걸어 본다"처럼 일반적인 문구를 써라.
- 피해자는 이름 대신 "피해자" 또는 "별장 주인"으로 부른다.
- suspicion_delta / trust_delta는 -15~15 정수. 근거가 없으면 0을 넣어라.
- change_note: 수치가 왜 움직였는지 25자 이내로 적어라. 플레이어가 나중에
  "이 의심은 무엇 때문이었나"를 되짚을 수 있어야 한다.
  예) "알리바이 시각이 어긋남", "질문을 회피함", "다툰 사실을 인정함"
  움직인 값이 없으면 빈 문자열을 넣어라. 진범을 지목하는 표현은 쓰지 마라.
- 아직 발견되지 않은 단서가 어느 장소에 있는지 당신은 알고 있지만, 나레이션이나
  선택지 문구로 그 위치를 암시하지 마라. "정원 쪽이 신경 쓰인다" 같은 유도는 금지다.
  플레이어가 어디를 뒤질지는 스스로 판단해야 한다. 이동 선택지는 단서 유무와
  무관하게 장면상 자연스러운 곳을 제시하라.
- 지정된 JSON 스키마만 출력한다. 그 외 텍스트는 금지.
"""


_NARRATION_RE = re.compile(r'"narration"\s*:\s*"((?:[^"\\]|\\.)*)')


def _partial_narration(buffer: str) -> str | None:
    """아직 끝나지 않은 JSON에서 narration 문자열만 뽑아낸다.

    스키마 순서상 narration이 첫 필드로 오므로, 도착하는 즉시 화면에 찍을 수 있다.
    이스케이프가 잘린 순간에는 None을 돌려주고 다음 조각을 기다린다.
    """
    match = _NARRATION_RE.search(buffer)
    if not match:
        return None
    try:
        return json.loads(f'"{match.group(1)}"')
    except json.JSONDecodeError:
        return None


def _normalize(data: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """선택지를 3개로 정규화하고 잘못된 target을 걸러낸다."""
    # 현재 장소에 남은 단서가 없으면 '조사' 선택지는 턴만 소모하므로 제거한다
    searchable = undiscovered_clue_at(state, state["location"]) is not None

    valid: list[dict[str, str]] = []
    for raw in data.get("choices", []):
        action = raw.get("action", "")
        target = (raw.get("target") or "").strip()
        label = (raw.get("label") or "").strip()
        if not label:
            continue
        if action == "이동" and target not in LOCATIONS:
            continue
        if action == "심문" and target not in SUSPECTS:
            continue
        if action == "조사":
            if not searchable:
                continue
            target = ""
        valid.append({"label": label, "action": action, "target": target})

    # 모델이 3개를 못 채웠을 때를 위한 결정론적 보충 선택지
    for location in LOCATIONS:
        if len(valid) >= 3:
            break
        if location == state["location"]:
            continue
        if any(c["action"] == "이동" and c["target"] == location for c in valid):
            continue
        valid.append({"label": f"{location}으로 이동한다", "action": "이동", "target": location})

    valid = valid[:3]

    # 이동은 턴을 소모하지 않으므로, 전부 이동이면 턴이 흐르지 않아 지목 화면에
    # 영원히 도달하지 못한다. 최소 1개는 턴을 쓰는 행동으로 강제한다.
    if valid and not any(costs_turn(c["action"]) for c in valid):
        if searchable:
            valid[-1] = {"label": "이곳을 조사한다", "action": "조사", "target": ""}
        else:
            # 특정 인물로 쏠리지 않게 턴 번호로 순환 선택한다
            keys = list(SUSPECTS)
            key = keys[state["turn"] % len(keys)]
            valid[-1] = {
                "label": f"{SUSPECTS[key]['name']}를 추궁한다",
                "action": "심문",
                "target": key,
            }

    data["choices"] = valid
    return data


def call_gm(
    client: anthropic.Anthropic,
    history: list[dict[str, Any]],
    state: dict[str, Any],
    player_input: str,
    on_narration: Optional[Callable[[str], None]] = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """GM 응답 1회. (파싱된 응답, 갱신된 대화 히스토리)를 돌려준다.

    on_narration이 주어지면 나레이션이 도착하는 대로 조각을 넘겨준다.
    응답 전체를 기다리는 대신 글자가 찍히기 시작하므로 체감 대기가 크게 줄고,
    서버가 혼잡해 느릴 때도 멈춘 것처럼 보이지 않는다.
    """
    user_text = (
        f"{player_input}\n\n"
        f"[현재 상태]\n{json.dumps(state_for_prompt(state), ensure_ascii=False)}"
    )
    # 요청에는 마지막 user 메시지에 캐시 브레이크포인트를 붙여 누적 히스토리를 캐싱한다.
    # 히스토리에는 평문으로 저장해 브레이크포인트가 턴마다 쌓이지 않게 한다(최대 4개 제한).
    plain_user = {"role": "user", "content": user_text}
    request_messages = history + [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": user_text,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        }
    ]

    try:
        with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=[
                {
                    "type": "text",
                    "text": build_system_prompt(),
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=request_messages,
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA},
            },
        ) as stream:
            buffer = ""
            shown = ""
            for chunk in stream.text_stream:
                buffer += chunk
                if on_narration is None:
                    continue
                partial = _partial_narration(buffer)
                if partial and partial != shown:
                    shown = partial
                    on_narration(partial)
            response = stream.get_final_message()
    except anthropic.APIStatusError as exc:
        # 529/429는 서버 혼잡이라 우리 쪽 문제가 아니다. SDK가 이미 여러 번
        # 재시도한 뒤이므로, 사용자에게 원인과 대처를 분명히 알린다.
        if exc.status_code in (429, 529):
            raise GMError(
                f"Claude API가 일시적으로 혼잡합니다 ({exc.status_code}). "
                "자동 재시도까지 실패했으니 잠시 뒤 '다시 시도'를 눌러 주세요."
            ) from exc
        raise GMError(f"API 오류 ({exc.status_code}): {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise GMError("네트워크 오류. 잠시 후 다시 시도하세요.") from exc

    if response.stop_reason == "refusal":
        raise GMError("모델이 이 요청을 거절했습니다. 다른 행동을 선택해 주세요.")

    text = next((block.text for block in response.content if block.type == "text"), "")
    if not text:
        raise GMError("빈 응답을 받았습니다. 다시 시도해 주세요.")
    if response.stop_reason == "max_tokens":
        raise GMError("응답이 잘렸습니다. 다시 시도해 주세요.")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GMError("JSON 파싱 실패. 다시 시도해 주세요.") from exc

    new_history = history + [plain_user, {"role": "assistant", "content": text}]
    return _normalize(data, state), new_history
