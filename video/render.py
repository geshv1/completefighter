#!/usr/bin/env python3
"""The Complete Fighter: launch video renderer.

Pure Python + Cairo, piped to ffmpeg. Every frame is drawn natively at
1440x1080 (no upscaling).

Usage:
  python3 video/render.py                 # renders video/out/launch_no_audio.mp4 + cover.png
  python3 video/render.py --frames 1.0 4.3 ...   # writes PNG stills of those times (for checking)

Fonts: Anton and Lora Italic (Google Fonts, OFL) are in video/fonts. They are
installed into ~/.fonts on first run so Cairo can find them via fontconfig.
"""
import math, os, random, shutil, subprocess, sys
from multiprocessing import Pool

import cairo

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")

W, H, FPS = 1440, 1080, 30
FREEZE = 48.7          # last moving frame; the final frame is held after this
END = FREEZE + 1.0     # 1 s hold on the last frame


def hexc(h):
    return tuple(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))


BG, BONE, RED, GREY = hexc("#0B0B0A"), hexc("#ECE8E0"), hexc("#C4161C"), hexc("#8F897F")
ANTON, LORA, ALICE = "Anton", "Lora", "Alice"
CAP = {ANTON: 0.86, LORA: 0.70, ALICE: 0.631}   # cap height / font size
RX, RY = 1050, 540     # centre of the right half (animation side)
TX = 120               # caption left edge (>= 90 px even at the 1.04 push-in)


# ----------------------------------------------------------------- helpers
def clamp(x, a=0.0, b=1.0):
    return a if x < a else b if x > b else x


def prog(t, a, b):
    return clamp((t - a) / (b - a))


def eo3(x):
    return 1 - (1 - x) ** 3


def eox(x):
    return 1.0 if x >= 1 else 1 - 2 ** (-10 * x)


def eio(x):
    return 4 * x ** 3 if x < .5 else 1 - (-2 * x + 2) ** 3 / 2


def eiq(x):
    return x * x


def lerp(a, b, u):
    return a + (b - a) * u


def lerpc(c1, c2, u):
    return tuple(lerp(a, b, u) for a, b in zip(c1, c2))


def src(ctx, col, a=1.0):
    ctx.set_source_rgba(col[0], col[1], col[2], a)


def stroke_style(ctx, w=3):
    ctx.set_line_width(w)
    ctx.set_line_join(cairo.LINE_JOIN_MITER)
    ctx.set_line_cap(cairo.LINE_CAP_BUTT)


def poly_partial(ctx, pts, frac, closed=False):
    """Add the first `frac` of a polyline's length to the path."""
    if closed:
        pts = list(pts) + [pts[0]]
    if frac <= 0 or len(pts) < 2:
        return False
    segs = [math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    total = sum(segs)
    left = total * clamp(frac)
    ctx.move_to(*pts[0])
    for i, s in enumerate(segs):
        if left >= s:
            ctx.line_to(*pts[i + 1])
            left -= s
        else:
            u = left / s if s else 0
            ctx.line_to(lerp(pts[i][0], pts[i + 1][0], u), lerp(pts[i][1], pts[i + 1][1], u))
            break
    if closed and frac >= 1:
        ctx.close_path()
    return True


def draw_poly(ctx, pts, frac=1.0, col=BONE, a=1.0, w=3, closed=False):
    if a <= 0:
        return
    stroke_style(ctx, w)
    if poly_partial(ctx, pts, frac, closed):
        src(ctx, col, a)
        ctx.stroke()


def bezier(p0, p1, p2, p3, n=80):
    out = []
    for i in range(n + 1):
        s = i / n
        m = 1 - s
        out.append((m ** 3 * p0[0] + 3 * m * m * s * p1[0] + 3 * m * s * s * p2[0] + s ** 3 * p3[0],
                    m ** 3 * p0[1] + 3 * m * m * s * p1[1] + 3 * m * s * s * p2[1] + s ** 3 * p3[1]))
    return out


def oct_pts(cx, cy, R, rot=0.0):
    """Regular octagon, flat top when rot == 0."""
    return [(cx + R * math.cos(rot + math.radians(22.5 + 45 * k)),
             cy + R * math.sin(rot + math.radians(22.5 + 45 * k))) for k in range(8)]


def oct_radius(a, R):
    """Distance from centre to a flat-top octagon edge along angle a."""
    ap = R * math.cos(math.radians(22.5))
    d = (a + math.radians(22.5)) % math.radians(45) - math.radians(22.5)
    return ap / math.cos(d)


def perimeter_points(pts, n):
    """n points spaced evenly along a closed polygon."""
    loop = list(pts) + [pts[0]]
    segs = [math.dist(loop[i], loop[i + 1]) for i in range(len(loop) - 1)]
    total = sum(segs)
    out = []
    for k in range(n):
        d = total * k / n
        i = 0
        while d > segs[i]:
            d -= segs[i]
            i += 1
        u = d / segs[i]
        out.append((lerp(loop[i][0], loop[i + 1][0], u), lerp(loop[i][1], loop[i + 1][1], u)))
    return out


def cam_pt(p, s):
    """Scene coords -> screen coords under a push-in of scale s about the frame centre."""
    return (W / 2 + (p[0] - W / 2) * s, H / 2 + (p[1] - H / 2) * s)


def uncam_pt(p, s):
    return (W / 2 + (p[0] - W / 2) / s, H / 2 + (p[1] - H / 2) / s)


class Fade:
    """Draw into a group, then shrink towards a point and/or fade (scene outros)."""

    def __init__(self, ctx, u=0.0, cx=RX, cy=RY, alpha=1.0):
        self.ctx, self.u, self.cx, self.cy, self.alpha = ctx, u, cx, cy, alpha

    def __enter__(self):
        c = self.ctx
        c.push_group()
        c.save()
        if self.u > 0:
            s = max(1 - eio(self.u), 1e-4)
            c.translate(self.cx, self.cy)
            c.scale(s, s)
            c.translate(-self.cx, -self.cy)
        return c

    def __exit__(self, *a):
        c = self.ctx
        c.restore()
        c.pop_group_to_source()
        c.paint_with_alpha(self.alpha * (1 - eo3(self.u) if self.u > 0 else 1))


# ----------------------------------------------------------------- captions
_mctx = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 8, 8))


def set_font(ctx, font, size):
    if font == LORA:
        ctx.select_font_face(LORA, cairo.FONT_SLANT_ITALIC, cairo.FONT_WEIGHT_NORMAL)
    elif font == ALICE:
        ctx.select_font_face(ALICE, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    else:
        ctx.select_font_face(ANTON, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    ctx.set_font_size(size)


def text_w(text, font, size):
    set_font(_mctx, font, size)
    return _mctx.text_extents(text).x_advance


def ink_w(text, font, size):
    set_font(_mctx, font, size)
    e = _mctx.text_extents(text.rstrip(".,"))
    return e.x_bearing, e.width


class Wd:
    def __init__(self, text, t, key=False, font=ALICE, size=None, color=None, underline=None):
        self.text, self.t, self.key, self.font = text, t, key, font
        self.size, self.color = size, color
        self.underline = key if underline is None else underline


class Group:
    """2-4 words on screen at a time. Words pop in on their voice time,
    the whole group leaves at t_out."""

    def __init__(self, lines, t_out, B=92, align="left", x=TX, cy=RY, y0=None, lh=None, keyscale=1.13, maxw=None):
        self.lines, self.t_out, self.align, self.x = lines, t_out, align, x
        font = lines[0][0].font
        if font == ALICE:      # Alice is a lighter, smaller-capped serif: start bigger, then fit
            B *= 1.12
        maxw = maxw or (580 if align == "left" else 1200)
        fit = 1.0
        for _ in range(2):
            self.items = []
            for li, line in enumerate(lines):
                xs, cx = [], 0.0
                space = B * fit * (0.24 if line[0].font == ANTON else 0.28)
                for w in line:
                    fs = (w.size or (B * keyscale if w.key else B)) * fit
                    adv = text_w(w.text, w.font, fs)
                    xs.append((w, cx, fs, adv))
                    cx += adv + space
                self.items.append((xs, cx - space))
            widest = max(lw for _, lw in self.items)
            if widest <= maxw:
                break
            fit *= maxw / widest
        B *= fit
        self.B = B
        self.lh = lh or B * (1.2 if font == ANTON else 1.12)
        n = len(lines)
        cap = CAP[font] * B * (keyscale if any(w.key for w in lines[0]) else 1)
        self.y0 = y0 if y0 is not None else cy + cap / 2 - (n - 1) * self.lh / 2
        self.t_in = min(w.t for line in lines for w in line)

    def draw(self, ctx, t):
        if t < self.t_in or (self.t_out is not None and t >= self.t_out):
            return
        q = prog(t, self.t_out - 0.15, self.t_out) if self.t_out is not None else 0
        ga, gdy = 1 - q, -10 * eo3(q)
        for li, (xs, lw) in enumerate(self.items):
            base = self.y0 + li * self.lh
            x0 = self.x - lw / 2 if self.align == "center" else self.x
            for w, ix, fs, adv in xs:
                if t < w.t:
                    continue
                e = eo3(prog(t, w.t, w.t + 0.12))
                a = e * ga
                if a <= 0:
                    continue
                dy = 12 * (1 - e) + gdy
                col = w.color or (RED if w.key else BONE)
                set_font(ctx, w.font, fs)
                src(ctx, col, a)
                ctx.move_to(x0 + ix, base + dy)
                ctx.show_text(w.text)
                if w.underline:
                    u = eo3(prog(t, w.t + 0.1, w.t + 0.4))
                    if u > 0:
                        bx, iw = ink_w(w.text, w.font, fs)
                        src(ctx, RED, a)
                        ctx.rectangle(x0 + ix + bx, base + dy + fs * 0.1, iw * u, 4)
                        ctx.fill()


# ----------------------------------------------------------------- scenes
def bg_octagon(ctx, t):
    pts = oct_pts(W / 2, H / 2, 430, math.radians(4.0 * t))
    draw_poly(ctx, pts, 1, BONE, 0.065, 2, closed=True)


# Scene 1 · 0:00-0:03 · hook -------------------------------------------------
S1_Y, S1_X0, S1_PIV, S1_L, S1_ANG, S1_R = 470, 300, 620, 500, math.radians(24), 34
S1_CAP = Group([[Wd("YOU'RE", 0.49), Wd("NOT", 0.72), Wd("LAZY.", 0.95, True)]],
               t_out=2.72, B=112, align="center", x=W / 2, cy=725)
S1_END_S = 1.04


def s1_slope_angle(t):
    return S1_ANG * (1 - eox(prog(t, 0.95, 1.15)))


def s1_center(sdist, ang):
    """Centre of the circle at path distance sdist (ground, then the slope)."""
    r = S1_R + 1.5
    gy = S1_Y - r
    # offset slope line: points p = pivot + u*dir + r*n
    d = (math.cos(ang), -math.sin(ang))
    n = (-math.sin(ang), -math.cos(ang))
    # intersection of y = gy with offset slope line
    if ang > 1e-4:
        u = (gy - (S1_Y + r * n[1])) / d[1]
        ix = S1_PIV + r * n[0] + u * d[0]
    else:
        ix = S1_PIV
    ground_len = ix - S1_X0
    if sdist <= ground_len:
        return (S1_X0 + sdist, gy)
    k = sdist - ground_len
    return (ix + k * d[0], gy + k * d[1])


def s1_dist(t):
    if t < 0.10:
        return 120
    if t < 0.55:   # rolls up the slope, losing speed
        return lerp(120, 470, eo3(prog(t, 0.10, 0.55)))
    if t < 0.95:   # slides back down
        return lerp(470, 230, eiq(prog(t, 0.55, 0.95)) * 0.6 + 0.4 * eio(prog(t, 0.55, 0.95)))
    return lerp(230, 560, eo3(prog(t, 1.05, 2.3)))   # free to roll once the slope is flat


def scene1(ctx, t):
    ang = s1_slope_angle(t)
    flash = 1 - prog(t, 1.3, 2.2) if t >= 0.95 else 0
    lcol = lerpc(BONE, RED, flash)
    end = (S1_PIV + S1_L * math.cos(ang), S1_Y - S1_L * math.sin(ang))
    pts = [(S1_X0, S1_Y), (S1_PIV, S1_Y), end]
    draw_frac = eo3(prog(t, 0.0, 0.35))
    out = eio(prog(t, 2.55, 2.95))
    c = s1_center(s1_dist(t), ang)
    # outro: line retracts into the circle, circle moves to scene 2's place and grows
    tgt = uncam_pt((RX, RY), S1_END_S)
    if out > 0:
        # retract line toward circle contact point
        cx = c[0]
        a_pts = [(lerp(S1_X0, cx, out), S1_Y), (lerp(end[0], cx, out), S1_Y)]
        draw_poly(ctx, a_pts, 1, lcol, 1 - out, 3)
    else:
        draw_poly(ctx, pts, draw_frac, lcol, 1, 3)
    r = lerp(S1_R, 120 / S1_END_S, out)
    cc = (lerp(c[0], tgt[0], out), lerp(c[1], tgt[1], out))
    pop = eox(prog(t, 0.05, 0.35))
    if pop > 0:
        stroke_style(ctx, 3)
        ctx.new_sub_path()
        ctx.arc(cc[0], cc[1], r * (0.6 + 0.4 * pop), 0, 2 * math.pi)
        src(ctx, BONE, pop)
        ctx.stroke()
        # rolling mark (fades out during the outro)
        rot = s1_dist(t) / S1_R
        ma = (1 - out) * pop
        if ma > 0:
            ctx.arc(cc[0] + (r - 9) * math.cos(rot), cc[1] + (r - 9) * math.sin(rot), 3.5, 0, 2 * math.pi)
            src(ctx, BONE, ma)
            ctx.fill()
    S1_CAP.draw(ctx, t)


# Scene 2 · 0:03-0:06 ------------------------------------------------------
S2_CAP = Group([[Wd("YOUR", 3.21), Wd("MIND", 3.47), Wd("IS", 3.87)],
                [Wd("UNTRAINED.", 4.30, True)]], t_out=5.75)
_rnd = random.Random(7)
S2_FRAG = [(_rnd.uniform(26, 95), _rnd.uniform(-0.7, 0.7), _rnd.uniform(-25, 25)) for _ in range(8)]


def scene2(ctx, t):
    R = 120
    out = prog(t, 5.62, 6.0)
    with Fade(ctx, out, RX, RY):
        shake = 0
        if 3.95 < t < 4.3:
            shake = 2.5 * prog(t, 3.95, 4.3)
        ox = shake * math.sin(t * 90)
        oy = shake * math.cos(t * 77)
        brk = prog(t, 4.30, 5.15)
        drift = eox(brk)
        crack = eo3(prog(t, 3.35, 4.25))
        for k in range(8):
            a0 = math.radians(-80 + 45 * k)
            a1 = a0 + math.radians(45)
            am = (a0 + a1) / 2
            dist, rot, lift = S2_FRAG[k]
            dx = math.cos(am) * dist * drift
            dy = math.sin(am) * dist * drift + lift * drift
            fc = (RX + ox + dx, RY + oy + dy)
            ctx.save()
            ctx.translate(fc[0], fc[1])
            ctx.rotate(rot * drift)
            stroke_style(ctx, 3)
            gap = 3 * drift
            gx, gy = math.cos(am) * gap, math.sin(am) * gap
            ctx.new_sub_path()
            ctx.arc(gx, gy, R, a0, a1)
            src(ctx, BONE)
            ctx.stroke()
            if brk > 0:
                ctx.move_to(gx + R * math.cos(a0), gy + R * math.sin(a0))
                ctx.line_to(gx, gy)
                ctx.line_to(gx + R * math.cos(a1), gy + R * math.sin(a1))
                src(ctx, BONE, min(1, brk * 4))
                ctx.stroke()
            ctx.restore()
            # crack lines before the break
            if brk == 0 and crack > 0:
                stroke_style(ctx, 2)
                ctx.move_to(RX + ox, RY + oy)
                ctx.line_to(RX + ox + R * crack * math.cos(a0), RY + oy + R * crack * math.sin(a0))
                src(ctx, GREY)
                ctx.stroke()
    S2_CAP.draw(ctx, t)


# Scene 3 · 0:06-0:12 ------------------------------------------------------
S3_CAPS = [
    Group([[Wd("EVERY", 6.35), Wd("DAY", 6.72)], [Wd("YOU", 6.95), Wd("LOSE", 7.08)]], t_out=7.35),
    Group([[Wd("THE", 7.35, True), Wd("SAME", 7.47, True)], [Wd("FIGHT.", 7.80, True)]], t_out=8.65),
    Group([[Wd("FIVE", 8.65), Wd("MORE", 9.09)], [Wd("MINUTES.", 9.28, True)]], t_out=9.88),
    Group([[Wd("ONE", 9.88), Wd("MORE", 10.21)], [Wd("VIDEO.", 10.40, True)]], t_out=10.83),
    Group([[Wd("I'LL", 10.83), Wd("START", 11.09)], [Wd("MONDAY.", 11.36, True)]], t_out=11.82),
]
S3_SQ, S3_GROUND = 120, 790
S3_DROP = [6.25, 6.85, 7.45]
S3_KNOCK = {2: (9.28, 1), 1: (10.40, 1), 0: (11.36, 1)}


def scene3(ctx, t):
    out = prog(t, 11.78, 12.0)
    with Fade(ctx, out, RX, 600):
        # ground
        g = eo3(prog(t, 6.0, 6.35))
        draw_poly(ctx, [(RX, S3_GROUND), (RX - 210 * g, S3_GROUND)], 1, GREY, 1, 2)
        draw_poly(ctx, [(RX, S3_GROUND), (RX + 210 * g, S3_GROUND)], 1, GREY, 1, 2)
        for i in range(3):
            td = S3_DROP[i]
            if t < td:
                continue
            e = eox(prog(t, td, td + 0.4))
            cx, cy = RX, S3_GROUND - S3_SQ / 2 - i * S3_SQ
            cy -= 70 * (1 - e)
            rot, alpha, col = 0.0, e, BONE
            tk, d = S3_KNOCK[i]
            if t >= tk:
                u = prog(t, tk, tk + 0.75)
                cx += d * 230 * eo3(u)
                cy += 90 * eiq(u)
                rot = d * 1.1 * eo3(u)
                alpha = 1 - eo3(u)
                col = lerpc(BONE, GREY, min(1, u * 3))
            ctx.save()
            ctx.translate(cx, cy)
            ctx.rotate(rot)
            h = S3_SQ / 2 - 1.5
            sq = [(-h, -h), (h, -h), (h, h), (-h, h)]
            draw_poly(ctx, sq, e, col, alpha, 3, closed=True)
            ctx.restore()
        # the loop: red circular arrow on "Monday"
        u = eo3(prog(t, 11.36, 11.76))
        if u > 0:
            R, c0 = 255, (RX, 600)
            a0 = math.radians(-60)
            a1 = a0 + math.radians(310) * u
            stroke_style(ctx, 3)
            ctx.new_sub_path()
            ctx.arc(c0[0], c0[1], R, a0, a1)
            src(ctx, RED)
            ctx.stroke()
            # arrowhead at the moving tip
            tip = (c0[0] + R * math.cos(a1), c0[1] + R * math.sin(a1))
            tang = a1 + math.pi / 2
            hl = 26
            for s in (-1, 1):
                ang = tang + math.pi + s * math.radians(32)
                ctx.move_to(*tip)
                ctx.line_to(tip[0] + hl * math.cos(ang), tip[1] + hl * math.sin(ang))
            ctx.stroke()
    for g_ in S3_CAPS:
        g_.draw(ctx, t)


# Scene 4 · 0:12-0:16 ------------------------------------------------------
S4_CAPS = [
    Group([[Wd("DISCIPLINE", 12.20)], [Wd("ISN'T", 12.92), Wd("A", 13.15), Wd("FEELING.", 13.26)]], t_out=14.11),
    Group([[Wd("IT'S", 14.11), Wd("A", 14.36)], [Wd("STRUCTURE.", 14.45, True)]], t_out=15.75),
]
_rnd = random.Random(11)
S4_GRID = [(RX - 210 + 84 * (i % 6), RY - 168 + 84 * (i // 6)) for i in range(30)]
S4_FREE = []
while len(S4_FREE) < 30:
    p = (_rnd.uniform(820, 1290), _rnd.uniform(310, 780))
    if all(math.dist(p, q) > 58 for q in S4_FREE):
        S4_FREE.append(p)
S4_PH = [(_rnd.uniform(0, 6.3), _rnd.uniform(0, 6.3), _rnd.uniform(0.6, 1.3)) for _ in range(30)]
S4_ORDER = list(range(30))
_rnd.shuffle(S4_ORDER)
S4_SNAP = 14.45


def s4_dot(i, t):
    f = S4_FREE[S4_ORDER[i]]
    p1, p2, sp = S4_PH[i]
    fl = (f[0] + 16 * math.sin(t * sp + p1), f[1] + 16 * math.cos(t * sp * 0.8 + p2))
    burst = eox(prog(t, 12.0, 12.55))
    fl = (lerp(RX, fl[0], burst), lerp(560, fl[1], burst))
    s = eox(prog(t, S4_SNAP, S4_SNAP + 0.35))
    return (lerp(fl[0], S4_GRID[i][0], s), lerp(fl[1], S4_GRID[i][1], s))


def scene4(ctx, t):
    lines_a = 1 - prog(t, 15.65, 15.95)
    # grid lines draw between the dots
    for r in range(5):
        u = eo3(prog(t, 14.62 + r * 0.05, 15.0 + r * 0.05))
        y = S4_GRID[r * 6][1]
        draw_poly(ctx, [(S4_GRID[0][0], y), (S4_GRID[5][0], y)], u, RED, lines_a, 2)
    for c in range(6):
        u = eo3(prog(t, 14.78 + c * 0.05, 15.15 + c * 0.05))
        x = S4_GRID[c][0]
        draw_poly(ctx, [(x, S4_GRID[0][1]), (x, S4_GRID[24][1])], u, RED, lines_a, 2)
    for i in range(30):
        p = s4_dot(i, t)
        ctx.arc(p[0], p[1], 7, 0, 2 * math.pi)
        src(ctx, BONE, min(1, prog(t, 12.0, 12.15) * 1))
        ctx.fill()
    for g_ in S4_CAPS:
        g_.draw(ctx, t)


# Scene 5 · 0:16-0:19 · title -----------------------------------------------
S5_R = 390
S5_OCT = oct_pts(W / 2, H / 2, S5_R)
S5_PERIM = perimeter_points([S5_OCT[5], S5_OCT[6], S5_OCT[7], S5_OCT[0], S5_OCT[1], S5_OCT[2], S5_OCT[3], S5_OCT[4]], 30)
S5_CAP = Group([[Wd("THE", 17.31), Wd("COMPLETE", 17.48)], [Wd("FIGHTER.", 17.95, True)]],
               t_out=18.68, B=112, align="center", x=W / 2, cy=505, maxw=640)
S5_MORPH = (18.62, 19.0)
S6_R = 170


def s5_morph_pts(u, s_end=1.04):
    """Octagon (scene 5 coords) -> scene 6 circle (expressed in scene 5 coords)."""
    tc = uncam_pt((RX, RY), s_end)
    rr = S6_R / s_end
    pts = []
    for i in range(96):
        a = -math.pi / 2 + 2 * math.pi * i / 96
        ro = oct_radius(a, S5_R)
        cx, cy = lerp(W / 2, tc[0], u), lerp(H / 2, tc[1], u)
        r = lerp(ro, rr, u)
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def scene5(ctx, t):
    # dots from the scene 4 grid fly onto the octagon's outline
    mv = eio(prog(t, 16.0, 16.6))
    da = 1 - prog(t, 16.45, 16.85)
    if da > 0:
        for i in range(30):
            g = cam_pt(S4_GRID[i], 1.04)
            p = (lerp(g[0], S5_PERIM[i][0], mv), lerp(g[1], S5_PERIM[i][1], mv))
            ctx.arc(p[0], p[1], 7, 0, 2 * math.pi)
            src(ctx, BONE, da)
            ctx.fill()
    m = eio(prog(t, *S5_MORPH))
    if m <= 0:
        order = [S5_OCT[5], S5_OCT[6], S5_OCT[7], S5_OCT[0], S5_OCT[1], S5_OCT[2], S5_OCT[3], S5_OCT[4]]
        draw_poly(ctx, order, eo3(prog(t, 16.25, 17.05)), RED, 1, 3, closed=True)
    else:
        draw_poly(ctx, s5_morph_pts(m), 1, lerpc(RED, BONE, m), 1, 3, closed=True)
    S5_CAP.draw(ctx, t)
    # tagline
    ta = eo3(prog(t, 18.25, 18.45)) * (1 - prog(t, 18.5, 18.68))
    if ta > 0:
        txt = "MIND · FIGHT · BODY"
        set_font(ctx, ANTON, 38)
        # letter-spaced
        sp = 7
        widths = [ctx.text_extents(ch).x_advance for ch in txt]
        tw = sum(widths) + sp * (len(txt) - 1)
        x = W / 2 - tw / 2
        y = 690 + 10 * (1 - ta)
        src(ctx, GREY, ta)
        for ch, wch in zip(txt, widths):
            ctx.move_to(x, y)
            ctx.show_text(ch)
            x += wch + sp


# Scene 6 · 0:19-0:23 ------------------------------------------------------
S6_CAP = Group([[Wd("6", 19.46), Wd("WEEKS", 19.92), Wd("·", 19.92)], [Wd("MIND", 20.70, True)]], t_out=22.65)
S6_TICKS = [19.75 + 0.45 * k for k in range(6)]


def scene6(ctx, t):
    out = prog(t, 22.6, 23.0)
    with Fade(ctx, out, RX, RY):
        R = S6_R
        for k in range(6):
            tk = S6_TICKS[k]
            if t >= tk:
                u = eox(prog(t, tk, tk + 0.12))
                a0 = -math.pi / 2 + k * math.pi / 3
                ctx.move_to(RX, RY)
                ctx.arc(RX, RY, R, a0, a0 + math.pi / 3 * u)
                ctx.close_path()
                src(ctx, RED, 0.92)
                ctx.fill()
        for k in range(6):
            u = eo3(prog(t, 19.05 + 0.04 * k, 19.45 + 0.04 * k))
            a = -math.pi / 2 + k * math.pi / 3
            filled = t >= S6_TICKS[k] or t >= S6_TICKS[k - 1] + 0.12
            draw_poly(ctx, [(RX, RY), (RX + R * math.cos(a), RY + R * math.sin(a))], u, BG if filled else GREY, 1, 2)
        stroke_style(ctx, 3)
        ctx.new_sub_path()
        ctx.arc(RX, RY, R, 0, 2 * math.pi)
        src(ctx, BONE)
        ctx.stroke()
        # clock hand, ticking
        done = sum(1 for tk in S6_TICKS if t >= tk)
        prev = done - 1 if done else 0
        if done:
            tk = S6_TICKS[done - 1]
            a = -math.pi / 2 + math.pi / 3 * (prev + eox(prog(t, tk, tk + 0.12)))
        else:
            a = -math.pi / 2
        ha = eo3(prog(t, 19.2, 19.5))
        draw_poly(ctx, [(RX, RY), (RX + R * 0.78 * math.cos(a), RY + R * 0.78 * math.sin(a))], 1, BONE, ha, 3)
        ctx.arc(RX, RY, 7, 0, 2 * math.pi)
        src(ctx, BONE, ha)
        ctx.fill()
    S6_CAP.draw(ctx, t)


# Scene 7 · 0:23-0:28 ------------------------------------------------------
S7_HEAD = Group([[Wd("20", 23.18), Wd("ROUNDS", 23.62), Wd("·", 23.62)]], t_out=27.65, y0=490)
S7_KEYS = [Group([[Wd("FEAR", 24.60, True)]], t_out=25.50, y0=610),
           Group([[Wd("EGO", 25.50, True)]], t_out=26.55, y0=610),
           Group([[Wd("PRESSURE", 26.55, True)]], t_out=27.65, y0=610)]
S7_SQ, S7_GAP = 54, 20


def scene7(ctx, t):
    out = prog(t, 27.6, 28.0)
    with Fade(ctx, out, RX, 560):
        # 20 rounds: 4 rows of 5
        gw = 5 * S7_SQ + 4 * S7_GAP
        gh = 4 * S7_SQ + 3 * S7_GAP
        ox, oy = RX - gw / 2, RY - gh / 2
        gone = eio(prog(t, 24.2, 24.5))
        if gone < 1:
            for i in range(20):
                r, c = divmod(i, 5)
                x, y = ox + c * (S7_SQ + S7_GAP), oy + r * (S7_SQ + S7_GAP)
                cx, cy = x + S7_SQ / 2, y + S7_SQ / 2
                s = (S7_SQ / 2) * (1 - gone)
                d = eo3(prog(t, 23.0 + 0.01 * i, 23.3 + 0.01 * i))
                sq = [(cx - s, cy - s), (cx + s, cy - s), (cx + s, cy + s), (cx - s, cy + s)]
                draw_poly(ctx, sq, d, GREY, 1 - gone, 2, closed=True)
                tl = 23.2 + 0.045 * i
                if t >= tl:
                    la = eo3(prog(t, tl, tl + 0.08)) * (1 - gone)
                    ctx.rectangle(cx - s, cy - s, 2 * s, 2 * s)
                    src(ctx, BONE, la)
                    ctx.fill()
        # the triangle
        if t >= 24.35:
            R, cy0 = 120, 565
            tri = [(0, -R), (R * math.sin(math.radians(120)), -R * math.cos(math.radians(120))),
                   (-R * math.sin(math.radians(120)), -R * math.cos(math.radians(120)))]
            # fear: tremble
            jx = jy = 0
            if t >= 24.6:
                amp = 5.5 * (1 - 0.7 * prog(t, 24.6, 25.5))
                if t > 25.5:
                    amp = 0
                jx = amp * math.sin(t * 97) * math.cos(t * 41)
                jy = amp * math.cos(t * 83) * math.sin(t * 29)
            # ego: inflate
            sc = 1 + 0.45 * eox(prog(t, 25.5, 26.0))
            # pressure: lines press in, triangle compresses slightly and holds
            pr = eox(prog(t, 26.55, 26.95))
            bump = math.sin(math.pi * prog(t, 26.75, 27.2))
            sx = sc * (1 - 0.04 * bump)
            pts = [(RX + jx + x * sx, cy0 + jy + y * sc) for x, y in tri]
            draw_poly(ctx, pts, eo3(prog(t, 24.35, 24.7)), RED, 1, 3, closed=True)
            if pr > 0:
                half = R * math.sin(math.radians(120)) * sc
                for side in (-1, 1):
                    x = lerp(RX + side * 270, RX + side * (half * (1 - 0.04 * bump) + 4), pr)
                    draw_poly(ctx, [(x, 380), (x, 720)], 1, BONE, pr, 3)
    S7_HEAD.draw(ctx, t)
    for g_ in S7_KEYS:
        g_.draw(ctx, t)


# Scene 8 · 0:28-0:33 ------------------------------------------------------
S8_CAP = Group([[Wd("12", 28.20), Wd("WEEKS", 28.63), Wd("·", 28.63)], [Wd("BODY", 31.40, True)]], t_out=32.65)
S8_X0, S8_Y0, S8_W, S8_H = 830, 860, 40, 40
S8_STAIRS = [(S8_X0 - 60, S8_Y0), (S8_X0, S8_Y0)]
for _i in range(12):
    S8_STAIRS.append((S8_X0 + _i * S8_W, S8_Y0 - (_i + 1) * S8_H))
    S8_STAIRS.append((S8_X0 + (_i + 1) * S8_W, S8_Y0 - (_i + 1) * S8_H))
S8_HOP0, S8_HOPDT = 29.30, 0.155
S8_BAR = (1150, 1310, 215)
S8_HANG = (1230, 300)


def s8_dot(t):
    r = 10
    spots = [(S8_X0 - 30, S8_Y0 - r - 1.5)] + [(S8_X0 + i * S8_W + S8_W / 2, S8_Y0 - (i + 1) * S8_H - r - 1.5)
                                                 for i in range(12)]
    pos = spots[0]
    for i in range(12):
        ts = S8_HOP0 + i * S8_HOPDT
        if t >= ts:
            u = prog(t, ts, ts + 0.13)
            a, b = spots[i], spots[i + 1]
            pos = (lerp(a[0], b[0], eio(u)), lerp(a[1], b[1], eio(u)) - 22 * math.sin(math.pi * u))
    if t >= 31.55:
        u = eio(prog(t, 31.55, 31.9))
        top = spots[-1]
        pos = (lerp(top[0], S8_HANG[0], u), lerp(top[1], S8_HANG[1], u) - 40 * math.sin(math.pi * u))
    return pos


def scene8(ctx, t):
    out = prog(t, 32.6, 33.0)
    with Fade(ctx, out, RX, RY):
        draw_poly(ctx, S8_STAIRS, eio(prog(t, 28.05, 29.25)), BONE, 1, 3)
        # bar + arms on "bodyweight"
        bu = eo3(prog(t, 31.40, 31.70))
        if bu > 0:
            x0, x1, y = S8_BAR
            draw_poly(ctx, [((x0 + x1) / 2, y), (lerp((x0 + x1) / 2, x0, bu), y)], 1, BONE, 1, 3)
            draw_poly(ctx, [((x0 + x1) / 2, y), (lerp((x0 + x1) / 2, x1, bu), y)], 1, BONE, 1, 3)
        stick_s8(ctx, t)
    S8_CAP.draw(ctx, t)


# Scene 9 · 0:33-0:37 ------------------------------------------------------
S9_CAPS = [Group([[Wd("TICK", 34.60), Wd("IT.", 34.98)]], t_out=35.30),
           Group([[Wd("WRITE", 35.30), Wd("IN", 35.65), Wd("IT.", 35.80)]], t_out=36.10),
           Group([[Wd("TRACK", 36.10, True), Wd("IT.", 36.55, True)]], t_out=36.75)]
S9_PAGE = [(820, 250), (1290, 250), (1290, 830), (820, 830)]
S9_BOX = (880, 318, 84)
S9_WRITE = []
for _i in range(241):
    s = _i / 240
    S9_WRITE.append((888 + 338 * s + 9 * math.cos(2 * math.pi * 7.5 * s),
                     548 - 16 * math.sin(2 * math.pi * 7.5 * s) * (0.55 + 0.45 * math.sin(2 * math.pi * 1.2 * s + 0.8))
                     - 6 * math.sin(2 * math.pi * 0.5 * s)))
S9_BAR = (880, 690, 360, 34)


def scene9(ctx, t):
    out = prog(t, 36.68, 37.0)
    with Fade(ctx, out, 1055, RY):
        draw_poly(ctx, S9_PAGE, eio(prog(t, 33.0, 33.7)), GREY, 1, 2, closed=True)
        x, y, s = S9_BOX
        draw_poly(ctx, [(x, y), (x + s, y), (x + s, y + s), (x, y + s)], eo3(prog(t, 33.3, 33.75)), BONE, 1, 3, closed=True)
        for i, (lw, yy) in enumerate([(240, y + 26), (160, y + 58)]):
            u = eo3(prog(t, 33.5 + 0.08 * i, 33.9 + 0.08 * i))
            draw_poly(ctx, [(x + s + 30, yy), (x + s + 30 + lw, yy)], u, GREY, 1, 2)
        # tick
        draw_poly(ctx, [(x + 16, y + 44), (x + 36, y + 64), (x + 70, y + 18)], eo3(prog(t, 34.60, 34.92)), RED, 1, 4)
        # writing line
        draw_poly(ctx, [(880, 580), (1230, 580)], eo3(prog(t, 33.6, 34.0)), GREY, 1, 2)
        draw_poly(ctx, S9_WRITE, eio(prog(t, 35.30, 35.95)), BONE, 1, 3)
        # progress bar
        bx, by, bw, bh = S9_BAR
        draw_poly(ctx, [(bx, by), (bx + bw, by), (bx + bw, by + bh), (bx, by + bh)], eo3(prog(t, 33.8, 34.25)), BONE, 1, 2, closed=True)
        f = eio(prog(t, 36.10, 36.55))
        if f > 0:
            ctx.rectangle(bx + 5, by + 5, (bw - 10) * f, bh - 10)
            src(ctx, RED)
            ctx.fill()
        ca = eo3(prog(t, 34.0, 34.3))
        if ca > 0:
            txt = f"{round(100 * f)}%"
            set_font(ctx, ANTON, 34)
            e = ctx.text_extents(txt)
            ctx.move_to(bx + bw - e.x_advance, by - 16)
            src(ctx, GREY, ca)
            ctx.show_text(txt)
    for g_ in S9_CAPS:
        g_.draw(ctx, t)


# Scene 10 · 0:37-0:41 ------------------------------------------------------
_q = 64
S10_QUOTE = Group([[Wd("“I’m", 37.25, font=LORA), Wd("far", 37.42, font=LORA), Wd("from", 37.58, font=LORA),
                    Wd("where", 37.75, font=LORA)],
                   [Wd("I", 37.95, font=LORA), Wd("started.”", 38.10, font=LORA)]],
                  t_out=40.72, B=_q, lh=82, cy=505)
S10_SIG = Group([[Wd("— GESHV1", 39.00, True, size=42, underline=False)]], t_out=40.72, B=42,
                y0=S10_QUOTE.y0 + 82 + 78)
S10_BAR = (955, 1145, 330)
S10_HANG, S10_TOP = 600, 362


def s10_y(t):
    if t < 37.4:
        return S10_HANG
    if t < 38.7:   # struggle: slow, shaky, never makes it
        return lerp(S10_HANG, 470, eio(prog(t, 37.4, 38.7)))
    if t < 39.1:   # falls back
        return lerp(470, S10_HANG, eiq(prog(t, 38.7, 39.05)))
    for k in range(3):   # three clean reps
        t0 = 39.15 + 0.5 * k
        if t0 <= t < t0 + 0.5:
            u = t - t0
            if u < 0.22:
                return lerp(S10_HANG, S10_TOP, eio(u / 0.22))
            if u < 0.26:
                return S10_TOP
            return lerp(S10_TOP, S10_HANG, eio((u - 0.26) / 0.24))
    return S10_HANG


def scene10(ctx, t):
    out = prog(t, 40.68, 41.0)
    with Fade(ctx, out, RX, 460):
        x0, x1, by = S10_BAR
        u = eo3(prog(t, 37.0, 37.35))
        mid = (x0 + x1) / 2
        draw_poly(ctx, [(mid, by), (lerp(mid, x0, u), by)], 1, BONE, 1, 3)
        draw_poly(ctx, [(mid, by), (lerp(mid, x1, u), by)], 1, BONE, 1, 3)
        y = s10_y(t)
        shake = 0
        if 37.6 < t < 38.75:
            shake = 3.2 * prog(t, 37.6, 38.6) * math.sin(t * 63)
        cx = mid + shake
        da = eo3(prog(t, 37.1, 37.4))
        stick_s10(ctx, t, cx, y, da)
    S10_QUOTE.draw(ctx, t)
    S10_SIG.draw(ctx, t)


# Scene 11 · 0:41-0:44 ------------------------------------------------------
S11_CAP = Group([[Wd("RESULTS", 42.35, True)], [Wd("OR", 42.90), Wd("EXCUSES.", 43.15)]], t_out=43.7)
S11_NODE = (975, 610)
S11_UP = bezier(S11_NODE, (1080, 610), (1160, 330), (1300, 300))
S11_DN = bezier(S11_NODE, (1080, 610), (1160, 780), (1300, 790))


def scene11(ctx, t):
    out = prog(t, 43.66, 44.0)
    with Fade(ctx, out, RX, RY):
        draw_poly(ctx, [(800, S11_NODE[1]), S11_NODE], eo3(prog(t, 41.05, 41.6)), BONE, 1, 3)
        if t >= 41.4:
            ctx.arc(S11_NODE[0], S11_NODE[1], 6, 0, 2 * math.pi)
            src(ctx, BONE, eo3(prog(t, 41.4, 41.6)))
            ctx.fill()
        # excuses: grey, fades out
        da = 1 - eio(prog(t, 43.15, 43.6))
        draw_poly(ctx, S11_DN, eo3(prog(t, 42.25, 42.85)), GREY, da, 3)
        # results: red, glowing, goes up
        u = eo3(prog(t, 42.25, 42.85))
        if u > 0:
            glow = 0.5 + 0.5 * eo3(prog(t, 42.35, 42.9))
            for w, a in ((22, 0.06), (14, 0.10), (8, 0.16)):
                draw_poly(ctx, S11_UP, u, RED, a * glow, w)
            draw_poly(ctx, S11_UP, u, RED, 1, 3)
            if u > 0.97:
                tip, prev = S11_UP[-1], S11_UP[-4]
                ang = math.atan2(tip[1] - prev[1], tip[0] - prev[0])
                stroke_style(ctx, 3)
                for s in (-1, 1):
                    a2 = ang + math.pi + s * math.radians(30)
                    ctx.move_to(*tip)
                    ctx.line_to(tip[0] + 22 * math.cos(a2), tip[1] + 22 * math.sin(a2))
                src(ctx, RED, prog(t, 42.75, 42.85) if t < 42.85 else 1)
                ctx.stroke()
    S11_CAP.draw(ctx, t)


# Scene 12 · 0:44-0:48 · call to action ------------------------------------
S12_TITLE = Group([[Wd("THE", 44.15), Wd("COMPLETE", 44.32)], [Wd("FIGHTER", 44.79)]],
                  t_out=45.40, B=112, align="center", x=W / 2, cy=520, maxw=640)
S12_PRICE_T = 45.40
S12_PRICE_Y = 585
S12_LINE_Y = 735
S12_WEEK = Group([[Wd("THIS", 46.30), Wd("WEEK", 46.48)]], t_out=47.28, B=88, align="center", x=W / 2, y0=S12_LINE_Y, maxw=520)
S12_ARROW_W = 60
S12_BIO = Group([[Wd("LINK", 47.28), Wd("IN", 47.71), Wd("BIO", 47.85, True)]], t_out=None, B=88,
                align="center", x=W / 2 - S12_ARROW_W / 2, y0=S12_LINE_Y, maxw=520)


def scene12(ctx, t):
    order = [S5_OCT[5], S5_OCT[6], S5_OCT[7], S5_OCT[0], S5_OCT[1], S5_OCT[2], S5_OCT[3], S5_OCT[4]]
    draw_poly(ctx, order, eo3(prog(t, 44.0, 44.6)), RED, 1, 3, closed=True)
    S12_TITLE.draw(ctx, t)
    # price: €12 big, €19 crossed out beside it
    if t >= S12_PRICE_T:
        e = eo3(prog(t, S12_PRICE_T, S12_PRICE_T + 0.14))
        fs = 230
        set_font(ctx, ANTON, fs)
        ext = ctx.text_extents("€12")
        px = W / 2 - ext.x_advance / 2 + 10
        py = S12_PRICE_Y + 14 * (1 - e)
        src(ctx, RED, e)
        ctx.move_to(px, py)
        ctx.show_text("€12")
        u = eo3(prog(t, S12_PRICE_T + 0.1, S12_PRICE_T + 0.4))
        ctx.rectangle(px + ext.x_bearing, py + 22, ext.width * u, 5)
        ctx.fill()
        # old price
        oe = eo3(prog(t, S12_PRICE_T + 0.2, S12_PRICE_T + 0.34))
        set_font(ctx, ANTON, 72)
        oext = ctx.text_extents("€19")
        ox = px - 40 - oext.x_advance
        oy = S12_PRICE_Y - 6 + 10 * (1 - oe)
        src(ctx, GREY, oe)
        ctx.move_to(ox, oy)
        ctx.show_text("€19")
        cu = eo3(prog(t, S12_PRICE_T + 0.35, S12_PRICE_T + 0.6))
        if cu > 0:
            ccy = oy - oext.height * 0.42
            draw_poly(ctx, [(ox - 6, ccy + 10), (ox - 6 + (oext.x_advance + 12) * cu, ccy + 10 - 20 * cu)], 1, BONE, oe, 3)
    S12_WEEK.draw(ctx, t)
    S12_BIO.draw(ctx, t)
    # arrow: draws upward from under the price, then pulses twice
    au = eo3(prog(t, 47.90, 48.22))
    if au > 0:
        xs, lw = S12_BIO.items[0]
        set_font(ctx, ANTON, 230)
        pr_right = W / 2 - ctx.text_extents("€12").x_advance / 2 + 10 + ctx.text_extents("€12").x_advance
        ax = max(S12_BIO.x + lw / 2 + 38, pr_right + 48)
        y_bot, y_top = S12_LINE_Y + 4, 420
        lift = 0
        glow = 0
        for p0 in (48.22, 48.46):
            p = prog(t, p0, p0 + 0.24)
            if 0 < p < 1:
                lift = 14 * math.sin(math.pi * p)
                glow = math.sin(math.pi * p)
        ytip = lerp(y_bot, y_top, au) - lift
        for w, a in ((16, 0.10 * glow), (9, 0.16 * glow)):
            if a > 0:
                draw_poly(ctx, [(ax, y_bot), (ax, ytip)], 1, RED, a, w)
        draw_poly(ctx, [(ax, y_bot), (ax, ytip)], 1, RED, 1, 4)
        stroke_style(ctx, 4)
        hl = 30 * min(1, au * 1.4)
        ctx.move_to(ax - hl * 0.8, ytip + hl * 0.8)
        ctx.line_to(ax, ytip)
        ctx.line_to(ax + hl * 0.8, ytip + hl * 0.8)
        src(ctx, RED)
        ctx.stroke()


# ----------------------------------------------------------------- stickman
# Angles are absolute, in degrees: 0 points down, 90 right, 180 up, -90 left.
STICK = dict(torso=70, neck=8, head=20, uarm=40, farm=38, thigh=48, shin=46)
POSES = {
    "stand":   dict(torso=180, uaL=-14, faL=-6, uaR=14, faR=6, thL=-7, shL=-3, thR=7, shR=3),
    "push":    dict(torso=128, uaL=100, faL=88, uaR=94, faR=84, thL=-42, shL=-30, thR=28, shR=-4),
    "strain":  dict(torso=150, uaL=108, faL=96, uaR=102, faR=92, thL=-22, shL=-12, thR=38, shR=18),
    "lift":    dict(torso=176, uaL=150, faL=168, uaR=140, faR=160, thL=-9, shL=-4, thR=9, shR=4),
    "shrug":   dict(torso=180, uaL=-60, faL=-140, uaR=60, faR=140, thL=-7, shL=-3, thR=7, shR=3),
    "slump":   dict(torso=158, uaL=6, faL=4, uaR=14, faR=10, thL=-4, shL=-2, thR=10, shR=6),
    "sit":     dict(torso=168, uaL=30, faL=60, uaR=40, faR=70, thL=88, shL=40, thR=96, shR=50),
    "lie":     dict(torso=-90, uaL=80, faL=90, uaR=95, faR=92, thL=88, shL=92, thR=94, shR=86),
    "head":    dict(torso=176, uaL=-140, faL=150, uaR=140, faR=-150, thL=-8, shL=-3, thR=8, shR=3),
    "guard":   dict(torso=170, uaL=38, faL=150, uaR=18, faR=170, thL=-24, shL=-10, thR=22, shR=4),
    "punch":   dict(torso=160, uaL=40, faL=150, uaR=92, faR=90, thL=-30, shL=-14, thR=24, shR=6),
    "cower":   dict(torso=165, uaL=60, faL=170, uaR=50, faR=175, thL=-14, shL=8, thR=14, shR=-8),
    "puff":    dict(torso=186, uaL=-48, faL=48, uaR=48, faR=-48, thL=-18, shL=-12, thR=18, shR=12),
    "brace":   dict(torso=174, uaL=36, faL=160, uaR=30, faR=165, thL=-38, shL=-6, thR=38, shR=6),
    "meditate": dict(torso=180, uaL=-28, faL=-72, uaR=28, faR=72, thL=-78, shL=72, thR=78, shR=-72),
    "victory": dict(torso=180, uaL=-150, faL=-162, uaR=150, faR=162, thL=-9, shL=-4, thR=9, shR=4),
    "point":   dict(torso=180, uaL=-14, faL=-6, uaR=146, faR=158, thL=-8, shL=-3, thR=8, shR=3),
    "run1":    dict(torso=160, uaL=55, faL=140, uaR=-40, faR=20, thL=62, shL=-6, thR=-28, shR=-70),
    "run2":    dict(torso=160, uaL=-40, faL=20, uaR=55, faR=140, thL=-28, shL=-70, thR=62, shR=-6),
    "step1":   dict(torso=168, uaL=30, faL=60, uaR=-25, faR=-10, thL=70, shL=0, thR=-12, shR=-8),
    "step2":   dict(torso=168, uaL=-25, faL=-10, uaR=30, faR=60, thL=-12, shL=-8, thR=70, shR=0),
    "hang":    dict(torso=180, uaL=-150, faL=-160, uaR=150, faR=160, thL=-5, shL=6, thR=5, shR=-6),
}


def dirv(a):
    r = math.radians(a)
    return (math.sin(r), math.cos(r))


def lerp_pose(p, q, u):
    return {k: p[k] + (q[k] - p[k]) * u for k in p}


def pose_at(t, keys):
    """keys: [(time, pose_name_or_dict), ...]; eased blend between keys."""
    def P(x):
        return POSES[x] if isinstance(x, str) else x
    if t <= keys[0][0]:
        return P(keys[0][1])
    for (t0, a), (t1, b) in zip(keys, keys[1:]):
        if t < t1:
            return lerp_pose(P(a), P(b), eio(prog(t, t0, t1)))
    return P(keys[-1][1])


def two_bone(S, T, l1, l2, bend):
    dx, dy = T[0] - S[0], T[1] - S[1]
    d = max(1e-3, min(math.hypot(dx, dy), l1 + l2 - 1e-3))
    a = math.acos(clamp((l1 * l1 + d * d - l2 * l2) / (2 * l1 * d), -1, 1))
    base = math.atan2(dy, dx)
    e = (S[0] + l1 * math.cos(base + bend * a), S[1] + l1 * math.sin(base + bend * a))
    ux, uy = (T[0] - S[0]), (T[1] - S[1])
    n = math.hypot(ux, uy) or 1
    h = (S[0] + ux / n * min(n, l1 + l2), S[1] + uy / n * min(n, l1 + l2))
    return e, h


def stick_joints(pose, hip, s, hands=None):
    L = {k: v * s for k, v in STICK.items()}
    td = dirv(pose["torso"])
    neck = (hip[0] + td[0] * L["torso"], hip[1] + td[1] * L["torso"])
    head = (neck[0] + td[0] * (L["neck"] + L["head"]), neck[1] + td[1] * (L["neck"] + L["head"]))
    J = {"hip": hip, "neck": neck, "head": head}
    for side in "LR":
        if hands and side in hands:
            e, h = two_bone(neck, hands[side], L["uarm"], L["farm"], -1 if side == "L" else 1)
        else:
            d1, d2 = dirv(pose["ua" + side]), dirv(pose["fa" + side])
            e = (neck[0] + d1[0] * L["uarm"], neck[1] + d1[1] * L["uarm"])
            h = (e[0] + d2[0] * L["farm"], e[1] + d2[1] * L["farm"])
        J["elbow" + side], J["hand" + side] = e, h
        d1, d2 = dirv(pose["th" + side]), dirv(pose["sh" + side])
        k = (hip[0] + d1[0] * L["thigh"], hip[1] + d1[1] * L["thigh"])
        f = (k[0] + d2[0] * L["shin"], k[1] + d2[1] * L["shin"])
        J["knee" + side], J["foot" + side] = k, f
    return J


def ground_hip(pose, x, ground_y, s):
    """Hip position so the lowest point of the body rests on ground_y."""
    J = stick_joints(pose, (x, 0.0), s)
    low = max(p[1] for k, p in J.items() if k != "head")
    low = max(low, J["head"][1] + STICK["head"] * s)
    return (x, ground_y - low - 2.5)


def draw_stick(ctx, pose, hip, s=1.0, col=None, a=1.0, lw=5, hands=None):
    if a <= 0:
        return
    col = col or BONE
    J = stick_joints(pose, hip, s, hands)
    ctx.set_line_width(lw)
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    ctx.set_line_join(cairo.LINE_JOIN_ROUND)
    for chain in (("hip", "neck"), ("neck", "elbowL", "handL"), ("neck", "elbowR", "handR"),
                  ("hip", "kneeL", "footL"), ("hip", "kneeR", "footR")):
        ctx.move_to(*J[chain[0]])
        for c in chain[1:]:
            ctx.line_to(*J[c])
    src(ctx, col, a)
    ctx.stroke()
    ctx.new_sub_path()
    ctx.arc(J["head"][0], J["head"][1], STICK["head"] * s, 0, 2 * math.pi)
    ctx.fill()
    ctx.set_line_cap(cairo.LINE_CAP_BUTT)
    ctx.set_line_join(cairo.LINE_JOIN_MITER)


def run_pose(t, rate=7.0):
    u = 0.5 + 0.5 * math.sin(t * rate * math.pi)
    return lerp_pose(POSES["run1"], POSES["run2"], u)


# Scene 1: pushes the ball up the slope ---------------------------------------
def s1_ground(x, ang):
    return S1_Y if x <= S1_PIV else S1_Y - (x - S1_PIV) * math.tan(ang)


def stick_s1(ctx, t):
    a = eo3(prog(t, 0.0, 0.3)) * (1 - eo3(prog(t, 2.5, 2.85)))
    if a <= 0:
        return
    ang = s1_slope_angle(t)
    c = s1_center(min(s1_dist(t), s1_dist(0.95)) if t < 0.95 else s1_dist(0.95), ang)
    if t < 0.95:
        x = c[0] - S1_R - 46
    else:
        x = s1_center(s1_dist(0.95), ang)[0] - S1_R - 46
    pose = pose_at(t, [(0.05, "push"), (0.55, "push"), (0.7, "strain"), (0.95, "strain"),
                       (1.2, "stand"), (1.55, "stand"), (1.85, "victory"), (2.3, "victory")])
    lean = math.degrees(ang) if t < 0.95 else 0
    pose = dict(pose, torso=pose["torso"] - lean * 0.6)
    hip = ground_hip(pose, x, s1_ground(x, ang), 0.85)
    draw_stick(ctx, pose, hip, 0.85, BONE, a, 5)


# Scene 2: hands on his head under the cracking mind ---------------------------
def stick_s2(ctx, t):
    out = prog(t, 5.62, 6.0)
    a = eo3(prog(t, 3.0, 3.3))
    with Fade(ctx, out, RX, RY):
        pose = pose_at(t, [(3.0, "stand"), (3.9, "slump"), (4.3, "head"), (5.3, "head")])
        jit = 0
        if t > 4.3:
            jit = 2.0 * math.sin(t * 70) * (1 - prog(t, 4.3, 5.4))
        hip = ground_hip(pose, RX + jit, 905, 0.72)
        draw_stick(ctx, pose, hip, 0.72, BONE, a, 5)


# Scene 3: builds the tower, then gives up -------------------------------------
def stick_s3(ctx, t):
    out = prog(t, 11.78, 12.0)
    a = eo3(prog(t, 6.0, 6.3))
    with Fade(ctx, out, RX, 600):
        keys = [(6.0, "stand"), (6.25, "lift"), (6.55, "stand"), (6.85, "lift"), (7.15, "stand"),
                (7.45, "lift"), (7.8, "stand"), (9.28, "stand"), (9.5, "shrug"), (10.2, "shrug"),
                (10.4, "slump"), (10.75, "sit"), (11.36, "sit"), (11.6, "lie")]
        pose = pose_at(t, keys)
        x = 865 if t < 11.36 else lerp(865, 880, prog(t, 11.36, 11.6))
        hip = ground_hip(pose, x, S3_GROUND, 0.78)
        draw_stick(ctx, pose, hip, 0.78, BONE, a, 5)


# Scene 6: meditates under the clock ------------------------------------------
def stick_s6(ctx, t):
    out = prog(t, 22.6, 23.0)
    a = eo3(prog(t, 19.1, 19.45))
    with Fade(ctx, out, RX, RY):
        breath = 1 + 0.03 * math.sin(t * 2.6)
        pose = dict(POSES["meditate"])
        hip = ground_hip(pose, RX, 905, 0.8)
        s = 0.8
        draw_stick(ctx, dict(pose, torso=180), (hip[0], hip[1]), s, BONE, a, 5)
        # calm aura on "mind"
        g = prog(t, 20.7, 21.6)
        if 0 < g < 1:
            J = stick_joints(pose, hip, s)
            ctx.new_sub_path()
            ctx.arc(J["head"][0], J["head"][1], 22 + 60 * eo3(g), 0, 2 * math.pi)
            src(ctx, RED, 0.6 * (1 - g))
            ctx.set_line_width(3)
            ctx.stroke()


# Scene 7: boxer through fear, ego and pressure --------------------------------
def stick_s7(ctx, t):
    out = prog(t, 27.6, 28.0)
    a = eo3(prog(t, 23.0, 23.3))
    with Fade(ctx, out, RX, 560):
        if t < 24.6:
            k = int(max(0, t - 23.2) / 0.22)
            ph = prog(t, 23.2 + 0.22 * k, 23.2 + 0.22 * k + 0.22)
            punch = 23.2 <= t < 24.3 and k % 2 == 0
            pose = lerp_pose(POSES["guard"], POSES["punch"], math.sin(math.pi * ph)) if punch else POSES["guard"]
            bob = 4 * math.sin(t * 11)
        else:
            pose = pose_at(t, [(24.6, "guard"), (24.75, "cower"), (25.5, "cower"), (25.75, "puff"),
                               (26.55, "puff"), (26.75, "brace")])
            bob = 0
        jx = 0
        if 24.6 <= t < 25.5:
            jx = 4.5 * math.sin(t * 95) * (1 - 0.6 * prog(t, 24.6, 25.5))
        hip = ground_hip(pose, RX + jx, 935, 0.74)
        draw_stick(ctx, pose, (hip[0], hip[1] + bob), 0.74, BONE, a, 5)


# Scene 8: climbs the 12 steps, then hangs from the bar -------------------------
def stick_s8(ctx, t):
    da = eo3(prog(t, 29.0, 29.25))
    if da <= 0:
        return
    s = 0.46
    p = s8_dot(t)
    if t < 31.55:
        i = sum(1 for k in range(12) if t >= S8_HOP0 + k * S8_HOPDT)
        pose = POSES["step1"] if i % 2 else POSES["step2"]
        if i == 0 or t > S8_HOP0 + 11 * S8_HOPDT + 0.14:
            pose = POSES["stand"]
        hip = ground_hip(pose, p[0], p[1] + 11.5, s)
        draw_stick(ctx, pose, hip, s, RED, da, 4)
    else:
        u = eio(prog(t, 31.55, 31.9))
        x0, x1, by = S8_BAR
        L = STICK
        hang_hip = (S8_HANG[0], by + (L["uarm"] + L["farm"]) * s * 0.92 + L["torso"] * s)
        top = s8_dot(31.549)
        start_hip = ground_hip(POSES["victory"], top[0], top[1] + 11.5, s)
        hip = (lerp(start_hip[0], hang_hip[0], u), lerp(start_hip[1], hang_hip[1], u) - 30 * math.sin(math.pi * u))
        pose = lerp_pose(POSES["victory"], POSES["hang"], u)
        hands = None
        if u >= 1:
            sw = 2.5 * math.sin((t - 31.9) * 5) * (1 - prog(t, 31.9, 32.6))
            hip = (hip[0] + sw, hip[1])
            hands = {"L": (S8_HANG[0] - 16, by), "R": (S8_HANG[0] + 16, by)}
        draw_stick(ctx, pose, hip, s, RED, da, 4, hands)


# Scene 10: the pull-ups -------------------------------------------------------
def stick_s10(ctx, t, cx, dot_y, a):
    s = 1.15
    x0, x1, by = S10_BAR
    mid = (x0 + x1) / 2
    hip_y = 488 + (dot_y - S10_HANG) * (76 / (S10_HANG - S10_TOP))
    lift = prog(S10_HANG - dot_y, 0, S10_HANG - S10_TOP)
    pose = lerp_pose(POSES["hang"], dict(POSES["hang"], thL=-18, shL=14, thR=18, shR=-14), lift)
    draw_stick(ctx, pose, (cx, hip_y), s, BONE, a, 5, hands={"L": (mid - 46, by), "R": (mid + 46, by)})


# Scene 11: runs, takes the red path up -----------------------------------------
def path_point(pts, u):
    n = len(pts) - 1
    f = clamp(u) * n
    i = min(int(f), n - 1)
    r = f - i
    return (lerp(pts[i][0], pts[i + 1][0], r), lerp(pts[i][1], pts[i + 1][1], r))


def stick_s11(ctx, t):
    out = prog(t, 43.66, 44.0)
    a = eo3(prog(t, 41.05, 41.3))
    with Fade(ctx, out, RX, RY):
        s = 0.42
        if t < 42.3:
            u = eo3(prog(t, 41.1, 42.25))
            p = (lerp(800, S11_NODE[0], u), S11_NODE[1])
            pose = run_pose(t) if u < 0.98 else POSES["stand"]
        else:
            u = eo3(prog(t, 42.3, 42.85))
            p = path_point(S11_UP, u)
            pose = run_pose(t, 9) if u < 0.99 else POSES["victory"]
        hip = ground_hip(pose, p[0], p[1] - 2, s)
        draw_stick(ctx, pose, hip, s, BONE, a, 4)
        # the ghost who took the other road
        ga = eo3(prog(t, 42.9, 43.15)) * (1 - eio(prog(t, 43.25, 43.65)))
        if ga > 0:
            q = S11_DN[-1]
            pose = POSES["sit"]
            hip = ground_hip(pose, q[0] - 30, q[1], s)
            draw_stick(ctx, pose, hip, s, GREY, ga, 4)


# Scene 12: points at the link -------------------------------------------------
def stick_s12(ctx, t):
    a = eo3(prog(t, 44.2, 44.5))
    keys = [(44.2, "guard"), (45.4, "guard"), (45.6, "victory"), (46.4, "victory"), (46.7, "stand"),
            (47.28, "stand"), (47.5, "point")]
    pose = pose_at(t, keys)
    bob = 3 * math.sin(t * 9) if t < 45.4 else 0
    hip = ground_hip(pose, 1225, 870, 0.85)
    draw_stick(ctx, pose, (hip[0], hip[1] + bob), 0.85, BONE, a, 5)


# Scene 4: constellation lines between the floating dots ---------------------------
def s4_links(ctx, t):
    k = 1 - prog(t, S4_SNAP, S4_SNAP + 0.25)
    if k <= 0 or t < 12.2:
        return
    pts = [s4_dot(i, t) for i in range(30)]
    ctx.set_line_width(1.5)
    for i in range(30):
        for j in range(i + 1, 30):
            d = math.dist(pts[i], pts[j])
            if d < 120:
                ctx.move_to(*pts[i])
                ctx.line_to(*pts[j])
                src(ctx, GREY, 0.55 * (1 - d / 120) * k * prog(t, 12.2, 12.6))
                ctx.stroke()


# Scene 5: a rotating dial of ticks around the cage ---------------------------------
def s5_dial(ctx, t):
    a = eo3(prog(t, 16.6, 17.1)) * (1 - prog(t, 18.55, 18.75))
    if a <= 0:
        return
    rot = t * 0.35
    ctx.set_line_width(2)
    for k in range(72):
        ang = rot + 2 * math.pi * k / 72
        r0 = 398 if k % 6 else 392
        ctx.move_to(W / 2 + r0 * math.cos(ang), H / 2 + r0 * math.sin(ang))
        ctx.line_to(W / 2 + 410 * math.cos(ang), H / 2 + 410 * math.sin(ang))
    src(ctx, GREY, 0.55 * a)
    ctx.stroke()


def with_extra(fn, *extras):
    def f(ctx, t):
        fn(ctx, t)
        for e in extras:
            e(ctx, t)
    f.__name__ = fn.__name__
    return f


# ----------------------------------------------------------------- themes & backgrounds
THEMES = {
    "dark": (hexc("#0B0B0A"), hexc("#ECE8E0"), hexc("#C4161C"), hexc("#8F897F")),
    "bone": (hexc("#ECE8E0"), hexc("#0B0B0A"), hexc("#C4161C"), hexc("#8F897F")),
    "red":  (hexc("#C4161C"), hexc("#F4F0E8"), hexc("#0B0B0A"), hexc("#F0A8AA")),
}
WIPE = 0.32
# (design time the wipe starts, theme, background pattern, wipe shape, wipe centre)
SCHEDULE = [
    (-9.0, "dark", "rays", None, None),
    (2.88, "bone", "grid", "circle", (RX, RY)),
    (5.85, "dark", "stripes", "slash", None),
    (9.10, "red", "rings", "circle", (RX, 600)),
    (11.85, "bone", "grid", "octagon", (RX, 560)),
    (14.30, "dark", "rays", "circle", (RX, RY)),
    (18.75, "bone", "rings", "circle", (RX, RY)),
    (22.75, "red", "stripes", "slash", None),
    (26.40, "dark", "rings", "circle", (RX, 560)),
    (27.80, "bone", "grid", "slash", None),
    (31.25, "dark", "rays", "circle", (1230, 215)),
    (32.85, "red", "grid", "octagon", (1055, RY)),
    (36.85, "dark", "stripes", "slash", None),
    (40.85, "bone", "rings", "circle", (800, 610)),
    (43.85, "dark", "rays", "octagon", (W / 2, H / 2)),
    (45.25, "red", "rings", "circle", (W / 2, 500)),
]


def set_theme(name):
    global BG, BONE, RED, GREY
    BG, BONE, RED, GREY = THEMES[name]


def bg_pattern(ctx, t, kind):
    ctx.save()
    if kind == "grid":
        sp, ox, oy = 54, (t * 14) % 54, (t * 9) % 54
        src(ctx, BONE, 0.11)
        for y in range(-1, H // sp + 2):
            for x in range(-1, W // sp + 2):
                ctx.new_sub_path()
                ctx.arc(x * sp + ox, y * sp + oy, 2.2, 0, 2 * math.pi)
        ctx.fill()
    elif kind == "rays":
        ctx.set_line_width(2)
        rot = math.radians(3 * t)
        for k in range(40):
            a = rot + 2 * math.pi * k / 40
            ctx.move_to(W / 2 + 140 * math.cos(a), H / 2 + 140 * math.sin(a))
            ctx.line_to(W / 2 + 1000 * math.cos(a), H / 2 + 1000 * math.sin(a))
        src(ctx, BONE, 0.06)
        ctx.stroke()
    elif kind == "rings":
        ctx.set_line_width(2)
        for k in range(12):
            r = (k * 85 + t * 38) % 1020
            ctx.new_sub_path()
            ctx.arc(W / 2, H / 2, r, 0, 2 * math.pi)
            src(ctx, BONE, 0.10 * (1 - r / 1020))
            ctx.stroke()
    elif kind == "stripes":
        ctx.set_line_width(2)
        sp, off = 46, (t * 30) % 46
        for k in range(-30, 60):
            x = k * sp + off
            ctx.move_to(x, 0)
            ctx.line_to(x - H, H)
        src(ctx, BONE, 0.055)
        ctx.stroke()
    ctx.restore()
    bg_octagon(ctx, t)


def vignette(ctx, strength):
    g = cairo.RadialGradient(W / 2, H / 2, 300, W / 2, H / 2, 900)
    g.add_color_stop_rgba(0, 0, 0, 0, 0)
    g.add_color_stop_rgba(1, 0, 0, 0, strength)
    ctx.set_source(g)
    ctx.paint()


def scene_index(t):
    for i, (t0, t1, fn) in enumerate(SCENES):
        if t0 <= t < t1 or (i == len(SCENES) - 1 and t >= t0):
            return i
    return len(SCENES) - 1


def hud(ctx, t):
    i = scene_index(t)
    set_font(ctx, ANTON, 30)
    src(ctx, GREY, 0.9)
    ctx.move_to(120, 150)
    ctx.show_text(f"{i + 1:02d}")
    src(ctx, GREY, 0.5)
    ctx.show_text(f"  /  {len(SCENES):02d}")
    # progress bar
    x0, x1, y = 260, 1320, 140
    ctx.rectangle(x0, y - 1.5, x1 - x0, 3)
    src(ctx, GREY, 0.25)
    ctx.fill()
    ctx.rectangle(x0, y - 1.5, (x1 - x0) * clamp(t / FREEZE), 3)
    src(ctx, RED, 0.95)
    ctx.fill()


KEY_TIMES = []


def collect_key_times():
    for name, g in list(globals().items()):
        for gg in (g if isinstance(g, list) else [g]):
            if isinstance(gg, Group) and gg is not S10_SIG:
                for line in gg.lines:
                    for w in line:
                        if w.key:
                            KEY_TIMES.append(w.t)
    KEY_TIMES.sort()


FULL_WIDTH = {0, 4, 11}


def punch_and_burst(ctx, t, i):
    p, burst = 0.0, None
    for k in KEY_TIMES:
        d = t - k
        if 0 <= d < 0.6:
            p += 0.035 * math.exp(-d / 0.11) * min(1, d / 0.03)
            burst = d
    return p, burst


def draw_layer(ctx, t, theme, pattern):
    set_theme(theme)
    src(ctx, BG)
    ctx.paint()
    bg_pattern(ctx, t, pattern)
    i = scene_index(t)
    t0, t1, fn = SCENES[i]
    p, burst = punch_and_burst(ctx, t, i)
    s = (1 + 0.04 * prog(t, t0, min(t1, FREEZE))) * (1 + p)
    ctx.save()
    ctx.translate(W / 2, H / 2)
    ctx.scale(s, s)
    ctx.translate(-W / 2, -H / 2)
    if burst is not None:
        c = (W / 2, H / 2) if i in FULL_WIDTH else (RX, RY)
        u = eo3(clamp(burst / 0.55))
        ctx.new_sub_path()
        ctx.arc(c[0], c[1], 80 + 420 * u, 0, 2 * math.pi)
        ctx.set_line_width(3)
        src(ctx, RED, 0.45 * (1 - u))
        ctx.stroke()
    fn(ctx, t)
    ctx.restore()
    hud(ctx, t)
    if theme != "bone":
        vignette(ctx, 0.35 if theme == "dark" else 0.22)


def wipe_path(ctx, kind, c, u):
    if kind == "circle":
        r = 1900 * eio(u)
        ctx.new_sub_path()
        ctx.arc(c[0], c[1], max(r, 0.1), 0, 2 * math.pi)
    elif kind == "octagon":
        pts = oct_pts(c[0], c[1], 1900 * eio(u) + 0.1)
        ctx.move_to(*pts[0])
        for q in pts[1:]:
            ctx.line_to(*q)
        ctx.close_path()
    else:   # diagonal slash from the left
        x = -H + (W + 2 * H) * eio(u)
        ctx.move_to(-H, 0)
        ctx.line_to(x, 0)
        ctx.line_to(x - H * 0.6, H)
        ctx.line_to(-H, H)
        ctx.close_path()


def render(t):
    t = min(t, FREEZE)
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    ctx = cairo.Context(surf)
    ctx.set_antialias(cairo.ANTIALIAS_BEST)
    fo = cairo.FontOptions()
    fo.set_antialias(cairo.ANTIALIAS_GRAY)
    fo.set_hint_style(cairo.HINT_STYLE_NONE)
    fo.set_hint_metrics(cairo.HINT_METRICS_OFF)
    ctx.set_font_options(fo)
    k = max(i for i, e in enumerate(SCHEDULE) if e[0] <= t)
    t0, theme, pattern, kind, c = SCHEDULE[k]
    u = prog(t, t0, t0 + WIPE)
    if u >= 1 or k == 0:
        draw_layer(ctx, t, theme, pattern)
    else:
        _, ptheme, ppattern, _, _ = SCHEDULE[k - 1]
        draw_layer(ctx, t, ptheme, ppattern)
        ctx.save()
        wipe_path(ctx, kind, c, u)
        ctx.clip()
        draw_layer(ctx, t, theme, pattern)
        ctx.restore()
        # thin accent edge on the wipe
        ctx.save()
        wipe_path(ctx, kind, c, u)
        ctx.set_line_width(6)
        src(ctx, THEMES[theme][2] if theme != "red" else THEMES["red"][1], 0.9)
        ctx.stroke()
        ctx.restore()
    set_theme("dark")
    return surf


# ----------------------------------------------------------------- timeline
SCENES = [
    (0.0, 3.0, with_extra(scene1, stick_s1)), (3.0, 6.0, with_extra(scene2, stick_s2)),
    (6.0, 12.0, with_extra(scene3, stick_s3)), (12.0, 16.0, with_extra(scene4, s4_links)),
    (16.0, 19.0, with_extra(scene5, s5_dial)), (19.0, 23.0, with_extra(scene6, stick_s6)),
    (23.0, 28.0, with_extra(scene7, stick_s7)), (28.0, 33.0, scene8),
    (33.0, 37.0, scene9), (37.0, 41.0, scene10), (41.0, 44.0, with_extra(scene11, stick_s11)),
    (44.0, END, with_extra(scene12, stick_s12)),
]
collect_key_times()


_WARP = None   # new time -> design time, when fitting to a recorded voice (see warp.py)


def load_warp(path):
    global _WARP
    from scipy.interpolate import PchipInterpolator
    import json
    w = json.load(open(path))
    xs, ys = zip(*w["anchors"])
    _WARP = (PchipInterpolator(xs, ys), w["end"])


def design_time(t):
    return float(_WARP[0](min(t, _WARP[1]))) if _WARP else t


def frame_bytes(i):
    return bytes(render(design_time(i / FPS)).get_data())


def ensure_fonts():
    dst = os.path.expanduser("~/.fonts")
    os.makedirs(dst, exist_ok=True)
    changed = False
    for f in ("Anton-Regular.ttf", "Lora-Italic.ttf", "Alice-Regular.ttf"):
        if not os.path.exists(os.path.join(dst, f)):
            shutil.copy(os.path.join(HERE, "fonts", f), dst)
            changed = True
    if changed:
        subprocess.run(["fc-cache", "-f"], check=False)


def main():
    os.makedirs(OUT, exist_ok=True)
    if len(sys.argv) > 1 and sys.argv[1] == "--frames":
        for a in sys.argv[2:]:
            render(float(a)).write_to_png(os.path.join(OUT, f"check_{float(a):05.2f}.png"))
        return
    render(1.4).write_to_png(os.path.join(OUT, "cover.png"))
    warp = os.environ.get("WARP")
    if warp:
        load_warp(warp)
    n = int(round((_WARP[1] if _WARP else END) * FPS))
    out = os.path.join(OUT, "launch_no_audio.mp4")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", "-vf", "scale=out_color_matrix=bt709:out_range=tv",
           "-c:v", "libx264", "-preset", "slow", "-crf", "14", "-tune", "animation",
           "-pix_fmt", "yuv420p", "-profile:v", "high",
           "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
           "-movflags", "+faststart", out]
    ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    with Pool(os.cpu_count()) as pool:
        for k, b in enumerate(pool.imap(frame_bytes, range(n), chunksize=8)):
            ff.stdin.write(b)
            if k % 150 == 0:
                print(f"frame {k}/{n}", flush=True)
    ff.stdin.close()
    ff.wait()
    print("wrote", out)


if __name__ == "__main__":
    ensure_fonts()
    main()
