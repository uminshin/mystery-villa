# 그날 밤, 별장에서

> 폭풍으로 갇힌 외딴 섬의 별장. 15턴 안에 세 용의자 중 진범을 **물증으로**
> 지목하는 LLM 기반 미스터리 텍스트 어드벤처.

**▶ 지금 바로 플레이 → https://mystery-villa.streamlit.app**

매 턴의 나레이션과 선택지는 Claude Sonnet 5가 실시간으로 생성한다. 같은 사건을
다시 플레이해도 문장은 매번 달라지지만, 진범과 단서 배치·판정 규칙은 고정되어
있어 추리가 성립한다.

Streamlit + anthropic SDK 직접 호출 (LangChain 미사용).

## 문서

| 문서 | 내용 |
| --- | --- |
| **[게임 소개 및 설명](docs/01-게임소개.md)** | 게임 방법, 목표·조작·종료 조건, 실행 방법, 화면 구성 |
| **[AI 활용 기술 문서](docs/02-AI활용-기술문서.md)** | 구조, 프롬프트 전문, API 기법, 개발 과정의 문제와 수정, 에셋 출처 |
| **[개발 역할 분담](docs/03-개발-역할-분담.md)** | 영역별 구현 내용 8개 |
| **[보완점 · 알려진 문제](docs/04-보완점.md)** | 사운드·스토리·버그 등 팀이 함께 채우는 개선 과제 |

처음 본다면 [게임 소개](docs/01-게임소개.md)의 "게임 방법"부터 읽으면 된다.

## 구성 요약

| | |
| --- | --- |
| 턴 | 15 |
| 장소 | 7곳 |
| 단서 | 7개 (장소당 1개) |
| 용의자 | 3명, 각각 3층으로 나뉜 진술 |
| 삽화 | 직접 작성한 SVG 15장 |
| 사운드 | numpy로 합성한 5종 |

외부 이미지·음원을 쓰지 않았다. 상세한 출처는
[AI 활용 기술 문서 8절](docs/02-AI활용-기술문서.md#8-외부-에셋-및-오픈소스-출처)에 있다.

## 배포

**[Streamlit Community Cloud](https://mystery-villa.streamlit.app)에 배포되어 있다.**
`main` 브랜치에 push하면 자동으로 재배포된다.

API 키는 **저장소에 없다.** Streamlit Secrets(환경변수 `ANTHROPIC_API_KEY`)로만
주입하며, 코드는 환경변수에서만 키를 읽으므로 수정 없이 그대로 동작한다.
키가 서버 측에 있어 방문자 플레이만큼 API 비용이 발생한다(1회 ≈ 15~20회 호출).

## 로컬 실행

```bash
pip install -r requirements.txt
```

| OS | 키 설정 | 실행 |
| --- | --- | --- |
| Windows | `setx ANTHROPIC_API_KEY sk-ant-...` (새 터미널 필요) | `python -m streamlit run app.py` |
| macOS / Linux | `export ANTHROPIC_API_KEY=sk-ant-...` | `python -m streamlit run app.py` |

> 최초 1회 Streamlit이 `Email:`을 물으면 그냥 Enter로 비워두면 된다(뉴스레터 안내).
>
> `streamlit run app.py`로 바로 실행하면 이 환경에서 `CommandNotFoundException`이 날 수
> 있다. `streamlit.exe`가 PATH에 없어서인데, `python -m streamlit`은 PATH를 타지 않아
> 항상 동작한다.

기계에 묶인 것이 없다 — DB도 로컬 저장 파일도 없고(게임 상태는 `st.session_state`
메모리에만 존재), 폴더 전체가 50KB 미만이다.

## 구조

| 파일 | 역할 |
| --- | --- |
| `game_state.py` | 상태 스키마, 장소·단서 표, 턴/단서/엔딩 규칙 (결정론적) |
| `gm.py` | Claude API 호출. 시스템 프롬프트 + JSON 스키마 구조화 출력 |
| `app.py` | Streamlit UI. session_state로 상태 유지, 나레이션 + 선택지 버튼 |
| `assets/` | 직접 그린 SVG 삽화, numpy로 합성한 WAV 5종 |
| `themes/`, `theme.ps1` | 색 테마 전환(로컬). 기본 테마는 `.streamlit/config.toml`(촛불) |
| `tools/make_audio.py` | 사운드 합성 스크립트(빌드용, 런타임엔 불필요) |

## 설계 메모

- **상태 권한은 Python에 있다.** LLM은 나레이션·선택지·의심도/신뢰도 델타만 제안하고,
  턴 증가·단서 발견·엔딩 분기는 `game_state.py`가 확정한다. 엔딩 조건이 모델 출력에
  흔들리지 않게 하려는 의도.
- **구조화 출력**: `output_config.format`의 `json_schema`로 응답 형태를 강제하고,
  선택지 개수·target 유효성은 `gm._normalize()`에서 한 번 더 검증한다.
- **prompt caching**: 브레이크포인트 2개(고정 시스템 프롬프트 + 매 턴 마지막 user 메시지)로
  턴이 늘어도 입력 비용이 선형으로 늘지 않게 한다. 히스토리에는 평문으로 저장해
  브레이크포인트가 턴마다 쌓이지 않게 한다(최대 4개 제한).
- **안전망**: 이미 조사한 장소의 `조사` 선택지 제거, 잘못된 target 필터, 선택지가
  전부 이동이면(→ 턴이 흐르지 않아 소프트락) 조사·심문으로 보충 — 모두 `_normalize()`에서
  결정론적으로 처리한다.

## 개선 과제

알려진 문제와 다음에 손볼 것은 [docs/04-보완점.md](docs/04-보완점.md)에 정리해 두었다.
팀원이 각자 발견한 것을 이어서 추가한다.
