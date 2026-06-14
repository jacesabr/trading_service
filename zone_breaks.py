"""
zone_breaks.py — Causal Python translation of 'Zone Breaks V4.317' (Pine v6).

Faithful to the Pine source in:
  * Volume profile: 25 price segments between highest high / lowest low of the
    last `lookback` chart bars; volume contributed by lower-timeframe (LTF)
    bars, split across segments proportionally to price overlap (exact same
    four-branch overlap logic as the Pine code).
  * Value area: smallest contiguous segment window whose volume >= va_share *
    total chart-TF volume of the window (same search, same tie behaviour,
    same off-by-one VAL definition: val = bottom of the segment BELOW the
    low-index segment, vah = bottom of the high-index segment).
  * Zones: 8 fib zones at (0-23.6, 38.2-50, 61.8-78.6, 100-127.2, 138.2-150,
    161.8-176.4, 200-224, 261.8-300)% of the VAL->VAH range, anchored at VAL.
  * State machine: close crossing above a zone top (zones 1-7) => breakout
    pending; invalidated if close < that zone's bottom (forces full zone
    recompute), validated if high >= next zone's bottom. Mirror logic for
    breakdowns (zones 2-8; valid if low <= the zone-below's top).

Deliberate differences (the honest-backtest changes):
  * CAUSAL ONLY. The Pine script scans the PAST `lookback` closes against
    zones computed from the present window (repainting labels). Here, zones
    computed at bar t's close can only produce events from bar t+1 onward.
  * Zone recompute happens at the close of the bar AFTER an invalidation
    (matches Pine's calc_new_set := true -> next-bar recompute).
  * On recompute, both up and down state machines reset (Pine leaves the
    non-triggering side's status variable dangling against deleted zones).
  * Pine's LTF warm-up/array-validation plumbing is replaced by requiring the
    window to contain >= 0.75 * lookback * tf_ratio LTF bars (same threshold
    the profile block uses).
"""

from dataclasses import dataclass
import numpy as np
import pandas as pd

ZONE_MULTS = [
    (0.0, 23.6), (38.2, 50.0), (61.8, 78.6), (100.0, 127.2),
    (138.2, 150.0), (161.8, 176.4), (200.0, 224.0), (261.8, 300.0),
]
N_SEGMENTS = 25


# Pine tf mapping: chart-TF seconds -> LTF minutes (from the script header)
def ltf_for_chart_tf(tf_seconds: int) -> int:
    if tf_seconds <= 300:   return 1
    if tf_seconds <= 1800:  return 3 if tf_seconds <= 900 else 5
    if tf_seconds <= 3600:  return 5
    if tf_seconds <= 14400: return 15
    return 60


@dataclass
class Zone:
    id: int
    btm: float
    top: float


@dataclass
class Event:
    t: int          # chart bar index at whose CLOSE the event was confirmed
    kind: str       # break_up | valid_up | invalid_up | break_dn | valid_dn | invalid_dn
    zone_id: int
    price: float = float('nan')   # confirming close (breaks/invalids) or target (valids)
    target: float = float('nan')  # validation level at break time
    stop: float = float('nan')    # invalidation level at break time


def ltf_side(open_, close_) -> np.ndarray:
    """Pine side(): +1/-1 by candle body, fallback to close-vs-prev-close,
    fallback to carry previous value."""
    n = len(close_)
    out = np.zeros(n, dtype=np.int8)
    prev = 1
    for i in range(n):
        if close_[i] > open_[i]:
            s = 1
        elif close_[i] < open_[i]:
            s = -1
        elif i > 0 and close_[i] > close_[i - 1]:
            s = 1
        elif i > 0 and close_[i] < close_[i - 1]:
            s = -1
        else:
            s = prev
        out[i] = s
        prev = s
    return out


def compute_profile(win_high, win_low, win_vol_chart_tf,
                    ltf_hi, ltf_lo, ltf_vol, va_share):
    """Returns (val, vah) or None. Mirrors the Pine profile block exactly."""
    hh = float(np.max(win_high))
    ll = float(np.min(win_low))
    total_volume = float(np.sum(win_vol_chart_tf))
    seg_size = (hh - ll) / N_SEGMENTS
    if seg_size <= 0 or total_volume <= 0 or len(ltf_vol) == 0:
        return None

    seg_vol = np.zeros(N_SEGMENTS)
    bar_size = ltf_hi - ltf_lo
    safe_size = np.where(bar_size > 0, bar_size, 1.0)
    for i in range(N_SEGMENTS):
        btm = ll + seg_size * i
        top = btm + seg_size
        overlap = np.minimum(ltf_hi, top) - np.maximum(ltf_lo, btm)
        full = (ltf_hi <= top) & (ltf_lo >= btm)          # incl. zero-size bars
        frac = np.where(full, 1.0, np.clip(overlap, 0.0, None) / safe_size)
        frac = np.where(overlap <= 0, np.where(full, 1.0, 0.0), frac)
        seg_vol[i] = float(np.sum(ltf_vol * np.clip(frac, 0.0, 1.0)))

    # value area search (identical loop incl. the (j-i) < len tie rule)
    target = total_volume * va_share
    length = 50000
    lo_i = hi_i = 0
    for i in range(N_SEGMENTS):
        s = 0.0
        for j in range(i, N_SEGMENTS):
            s += seg_vol[j]
            if s >= target:
                if (j - i) < length:
                    lo_i, hi_i = i, j
                    length = j - i + 1
                break

    vah = ll + seg_size * hi_i
    val = ll + seg_size * lo_i - seg_size   # Pine: valTop - segment_size
    if vah <= val:
        return None
    return val, vah


def make_zones(val, vah):
    rng = vah - val
    return [Zone(i + 1, val + rng * b / 100.0, val + rng * t / 100.0)
            for i, (b, t) in enumerate(ZONE_MULTS)]


def run_engine(chart: pd.DataFrame, ltf: pd.DataFrame,
               lookback=100, value_area_share=0.70,
               breakouts=True, breakdowns=True, stale_bars=3,
               qualify=False):
    """
    NOTE — liveness fix not present in the Pine original: the Pine script only
    recomputes zones on an invalidation. If price exits the whole grid (below
    zone 1's bottom / above zone 8's top) without one, the indicator freezes
    forever (observed on BTCUSDT 1h: silent from 2025-10-09 onward). Here,
    `stale_bars` consecutive closes outside the grid force a recompute.

    qualify=True implements the zone-set qualification from the Pine script's
    retrospective scan: a freshly computed zone set is REPLAYED over the same
    lookback window; if the replay produces any invalidation, the set is
    rejected and a new window is tried next bar. Only sets that explain the
    window without error go live. A setup still pending at the end of the
    replay carries into live bars (its break event is stamped at the
    acceptance bar, since that is the first moment it was tradeable).
    Returns (events, up_state, dn_state, n_accepted, n_rejected).
    """
    """
    chart, ltf: DataFrames with columns ts (ms), open, high, low, close, volume.
    Returns (events, status_series) where status_series[t] in
    {0: idle/no zones, +1: breakout pending validation, -1: breakdown pending,
     +2/-2: both pending (up listed first)} — recorded at each bar's CLOSE.
    """
    o = chart['open'].to_numpy(); h = chart['high'].to_numpy()
    l = chart['low'].to_numpy();  c = chart['close'].to_numpy()
    v = chart['volume'].to_numpy(); ts = chart['ts'].to_numpy()
    n = len(chart)

    # map each LTF bar to its chart bar via timestamp bucketing
    chart_tf_ms = int(np.median(np.diff(ts)))
    ltf_ts = ltf['ts'].to_numpy()
    ltf_bucket = np.searchsorted(ts, ltf_ts, side='right') - 1
    in_range = (ltf_bucket >= 0) & (ltf_ts < ts[np.clip(ltf_bucket, 0, n - 1)] + chart_tf_ms)
    lhi = ltf['high'].to_numpy(); llo = ltf['low'].to_numpy()
    lvo = ltf['volume'].to_numpy()
    # start index of each chart bar's LTF rows (LTF assumed time-sorted)
    bar_start = np.searchsorted(ltf_ts, ts, side='left')
    bar_end = np.searchsorted(ltf_ts, ts + chart_tf_ms, side='left')

    ltf_min = ltf_for_chart_tf(chart_tf_ms // 1000)
    tf_ratio = (chart_tf_ms // 1000) // (ltf_min * 60)
    min_ltf_bars = 0.75 * lookback * tf_ratio

    events: list[Event] = []
    status = np.zeros(n, dtype=np.int8)
    up_state = np.zeros(n, dtype=np.int8)   # 1 while breakout pending
    dn_state = np.zeros(n, dtype=np.int8)

    calc_new = True
    zones = None
    status_up = 'wait'; broken_up = None
    status_dn = 'wait'; broken_dn = None
    outside_count = 0
    n_accepted = 0; n_rejected = 0

    for t in range(lookback, n):
        if calc_new:
            w0 = t - lookback + 1
            s_ltf, e_ltf = bar_start[w0], bar_end[t]
            if e_ltf - s_ltf >= min_ltf_bars:
                prof = compute_profile(h[w0:t + 1], l[w0:t + 1], v[w0:t + 1],
                                       lhi[s_ltf:e_ltf], llo[s_ltf:e_ltf],
                                       lvo[s_ltf:e_ltf], value_area_share)
                if prof is not None:
                    cand = make_zones(*prof)
                    q_up, q_bu, q_dn, q_bd, ok = 'wait', None, 'wait', None, True
                    if qualify:
                        for i in range(w0, t + 1):
                            pc_i, c_i, h_i, l_i = c[i - 1], c[i], h[i], l[i]
                            if breakouts:
                                if q_up == 'wait':
                                    for z in cand:
                                        if pc_i <= z.top and c_i > z.top and z.id != len(cand):
                                            q_bu = z.id; q_up = 'pending'; break
                                if q_up == 'pending':
                                    if c_i < cand[q_bu - 1].btm:
                                        ok = False; break
                                    elif h_i >= cand[q_bu].btm:
                                        q_bu = None; q_up = 'wait'
                            if breakdowns:
                                if q_dn == 'wait':
                                    for z in cand:
                                        if pc_i >= z.btm and c_i < z.btm and z.id != 1:
                                            q_bd = z.id; q_dn = 'pending'; break
                                if q_dn == 'pending':
                                    if c_i > cand[q_bd - 1].top:
                                        ok = False; break
                                    elif l_i <= cand[q_bd - 2].top:
                                        q_bd = None; q_dn = 'wait'
                    if ok:
                        zones = cand; calc_new = False; n_accepted += 1
                        status_up, broken_up = q_up, q_bu
                        status_dn, broken_dn = q_dn, q_bd
                        if status_up == 'pending':
                            events.append(Event(t, 'break_up', broken_up, price=c[t],
                                                target=zones[broken_up].btm,
                                                stop=zones[broken_up - 1].btm))
                        if status_dn == 'pending':
                            events.append(Event(t, 'break_dn', broken_dn, price=c[t],
                                                target=zones[broken_dn - 2].top,
                                                stop=zones[broken_dn - 1].top))
                        outside_count = 0
                        up_state[t] = 1 if status_up == 'pending' else 0
                        dn_state[t] = 1 if status_dn == 'pending' else 0
                    else:
                        n_rejected += 1
                        status_up = status_dn = 'wait'
                        broken_up = broken_dn = None
                    continue
            status_up = status_dn = 'wait'
            broken_up = broken_dn = None
            continue

        prev_c, cl, hi, lo = c[t - 1], c[t], h[t], l[t]

        if breakouts:
            if status_up == 'wait':
                for z in zones:
                    if prev_c <= z.top and cl > z.top and z.id != len(zones):
                        broken_up = z.id
                        status_up = 'pending'
                        events.append(Event(t, 'break_up', z.id, price=cl,
                                            target=zones[z.id].btm, stop=z.btm))
                        break
            if status_up == 'pending':                       # same-bar check, like Pine
                if cl < zones[broken_up - 1].btm:
                    events.append(Event(t, 'invalid_up', broken_up, price=cl))
                    broken_up = None; status_up = 'wait'; calc_new = True
                elif hi >= zones[broken_up].btm:
                    events.append(Event(t, 'valid_up', broken_up,
                                        price=zones[broken_up].btm))
                    broken_up = None; status_up = 'wait'

        if breakdowns and not calc_new:
            if status_dn == 'wait':
                for z in zones:
                    if prev_c >= z.btm and cl < z.btm and z.id != 1:
                        broken_dn = z.id
                        status_dn = 'pending'
                        events.append(Event(t, 'break_dn', z.id, price=cl,
                                            target=zones[z.id - 2].top, stop=z.top))
                        break
            if status_dn == 'pending':
                if cl > zones[broken_dn - 1].top:
                    events.append(Event(t, 'invalid_dn', broken_dn, price=cl))
                    broken_dn = None; status_dn = 'wait'; calc_new = True
                elif lo <= zones[broken_dn - 2].top:
                    events.append(Event(t, 'valid_dn', broken_dn,
                                        price=zones[broken_dn - 2].top))
                    broken_dn = None; status_dn = 'wait'

        # liveness: price escaped the entire grid with nothing pending
        if cl < zones[0].btm or cl > zones[-1].top:
            outside_count += 1
        else:
            outside_count = 0
        if (outside_count >= stale_bars
                and status_up != 'pending' and status_dn != 'pending'):
            calc_new = True
            outside_count = 0
            events.append(Event(t, 'stale', 0))

        up_state[t] = 1 if status_up == 'pending' else 0
        dn_state[t] = 1 if status_dn == 'pending' else 0

    return events, up_state, dn_state, n_accepted, n_rejected
