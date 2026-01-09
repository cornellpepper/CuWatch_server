// Shared rate calculation utilities
// Exposes functions on window: computeSlidingRates, computeInstantRates, computeRollingAvgInstantRate

(function(){
  function computeSlidingRates(rows, windowSec) {
    const YEAR2000 = 946684800; // seconds since epoch
    const asc = [];
    for (const r of [...rows]) {
      if (!r || r.muon_count == null || !r.ts) continue;
      const tSec = new Date(r.ts).getTime() / 1000; // seconds
      if (!isFinite(tSec) || tSec < YEAR2000) continue;
      asc.push({ t: tSec, c: Number(r.muon_count) });
    }
    // sort ascending by time; break ties with muon_count
    asc.sort((a,b) => (a.t !== b.t) ? (a.t - b.t) : (a.c - b.c));
    if (asc.length < 2) return [];

    const rates = [];
    let l = 0; // left pointer (last point with t <= tLeft)
    for (let i = 0; i < asc.length; i++) {
      const ti = asc[i].t;
      const tLeft = ti - windowSec;
      while (l + 1 < asc.length && asc[l + 1].t <= tLeft) l++;
      if (tLeft < asc[0].t) continue; // not enough history
      if (l >= i) continue;

      const tL = asc[l].t, cL = asc[l].c;
      let cLeft = cL;
      if (l + 1 <= i) {
        const tR = asc[l + 1].t, cR = asc[l + 1].c;
        if (tR > tL && tLeft > tL) {
          const f = (tLeft - tL) / (tR - tL);
          cLeft = cL + f * (cR - cL);
        }
      }
      let dc = asc[i].c - cLeft;
      if (dc < 0) dc = 0; // guard reset
      const r = dc / windowSec;
      if (isFinite(r)) rates.push({ t: ti, r });
    }
    return rates;
  }

  function computeInstantRates(rows) {
    // rows should be in ascending order of time for best results; we use given order
    const out = [];
    for (let i = 0; i < rows.length; i++) {
      const curr = rows[i];
      const prev = rows[i - 1];
      if (!curr) { out.push(null); continue; }
      const currTs = curr.ts ? new Date(curr.ts).getTime() : null;
      const prevTs = prev && prev.ts ? new Date(prev.ts).getTime() : null;
      let deltaMs = null;
      if (currTs != null && prevTs != null) {
        deltaMs = currTs - prevTs;
      }
      if (deltaMs != null && deltaMs > 0) {
        out.push(1000.0 / deltaMs);
        continue;
      }
      const dtVal = Number(curr.dt);
      if (dtVal > 0) {
        out.push(1000.0 / dtVal);
      } else {
        out.push(null);
      }
    }
    return out;
  }

  function computeRollingAvgInstantRate(rows, windowSamples) {
    const inst = computeInstantRates(rows);
    const w = Math.max(1, windowSamples || 30);
    return inst.map((rate, idx) => {
      const start = Math.max(0, idx - w + 1);
      const windowRates = inst.slice(start, idx + 1).filter(r => r !== null);
      if (windowRates.length === 0) return null;
      return windowRates.reduce((a, b) => a + b, 0) / windowRates.length;
    });
  }

  window.computeSlidingRates = computeSlidingRates;
  window.computeInstantRates = computeInstantRates;
  window.computeRollingAvgInstantRate = computeRollingAvgInstantRate;
})();
