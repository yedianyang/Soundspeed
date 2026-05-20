#!/usr/bin/env python3
"""
soxr GIL-release / thread-parallelism probe.

Question for the Audio Input Layer design: does soxr's streaming resampler
(`soxr.ResampleStream`) release the GIL during native compute, so per-channel
resampling can run in parallel threads (the parallel-channel ThreadPoolExecutor
design)?

Two parts:
  Part A -- clean GIL signal: a fixed bulk of resample work, sequential vs
            T threads. speedup ~T => GIL released; ~1 => GIL held.
  Part B -- realistic per-block cost: one 200ms block, and 2-channel
            sequential vs threaded, in absolute milliseconds. Answers
            "does parallelism matter for our actual 1-2ch / 200ms workload".

Run with the Cactus venv Python (3.14 -- where the pipeline runs):
    /opt/homebrew/Cellar/cactus/1.14_1/libexec/venv/bin/python \
        experiments/2026-05-20-soxr-gil-probe/benchmark.py
"""
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import soxr

IN_RATE = 48000          # typical interface native rate
OUT_RATE = 16000         # Cactus ASR hard requirement
BLOCK_MS = 200           # probe's streaming feed granularity
IN_BLOCK = IN_RATE * BLOCK_MS // 1000   # 9600 frames @48k
DTYPE = "float32"        # what soundfile yields; cast to int16 after resample


def gil_status():
    fn = getattr(sys, "_is_gil_enabled", None)
    return fn() if fn else "n/a (<3.13)"


def make_block():
    """One realistic 200ms mono block: float32 noise in [-1, 1)."""
    return np.random.rand(IN_BLOCK).astype(np.float32) * 2 - 1


def resample_n_blocks(n_blocks, block):
    """Push n_blocks 200ms blocks through one stateful mono stream resampler.

    A fresh ResampleStream per call -> each thread is fully independent.
    """
    rs = soxr.ResampleStream(IN_RATE, OUT_RATE, 1, dtype=DTYPE)
    out_frames = 0
    for _ in range(n_blocks):
        out_frames += len(rs.resample_chunk(block))
    return out_frames


def bench_single_block(block, iters=5000):
    """Absolute cost of one 200ms-block resample through a steady stream."""
    rs = soxr.ResampleStream(IN_RATE, OUT_RATE, 1, dtype=DTYPE)
    for _ in range(50):                       # warmup / reach steady state
        rs.resample_chunk(block)
    t0 = time.perf_counter()
    for _ in range(iters):
        rs.resample_chunk(block)
    return (time.perf_counter() - t0) / iters * 1000   # ms per block


def main():
    print(f"Python {sys.version.split()[0]}  |  GIL enabled: {gil_status()}"
          f"  |  CPU count: {os.cpu_count()}")
    print(f"Resample {IN_RATE} -> {OUT_RATE} Hz, {BLOCK_MS}ms blocks "
          f"({IN_BLOCK} in-frames), dtype={DTYPE}\n")

    block = make_block()

    # ---- Part B: realistic per-block cost --------------------------------
    print("=== Part B: realistic per-block cost (1-2 channels) ===")
    ms1 = bench_single_block(block)
    print(f"  single channel, one 200ms block : {ms1*1000:7.1f} us")

    streams2 = [soxr.ResampleStream(IN_RATE, OUT_RATE, 1, dtype=DTYPE)
                for _ in range(2)]

    def two_ch_seq():
        for rs in streams2:
            rs.resample_chunk(block)

    iters = 5000
    for _ in range(50):
        two_ch_seq()
    t0 = time.perf_counter()
    for _ in range(iters):
        two_ch_seq()
    seq2 = (time.perf_counter() - t0) / iters * 1000

    with ThreadPoolExecutor(max_workers=2) as pool:
        def two_ch_thr():
            list(pool.map(lambda rs: rs.resample_chunk(block), streams2))
        for _ in range(50):
            two_ch_thr()
        t0 = time.perf_counter()
        for _ in range(iters):
            two_ch_thr()
        thr2 = (time.perf_counter() - t0) / iters * 1000

    print(f"  2 channels, sequential          : {seq2:7.4f} ms / chunk")
    print(f"  2 channels, threaded (pool=2)   : {thr2:7.4f} ms / chunk")
    verdict = "faster" if thr2 < seq2 else "SLOWER -- pool overhead dominates"
    print(f"  -> threaded / sequential        : {thr2/seq2:.2f}x  ({verdict})\n")

    # ---- Part A: clean GIL-release signal --------------------------------
    print("=== Part A: GIL release (bulk work, sequential vs threaded) ===")
    W = max(2000, int(4.0 / (ms1 / 1000)))   # size so sequential ~4s
    print(f"  total work W = {W} blocks (~{W*ms1/1000:.1f}s sequential expected)\n")

    t0 = time.perf_counter()
    resample_n_blocks(W, block)
    seq = time.perf_counter() - t0
    print(f"  sequential (1 thread)             : {seq:6.2f}s   "
          f"speedup 1.00x  (baseline)")

    for t in (2, 4, 8):
        per = W // t
        with ThreadPoolExecutor(max_workers=t) as pool:
            t0 = time.perf_counter()
            list(pool.map(lambda _: resample_n_blocks(per, block), range(t)))
            wall = time.perf_counter() - t0
        print(f"  threaded T={t} ({per:6d} blocks/thread) : {wall:6.2f}s   "
              f"speedup {seq/wall:.2f}x")

    print()
    print("Interpretation:")
    print("  Part A speedup -> ~T : soxr releases the GIL; parallel channels")
    print("                        give real wall-clock gain.")
    print("  Part A speedup -> ~1 : GIL held; threads serialize -- parallel-")
    print("                        channel design gains nothing on threads.")
    print("  Part B            : absolute cost at the real 1-2ch / 200ms")
    print("                        workload -- tells you if it even matters.")


if __name__ == "__main__":
    main()
