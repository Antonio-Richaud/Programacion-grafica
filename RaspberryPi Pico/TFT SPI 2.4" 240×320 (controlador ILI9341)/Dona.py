from machine import Pin, SPI
from ili9341 import ILI9341, color565
import framebuf, time, gc
from array import array
import math

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

# FPS / Responsividad a Ctrl+C
FRAME_MS = 30  # 30-40 recomendado

# Grid ASCII (font 8x8)
COLS = WIDTH  // 8   # 30
ROWS = HEIGHT // 8   # 40

# Donut detalle (más grande step = más rápido)
THETA_STEP = 18
PHI_STEP   = 12

# Fixed-point
S  = 1024
R1 = 1 * S
R2 = 2 * S
K2 = 5 * S

# Escala de proyección (tamaño del donut)
X_SCALE = 16
Y_SCALE = 11

# ---- AJUSTE DE CENTRADO ----
# Más a la derecha: +1, +2...
# Más arriba: -1, -2...
CENTER_X_OFF = 4
CENTER_Y_OFF = -4

# HUD
SHOW_TITLE  = True
TITLE_TEXT  = "ASCII DONUT // PICO"
TITLE_Y_PIX = HEIGHT - 8  # abajo

BG = color565(0, 0, 0)

# =========================
# INIT DISPLAY + FRAMEBUFFER
# =========================
spi = SPI(0, baudrate=SPI_BAUD, polarity=0, phase=0,
          sck=Pin(PIN_SCK), mosi=Pin(PIN_MOSI))

tft = ILI9341(spi, cs=PIN_CS, dc=PIN_DC, rst=PIN_RST,
              width=WIDTH, height=HEIGHT, rotation=ROTATION, bgr=BGR)

buf = bytearray(tft.width * tft.height * 2)
fb = framebuf.FrameBuffer(buf, tft.width, tft.height, framebuf.RGB565)

def blit_fullscreen():
    tft._begin_write(0, 0, tft.width - 1, tft.height - 1)
    tft.spi.write(buf)
    tft._end_write()

# =========================
# LUT sin/cos (0..359) * S
# =========================
sinLUT = array('h', [0] * 360)
for a in range(360):
    sinLUT[a] = int(math.sin(a * math.pi / 180.0) * S)

def sin_deg(a): return sinLUT[a % 360]
def cos_deg(a): return sinLUT[(a + 90) % 360]

# =========================
# Shading ASCII
# =========================
SHADE = " .,-~:;=!*#$@"
NLEV  = len(SHADE) - 1

zbuf  = array('h', [0] * (COLS * ROWS))
lines = [bytearray(b" " * COLS) for _ in range(ROWS)]

# Color neón para el texto
PAL = array('H', [0] * 256)
for i in range(256):
    if i < 85:
        r = 0
        g = i * 3
        b = 255
    elif i < 170:
        k = i - 85
        r = k * 3
        g = 255
        b = 255 - (k * 3)
    else:
        k = i - 170
        r = 255
        g = 255 - (k * 3)
        b = k * 3

    if g < 0: g = 0
    if b < 0: b = 0
    if g > 255: g = 255
    if b > 255: b = 255

    PAL[i] = color565(r, g, b)

# =========================
# Rotaciones fixed-point
# =========================
def rot_x(x, y, z, cx, sx):
    y1 = (y * cx - z * sx) // S
    z1 = (y * sx + z * cx) // S
    return x, y1, z1

def rot_z(x, y, z, cz, sz):
    x1 = (x * cz - y * sz) // S
    y1 = (x * sz + y * cz) // S
    return x1, y1, z

# =========================
# Render
# =========================
def render_ascii_donut(A, B, text_color):
    # limpia buffers
    for i in range(COLS * ROWS):
        zbuf[i] = 0
    for r in range(ROWS):
        ln = lines[r]
        for c in range(COLS):
            ln[c] = 32

    cxA = cos_deg(A); sxA = sin_deg(A)
    czB = cos_deg(B); szB = sin_deg(B)

    # Centro para grid par: usar COLS//2, ROWS//2 y ajustar offsets
    cx2 = (COLS // 2) + CENTER_X_OFF
    cy2 = (ROWS // 2) + CENTER_Y_OFF

    for phi in range(0, 360, PHI_STEP):
        cph = cos_deg(phi)
        sph = sin_deg(phi)

        for th in range(0, 360, THETA_STEP):
            cth = cos_deg(th)
            sth = sin_deg(th)

            circlex = R2 + (R1 * cth) // S
            circley = (R1 * sth) // S

            x = (circlex * cph) // S
            y = circley
            z = (circlex * sph) // S

            nx = (cph * cth) // S
            ny = sth
            nz = (sph * cth) // S

            x, y, z = rot_x(x, y, z, cxA, sxA)
            x, y, z = rot_z(x, y, z, czB, szB)

            nx, ny, nz = rot_x(nx, ny, nz, cxA, sxA)
            nx, ny, nz = rot_z(nx, ny, nz, czB, szB)

            zz = z + K2
            if zz <= 0:
                continue

            ooz = (S * 64) // (zz // S + 1)

            xp = cx2 + (x * X_SCALE) // zz
            yp = cy2 - (y * Y_SCALE) // zz

            if 0 <= xp < COLS and 0 <= yp < ROWS:
                idx = yp * COLS + xp
                if ooz > zbuf[idx]:
                    zbuf[idx] = ooz

                    dot = ny - nz
                    if dot <= 0:
                        ch = SHADE[0]
                    else:
                        lev = (dot * NLEV) // (2 * S)
                        if lev < 0: lev = 0
                        if lev > NLEV: lev = NLEV
                        ch = SHADE[lev]

                    lines[yp][xp] = ord(ch)

    # dibuja en pantalla
    fb.fill(BG)
    ypix = 0
    for r in range(ROWS):
        fb.text(lines[r].decode("ascii"), 0, ypix, text_color)
        ypix += 8

    if SHOW_TITLE:
        fb.text(TITLE_TEXT, 8, TITLE_Y_PIX, text_color)

# =========================
# LOOP (stop seguro)
# =========================
def main():
    # Ventana para detener fácil antes de arrancar
    time.sleep(1)

    A = 0
    B = 0
    t = 0
    frame = 0

    while True:
        start = time.ticks_ms()

        col = PAL[t & 255]
        render_ascii_donut(A, B, col)
        blit_fullscreen()

        A = (A + 6) % 360
        B = (B + 4) % 360
        t = (t + 3) & 255

        # Cede CPU siempre -> Thonny puede interrumpir
        dt = time.ticks_diff(time.ticks_ms(), start)
        if dt < FRAME_MS:
            time.sleep_ms(FRAME_MS - dt)
        else:
            time.sleep_ms(1)

        frame += 1
        if (frame & 31) == 0:
            gc.collect()

try:
    main()
except KeyboardInterrupt:
    fb.fill(0)
    blit_fullscreen()
