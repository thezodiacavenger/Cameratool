"""Generate Windows build resources: the .ico and the version-info resource that
shows 'Emmet Adriaans' / 'Created for Vistek Ltd.' in the exe's Properties > Details.
usage: python tools/make_build_files.py   (writes build/CameraCheck.ico, build/version_info.txt)"""
import math
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "build")
os.makedirs(OUT, exist_ok=True)
init = open(os.path.join(ROOT, "camcheck", "__init__.py"), encoding="utf-8").read()
ver = re.search(r'__version__ = "([^"]+)"', init).group(1)
nums = (list(map(int, re.findall(r"\d+", ver))) + [0, 0, 0, 0])[:4]

info = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={tuple(nums)}, prodvers={tuple(nums)}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Emmet Adriaans'),
      StringStruct('FileDescription', 'Camera Check - used camera evaluation'),
      StringStruct('FileVersion', '{ver}'),
      StringStruct('InternalName', 'CameraCheck'),
      StringStruct('LegalCopyright', '\\u00a9 Emmet Adriaans. Created for Vistek Ltd.'),
      StringStruct('OriginalFilename', 'CameraCheck.exe'),
      StringStruct('ProductName', 'Camera Check'),
      StringStruct('ProductVersion', '{ver}'),
      StringStruct('Comments', 'Created by Emmet Adriaans for Vistek Ltd.')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
with open(os.path.join(OUT, "version_info.txt"), "w", encoding="utf-8") as fh:
    fh.write(info)

try:
    from PIL import Image, ImageDraw
except ImportError:
    sys.exit("Pillow is needed to draw the icon: pip install pillow")

S = 512
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
# rounded square with vertical gradient (rose -> crimson)
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([16, 16, S - 16, S - 16], radius=112, fill=255)
grad = Image.new("RGBA", (S, S))
gd = ImageDraw.Draw(grad)
for y in range(S):
    t = y / S
    gd.line([(0, y), (S, y)], fill=(int(225 - 66 * t), int(29 - 11 * t), int(72 - 15 * t), 255))
img.paste(grad, (0, 0), mask)
cx = cy = S / 2
d.ellipse([cx - 150, cy - 150, cx + 150, cy + 150], outline=(255, 255, 255, 255), width=28)
for i in range(6):  # aperture blades
    a = math.radians(i * 60 - 90)
    b = math.radians(i * 60 - 90 + 38)
    p1 = (cx + 128 * math.cos(a), cy + 128 * math.sin(a))
    p2 = (cx + 128 * math.cos(b), cy + 128 * math.sin(b))
    p3 = (cx + 46 * math.cos(b + 0.9), cy + 46 * math.sin(b + 0.9))
    d.polygon([p1, p2, p3], fill=(255, 255, 255, 235))
d.ellipse([cx - 40, cy - 40, cx + 40, cy + 40], fill=(255, 255, 255, 255))
img.save(os.path.join(OUT, "CameraCheck.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.resize((256, 256)).save(os.path.join(OUT, "CameraCheck.png"))
print(f"build files written for {ver}")
