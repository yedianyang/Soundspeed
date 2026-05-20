import numpy as np
import pytest

from backend.audio.constants import OUTPUT_SAMPLE_RATE
from backend.audio.source import AudioChunk, AudioConfig


def test_output_sample_rate_is_16k():
    assert OUTPUT_SAMPLE_RATE == 16000


def test_audio_config_defaults():
    cfg = AudioConfig()
    assert cfg.chunk_ms == 200
    assert cfg.max_channels == 2


def test_audio_chunk_holds_independent_channel_arrays():
    ch0 = np.zeros(3200, dtype=np.int16)
    ch1 = np.ones(3200, dtype=np.int16)
    chunk = AudioChunk(seq=0, channels=[ch0, ch1], n_frames=3200, start_frame=0)
    assert chunk.sample_rate == 16000
    assert len(chunk.channels) == 2
    assert chunk.channels[0] is ch0 and chunk.channels[1] is ch1


def test_audio_chunk_is_frozen():
    import dataclasses

    chunk = AudioChunk(seq=0, channels=[], n_frames=0, start_frame=0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        chunk.seq = 1
