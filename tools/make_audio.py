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


def spectral_noise(seconds: float, alpha: float, cutoff_hz: float) -> np.ndarray:
    """1/f^alpha 기울기 + 저역 통과를 걸어 성형한 노이즈.

    scipy 없이 필터를 쓰려고 주파수 영역에서 바로 곱한다.
    alpha가 크면 낮은 주파수가 강해져(=브라운 노이즈) 먹먹한 소리가 된다.
    """
    count = int(RATE * seconds)
    spectrum = np.fft.rfft(RNG.standard_normal(count))
    freqs = np.fft.rfftfreq(count, 1 / RATE)
    shape = np.ones_like(freqs)
    nonzero = freqs > 0
    shape[nonzero] = 1.0 / np.power(freqs[nonzero], alpha)
    shape[0] = 0.0  # DC 제거
    shape *= 1.0 / (1.0 + np.power(freqs / cutoff_hz, 2.0))
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


def rain() -> np.ndarray:
    """폭우 보량음. 굵은 빗줄기 + 창을 때리는 저역 + 느린 바람 흔들림."""
    seconds = 12.0
    drizzle = spectral_noise(seconds, alpha=0.5, cutoff_hz=6000) * 1.0
    body = spectral_noise(seconds, alpha=1.1, cutoff_hz=1400) * 2.2
    rumble = spectral_noise(seconds, alpha=1.8, cutoff_hz=220) * 3.0

    time = np.arange(int(RATE * seconds)) / RATE
    # 바람이 몰아치는 느낌을 주는 아주 느린 진폭 변화
    gust = 1.0 + 0.35 * np.sin(2 * np.pi * 0.07 * time + 0.4)
    gust *= 1.0 + 0.18 * np.sin(2 * np.pi * 0.019 * time)

    mixed = (drizzle + body + rumble) * gust
    return loop_seamlessly(normalize(mixed, 0.55))


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
    return normalize(total, 0.5)


def footsteps() -> np.ndarray:
    """이동음. 나무 바닥을 딛는 둔탁한 두 걸음."""
    total = np.zeros(int(RATE * 1.0))
    for offset, level in ((0.02, 1.0), (0.42, 0.82)):
        start = int(RATE * offset)
        count = int(RATE * 0.3)
        time = np.arange(count) / RATE
        thud = spectral_noise(0.3, alpha=1.5, cutoff_hz=300)[:count]
        thud = thud * np.exp(-time * 26.0)
        body = np.sin(2 * np.pi * 92.0 * time) * np.exp(-time * 34.0) * 0.5
        total[start : start + count] += (thud * 2.4 + body) * level
    return normalize(total, 0.5)


def rustle() -> np.ndarray:
    """조사음. 종이를 넘기며 스치는 소리에 가까운 고역 노이즈."""
    seconds = 0.85
    count = int(RATE * seconds)
    time = np.arange(count) / RATE
    base = spectral_noise(seconds, alpha=0.2, cutoff_hz=9000)[:count]
    # 두 번 스치도록 진폭에 봉우리를 만든다
    envelope = np.exp(-((time - 0.12) ** 2) / 0.0035)
    envelope += 0.8 * np.exp(-((time - 0.46) ** 2) / 0.0050)
    return normalize(base * envelope, 0.42)


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
    write_wav("move.wav", footsteps())
    write_wav("search.wav", rustle())
    print("완료")


if __name__ == "__main__":
    main()
