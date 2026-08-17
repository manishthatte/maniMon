#!/usr/bin/env python3
"""
maniMon — the palette. Single source of truth.

Before 18 Aug 2026 the colours were defined TWICE — once in `panel_common.py`
(BG, CYAN, BLUE …) and once in `widgets.py` (SURFACE, TRACK, INK, CAT, SEQ …).
Two definitions of one idea drift. This module is now the only place a colour
is written down; both of those files import from here.

Theme: LIGHT (parchment), chosen 18 Aug 2026. Every value below was picked by
measurement, not by eye — see the three rules and the numbers that back them.

    1. MAGNITUDE  (CPU heat grid, load, temperature ramps)
       -> SEQUENTIAL single-hue ramp, lightness falling monotonically from the
          page colour to a deep burnt amber. Idle cells therefore melt into the
          page and only real activity draws the eye.
          Verified: L* 93.7 -> 25.3, monotone, min adjacent dE 9.8.
          NEVER a green->red rainbow: hue is not a quantity, and green/amber are
          nearly identical under deuteranopia.

    2. IDENTITY   (stacked-bar segments — used/buffers/cache/free, campaign)
       -> CATEGORICAL blue / amber / purple / teal, separated by LIGHTNESS
          first and hue second, because hue is what colour-vision deficiency
          takes away.
          Verified by Vienot-Brettel-Mollon simulation + CIE76:
             normal dE 39.5 · protan 29.3 · deutan 24.5 · tritan 21.0
          (The obvious "just darken the dark-theme colours" set was measured
          first and REJECTED: it collapsed to dE 3.0 under deuteranopia and
          2.7 under tritanopia, because all four sat at the same L*.)

    3. STATE      (disk full, temperature, failure)
       -> STATUS green / amber / red, reserved, never reused as a series
          colour, and ALWAYS paired with a glyph or a number so meaning never
          rests on colour alone.

Contrast, measured against both grounds (page #f4ece0 and track #e6d9c6):
every accent that can carry text clears **4.8:1 on the track** and 5.7:1 on
the page — i.e. WCAG AA for normal text on the harsher of the two surfaces.
The categorical marks clear 3.5:1, the mark-level floor.

The previous dark-espresso values are preserved at the bottom of this file so
the old scheme can be restored by hand if the light one does not suit.

Author: Manish Jagdish Thatte
"""

# ── Surfaces ──────────────────────────────────────────────────────────────────
# PAGE is the window background; TRACK is the unfilled part of any bar, and is
# the harsher ground to sit text on, so it is what the contrast target is set
# against. RULE is hairline separators only — deliberately low contrast.
PAGE    = "#f4ece0"      # parchment
TRACK   = "#e6d9c6"
RULE    = "#cbb99e"
INK     = "#2b1a10"      # primary text — 14.25:1 on PAGE
INK_DIM = "#6a5946"      # secondary text — 5.73:1 on PAGE, 4.83:1 on TRACK

# ── Named accents ─────────────────────────────────────────────────────────────
# Each was darkened from its nominal hue until it cleared 4.8:1 against TRACK,
# preserving hue and saturation. These carry text as well as marks.
CYAN   = "#0d637f"
BLUE   = "#275d98"
GREEN  = "#27692a"
YELLOW = "#7b5500"
ORANGE = "#944514"
RED    = "#b02424"
PURPLE = "#6b3fa0"
LIME   = "#2e6822"
GOLD   = "#75560e"
TEAL   = "#11665b"
ROSE   = "#a52d5a"

# ── Categorical: identity. Fixed order, never cycled, never recoloured. ──────
# Lightness-first separation (L* 27.0 / 46.4 / 31.3 / 43.8) is what keeps these
# apart under CVD. Do not "brighten" one of them without re-running the check.
CAT = ["#2b4257",   # blue
       "#94642c",   # amber
       "#662c94",   # purple
       "#17755f"]   # teal

# ── Sequential: magnitude. ───────────────────────────────────────────────────
# The first step is deliberately a shade OFF the page colour, not equal to it.
# Setting SEQ[0] = PAGE was tried first and rejected on sight: idle cells then
# vanish completely and the 64-thread grid stops reading as a grid, so you lose
# the reference frame that makes a busy core legible as a 2-cell block.
SEQ = ["#eee2d0", "#f0e0c4", "#ecd0a0", "#e8bc74", "#dea34a",
       "#cc8628", "#b06a14", "#8c4e0c", "#5e3006"]

# ── Status: reserved. Never used as a series colour. ────────────────────────
OK, WARN, CRIT = GREEN, YELLOW, RED

# ── Gloss ─────────────────────────────────────────────────────────────────────
# The dark theme lit the top of a filled bar with white at 12% alpha. On a light
# ground a white gloss is invisible; a dark one at low alpha reads the same way.
GLOSS_RGBA = (0.0, 0.0, 0.0, 0.10)

# Hairline on a SATURATED heat cell. Stays white because the hot end of SEQ is
# dark (#5e3006) — this outline is drawn only on cells at >=90%, never on the
# pale idle ones, so it is high-contrast exactly where it is used.
HILITE_RGBA = (1.0, 1.0, 1.0, 0.30)

# ── Legacy aliases ────────────────────────────────────────────────────────────
# panel_common.py exported these names and the panels use them throughout.
# Keeping the names avoids a rename sweep across ~1000 lines of rendering code
# for no behavioural gain. Note WHITE is now dark ink — the name is historical.
BG    = PAGE
WHITE = INK
DIM   = INK_DIM
DIM2  = TRACK
DIM3  = RULE

# Widget-side names, same objects.
SURFACE = PAGE


# ── Self-test ─────────────────────────────────────────────────────────────────
def _contrast(a, b):
    def lin(c):
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    def lum(h):
        r, g, bl = (lin(int(h[i:i + 2], 16)) for i in (1, 3, 5))
        return 0.2126 * r + 0.7152 * g + 0.0722 * bl

    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _main():
    """python3 palette.py — print the contrast table and flag any regression."""
    named = {k: v for k, v in globals().items()
             if k.isupper() and isinstance(v, str) and v.startswith('#')}
    print(f"{'name':9} {'hex':9} {'vs PAGE':>8} {'vs TRACK':>9}  verdict")
    bad = 0
    # Exempt by VALUE, not by name: BG/SURFACE/DIM2/DIM3 are aliases of the
    # surfaces and must not be judged as if they were text colours.
    surfaces = {PAGE, TRACK, RULE}
    for n, h in sorted(named.items()):
        a, b = _contrast(h, PAGE), _contrast(h, TRACK)
        if h in surfaces:
            verdict = 'surface'
        elif b >= 4.5:
            verdict = 'AA text on both grounds'
        elif b >= 3.0:
            verdict = 'mark only'
        else:
            verdict = '** FAIL **'
            bad += 1
        print(f"{n:9} {h:9} {a:8.2f} {b:9.2f}  {verdict}")
    print(f"\nCAT marks (identity):")
    for i, h in enumerate(CAT):
        print(f"  cat{i}    {h}  vs TRACK {_contrast(h, TRACK):5.2f}")
    print(f"\nSEQ ramp: {len(SEQ)} steps, {SEQ[0]} -> {SEQ[-1]}")
    print(f"\n{'FAILURES: ' + str(bad) if bad else 'all colours pass'}")
    return 1 if bad else 0


if __name__ == '__main__':
    import sys
    sys.exit(_main())


# ══════════════════════════════════════════════════════════════════════════════
#  PREVIOUS THEME — dark espresso, in use 14 Aug to 18 Aug 2026.
#  Kept only so the old look can be restored by hand. Not imported.
#
#    PAGE/BG "#1a0d06"   TRACK/DIM2 "#2e1a0c"   RULE/DIM3 "#4a2e18"
#    INK/WHITE "#e8d0a0" INK_DIM "#9a7550"      DIM "#806040"
#    CYAN "#4ab8e0"  BLUE "#5090d0"  GREEN "#5cc85c"  YELLOW "#d8a820"
#    ORANGE "#e07030" RED "#e04848"  PURPLE "#9870e0" LIME "#60d060"
#    GOLD "#d09030"  TEAL "#30b8a0"  ROSE "#d06080"
#    CAT ["#4a90d0", "#b07818", "#8a5fc4", "#2f9e8f"]
#    SEQ ["#2a1810","#3a2410","#573414","#7a4a16","#9c6018",
#         "#bc7c1e","#d69a2e","#eeba4e","#ffd97a"]
#    GLOSS was white at 0.12 alpha.
# ══════════════════════════════════════════════════════════════════════════════
