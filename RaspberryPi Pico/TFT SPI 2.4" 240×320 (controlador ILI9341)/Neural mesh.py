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

FRAME_MS = 25  # 20-30 fluido

# Kill switch: GP22 a GND
KILL_PIN = 22
kill = Pin(KILL_PIN, Pin.IN, Pin.PULL_UP)

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
# RNG rápido (LCG)
# =========================
_seed = 0xC0FFEE12
def rnd():
    global _seed
    _seed = (_seed * 1664525 + 1013904223) & 0xFFFFFFFF
    return _seed

def rndi(a, b):
    return a + (rnd() % (b - a + 1))

# =========================
# LOOK & PERFORMANCE
# =========================
NODES = 34              # 28-40 recomendado
LINK_DIST = 62          # distancia para conectar
LINK_DIST2 = LINK_DIST * LINK_DIST

# si quieres más loco: baja a 45-55 pero más líneas = más CPU
MAX_LINKS_PER_NODE = 5  # limita conexiones para FPS estable

# Coordenadas: mostrar solo algunos nodos para que se vea “desgraciado” pero legible
SHOW_COORDS = True
COORDS_EVERY_NTH = 4     # 3-6: entre más bajo, más texto
COORDS_STYLE_HEX = True  # True: "xAF y3C" / False: "(123,45)"

# =========================
# Colors (cyber)
# =========================
BG          = color565(0, 0, 0)
NODE_COL    = color565(0, 240, 255)   # cian
NODE_DIM    = color565(0, 120, 140)
LINK_COL    = color565(0, 90, 120)    # líneas suaves
LINK_HI     = color565(0, 160, 200)   # líneas cercanas
TEXT_COL    = color565(180, 180, 220)
HUD_COL     = color565(90, 90, 130)

# =========================
# Nodos: posiciones y velocidades (prealocado)
# =========================
x = array('h', [0] * NODES)
y = array('h', [0] * NODES)
vx = array('h', [0] * NODES)
vy = array('h', [0] * NODES)

MARGIN = 10

for i in range(NODES):
    x[i] = rndi(MARGIN, W - MARGIN - 1)
    y[i] = rndi(MARGIN, H - MARGIN - 1)
    # vel suave
    vx[i] = rndi(-14, 14)
    vy[i] = rndi(-14, 14)
    if vx[i] == 0: vx[i] = 7
    if vy[i] == 0: vy[i] = -9

# =========================
# Util: dibujar nodo con glow barato
# =========================
def node_glow(px, py, bright=True):
    # cross + pixel central
    if bright:
        c0 = NODE_COL
        c1 = NODE_DIM
    else:
        c0 = NODE_DIM
        c1 = LINK_COL

    fb.pixel(px, py, c0)
    if px > 0:       fb.pixel(px - 1, py, c1)
    if px < W - 1:   fb.pixel(px + 1, py, c1)
    if py > 0:       fb.pixel(px, py - 1, c1)
    if py < H - 1:   fb.pixel(px, py + 1, c1)

# =========================
# MAIN LOOP
# =========================
try:
    # ventana anti-busy
    time.sleep(1)

    frame = 0
    cx = W // 2
    cy = H // 2

    while True:
        start = time.ticks_ms()

        # salida garantizada
        if not kill.value():
            raise KeyboardInterrupt

        fb.fill(BG)

        # Update nodos
        for i in range(NODES):
            xi = x[i] + (vx[i] >> 2)  # /4 -> movimiento suave
            yi = y[i] + (vy[i] >> 2)

            # bounce
            if xi < MARGIN:
                xi = MARGIN
                vx[i] = -vx[i]
            elif xi > W - MARGIN - 1:
                xi = W - MARGIN - 1
                vx[i] = -vx[i]

            if yi < MARGIN:
                yi = MARGIN
                vy[i] = -vy[i]
            elif yi > H - MARGIN - 1:
                yi = H - MARGIN - 1
                vy[i] = -vy[i]

            x[i] = xi
            y[i] = yi

        # Conexiones (distancia) con límite por nodo (FPS)
        # Estrategia: para cada i, conecta con j>i y cuenta links_i
        for i in range(NODES):
            links_i = 0
            xi = x[i]
            yi = y[i]

            for j in range(i + 1, NODES):
                dx = xi - x[j]
                dy = yi - y[j]
                d2 = dx*dx + dy*dy

                if d2 <= LINK_DIST2:
                    # brillo según cercanía (sin sqrt)
                    # muy cerca => link fuerte
                    col = LINK_HI if d2 < (LINK_DIST2 >> 2) else LINK_COL
                    fb.line(xi, yi, x[j], y[j], col)

                    links_i += 1
                    if links_i >= MAX_LINKS_PER_NODE:
                        break

        # Dibuja nodos + coords
        # “Seleccionados” (más cercanos al centro) brillan
        for i in range(NODES):
            xi = x[i]
            yi = y[i]

            dcx = xi - cx
            dcy = yi - cy
            near_center = (dcx*dcx + dcy*dcy) < 70*70

            node_glow(xi, yi, bright=near_center)

            if SHOW_COORDS and (i % COORDS_EVERY_NTH == 0):
                if COORDS_STYLE_HEX:
                    # xAF y3C
                    s = "x{:02X} y{:02X}".format(xi & 0xFF, yi & 0xFF)
                else:
                    # (123,45)
                    s = "({},{})".format(xi, yi)

                # evita que el texto se salga
                tx = xi + 4
                ty = yi - 6
                if tx > W - 8*len(s): tx = xi - 8*len(s) - 2
                if ty < 0: ty = yi + 2
                if ty > H - 8: ty = H - 8

                fb.text(s, tx, ty, TEXT_COL)

        # HUD mínimo
        fb.text("", 4, 4, HUD_COL)

        blit()

        # anti-busy: cede CPU siempre
        dt = time.ticks_diff(time.ticks_ms(), start)
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
