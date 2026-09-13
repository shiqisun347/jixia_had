from __future__ import annotations

import importlib.util
import sys
from io import BytesIO
from pathlib import Path

import av

from jx_core.agent.audio import decode_ogg_opus_pcm


def _load_probe_module():
    path = Path(__file__).parents[3] / "scripts/ops/provider_chain_probe.py"
    spec = importlib.util.spec_from_file_location("provider_chain_probe", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load_probe_module()


def test_opus_to_pcm16k_decodes_in_memory_audio() -> None:
    output = BytesIO()
    with av.open(output, mode="w", format="ogg") as container:
        stream = container.add_stream(  # pyright: ignore[reportUnknownMemberType]
            "libopus", rate=48_000
        )
        stream.layout = "mono"
        frame = av.AudioFrame(format="s16", layout="mono", samples=4_800)
        frame.sample_rate = 48_000
        frame.planes[0].update(bytes(frame.planes[0].buffer_size))
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)

    pcm = decode_ogg_opus_pcm(output.getvalue())

    assert 2_800 <= len(pcm) <= 3_600


def test_load_summary_reports_only_aggregate_latency_and_error_types() -> None:
    summary = probe.summarize_latencies(
        [(True, 10, None), (True, 20, None), (True, 30, None), (False, 40, "TimeoutError")]
    )

    assert summary == {
        "requested": 4,
        "succeeded": 3,
        "failed": 1,
        "latency_ms": {"p50": 20, "p95": 30, "max": 30},
        "errors": {"TimeoutError": 1},
    }
