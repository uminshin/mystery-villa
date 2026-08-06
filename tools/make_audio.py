"""게임 사운드를 합성해 assets/audio/에 WAV로 굽는다.

    python tools/make_audio.py

외부 음원을 쓰지 않으므로 라이선스 문제가 없다. 결과 WAV는 저장소에 커밋되어
있어서 다른 기계에서 다시 돌릴 필요는 없다 — 소리를 손보고 싶을 때만 실행한다.

합성으로 그럴듯하게 나오는 것(빗소리, 메트로놈)과 어색한 것(발소리, 책장 넘기기)이
갈린다. 후자는 '진짜 흉내'를 포기하고 낮은 톤의 추상적인 신호로 만들었다.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

RATE = 22050
OUT_DIR = Path(__file__).resolve().parent.parent / "assets" / "audio"
RNG = np.random.default_rng(20260806)  # 결과를 재현 가능하게 고정

# 소리별 최종 피크. 플레이 테스트 기준으로 심문(메트로놈)이 적당했고,
# 조사는 컸고 이동은 거의 안 들렸다. 그 셋을 심문에 맞춰 다시 잡은 값이다.
# 배경음은 나레이션을 읽는 데 방해되지 않을 만큼 낮춘다.
LEVELS = {
    "rain": 0.40,
    "interrogate": 0.50,
    "search": 0.30,
    "move": 0.62,
    "clue": 0.44,
}


def spectral_noise(
    seconds: float,
    alpha: float,
    cutoff_hz: float,
    highpass_hz: float | None = None,
) -> np.ndarray:
    """1/f^alpha 기울기에 저역 통과(+선택적 고역 통과)를 걸어 성형한 노이즈.

    scipy 없이 필터를 쓰려고 주파수 영역에서 바로 곱한다.
    alpha가 크면 낮은 주파수가 강해져(=브라운 노이즈) 먹먹한 소리가 된다.
    highpass_hz를 주면 그 아래를 깎아 대역 통과가 된다 — 1/f 기울기만으로는
    에너지가 죄다 저역에 쌓여서 원하는 음색이 나오지 않는다.
    """
    count = int(RATE * seconds)
    spectrum = np.fft.rfft(RNG.standard_normal(count))
    freqs = np.fft.rfftfreq(count, 1 / RATE)
    shape = np.ones_like(freqs)
    nonzero = freqs > 0
    shape[nonzero] = 1.0 / np.power(freqs[nonzero], alpha)
    shape[0] = 0.0  # DC 제거
    shape *= 1.0 / (1.0 + np.power(freqs / cutoff_hz, 2.0))
    if highpass_hz:
        ratio = np.zeros_like(freqs)
        ratio[nonzero] = highpass_hz / freqs[nonzero]
        shape *= 1.0 / (1.0 + np.power(ratio, 4.0))
    return np.fft.irfft(spectrum * shape, n=count)


def normalize(signal: np.ndarray, peak: float = 0.85) -> np.ndarray:
    largest = np.max(np.abs(signal))
    if largest == 0:
        return signal
    return signal / largest * peak


def loop_seamlessly(signal: np.ndarray, fade_seconds: float = 0.6) -> np.ndarray:
    """앞뒤를 교차 페이드해서 이어 붙였을 때 이음새가 들리지 않게 한다."""
    fade = int(RATE * fade_seconds)
    if fade * 2 >= len(signal):
        return signal
    ramp = np.linspace(0.0, 1.0, fade)
    head, tail = signal[:fade], signal[-fade:]
    blended = tail * (1.0 - ramp) + head * ramp
    return np.concatenate([blended, signal[fade:-fade]])


def droplets(
    seconds: float, count: int, low: float, high: float, decay: float = 190.0
) -> np.ndarray:
    """물방울 충격을 흩뿌린다.

    수가 적고 크면 하나하나 들려서 ASMR이 되므로, 비에 쓸 때는
    아주 많이·아주 작게 넣어 연속된 질감으로만 기여하게 한다.
    """
    total = np.zeros(int(RATE * seconds))
    length = int(RATE * 0.05)
    envelope = np.exp(-np.arange(length) / RATE * decay)
    positions = RNG.integers(0, len(total) - length, size=count)
    pitches = RNG.uniform(low, high, size=count)
    levels = RNG.uniform(0.25, 1.0, size=count)
    time = np.arange(length) / RATE
    for start, pitch, level in zip(positions, pitches, levels):
        grain = np.sin(2 * np.pi * pitch * time) * envelope * level
        total[start : start + length] += grain
    return total


def rain() -> np.ndarray:
    """비 보량음.

    두 번 헛짚었다. 처음엔 고역 노이즈가 많아 '접촉 불량' 잡음처럼 들렸고,
    고치면서 물방울을 키우니 알갱이가 하나하나 들려 ASMR이 됐다.
    실제 비는 초당 수천 개의 충격이 겹친 '연속된 면'이다. 그래서
    ① 400Hz~5kHz 대역의 연속음을 주역으로 두고
    ② 물방울은 아주 작고 아주 많게 넣어 개별로 들리지 않게 texture로 녹인다.
    """
    seconds = 12.0
    # 대역 분포를 측정해 가며 잡은 값이다. 결과는 중역 71% / 고역 29%,
    # 크레스트 팩터 3.4(=연속음). 럼블 층을 넣으면 1/f 기울기 때문에 저역이
    # 에너지를 다 먹어서 다른 층이 안 들린다 — 그래서 아예 뺐다.
    sheet = spectral_noise(seconds, alpha=0.45, cutoff_hz=6000, highpass_hz=600) * 9.0
    mid = spectral_noise(seconds, alpha=0.8, cutoff_hz=1600, highpass_hz=260) * 2.2
    body = spectral_noise(seconds, alpha=1.1, cutoff_hz=420, highpass_hz=120) * 1.2
    # 개별로 들리면 ASMR이 된다. 수를 늘리고 크기를 확 낮춰 질감으로만 남긴다.
    grains = droplets(seconds, count=5200, low=1600.0, high=6000.0, decay=520.0) * 0.10

    time = np.arange(int(RATE * seconds)) / RATE
    # 비가 몰아치고 잦아드는 아주 느린 진폭 변화
    gust = 1.0 + 0.22 * np.sin(2 * np.pi * 0.06 * time + 0.4)
    gust *= 1.0 + 0.12 * np.sin(2 * np.pi * 0.017 * time)

    # 배 타고 건너온 섬이다. 파도를 아주 낮게 두 번 얹어 위치감을 준다.
    # 비의 중역을 덮지 않을 만큼만(0.35) 섞는다.
    swell = waves(seconds, count=2) * 0.35

    mixed = (sheet + mid + body + grains) * gust + swell
    return loop_seamlessly(normalize(mixed, LEVELS["rain"]))


def door() -> np.ndarray:
    """이동음. 걸쇠가 열리고 문이 닫히는 소리.

    긴 경첩 스윕(0.72초)을 넣었더니 사인파 신음처럼 들려서 걷어냈다.
    합성으로 신뢰할 수 있는 건 '짧은 충격 + 나무 공진'이다.
    그래서 ① 걸쇠 딸깍 ② 문이 닫히며 나는 나무 울림 두 개만 남겼다.
    """
    seconds = 0.85
    count = int(RATE * seconds)
    total = np.zeros(count)

    def wood_hit(length: float, partials, noise_decay: float, level: float):
        """(주파수, 감쇠, 세기) 목록으로 나무 공진 한 방을 만든다."""
        n = int(RATE * length)
        t = np.arange(n) / RATE
        sig = np.zeros(n)
        for freq, decay, weight in partials:
            sig += np.sin(2 * np.pi * freq * t) * np.exp(-t * decay) * weight
        sig += (
            spectral_noise(length, alpha=0.5, cutoff_hz=4000, highpass_hz=250)[:n]
            * np.exp(-t * noise_decay)
            * 1.6
        )
        return sig * level, n

    # 걸쇠: 짧고 밝은 딸깍
    latch, n = wood_hit(
        0.09, [(1180.0, 120.0, 0.5), (640.0, 90.0, 0.35)], 300.0, 0.85
    )
    total[:n] += latch

    # 문이 닫히며 판이 울리는 소리. 나무 공진은 저·중역 몇 개로 충분하다.
    start = int(RATE * 0.30)
    thud, n = wood_hit(
        0.45,
        [(196.0, 26.0, 0.9), (312.0, 34.0, 0.5), (520.0, 52.0, 0.25)],
        90.0,
        1.0,
    )
    total[start : start + n] += thud

    return normalize(total, LEVELS["move"])


def waves(seconds: float, count: int) -> np.ndarray:
    """파도가 절벽을 치는 소리. 비 위에 아주 드물게 얹는다.

    파도는 '느리게 부풀었다 빠지는' 광대역 소음이다. 저역이 두꺼우면
    비의 중역을 덮어버리므로 대역을 200~2000Hz로 좁혀 둔다.
    """
    total = np.zeros(int(RATE * seconds))
    span = int(RATE * 3.2)
    positions = np.linspace(0, len(total) - span, count).astype(int)
    for index, start in enumerate(positions):
        t = np.arange(span) / RATE
        # 부딪히고(빠른 상승) 물러나는(느린 하강) 비대칭 포락선
        rise = np.clip(t / 0.45, 0, 1) ** 1.6
        fall = np.exp(-np.clip(t - 0.45, 0, None) * 0.85)
        envelope = rise * fall
        body = spectral_noise(
            span / RATE, alpha=0.7, cutoff_hz=2000, highpass_hz=200
        )[:span]
        total[start : start + span] += body * envelope * (0.9 if index % 2 else 0.6)
    return total


def tick(duration: float, pitch: float, brightness: float) -> np.ndarray:
    """딸깍 소리 하나. 짧은 노이즈 트랜지언트 + 빠르게 감쇠하는 사인."""
    count = int(RATE * duration)
    time = np.arange(count) / RATE
    envelope = np.exp(-time * 55.0)
    tone = np.sin(2 * np.pi * pitch * time) * envelope
    click = RNG.standard_normal(count) * np.exp(-time * 320.0) * brightness
    return tone * 0.7 + click * 0.5


def metronome() -> np.ndarray:
    """심문용 메트로놈. 1초 간격으로 강/약이 번갈아 울린다."""
    interval = 1.0
    beats = 8
    total = np.zeros(int(RATE * interval * beats))
    for index in range(beats):
        start = int(RATE * interval * index)
        strong = index % 2 == 0
        sound = tick(0.28, 1180.0 if strong else 940.0, 1.0 if strong else 0.7)
        sound *= 1.0 if strong else 0.72
        total[start : start + len(sound)] += sound
    return normalize(total, LEVELS["interrogate"])


def clue_chime() -> np.ndarray:
    """단서 발견음. 작은 종을 한 번 치는 소리.

    종이 종처럼 들리는 이유는 배음이 정수배가 아니기 때문이다(비조화 배음).
    사인파를 2배·3배로 쌓으면 오르간이 되고, 아래 비율로 쌓으면 종이 된다.
    낮은 배음은 길게, 높은 배음은 짧게 감쇠시켜 '뎅' 하고 남는 여운을 만든다.
    """
    seconds = 1.6
    count = int(RATE * seconds)
    time = np.arange(count) / RATE
    fundamental = 588.0

    # (주파수 비율, 세기, 감쇠) — 실제 종의 부분음 비율에 가깝게
    partials = [
        (0.50, 0.30, 1.8),
        (1.00, 1.00, 2.2),
        (2.00, 0.42, 3.4),
        (2.76, 0.30, 4.6),
        (5.40, 0.16, 7.0),
        (8.93, 0.08, 9.5),
    ]
    total = np.zeros(count)
    for ratio, level, decay in partials:
        total += np.sin(2 * np.pi * fundamental * ratio * time) * level * np.exp(
            -time * decay
        )

    # 때리는 순간의 금속성 잡음. 없으면 삼각파 같은 전자음이 된다.
    strike = int(RATE * 0.05)
    st_time = np.arange(strike) / RATE
    total[:strike] += (
        spectral_noise(0.05, alpha=0.3, cutoff_hz=8000, highpass_hz=1500)[:strike]
        * np.exp(-st_time * 150.0)
        * 2.0
    )

    # 아주 짧은 페이드인으로 딸깍하는 클릭을 없앤다
    ramp = int(RATE * 0.002)
    total[:ramp] *= np.linspace(0.0, 1.0, ramp)
    return normalize(total, LEVELS["clue"])


def rustle() -> np.ndarray:
    """조사음. 종이를 넘기며 스치는 소리.

    9kHz까지 열어두면 '치익' 하고 날카롭게 튀어 다른 효과음보다 크게 들린다.
    상단을 4.5kHz로 눌러 둔다.
    """
    seconds = 0.85
    count = int(RATE * seconds)
    time = np.arange(count) / RATE
    base = spectral_noise(seconds, alpha=0.3, cutoff_hz=4200, highpass_hz=900)[:count]
    # 두 번 스치도록 진폭에 봉우리를 만든다
    envelope = np.exp(-((time - 0.12) ** 2) / 0.0035)
    envelope += 0.8 * np.exp(-((time - 0.46) ** 2) / 0.0050)
    return normalize(base * envelope, LEVELS["search"])


def write_wav(name: str, signal: np.ndarray) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    samples = np.clip(signal, -1.0, 1.0)
    pcm = (samples * 32767.0).astype("<i2")
    path = OUT_DIR / name
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(pcm.tobytes())
    seconds = len(samples) / RATE
    print(f"  {name:18} {seconds:5.2f}초  {path.stat().st_size / 1024:7.1f} KB")


def main() -> None:
    print(f"합성 -> {OUT_DIR}")
    write_wav("rain.wav", rain())
    write_wav("interrogate.wav", metronome())
    write_wav("move.wav", door())
    write_wav("search.wav", rustle())
    write_wav("clue.wav", clue_chime())
    print("완료")


if __name__ == "__main__":
    main()
