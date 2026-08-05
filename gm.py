"""Claude API 직접 호출 레이어 (GM 역할).

- anthropic SDK 직접 사용 (LangChain 없음)
- 시스템 프롬프트에 진실/단서표를 담고 prompt caching 적용
- 구조화 출력(output_config.format)으로 JSON 스키마를 강제
"""

from __future__ import annotations

import json
from typing import Any

import anthropic

from game_state import (
    CLUES,
    CULPRIT,
    LOCATIONS,
    MAX_TURNS,
    SUSPECTS,
    costs_turn,
    state_for_prompt,
    undiscovered_clue_at,
)

MODEL = "claude-opus-5"
MAX_TOKENS = 8000  # thinking + 응답 텍스트 합산 상한
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


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
    },
    "required": ["narration", "choices", "suspicion_delta", "trust_delta"],
    "additionalProperties": False,
}


def build_system_prompt() -> str:
    suspect_lines = "\n".join(
        f"- {key}: {value['name']} / 동기: {value['motive']}"
        for key, value in SUSPECTS.items()
    )
    clue_lines = "\n".join(
        f"- {clue['location']}: {clue['name']} — {clue['detail']} (가리키는 인물: {clue['points_to']})"
        for clue in CLUES.values()
    )
    return f"""당신은 미스터리 텍스트 어드벤처 "그날 밤, 별장에서"의 게임 마스터(GM)다.

[상황]
폭풍으로 뱃길이 끊긴 외딴 별장. 어젯밤 별장 주인이 서재에서 살해당했다.
플레이어는 손님 중 한 명이며, 탐정 역할로 하룻밤 동안 진범을 찾아야 한다.
총 {MAX_TURNS}턴이 주어지고, 마지막 턴이 끝나면 반드시 한 명을 지목해야 한다.

[용의자]
{suspect_lines}

[장소]
{", ".join(LOCATIONS)}

[단서 배치 — 장소당 1개]
{clue_lines}

[사건 타임라인 — 심문으로 알 수 있는 공용 사실]
22:30  저녁 식사가 끝나고 손님들이 흩어졌다.
23:20  폭풍우가 잠시 그쳤다. (이 시각 이후에야 정원 흙에 발자국이 남을 수 있다)
23:40  사망 추정 시각. 서재에서 둔기에 맞았다.
00:10  비서가 시신을 발견하고 사람들을 불러모았다.

[알리바이 표 — 심문 시 이 진술을 일관되게 유지하라]
■ A (배다른 형제)
  주장: 22:40~23:10 거실에서 피해자와 위스키를 마시며 유산 문제로 다퉜다. 이후 침실에서 잤다.
  검증: 다툰 사실은 본인이 순순히 인정한다. 23:10 이후를 봐준 사람은 없다 — 알리바이 없음.
  숨기는 것: 유언장이 자신에게 불리하게 고쳐진 걸 이미 알고 있었다.
  신뢰도 70 이상: 23:30경 복도에서 여자 구두 소리를 들었다고 말한다. 누구인지는 단정하지 않는다.

■ B (전 비즈니스 파트너)
  주장: 23:00~23:50 금고실에서 채무 서류를 검토했다. 혼자였다.
  검증: 금고실 출입 기록에 23:05 입장만 있고 퇴장 기록이 없다. 본인은 기계 고장이라고 주장한다.
  숨기는 것: 채무 상환 계약서 서명을 위조했다. 사기는 사실이지만 살인은 하지 않았다.
  신뢰도 70 이상: 23:40경 서재 쪽에서 무언가 깨지는 소리를 들었다고 실토한다.
    금고실에서는 들릴 수 없는 거리다 — 즉 알리바이는 거짓이다. 그러나 B는 범인이 아니다.

■ C (개인 비서)
  주장: 23:00에 방에 들어가 잤고, 00:10에 물을 마시러 나왔다가 시신을 발견했다.
  검증: 발견 시각만 다른 증언과 맞는다. 23:00~00:10은 확인할 수 없다.
  숨기는 것: 피해자와 오래된 연인 관계였고, 관계를 끝내려 한 쪽은 피해자였다.
  약점: 비는 23:20에 그쳤다. 정원 젖은 흙에 구두 발자국이 남으려면 그 이후에 밖에 나갔어야 한다.
    "잤다"는 진술과 충돌한다. 단, 플레이어가 정원 단서를 찾은 경우에만 이 모순을 지적할 수 있다.
  신뢰도 70 이상: 관계가 있었다는 것만 인정한다. 그날 밤 밖에 나간 사실은 끝까지 부인한다.

[심문 규칙]
- 진술 일관성이 최우선이다. 같은 인물을 다시 심문하면 기존 진술을 뒤집지 말고 세부만 덧붙인다.
- 신뢰도 구간별 태도:
  0~30   적대적. 짧게 자르거나 질문을 되받는다. 알리바이의 뼈대만 반복한다.
  31~69  기본. 알리바이 표의 "주장"을 말한다. "숨기는 것"은 말하지 않는다.
  70~100 협조적. "신뢰도 70 이상" 항목까지 흘린다.
- 플레이어가 이미 찾은 단서(현재 상태의 clues_found)를 들이대는 흐름이면,
  해당 인물이 한 단계 더 실토하게 하라. 찾지 않은 단서는 언급조차 하지 마라.
- A와 B는 압박이 심하면 자신의 비밀(유언장 인지 / 서명 위조)까지 인정할 수 있다.
  둘 다 거짓말을 하고 있지만 살인범은 아니다 — 의심스럽게 보이는 것이 이들의 역할이다.
- C는 어떤 상황에서도 살인을 자백하지 않는다. 관계까지만 인정하고 그 이상은 침묵하거나 화제를 돌린다.

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
- suspicion_delta / trust_delta는 -15~15 정수. 근거가 없으면 0을 넣어라.
- 아직 발견되지 않은 단서가 어느 장소에 있는지 당신은 알고 있지만, 나레이션이나
  선택지 문구로 그 위치를 암시하지 마라. "정원 쪽이 신경 쓰인다" 같은 유도는 금지다.
  플레이어가 어디를 뒤질지는 스스로 판단해야 한다. 이동 선택지는 단서 유무와
  무관하게 장면상 자연스러운 곳을 제시하라.
- 지정된 JSON 스키마만 출력한다. 그 외 텍스트는 금지.
"""


_fallback_supported = True


def _create_message(client: anthropic.Anthropic, **kwargs: Any):
    """서버측 refusal fallback을 우선 시도하고, 미지원 환경이면 일반 호출로 내려간다.

    한 번 미지원으로 판정되면 이후 턴에서는 곧바로 일반 호출을 쓴다.
    인증 실패 같은 다른 TypeError는 삼키지 않고 그대로 올린다.
    """
    global _fallback_supported

    if _fallback_supported:
        try:
            return client.beta.messages.create(
                betas=[_FALLBACK_BETA], fallbacks="default", **kwargs
            )
        except TypeError as exc:
            if "fallbacks" not in str(exc) and "betas" not in str(exc):
                raise  # SDK 파라미터 문제가 아니라면 그대로 전달
            _fallback_supported = False
        except (anthropic.BadRequestError, anthropic.NotFoundError):
            _fallback_supported = False  # beta 미허용 조직/버전

    return client.messages.create(**kwargs)


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
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """GM 응답 1회. (파싱된 응답, 갱신된 대화 히스토리)를 돌려준다."""
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
        response = _create_message(
            client,
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
        )
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
