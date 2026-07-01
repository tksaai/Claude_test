import numpy as np

from twitch_monitor.audio import FRAME_SAMPLES, SAMPLE_RATE, AudioAnalyzer


def tone(freq: float, amplitude: float) -> np.ndarray:
    t = np.arange(FRAME_SAMPLES) / SAMPLE_RATE
    return (np.sin(2 * np.pi * freq * t) * amplitude * 32767).astype(np.int16)


def mix(*frames: np.ndarray) -> np.ndarray:
    total = np.sum([f.astype(np.int32) for f in frames], axis=0)
    return np.clip(total, -32768, 32767).astype(np.int16)


BGM = tone(150, 0.05)          # 低域中心の定常BGM/ゲーム音
SHOUT = tone(1000, 0.4)        # 人声帯域の大音量(絶叫/笑い声の代替)


def test_constant_bgm_stays_calm():
    analyzer = AudioAnalyzer()
    frames = [analyzer.process_frame(BGM, offset=float(i)) for i in range(30)]
    assert all(f.excitement == 0.0 for f in frames)


def test_shout_over_bgm_detected():
    analyzer = AudioAnalyzer()
    for i in range(30):
        analyzer.process_frame(BGM, offset=float(i))
    frame = analyzer.process_frame(mix(BGM, SHOUT), offset=30.0)
    assert frame.excitement > 0.8
    assert frame.voice_ratio > 0.6  # 人声帯域が支配的と判定されている


def test_low_band_burst_scores_below_voice_burst():
    """同じ音量増でも、人声帯域外(ゲームの低音等)はスコアが低い。"""
    analyzer_voice = AudioAnalyzer()
    analyzer_bass = AudioAnalyzer()
    for i in range(30):
        analyzer_voice.process_frame(BGM, offset=float(i))
        analyzer_bass.process_frame(BGM, offset=float(i))
    voice = analyzer_voice.process_frame(mix(BGM, tone(1000, 0.4)), offset=30.0)
    bass = analyzer_bass.process_frame(mix(BGM, tone(80, 0.4)), offset=30.0)
    assert voice.excitement > bass.excitement


def test_sustained_loudness_decays():
    """音量上昇が続く場合(BGM切替・戦闘シーン等)はスコアが減衰する。"""
    analyzer = AudioAnalyzer()
    for i in range(30):
        analyzer.process_frame(BGM, offset=float(i))
    excitements = [
        analyzer.process_frame(mix(BGM, SHOUT), offset=30.0 + i).excitement for i in range(60)
    ]
    assert excitements[0] > 0.8
    assert excitements[-1] < excitements[0] * 0.5


def test_baseline_not_lifted_by_short_shout():
    analyzer = AudioAnalyzer()
    for i in range(30):
        analyzer.process_frame(BGM, offset=float(i))
    baseline_before = analyzer.baseline_db
    for i in range(3):
        analyzer.process_frame(mix(BGM, SHOUT), offset=30.0 + i)
    assert analyzer.baseline_db < baseline_before + 2.0
