#!/usr/bin/env python3
"""
Mooncake Trace Workload Generator

Core library for loading real Mooncake traces, analyzing their enterprise
patterns (bursty arrivals, structured KV block prefix sharing, heavy-tailed
lengths), and generating scaled, parameterized extensions that preserve
realistic characteristics for perf modeling.

DO NOT generate generic IID fakes. All extensions are derived from or
mimic the statistical structure of the provided production traces.
"""

import json
import math
import random
from collections import defaultdict, Counter
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Tuple, Optional, Sequence
import statistics as stats

# Safe offset larger than any seen hash ids (~183k)
HASH_ID_OFFSET = 1_000_000

@dataclass
class TraceAnalysis:
    """Empirical distributions and structural priors extracted from a real trace."""
    name: str
    n_reqs: int
    duration_ms: int
    avg_rps: float
    # length samples (for resampling + jitter)
    input_lens: List[int]
    output_lens: List[int]
    block_counts: List[int]  # len(hash_ids)
    # burst structure (critical)
    burst_sizes: List[int]          # concurrency at each unique timestamp
    unique_ts_count: int
    # interarrival samples between *burst starts* (for placing bursts)
    burst_gaps_ms: List[int]
    # prefix sharing structure
    hot_prefixes: List[Tuple[Tuple[int, ...], int]]  # (prefix_tuple, freq)
    hot_ids: set  # individual hot block ids worth sharing across scaled copies
    max_prefix_len_considered: int
    # hit depth distribution (for new req modeling)
    hit_depth_samples: List[int]
    # raw max hash seen (for fresh id allocation)
    max_hash_id: int
    # misc stats
    median_input: int
    median_output: int
    approx_cache_hit_ratio: float  # block level, from causal prefix analysis

    def summary(self) -> str:
        return (f"{self.name}: {self.n_reqs} reqs, {self.duration_ms/1000:.0f}s, "
                f"RPS~{self.avg_rps:.1f}, bursts up to {max(self.burst_sizes)}, "
                f"hot_prefixes={len(self.hot_prefixes)}, hit_ratio~{self.approx_cache_hit_ratio*100:.0f}%")


def load_trace(path: str) -> List[Dict[str, Any]]:
    reqs = []
    with open(path) as f:
        for line in f:
            if line.strip():
                reqs.append(json.loads(line))
    # Ensure sorted by ts (some traces may not be strictly?)
    reqs.sort(key=lambda r: (r["timestamp"], r.get("input_length", 0)))
    return reqs


def _compute_causal_hits(reqs: List[Dict]) -> List[int]:
    """For each req, longest prefix length that appeared in any prior req."""
    past_prefixes = set()
    hits = []
    for r in reqs:
        h = r["hash_ids"]
        m = 0
        for k in range(len(h), 0, -1):
            if tuple(h[:k]) in past_prefixes:
                m = k
                break
        hits.append(m)
        for k in range(1, len(h) + 1):
            past_prefixes.add(tuple(h[:k]))
    return hits


def analyze_trace(reqs: List[Dict[str, Any]], name: str = "trace") -> TraceAnalysis:
    if not reqs:
        raise ValueError("empty trace")
    ts = [r["timestamp"] for r in reqs]
    ins = [r["input_length"] for r in reqs]
    outs = [r["output_length"] for r in reqs]
    hls = [len(r["hash_ids"]) for r in reqs]
    dur = max(ts) - min(ts)
    n = len(reqs)
    avg_rps = n / max(1, (dur / 1000.0))

    # Bursts (by exact timestamp)
    ts_counter = Counter(ts)
    burst_sizes = list(ts_counter.values())
    unique_ts = len(ts_counter)

    # Gaps between burst starts (unique ts sorted)
    uniq_ts_sorted = sorted(ts_counter.keys())
    gaps = [uniq_ts_sorted[i+1] - uniq_ts_sorted[i] for i in range(len(uniq_ts_sorted)-1)]

    # Hot prefixes: collect at a few depths, keep those with freq >=2
    hot: Dict[Tuple[int, ...], int] = defaultdict(int)
    for r in reqs:
        h = r["hash_ids"]
        for k in (2, 3, 4, 5, 6, 8):
            if len(h) >= k:
                hot[tuple(h[:k])] += 1
    hot_list = [(p, f) for p, f in hot.items() if f >= 2]
    hot_list.sort(key=lambda x: -x[1])

    # Causal hit depths
    hits = _compute_causal_hits(reqs)
    hit_ratio = sum(hits[i] / hls[i] for i in range(n) if hls[i] > 0) / n if n else 0.0

    max_h = max((max(r["hash_ids"]) for r in reqs), default=0)

    # Hot individual ids (those appearing in many hot prefixes or high overall freq)
    hot_id_freq: Counter = Counter()
    for p, f in hot_list:
        for hid in p:
            hot_id_freq[hid] += f
    # top ~8% or those with high multiplicity (used for cross-copy sharing)
    hot_ids = {hid for hid, f in hot_id_freq.most_common(max(80, int(len(hot_id_freq) * 0.08)))}

    return TraceAnalysis(
        name=name,
        n_reqs=n,
        duration_ms=dur,
        avg_rps=avg_rps,
        input_lens=ins,
        output_lens=outs,
        block_counts=hls,
        burst_sizes=burst_sizes,
        unique_ts_count=unique_ts,
        burst_gaps_ms=gaps or [1000],
        hot_prefixes=hot_list[:2000],  # cap for memory
        hot_ids=hot_ids,
        max_prefix_len_considered=8,
        hit_depth_samples=hits,
        max_hash_id=max_h,
        median_input=int(stats.median(ins)),
        median_output=int(stats.median(outs)),
        approx_cache_hit_ratio=hit_ratio,
    )


def _resample_with_mult(vals: List[int], mult: float, rng: random.Random, jitter: float = 0.12) -> int:
    """Resample a value from empirical list, scale by mult, add small relative jitter."""
    v = rng.choice(vals)
    v = int(v * mult)
    if jitter > 0:
        j = rng.gauss(0, jitter)
        v = max(1, int(v * (1 + j)))
    return v


def _choose_hit_prefix(analysis: TraceAnalysis, reuse_bias: float, rng: random.Random) -> Tuple[int, ...]:
    """
    Pick a prefix to "hit", biased by reuse_bias (0=cold/new, 1=always hottest).
    reuse_bias=0.7 means 70% chance to pick from weighted hot, else short or none.
    """
    if not analysis.hot_prefixes or rng.random() > reuse_bias:
        # cold start: small random or empty
        return tuple()
    # Weighted pick from hot (more popular prefixes more likely)
    prefs, weights = zip(*analysis.hot_prefixes)
    # temperature on weights to avoid always same few
    total = sum(weights)
    if total == 0:
        return tuple()
    r = rng.random() * total
    cum = 0
    for p, w in zip(prefs, weights):
        cum += w
        if r <= cum:
            # occasionally take shorter version of it for variety
            if len(p) > 2 and rng.random() < 0.3:
                return p[: rng.randint(2, len(p))]
            return p
    return prefs[0]


def _generate_hash_list(
    analysis: TraceAnalysis,
    target_blocks: int,
    reuse_bias: float,
    fresh_start: "FreshIdGen",
    rng: random.Random,
) -> List[int]:
    """
    Generate a realistic hash_id list:
    - Choose a hot prefix (biased)
    - Append enough fresh unique blocks for the remainder.
    - target_blocks is desired len(hash_ids), derived from sampled input_len.
    """
    prefix = _choose_hit_prefix(analysis, reuse_bias, rng)
    hit_len = min(len(prefix), target_blocks)
    result = list(prefix[:hit_len])
    need = target_blocks - hit_len
    for _ in range(max(0, need)):
        result.append(fresh_start.next())
    # If we picked a long prefix but target small, truncate (still realistic: short query on long shared ctx)
    if len(result) > target_blocks:
        result = result[:target_blocks]
    # Guarantee at least 1-2 blocks
    while len(result) < 1:
        result.append(fresh_start.next())
    return result


class FreshIdGen:
    """Monotonic fresh block id allocator for new content."""
    def __init__(self, start: int):
        self._next = start
    def next(self) -> int:
        v = self._next
        self._next += 1
        return v
    @property
    def current(self) -> int:
        return self._next


def _make_idmap_for_copy(analysis: TraceAnalysis, fresh: FreshIdGen, share_hot: bool, rng: random.Random) -> Dict[int, int]:
    """Create a consistent remapping for this copy.
    Hot ids (and optionally more) stay as original value (cross-copy + within hits).
    Everything else gets a fresh private id (but consistent within this copy's reqs).
    """
    idmap: Dict[int, int] = {}
    # Pre-populate known hots
    if share_hot:
        for hid in analysis.hot_ids:
            idmap[hid] = hid
    # Also force-keep very low "global" ids observed in all real traces (0, small numbers)
    for small in range(0, 100):
        if small in analysis.hot_ids or small < 80:  # conservative
            idmap[small] = small
    return idmap


def _get_remapped(h: int, idmap: Dict[int, int], fresh: FreshIdGen) -> int:
    if h in idmap:
        return idmap[h]
    nid = fresh.next()
    idmap[h] = nid  # memoize for this copy
    return nid


def _make_burst_template(reqs_at_ts: List[Dict], analysis: TraceAnalysis,
                         input_mult: float, output_mult: float,
                         reuse_bias: float, fresh: FreshIdGen,
                         rng: random.Random,
                         share_hot: bool = True,
                         idmap: Optional[Dict[int, int]] = None) -> List[Dict]:
    """
    Faithful clone of a burst for one copy:
    - Build (or reuse) a *per-copy* idmap so that any two reqs that shared an id
      in the original will share the *exact same remapped id* in this copy.
    - Hot ids are kept as their original small values (shared across copies too).
    - Non-hot ids are remapped consistently but privately per copy.
    - Then scale the *unique tail length* by input_mult by extending or trimming
      the remapped unique suffix.
    This reproduces the *exact* sharing topology of the real trace (within copy)
    plus realistic extra hits on hot enterprise prompts (across copies).
    """
    if idmap is None:
        idmap = _make_idmap_for_copy(analysis, fresh, share_hot, rng)
    out = []
    for r in reqs_at_ts:
        new_in = max(128, _resample_with_mult(analysis.input_lens, input_mult, rng))
        new_out = max(1, _resample_with_mult(analysis.output_lens, output_mult, rng))
        orig_h = list(r["hash_ids"])

        # remap every id consistently for this copy
        remapped = [_get_remapped(h, idmap, fresh) for h in orig_h]

        # scale the tail: decide how many total blocks after remap
        orig_blocks = len(orig_h)
        tgt = max(2, int(orig_blocks * input_mult * (1 + rng.gauss(0, 0.05))))
        if tgt < len(remapped):
            remapped = remapped[:tgt]
        else:
            while len(remapped) < tgt:
                remapped.append(fresh.next())

        out.append({
            "timestamp": r["timestamp"],
            "input_length": new_in,
            "output_length": new_out,
            "hash_ids": remapped,
        })
    return out


def generate_extended(
    base_reqs: List[Dict[str, Any]],
    analysis: TraceAnalysis,
    *,
    scale: float = 2.0,
    input_mult: float = 1.0,
    output_mult: float = 1.0,
    reuse_bias: float = 0.75,
    burst_mult: float = 1.0,
    time_jitter_ms: int = 0,
    share_hot_prefixes: bool = True,
    seed: int = 42,
    target_duration_ms: Optional[int] = None,
    add_new_sessions: int = 0,  # number of additional generated "session chains"
    new_req_fraction: float = 0.0,  # 0.0-0.3 mix in freshly sampled reqs
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Produce a bulked-up trace that follows the real patterns.

    Primary technique: clone the burst structure N times (or fractional),
    remapping only the unique suffix hashes while (optionally) keeping hot
    prefixes shared across clones. This multiplies load while *realistically
    increasing* cache pressure on popular content.

    Perturb lengths slightly per copy.
    Optionally inject additional modeled requests/sessions.
    """
    rng = random.Random(seed)
    if not base_reqs:
        raise ValueError("base_reqs empty")

    base_dur = analysis.duration_ms
    n_base = analysis.n_reqs

    # Determine how many full copies + fraction
    n_copies = max(1, int(scale))
    frac = scale - n_copies

    # Group base by timestamp to clone bursts exactly
    by_ts: Dict[int, List[Dict]] = defaultdict(list)
    for r in base_reqs:
        by_ts[r["timestamp"]].append(r)

    # Sorted burst starts
    burst_starts = sorted(by_ts.keys())
    burst_templates: List[List[Dict]] = [by_ts[t] for t in burst_starts]

    fresh = FreshIdGen(analysis.max_hash_id + 1 + (0 if share_hot_prefixes else HASH_ID_OFFSET))

    generated: List[Dict] = []
    used_fresh_base = fresh.current

    # For each copy, place the burst sequence at offset time, with fresh (or shared-hot) hashes
    for c in range(n_copies):
        t_offset = c * base_dur
        copy_fresh = fresh
        # per-copy consistent idmap (hots kept, non-hots remapped privately but consistently inside copy)
        copy_idmap = _make_idmap_for_copy(analysis, copy_fresh, share_hot_prefixes, rng)

        for i, template in enumerate(burst_templates):
            # clone + perturb this burst (faithful sharing-preserving remap)
            burst = _make_burst_template(
                template, analysis, input_mult, output_mult, reuse_bias, copy_fresh, rng,
                share_hot=share_hot_prefixes, idmap=copy_idmap
            )
            for breq in burst:
                new_ts = breq["timestamp"] + t_offset
                if time_jitter_ms > 0:
                    new_ts += rng.randint(-time_jitter_ms, time_jitter_ms)
                breq["timestamp"] = max(0, new_ts)
                generated.append(breq)

    # Fractional extra: take first frac of bursts, shifted by n_copies * dur
    if frac > 0.01 and burst_templates:
        t_offset = n_copies * base_dur
        n_frac_bursts = max(1, int(len(burst_templates) * frac))
        frac_idmap = _make_idmap_for_copy(analysis, fresh, share_hot_prefixes, rng)
        for template in burst_templates[:n_frac_bursts]:
            burst = _make_burst_template(
                template, analysis, input_mult, output_mult, reuse_bias, fresh, rng,
                share_hot=share_hot_prefixes, idmap=frac_idmap
            )
            for breq in burst:
                breq["timestamp"] = breq["timestamp"] + t_offset
                generated.append(breq)

    # Optionally add purely generated new requests (modeled, not cloned)
    if new_req_fraction > 0:
        n_new = int(len(generated) * new_req_fraction)
        # place them spread over the duration
        max_t = max(r["timestamp"] for r in generated) if generated else base_dur
        for _ in range(n_new):
            # sample a "burst" of 1-3
            bsize = max(1, min(5, int(rng.choice(analysis.burst_sizes) * burst_mult * rng.uniform(0.5, 1.2))))
            base_t = rng.randint(0, max(1, max_t - 1000))
            for bi in range(bsize):
                inl = _resample_with_mult(analysis.input_lens, input_mult, rng)
                outl = _resample_with_mult(analysis.output_lens, output_mult, rng)
                blks = max(2, int(rng.choice(analysis.block_counts) * input_mult))
                hlist = _generate_hash_list(analysis, blks, reuse_bias, fresh, rng)  # modeled new, not clone
                generated.append({
                    "timestamp": base_t + bi * rng.randint(0, 3),
                    "input_length": inl,
                    "output_length": max(1, outl),
                    "hash_ids": hlist,
                })

    # Add explicit new "sessions" (short chains with extending hashes for realism)
    if add_new_sessions > 0:
        max_t = max((r["timestamp"] for r in generated), default=base_dur)
        for s in range(add_new_sessions):
            # start a new chain with a (possibly hot) prefix
            start_t = rng.randint(0, max(0, max_t - 30000))
            chain_len = rng.randint(2, 5)
            prev_h: List[int] = []
            for turn in range(chain_len):
                inl = _resample_with_mult(analysis.input_lens, input_mult * (0.8 + 0.4*turn), rng)  # growing ctx
                outl = _resample_with_mult(analysis.output_lens, output_mult, rng)
                base_blks = max(3, int(rng.choice(analysis.block_counts) * input_mult))
                # force some extension from previous turn
                if prev_h:
                    # keep first 60-90% of prev as "history"
                    keep = int(len(prev_h) * rng.uniform(0.6, 0.9))
                    hlist = prev_h[:keep]
                    need = max(2, base_blks - len(hlist))
                    for _ in range(need):
                        hlist.append(fresh.next())
                else:
                    hlist = _generate_hash_list(analysis, base_blks, reuse_bias * 0.9, fresh, rng)
                prev_h = hlist
                t = start_t + turn * rng.randint(800, 45000)  # think time + output time proxy
                generated.append({
                    "timestamp": t,
                    "input_length": inl,
                    "output_length": max(1, outl),
                    "hash_ids": hlist,
                })

    # Finalize: sort, clamp, dedup-ish by ts+size if exact dups from frac
    generated.sort(key=lambda r: (r["timestamp"], r["input_length"], len(r["hash_ids"])))

    # If target_duration specified, filter or compress time (simple linear stretch/compress)
    final_dur = max((r["timestamp"] for r in generated), default=0) - min((r["timestamp"] for r in generated), default=0)
    if target_duration_ms and final_dur > 0 and target_duration_ms > 0:
        ratio = target_duration_ms / final_dur
        base_t0 = min(r["timestamp"] for r in generated)
        for r in generated:
            r["timestamp"] = int((r["timestamp"] - base_t0) * ratio)

    # Recompute some manifest stats
    final_n = len(generated)
    final_ts = [r["timestamp"] for r in generated]
    final_dur2 = max(final_ts) - min(final_ts) if final_ts else 0
    final_ins = [r["input_length"] for r in generated]
    final_outs = [r["output_length"] for r in generated]
    final_hls = [len(r["hash_ids"]) for r in generated]
    final_rps = final_n / max(1, final_dur2 / 1000)

    # Approximate hit ratio on output (expensive to do full, sample)
    sample_hits = _compute_causal_hits(generated[: min(3000, len(generated))])
    sample_hls = final_hls[: len(sample_hits)]
    hit_ratio2 = (sum(sample_hits[i] / max(1, sample_hls[i]) for i in range(len(sample_hits))) /
                  max(1, len(sample_hits))) if sample_hits else 0.0

    manifest = {
        "generator": "mooncake-trace-gen v0.1",
        "base_trace": analysis.name,
        "base_stats": {
            "n": analysis.n_reqs,
            "duration_ms": analysis.duration_ms,
            "avg_rps": round(analysis.avg_rps, 2),
            "median_input": analysis.median_input,
            "median_output": analysis.median_output,
            "approx_cache_hit_ratio": round(analysis.approx_cache_hit_ratio, 3),
        },
        "params": {
            "scale": scale,
            "input_mult": input_mult,
            "output_mult": output_mult,
            "reuse_bias": reuse_bias,
            "burst_mult": burst_mult,
            "time_jitter_ms": time_jitter_ms,
            "share_hot_prefixes": share_hot_prefixes,
            "seed": seed,
            "target_duration_ms": target_duration_ms,
            "add_new_sessions": add_new_sessions,
            "new_req_fraction": new_req_fraction,
        },
        "output_stats": {
            "n_requests": final_n,
            "duration_ms": final_dur2,
            "avg_rps": round(final_rps, 2),
            "median_input": int(stats.median(final_ins)) if final_ins else 0,
            "median_output": int(stats.median(final_outs)) if final_outs else 0,
            "p95_input": int(stats.quantiles(final_ins, n=20)[18]) if len(final_ins) > 20 else max(final_ins),
            "approx_cache_hit_ratio": round(hit_ratio2, 3),
            "unique_block_ids": len(set(h for r in generated for h in r["hash_ids"])),
            "max_concurrency": max(Counter(final_ts).values()) if final_ts else 0,
        },
        "notes": "Generated by cloning real burst+prefix structure with controlled perturbations. Hot prefixes kept shared to reproduce realistic enterprise cache behavior.",
    }

    return generated, manifest


def save_trace(reqs: List[Dict], path: str) -> None:
    with open(path, "w") as f:
        for r in reqs:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")


def save_manifest(manifest: Dict, path: str) -> None:
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)


# ---------- Convenience: load built-in traces ----------

BUILTIN_TRACES = {
    "conversation": "traces/conversation_trace.jsonl",
    "toolagent": "traces/toolagent_trace.jsonl",
    "synthetic": "traces/synthetic_trace.jsonl",
}

def load_builtin(name: str) -> Tuple[List[Dict], TraceAnalysis]:
    if name not in BUILTIN_TRACES:
        raise KeyError(f"unknown builtin {name}, try one of {list(BUILTIN_TRACES)}")
    # relative to this file or cwd
    base = __file__.rsplit("/", 1)[0] if "/" in __file__ else "."
    p = f"{base}/../{BUILTIN_TRACES[name]}" if base else BUILTIN_TRACES[name]
    # try a few common locations
    candidates = [
        p,
        f"Mooncake/{BUILTIN_TRACES[name]}",
        BUILTIN_TRACES[name],
        f"../{BUILTIN_TRACES[name]}",
    ]
    reqs = None
    for cand in candidates:
        try:
            reqs = load_trace(cand)
            break
        except FileNotFoundError:
            continue
    if reqs is None:
        raise FileNotFoundError(f"could not locate builtin trace for {name}")
    analysis = analyze_trace(reqs, name=name)
    return reqs, analysis


if __name__ == "__main__":
    # quick self test
    print("Loading conversation builtin for self-test...")
    reqs, an = load_builtin("conversation")
    print(an.summary())
    print("Generating 1.5x scale, high reuse, + some new sessions...")
    ext, man = generate_extended(
        reqs, an,
        scale=1.5,
        input_mult=1.1,
        output_mult=1.0,
        reuse_bias=0.85,
        seed=123,
        add_new_sessions=20,
        new_req_fraction=0.05,
    )
    print("Output:", man["output_stats"])
    print("First 2 lines of generated:")
    for r in ext[:2]:
        print(" ", r)
    print("Manifest keys:", list(man.keys()))
    print("Self-test OK.")