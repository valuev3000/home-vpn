from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

FONT = "/usr/share/fonts/noto/NotoSans-Bold.ttf"
RED = "#ef3340"
WHITE = "#ffffff"
DARK = "#111827"


def font(size):
    return ImageFont.truetype(FONT, size)


def arrow(draw, start, end, width=18):
    draw.line([start, end], fill=RED, width=width)
    import math
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    length = 42
    spread = 0.65
    p1 = (end[0] - length * math.cos(angle - spread), end[1] - length * math.sin(angle - spread))
    p2 = (end[0] - length * math.cos(angle + spread), end[1] - length * math.sin(angle + spread))
    draw.polygon([end, p1, p2], fill=RED)


def badge(draw, xy, number):
    x, y = xy
    r = 54
    draw.ellipse((x-r-6, y-r-6, x+r+6, y+r+6), fill=WHITE)
    draw.ellipse((x-r, y-r, x+r, y+r), fill=RED)
    text = str(number)
    box = draw.textbbox((0, 0), text, font=font(62))
    draw.text((x-(box[2]-box[0])/2, y-(box[3]-box[1])/2-5), text, font=font(62), fill=WHITE)


def label(draw, box, text):
    draw.rounded_rectangle(box, radius=28, fill=DARK, outline=WHITE, width=5)
    x1, y1, x2, y2 = box
    draw.text((x1+28, y1+18), text, font=font(38), fill=WHITE)


root = Path("/home/user/Загрузки")
out = Path(__file__).parent / "static" / "original"

# Шаг 1: вставить заранее скопированную ссылку.
im = Image.open(root / "Screenshot_20260915-213846_Happ.png").convert("RGB")
d = ImageDraw.Draw(im)
label(d, (38, 1850, 650, 1935), "1. Нажмите «Из буфера»")
badge(d, (130, 2185), 1)
arrow(d, (205, 2140), (285, 2250))
d.rounded_rectangle((55, 2170, 500, 2335), radius=45, outline=RED, width=16)
im.save(out / "happ-original-step-1.png", optimize=True)

# Шаги 2–3: выбрать сервер и включить VPN.
im = Image.open(root / "Screenshot_20260915-215012_Happ.png").convert("RGB")
d = ImageDraw.Draw(im)
label(d, (35, 820, 695, 905), "2. Выберите сервер")
badge(d, (1000, 930), 2)
arrow(d, (940, 965), (820, 1035))
d.rounded_rectangle((30, 960, 1045, 1115), radius=32, outline=RED, width=14)
label(d, (35, 650, 680, 735), "3. Нажмите питание")
badge(d, (795, 535), 3)
arrow(d, (735, 545), (610, 565))
d.ellipse((355, 385, 725, 755), outline=RED, width=16)
im.save(out / "happ-original-step-2.png", optimize=True)

# Дополнительный режим: выбрать приложения, которые должны использовать VPN.
im = Image.open(root / "000.png").convert("RGB")
d = ImageDraw.Draw(im)
label(d, (145, 235, 700, 320), "4. Откройте настройки")
badge(d, (205, 170), 4)
arrow(d, (155, 175), (90, 205))
d.ellipse((35, 130, 125, 245), outline=RED, width=14)
im.save(out / "happ-apps-step-4.png", optimize=True)

im = Image.open(root / "111.png").convert("RGB")
d = ImageDraw.Draw(im)
label(d, (40, 660, 820, 745), "5. Откройте выбор приложений")
badge(d, (965, 1085), 5)
arrow(d, (910, 1095), (800, 1125))
d.rounded_rectangle((20, 1050, 1055, 1195), radius=30, outline=RED, width=14)
im.save(out / "happ-apps-step-5.png", optimize=True)

im = Image.open(root / "222.png").convert("RGB")
d = ImageDraw.Draw(im)
label(d, (35, 555, 820, 640), "6. Выберите «ВКЛ» и программы")
badge(d, (540, 420), 6)
arrow(d, (540, 485), (540, 455))
d.rounded_rectangle((350, 355, 700, 485), radius=28, outline=RED, width=14)
badge(d, (980, 880), 6)
arrow(d, (925, 890), (855, 920))
d.rounded_rectangle((25, 795, 1055, 1135), radius=28, outline=RED, width=14)
im.save(out / "happ-apps-step-6.png", optimize=True)
