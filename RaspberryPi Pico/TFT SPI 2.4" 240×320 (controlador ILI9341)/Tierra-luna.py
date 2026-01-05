from machine import Pin, SPI
from ili9341 import ILI9341, color565
import framebuf, time, gc, math
from array import array

# =========================
# Pines (tu setup)
# =========================
PIN_SCK  = 6
PIN_MOSI = 7
PIN_CS   = 13
PIN_RST  = 14
PIN_DC   = 15

SPI_BAUD = 40_000_000
ROTATION = 3
BGR      = True

FRAME_MS = 25  # 20-30 fluido y estable

# Kill switch: GP22 a GND
KILL_PIN = 22
kill = Pin(KILL_PIN, Pin.IN, Pin.PULL_UP)

# Ajuste fino de centrado (pixeles) por si quieres
CENTER_X_OFF = 0
CENTER_Y_OFF = 0

# =========================
# Init display + framebuffer
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
# Trig LUT (fixed)
# =========================
S = 1024
sinLUT = array('h', [0] * 360)
for a in range(360):
    sinLUT[a] = int(math.sin(a * math.pi / 180.0) * S)

def sin_deg(a): return sinLUT[a % 360]
def cos_deg(a): return sinLUT[(a + 90) % 360]

# =========================
# 3D helpers
# =========================
D = 220  # distancia "cámara"

def rot_y(x, y, z, cy, sy):
    x1 = (x * cy + z * sy) // S
    z1 = (-x * sy + z * cy) // S
    return x1, y, z1

def rot_x(x, y, z, cx, sx):
    y1 = (y * cx - z * sx) // S
    z1 = (y * sx + z * cx) // S
    return x, y1, z1

def project(x, y, z, cx2d, cy2d):
    den = D + z
    if den < 60:
        den = 60
    k = (D * S) // den
    sx2d = cx2d + (x * k) // S
    sy2d = cy2d + (y * k) // S
    return sx2d, sy2d

def circle_poly(cx, cy, r, col, seg=44):
    px = cx + (r * cos_deg(0)) // S
    py = cy + (r * sin_deg(0)) // S
    for i in range(1, seg + 1):
        a = (i * 360) // seg
        x = cx + (r * cos_deg(a)) // S
        y = cy + (r * sin_deg(a)) // S
        fb.line(px, py, x, y, col)
        px, py = x, y

def fill_circle(cx, cy, r, col):
    # Midpoint circle + hlines (rápido y sin floats)
    x = 0
    y = r
    d = 1 - r
    fb.hline(cx - r, cy, 2 * r + 1, col)
    while x < y:
        if d >= 0:
            y -= 1
            d += 2 * (x - y) + 5
        else:
            d += 2 * x + 3
        x += 1
        fb.hline(cx - x, cy + y, 2 * x + 1, col)
        fb.hline(cx - x, cy - y, 2 * x + 1, col)
        fb.hline(cx - y, cy + x, 2 * y + 1, col)
        fb.hline(cx - y, cy - x, 2 * y + 1, col)

# =========================
# Geometría Tierra/Luna
# =========================
EARTH_R = 78
ORBIT_R = 118
MOON_R  = 13

LON_STEP = 30
LAT_LIST = [-75, -45, -15, 15, 45, 75]

lons = list(range(0, 360, LON_STEP))

earth_pts = []
earth_idx = {}
for li, lat in enumerate(LAT_LIST):
    cl = cos_deg(lat)
    sl = sin_deg(lat)
    for oi, lon in enumerate(lons):
        co = cos_deg(lon)
        so = sin_deg(lon)
        x = (EARTH_R * cl // S) * co // S
        y = (EARTH_R * sl) // S
        z = (EARTH_R * cl // S) * so // S
        earth_idx[(li, oi)] = len(earth_pts)
        earth_pts.append((x, y, z))

earth_edges = []
for li in range(len(LAT_LIST)):
    for oi in range(len(lons)):
        a = earth_idx[(li, oi)]
        b = earth_idx[(li, (oi + 1) % len(lons))]
        earth_edges.append((a, b))
for oi in range(len(lons)):
    for li in range(len(LAT_LIST) - 1):
        a = earth_idx[(li, oi)]
        b = earth_idx[(li + 1, oi)]
        earth_edges.append((a, b))

N_E = len(earth_pts)
px = array('h', [0] * N_E)
py = array('h', [0] * N_E)
pz = array('h', [0] * N_E)

ORBIT_SEG = 48
orbit_pts = []
for i in range(ORBIT_SEG):
    ang = (i * 360) // ORBIT_SEG
    x = (ORBIT_R * cos_deg(ang)) // S
    y = 0
    z = (ORBIT_R * sin_deg(ang)) // S
    orbit_pts.append((x, y, z))
orbit_edges = [(i, (i + 1) % ORBIT_SEG) for i in range(ORBIT_SEG)]

# Trail luna (prealocado)
TRAIL_LEN = 18
trail_x = array('h', [0] * TRAIL_LEN)
trail_y = array('h', [0] * TRAIL_LEN)
trail_i = 0

TRAIL_COL = array('H', [0] * TRAIL_LEN)
for i in range(TRAIL_LEN):
    v = 35 + (i * 9)
    if v > 200: v = 200
    TRAIL_COL[i] = color565(v, v, v + 25)

# =========================
# Fondo estrellas (prealocado)
# =========================
_seed = 0xA5A5A5A5
def rnd():
    global _seed
    _seed = (_seed * 1664525 + 1013904223) & 0xFFFFFFFF
    return _seed

STAR_N = 90
star_x = array('H', [0] * STAR_N)
star_y = array('H', [0] * STAR_N)
star_l = array('B', [0] * STAR_N)
for i in range(STAR_N):
    star_x[i] = rnd() % W
    star_y[i] = rnd() % H
    star_l[i] = 1 if (rnd() & 1) else 2

# =========================
# Colores
# =========================
BG        = color565(0, 0, 0)
STAR1     = color565(12, 12, 18)
STAR2     = color565(25, 25, 40)
EARTH_F   = color565(0, 220, 255)
EARTH_B   = color565(0, 90, 120)
ATM1      = color565(0, 70, 90)
ATM2      = color565(0, 35, 45)
ORBIT_C   = color565(35, 35, 55)

MOON_FILL_NEAR = color565(240, 240, 240)  # cuando está enfrente (cerca)
MOON_FILL_FAR  = color565(130, 130, 130)  # cuando está atrás (lejos)
MOON_RIM_NEAR  = color565(255, 255, 255)
MOON_RIM_FAR   = color565(170, 170, 170)

HUD_COL   = color565(120, 120, 160)

# =========================
# LOOP
# =========================
try:
    # Ventana anti-busy
    time.sleep(1)

    cx2d = W // 2 + CENTER_X_OFF
    cy2d = H // 2 + CENTER_Y_OFF

    angY = 0
    angX = 18
    moonA = 0
    frame = 0

    while True:
        start = time.ticks_ms()

        # salida garantizada
        if not kill.value():
            raise KeyboardInterrupt

        fb.fill(BG)

        # estrellas (parallax)
        drift = (frame // 2) % W
        for i in range(STAR_N):
            x = (star_x[i] + drift) % W
            y = star_y[i]
            fb.pixel(x, y, STAR1 if star_l[i] == 1 else STAR2)

        cy = cos_deg(angY); sy = sin_deg(angY)
        cx = cos_deg(angX); sx = sin_deg(angX)

        # órbita
        for a, b in orbit_edges:
            x, y, z = orbit_pts[a]
            x1, y1, z1 = rot_y(x, y, z, cy, sy)
            x2, y2, z2 = rot_x(x1, y1, z1, cx, sx)
            xA, yA = project(x2, y2, z2, cx2d, cy2d)

            x, y, z = orbit_pts[b]
            x1, y1, z1 = rot_y(x, y, z, cy, sy)
            x2, y2, z2 = rot_x(x1, y1, z1, cx, sx)
            xB, yB = project(x2, y2, z2, cx2d, cy2d)

            fb.line(xA, yA, xB, yB, ORBIT_C)

        # Tierra: proyecta puntos
        for i in range(N_E):
            x, y, z = earth_pts[i]
            x1, y1, z1 = rot_y(x, y, z, cy, sy)
            x2, y2, z2 = rot_x(x1, y1, z1, cx, sx)
            pz[i] = z2
            sxp, syp = project(x2, y2, z2, cx2d, cy2d)
            px[i] = sxp
            py[i] = syp

        # Tierra wireframe: Z negativa = cerca (frente)
        for a, b in earth_edges:
            col = EARTH_F if (pz[a] + pz[b]) < 0 else EARTH_B
            fb.line(px[a], py[a], px[b], py[b], col)

        # Atmosfera
        circle_poly(cx2d, cy2d, EARTH_R + 5, ATM2, seg=44)
        circle_poly(cx2d, cy2d, EARTH_R + 3, ATM1, seg=44)

        # Luna
        mx = (ORBIT_R * cos_deg(moonA)) // S
        mz = (ORBIT_R * sin_deg(moonA)) // S
        my = 0

        mx1, my1, mz1 = rot_y(mx, my, mz, cy, sy)
        mx2, my2, mz2 = rot_x(mx1, my1, mz1, cx, sx)
        moon_x, moon_y = project(mx2, my2, mz2, cx2d, cy2d)

        # trail
        trail_x[trail_i] = moon_x
        trail_y[trail_i] = moon_y
        trail_i = (trail_i + 1) % TRAIL_LEN

        for k in range(TRAIL_LEN):
            idx = (trail_i + k) % TRAIL_LEN
            x = trail_x[idx]
            y = trail_y[idx]
            if 0 <= x < W and 0 <= y < H:
                fb.pixel(x, y, TRAIL_COL[k])

        # tamaño aparente (si Z es negativa => más cerca => más grande)
        den = D + mz2
        if den < 60: den = 60
        kproj = (D * S) // den
        moon_r = max(6, (MOON_R * kproj) // S)

        # ✅ FIX CLAVE: cerca = mz2 NEGATIVA
        near = (mz2 < 0)

        fill_col = MOON_FILL_NEAR if near else MOON_FILL_FAR
        rim_col  = MOON_RIM_NEAR  if near else MOON_RIM_FAR

        # relleno + contorno para que nunca "se pierda"
        fill_circle(moon_x, moon_y, moon_r, fill_col)
        circle_poly(moon_x, moon_y, moon_r, rim_col, seg=20)

        # HUD
        fb.text("", 4, 4, HUD_COL)

        blit()

        # animación
        angY = (angY + 3) % 360
        moonA = (moonA + 6) % 360
        frame += 1

        # anti-busy: cede CPU SIEMPRE
        dt = time.ticks_diff(time.ticks_ms(), start)
        if dt < FRAME_MS:
            time.sleep_ms(FRAME_MS - dt)
        else:
            time.sleep_ms(1)

        if (frame & 31) == 0:
            gc.collect()

except KeyboardInterrupt:
    fb.fill(0)
    blit()
