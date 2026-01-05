from machine import Pin, SPI
from ili9341 import ILI9341, color565
import framebuf, time, gc
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

WIDTH  = 240
HEIGHT = 320

FRAME_MS   = 35     # 25-45 (más bajo = más FPS)
STAR_COUNT = 520    # sube/baja según fluidez (450-700)
ARMS       = 4
MAX_R      = 150
ELLIPSE_Y  = 650    # 1000 círculo; menos = disco
TWIST      = 220

# Shooting stars
SHOOT_CHANCE = 45   # menor = más frecuentes
SHOOT_LEN    = 16

# =========================
# INIT DISPLAY
# =========================
spi = SPI(0, baudrate=SPI_BAUD, polarity=0, phase=0,
          sck=Pin(PIN_SCK), mosi=Pin(PIN_MOSI))

tft = ILI9341(spi, cs=PIN_CS, dc=PIN_DC, rst=PIN_RST,
              width=WIDTH, height=HEIGHT, rotation=ROTATION, bgr=BGR)

gc.collect()

# =========================
# FRAMEBUFFER
# =========================
buf = bytearray(tft.width * tft.height * 2)
fb = framebuf.FrameBuffer(buf, tft.width, tft.height, framebuf.RGB565)

def blit_fullscreen():
    tft._begin_write(0, 0, tft.width - 1, tft.height - 1)
    tft.spi.write(buf)
    tft._end_write()

# =========================
# FIXED TRIG LUT (sin/cos) * 1024
# =========================
# Para ahorrar RAM: int16
S = 1024
import math
sinLUT = array('h', [0] * 360)
for a in range(360):
    sinLUT[a] = int(math.sin(a * math.pi / 180.0) * S)

def sin_deg(a): return sinLUT[a % 360]
def cos_deg(a): return sinLUT[(a + 90) % 360]

# =========================
# RNG (LCG)
# =========================
_seed = 0x12345678
def rnd():
    global _seed
    _seed = (_seed * 1664525 + 1013904223) & 0xFFFFFFFF
    return _seed

def rnd_range(a, b):
    return a + (rnd() % (b - a + 1))

# =========================
# PALETA (256) en uint16 RGB565 (sin floats, sin trig)
# =========================
PAL = array('H', [0] * 256)
for i in range(256):
    if i < 90:
        # centro: blanco-azul
        r = 140 + (i * 1) // 2
        g = 150 + (i * 2) // 3
        b = 210 + (i * 1) // 4
    elif i < 170:
        # transición a violeta
        k = i - 90
        r = 160 + k
        g = 200 - (k * 2) // 3
        b = 255
    else:
        # periferia: morado profundo
        k = i - 170
        r = 255
        g = 90 - (k // 2)
        b = 255 - (k // 3)

    if r > 255: r = 255
    if g < 0: g = 0
    if b < 0: b = 0
    if g > 255: g = 255
    if b > 255: b = 255

    PAL[i] = color565(r, g, b)

BG   = color565(0, 0, 0)
DUST = color565(6, 6, 10)
ORNG = color565(255, 140, 40)
WHT  = color565(255, 255, 255)

# =========================
# STARS (arrays, no listas de listas)
# r: uint16, ang: uint16, bright: uint8, drift: int8
# =========================
r_arr   = array('H', [0] * STAR_COUNT)
ang_arr = array('H', [0] * STAR_COUNT)
b_arr   = bytearray(STAR_COUNT)
d_arr   = array('b', [0] * STAR_COUNT)

arm_step = 360 // ARMS

for i in range(STAR_COUNT):
    u = rnd() & 0xFFFF
    # Distribución radial sin floats: r ~ u^2 (más denso al centro)
    u2 = (u * u) >> 16          # 0..65535
    r  = (u2 * MAX_R) >> 16     # 0..MAX_R

    arm = rnd() % ARMS
    base_ang = rnd() % 360
    ang = (base_ang + (r * TWIST) // (MAX_R if MAX_R else 1) + arm * arm_step) % 360

    br = rnd_range(50, 255)
    if r < 35 and (rnd() & 3) == 0:
        br = 255

    drift = rnd_range(-1, 2)

    r_arr[i]   = r
    ang_arr[i] = ang
    b_arr[i]   = br
    d_arr[i]   = drift

# Shooting star state: x,y,vx,vy,life
shoot = None

# =========================
# MAIN LOOP
# =========================
try:
    cx = WIDTH // 2
    cy = HEIGHT // 2
    frame = 0

    while True:
        t0 = time.ticks_ms()
        fb.fill(BG)

        # Fondo estelar sin guardar lista (determinista)
        for i in range(90):
            x = (i * 97 + frame * 3) % WIDTH
            y = (i * 57 + frame * 5) % HEIGHT
            fb.pixel(x, y, PAL[(i * 19) & 255])

        # Polvo barato
        for _ in range(50):
            fb.pixel(rnd() % WIDTH, rnd() % HEIGHT, DUST)

        # Galaxia: rotación diferencial
        for i in range(STAR_COUNT):
            r = r_arr[i]
            ang = ang_arr[i]
            br = b_arr[i]

            omega = 2 + (90 // (r + 12))   # centro gira más rápido
            ang = (ang + omega) % 360

            # drift radial leve cada tanto
            if (frame & 15) == 0:
                rr = r + d_arr[i]
                if rr < 0: rr = 0
                if rr > MAX_R: rr = MAX_R
                r = rr

            x = cx + (r * cos_deg(ang)) // S
            y = cy + (r * sin_deg(ang) * ELLIPSE_Y) // (S * 1000)

            if 0 <= x < WIDTH and 0 <= y < HEIGHT:
                rad_t = (r * 255) // (MAX_R if MAX_R else 1)
                idx = (rad_t + (br // 3)) & 255
                c = PAL[idx]
                fb.pixel(x, y, c)

                # sparkle para las más brillantes (sin gastar mucho)
                if br > 240:
                    if x + 1 < WIDTH: fb.pixel(x + 1, y, c)
                    if y + 1 < HEIGHT: fb.pixel(x, y + 1, c)

            r_arr[i] = r
            ang_arr[i] = ang

        # Núcleo (glow simple)
        for _ in range(220):
            a = rnd() % 360
            rr = rnd() % 18
            x = cx + (rr * cos_deg(a)) // S
            y = cy + (rr * sin_deg(a) * ELLIPSE_Y) // (S * 1000)
            if 0 <= x < WIDTH and 0 <= y < HEIGHT:
                fb.pixel(x, y, PAL[25])

        # Shooting star spawn
        if shoot is None and (rnd() % SHOOT_CHANCE) == 0:
            sx = rnd() % WIDTH
            sy = rnd() % HEIGHT
            vx = rnd_range(-5, 5)
            vy = rnd_range(-5, 5)
            if vx == 0 and vy == 0:
                vx = 4
            shoot = [sx, sy, vx, vy, rnd_range(10, 18)]

        # Shooting star update/draw
        if shoot is not None:
            sx, sy, vx, vy, life = shoot

            # trail
            x2, y2 = sx, sy
            for _ in range(SHOOT_LEN):
                x2 -= vx
                y2 -= vy
                if 0 <= x2 < WIDTH and 0 <= y2 < HEIGHT:
                    fb.pixel(x2, y2, ORNG)

            if 0 <= sx < WIDTH and 0 <= sy < HEIGHT:
                fb.pixel(sx, sy, WHT)

            sx += vx
            sy += vy
            life -= 1
            if life <= 0 or sx < -20 or sx > WIDTH + 20 or sy < -20 or sy > HEIGHT + 20:
                shoot = None
            else:
                shoot[0], shoot[1], shoot[4] = sx, sy, life

        blit_fullscreen()

        frame += 1
        if (frame & 31) == 0:
            gc.collect()

        dt = time.ticks_diff(time.ticks_ms(), t0)
        if dt < FRAME_MS:
            time.sleep_ms(FRAME_MS - dt)

except KeyboardInterrupt:
    fb.fill(0)
    blit_fullscreen()
