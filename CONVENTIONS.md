# Conventions

Rules learned by getting them wrong. Each one cost a CI failure or a wasted
debugging cycle, and each is here so it is not repeated.

---

## 1. A clean environment is a test, not a formality

**Three consecutive CI failures, three different classes, all invisible locally.**

| Failure | Cause | Why local runs missed it |
|---|---|---|
| `ModuleNotFoundError: cv2` | CI installed only numpy+pytest | my dev env already had opencv |
| `ModuleNotFoundError: PIL` | same shape, one layer deeper | my dev env already had Pillow |
| `Could not open requirements file` | `.gitignore` swallowed it | the file existed *on disk* |

The first two are dependency drift. The third is worse — the file was **never
committed**, so no local run could ever have caught it.

**Rules:**

- When a dependency error appears in CI, reproduce it in a **fresh venv** before
  fixing. Guessing the missing package produces a second failure. I found `PIL`
  only because I rebuilt the venv and ran it, rather than adding the package I
  assumed was missing.
- Keep one `requirements-dev.txt` and have CI install *from it*. Two lists drift;
  one cannot.
- After adding a file CI needs, confirm git can see it:
  `git check-ignore -v <file>` and `git ls-files <file>`.

---

## 2. Test vectors are generated, never hand-typed

Two ADS-B frames I wrote from memory failed their own CRC while the decoder was
**correct**. I nearly "fixed" working code.

A test vector that fails its own checksum is worse than no test vector: it trains
you to distrust a correct implementation.

**Rule:** construct vectors with a helper that computes the checksum
(`make_frame()` in `adsb/decoder.py`), or copy them from a published capture.
Never transcribe hex from memory.

---

## 3. Thresholds scale with the number of trials

A fixed 6 dB detection gate fired on **pure noise**, because taking the max over
~2000 noise bins means the expected maximum grows like `ln(N)`. Measured: pure
chi-square noise reaches **16.7 dB** above the median floor.

**Rule:** use a CFAR threshold derived from the noise distribution
(`_cfar_threshold()` in `system/radar_trigger.py`). And require corroboration —
for a wingbeat, the **symmetric sideband pair**, since a lone line is noise.

---

## 4. Never open first when hunting small fast objects

Measured on a synthetic 4-blob video:

| Morphology | Frames detecting |
|---|---|
| `open 3×3 + close 3×3` | **13 / 90 (14%)** |
| `dilate 3×3 + close` | **90 / 90 (100%)** |

MOG2 produces a *thin, partial* mask for a small fast target, and `MORPH_OPEN`
erodes first — deleting it. The masks are too **small**, not too **noisy**.

**Rule:** dilate small-object masks. Opening is for removing speckle from a mask
that is already the right size.

---

## 5. Assert what the physics supports, not what you wish

The MLAT test originally asserted "position error < 500 m always". That is
asserting something MLAT cannot do — at 320 km with an 80 km baseline, GDOP alone
puts a kilometre-scale floor on the error.

**Rule:** assert the thing the code controls (residual quality), and bound the
rest by the physics (GDOP-scaled tolerance). When a test fails, first ask whether
the *expectation* is wrong. Three times in this project it was.

Same pattern in the wingbeat classifier: a 600 Hz reading is genuinely ambiguous
between *Aedes* female and *Anopheles* male, so the test asserts that the code
**refuses to fire** rather than pretending to identify it.

---

## 6. Tkinter is not thread-safe for UI calls

A background thread calling `update()` **segfaults** the process. This cost a
debugging cycle where the app crashed with no Python traceback.

**Rule:** worker thread owns state; marshal every UI mutation through
`app.after(0, ...)`. Poll a snapshot rather than sharing mutable objects.

---

## 7. One filter source of truth

Both desktop apps compute their filtered track list in a single
`visible_tracks()` method, and the map and the table both read it. They cannot
disagree, because there is nothing to disagree about.

**Rule:** if two views must show the same subset, they call the same function.
Do not re-implement the predicate.

---

## 8. Do not trust a number you have not printed

A drift metric that reported only the least-squares *slope* classified a
500 ns/s frequency offset (0.5 ppm — 1.8 ms/hour) as **PASS**, because a constant
offset has zero slope. The dangerous error was invisible to the metric.

**Rule:** when a measurement drives a decision, print the raw components
(offset *and* slope, not just a derived verdict) and sanity-check one case by
hand.

---

## 9. Mark simulated data as simulated, everywhere it surfaces

The synthetic ADS-B generator, the mock radar backend and the demo MLAT fixes all
label their output (`source='synthetic'`, a `SYNTHETIC — NOT REAL AIRSPACE`
banner, violet aircraft on the scope). A demo that can be mistaken for real data
is a liability.

**Rule:** synthetic data carries its provenance into every layer that displays it.
Never let a mock quietly satisfy a test that is meant to validate the real path.

---

## 10. Document what is verified versus designed

`BRAINSHEET.md` §7 separates three states, and the distinction is load-bearing:

- **Verified** — real execution, real output, reproducible via `run_all_tests.sh`
- **Designed** — a specification; costs are researched BOMs, not receipts
- **Unknown** — stated explicitly, e.g. the 3 W kill figure is from
  *anaesthetised* mosquitoes and in-flight may need 2–3× more

**Rule:** never let a design document imply a build. The gap between "computed
from physics" and "measured on a bench" is where money gets wasted.

---

## 11. `.gitignore` patterns are a footgun

`*.txt` plus `!requirements.txt` looks like it excludes captures and keeps the
manifest. It does not: `requirements-dev.txt` matches `*.txt` and not the
negation, so it was silently untracked and CI failed on a file that existed.

**Rule:** ignore by *intent* (`capture*.txt`, `adsb_*.txt`) rather than by broad
extension with exceptions, and verify with `git check-ignore -v`.

---

## Testing

```bash
bash run_all_tests.sh            # everything: 10 checks
bash run_all_tests.sh --quick    # skip the Chrome simulation matrix
```

CI runs the same suite on Python 3.10 and 3.12 (`.github/workflows/verify.yml`).
If it is green, the "verified" column in `BRAINSHEET.md` §7 is true.

**Every test is self-contained**: synthesised data, real algorithms, no hardware,
no network. That is deliberate — it means the suite proves the *logic* is sound
and says nothing about whether your dongle is plugged in. Hardware bring-up is a
separate exercise with its own gates, listed in `BRAINSHEET.md` §5.
