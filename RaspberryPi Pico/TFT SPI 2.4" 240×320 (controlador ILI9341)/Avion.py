from machine import Pin, SPI
from ili9341 import ILI9341
import framebuf, time, gc, math
from array import array

# =========================
# CONFIG (tu setup)
# =========================
PIN_SCK  = 6
PIN_MOSI = 7
PIN_CS   = 13
PIN_RST  = 14
PIN_DC   = 15

SPI_BAUD = 40_000_000
ROTATION = 3
BGR      = True

FRAME_MS = 25

# Kill switch: GP22 a GND (salida garantizada)
KILL_PIN = 22
kill = Pin(KILL_PIN, Pin.IN, Pin.PULL_UP)

# ✅ FIX CLAVE PARA TU "FONDO VERDE"
# Si ves el navy como verde, deja esto en True.
# Si por alguna razón se te invierte, ponlo en False.
SWAP_BYTES = True

def rgb565(r, g, b):
    # RGB565 estándar
    v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    # swap para cuando el framebuffer se manda byte-reversed
    if SWAP_BYTES:
        v = ((v & 0xFF) << 8) | (v >> 8)
    return v

# =========================
# INIT display + framebuffer
# =========================
spi = SPI(0, baudrate=SPI_BAUD, polarity=0, phase=0,
          sck=Pin(PIN_SCK), mosi=Pin(PIN_MOSI))

tft = ILI9341(spi, cs=PIN_CS, dc=PIN_DC, rst=PIN_RST,
              width=240, height=320, rotation=ROTATION, bgr=BGR)

W = tft.width
H = tft.height

buf = bytearray(W * H * 2)
fb  = framebuf.FrameBuffer(buf, W, H, framebuf.RGB565)

def blit():
    tft._begin_write(0, 0, W - 1, H - 1)
    tft.spi.write(buf)
    tft._end_write()

# =========================
# COLORS (blueprint navy)
# =========================
BG          = rgb565(0,  8, 40)      # azul marino real
GRID_MINOR  = rgb565(0, 20, 70)
GRID_MAJOR  = rgb565(0, 35, 120)
AXIS_COL    = rgb565(0, 55, 170)

GLOW_FAR    = rgb565(0, 70, 95)
GLOW_NEAR   = rgb565(0, 210, 255)
LINE_WHITE  = rgb565(255, 255, 255)
TEXT_COL    = rgb565(200, 220, 255)

# =========================
# TRIG LUT (fixed)
# =========================
S = 1024
sinLUT = array('h', [0]*360)
for a in range(360):
    sinLUT[a] = int(math.sin(a * math.pi / 180.0) * S)

def sin_deg(a): return sinLUT[a % 360]
def cos_deg(a): return sinLUT[(a + 90) % 360]

# =========================
# 3D helpers (fixed-point)
# =========================
def rot_x(x, y, z, cx, sx):
    y1 = (y * cx - z * sx) // S
    z1 = (y * sx + z * cx) // S
    return x, y1, z1

def rot_y(x, y, z, cy, sy):
    x1 = (x * cy + z * sy) // S
    z1 = (-x * sy + z * cy) // S
    return x1, y, z1

def rot_z(x, y, z, cz, sz):
    x1 = (x * cz - y * sz) // S
    y1 = (x * sz + y * cz) // S
    return x1, y1, z

CAM_Z = 360
FOV   = 230

def project(x, y, z, cx2d, cy2d):
    den = CAM_Z + z
    if den < 120:
        den = 120
    sx2d = cx2d + (x * FOV) // den
    # ✅ Y hacia arriba (timón ya no se “cae”)
    sy2d = cy2d - (y * FOV) // den
    return sx2d, sy2d

def blueprint_line(x0, y0, x1, y1, zavg):
    glow = GLOW_NEAR if zavg < 0 else GLOW_FAR
    fb.line(x0, y0, x1, y1, glow)
    if zavg < -40:
        fb.line(x0+1, y0, x1+1, y1, glow)  # glow extra cerca
        fb.line(x0, y0+1, x1, y1+1, glow)
    fb.line(x0, y0, x1, y1, LINE_WHITE)

# =========================
# Blueprint grid
# =========================
def draw_grid():
    fb.fill(BG)

    step = 20
    for x in range(0, W, step):
        fb.vline(x, 0, H, GRID_MINOR)
    for y in range(0, H, step):
        fb.hline(0, y, W, GRID_MINOR)

    step2 = 40
    for x in range(0, W, step2):
        fb.vline(x, 0, H, GRID_MAJOR)
    for y in range(0, H, step2):
        fb.hline(0, y, W, GRID_MAJOR)

    cx = W // 2
    cy = H // 2
    fb.hline(0, cy, W, AXIS_COL)
    fb.vline(cx, 0, H, AXIS_COL)

# =========================
# Build Airliner (fuselaje mesh + alas + cola + motores)
# =========================
def build_airliner():
    RINGS = 14
    SEG   = 18

    z_list = [-160, -142, -124, -98, -70, -40, -10, 20, 55, 90, 120, 140, 155, 165]
    r_list = [   4,    9,   14,   19,   23,   26,   27, 27,  26,  23,  18,  12,   7,   4]

    y_squash = 0.75

    verts = []
    edges = []

    for ri in range(RINGS):
        z = z_list[ri]
        r = r_list[ri]
        for si in range(SEG):
            ang = (si * 360) // SEG
            co = cos_deg(ang)
            so = sin_deg(ang)
            x = (r * co) // S
            y = int(((r * so) // S) * y_squash)
            verts.append((x, y, z))

    def vid(ring, seg):
        return ring * SEG + (seg % SEG)

    # mesh fuselaje
    for ri in range(RINGS):
        for si in range(SEG):
            a = vid(ri, si)
            b = vid(ri, si+1)
            edges.append((a, b))
            if ri < RINGS - 1:
                c = vid(ri+1, si)
                edges.append((a, c))

    # ===== Alas =====
    root_ring = 7
    z_root = z_list[root_ring]
    y_root = -2

    base = len(verts)
    wing_span  = 115
    wing_sweep = 32
    wing_back  = -18

    # leading edge
    pts = [
        (-22, y_root, z_root + 12), ( 22, y_root, z_root + 12),
        (-70, y_root, z_root - wing_sweep), ( 70, y_root, z_root - wing_sweep),
        (-wing_span, y_root, z_root - (wing_sweep + 22)), ( wing_span, y_root, z_root - (wing_sweep + 22)),
        # trailing edge
        (-18, y_root, z_root + wing_back), ( 18, y_root, z_root + wing_back),
        (-60, y_root, z_root + wing_back - 6), ( 60, y_root, z_root + wing_back - 6),
        (-wing_span + 16, y_root, z_root + wing_back), ( wing_span - 16, y_root, z_root + wing_back),
    ]
    for p in pts:
        verts.append(p)

    i_wrl, i_wrr, i_wml, i_wmr, i_wtl, i_wtr, i_wrl2, i_wrr2, i_wml2, i_wmr2, i_wtl2, i_wtr2 = range(base, base+12)

    edges += [
        # contornos
        (i_wrl, i_wml), (i_wml, i_wtl), (i_wtl, i_wtl2), (i_wtl2, i_wml2),
        (i_wml2, i_wrl2), (i_wrl2, i_wrl),
        (i_wrr, i_wmr), (i_wmr, i_wtr), (i_wtr, i_wtr2), (i_wtr2, i_wmr2),
        (i_wmr2, i_wrr2), (i_wrr2, i_wrr),
        # paneles
        (i_wrl, i_wrr), (i_wrl2, i_wrr2),
        (i_wml, i_wmr), (i_wml2, i_wmr2),
        (i_wtl, i_wtr), (i_wtl2, i_wtr2),
    ]

    # ===== Cola + timón =====
    tail_ring = 11
    z_tail = z_list[tail_ring]

    base2 = len(verts)
    tp_span = 70
    tp_sweep = 20

    verts += [
        (-tp_span, 0, z_tail - tp_sweep), (tp_span, 0, z_tail - tp_sweep),
        (-tp_span + 12, 0, z_tail + 16),  (tp_span - 12, 0, z_tail + 16),
    ]
    i_tpl, i_tpr, i_tpl2, i_tpr2 = range(base2, base2+4)
    edges += [(i_tpl, i_tpr), (i_tpl, i_tpl2), (i_tpr, i_tpr2), (i_tpl2, i_tpr2)]

    base3 = len(verts)
    fin_h = 75
    verts += [(0, 0, z_tail + 6), (0, fin_h, z_tail - 22), (0, 18, z_tail - 48)]
    i_f0, i_f1, i_f2 = range(base3, base3+3)
    edges += [(i_f0, i_f1), (i_f1, i_f2), (i_f2, i_f0)]

    # ===== Motores =====
    def add_engine(xc, yc, zc, pylon_to):
        ENG_RINGS = 3
        ENG_SEG   = 10
        eng_z = [zc - 12, zc, zc + 12]
        eng_r = [10, 11, 9]
        baseE = len(verts)

        for ri in range(ENG_RINGS):
            z = eng_z[ri]
            r = eng_r[ri]
            for si in range(ENG_SEG):
                ang = (si * 360) // ENG_SEG
                co = cos_deg(ang)
                so = sin_deg(ang)
                x = xc + (r * co) // S
                y = yc + (r * so) // S
                verts.append((x, y, z))

        def ev(ring, seg):
            return baseE + ring * ENG_SEG + (seg % ENG_SEG)

        for ri in range(ENG_RINGS):
            for si in range(ENG_SEG):
                edges.append((ev(ri, si), ev(ri, si+1)))
                if ri < ENG_RINGS - 1:
                    edges.append((ev(ri, si), ev(ri+1, si)))

        edges.append((ev(1, 0), pylon_to))

    add_engine(-40, -22, z_root - 22, i_wml2)
    add_engine( 40, -22, z_root - 22, i_wmr2)

    return verts, edges

verts_list, edges_list = build_airliner()
NV = len(verts_list)
NE = len(edges_list)

VX = array('h', [v[0] for v in verts_list])
VY = array('h', [v[1] for v in verts_list])
VZ = array('h', [v[2] for v in verts_list])

EA = array('H', [e[0] for e in edges_list])
EB = array('H', [e[1] for e in edges_list])

PX = array('h', [0]*NV)
PY = array('h', [0]*NV)
PZ = array('h', [0]*NV)

# =========================
# LOOP
# =========================
try:
    time.sleep(1)

    cx2d = W // 2
    cy2d = H // 2 + 8

    frame = 0
    while True:
        t0 = time.ticks_ms()

        if not kill.value():
            raise KeyboardInterrupt

        draw_grid()

        yaw   = (frame * 3) % 360
        t     = (frame * 2) % 360
        pitch = 8 + (sin_deg(t) * 10) // S
        roll  = (sin_deg((t*2) % 360) * 22) // S

        cy = cos_deg(yaw);   sy = sin_deg(yaw)
        cx = cos_deg(pitch); sx = sin_deg(pitch)
        cz = cos_deg(roll);  sz = sin_deg(roll)

        z_bob = (sin_deg((frame*3) % 360) * 22) // S

        for i in range(NV):
            x = VX[i]; y = VY[i]; z = VZ[i]
            x,y,z = rot_y(x,y,z, cy,sy)
            x,y,z = rot_x(x,y,z, cx,sx)
            x,y,z = rot_z(x,y,z, cz,sz)
            z = z + z_bob

            PZ[i] = z
            sx2d, sy2d = project(x, y, z, cx2d, cy2d)
            PX[i] = sx2d
            PY[i] = sy2d

        # 2 pasadas: lejos primero, cerca al final (se ve más 3D)
        for pass_id in (0, 1):
            for k in range(NE):
                a = EA[k]; b = EB[k]
                zavg = (PZ[a] + PZ[b]) // 2

                if pass_id == 0 and zavg < 0:
                    continue
                if pass_id == 1 and zavg >= 0:
                    continue

                x0 = PX[a]; y0 = PY[a]
                x1 = PX[b]; y1 = PY[b]

                if (x0 < -50 and x1 < -50) or (x0 > W+50 and x1 > W+50): continue
                if (y0 < -50 and y1 < -50) or (y0 > H+50 and y1 > H+50): continue

                blueprint_line(x0, y0, x1, y1, zavg)

        fb.text("", 6, 6, TEXT_COL)
        fb.text("", 6, 16, TEXT_COL)

        blit()

        dt = time.ticks_diff(time.ticks_ms(), t0)
        if dt < FRAME_MS:
            time.sleep_ms(FRAME_MS - dt)
        else:
            time.sleep_ms(1)

        frame += 1
        if (frame & 31) == 0:
            gc.collect()

except KeyboardInterrupt:
    fb.fill(0)
    blit()
