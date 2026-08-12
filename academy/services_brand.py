"""학원 로고·아이콘 보관.

올린 파일을 그대로 두면 사진기로 찍은 몇 MB 짜리가 매 화면마다 내려가 느려진다.
받는 즉시 화면에 필요한 만큼으로 줄여 PNG 로 저장한다.

파일은 /data/public/brand/ 에 두며 이 경로는 호스트에 연결돼 있어 컨테이너를 다시
만들어도 남는다(등하원 음성과 같은 방식).
"""
import os

from django.conf import settings

BRAND_SUBDIR = "brand"
BRAND_URL_PREFIX = "/public/brand"

# logo  : 화면 맨 위 가로형. 높이 28px 로 쓰지만 고해상도 화면을 위해 2~3배로 둔다.
# icon  : 즐겨찾기·주소창 아이콘. 반드시 정사각형이라 남는 쪽을 잘라 맞춘다.
KINDS = {
    "logo": {"label": "로고(가로형)", "max": (480, 120), "square": False},
    "icon": {"label": "아이콘(정사각형)", "max": (180, 180), "square": True},
}

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def brand_dir():
    d = os.path.join(settings.DATA_DIR, "public", BRAND_SUBDIR)
    os.makedirs(d, exist_ok=True)
    return d


def brand_path(kind):
    return os.path.join(brand_dir(), "%s.png" % kind)


def brand_url(kind):
    """주소 끝에 저장 시각을 붙인다. 새로 올렸는데 예전 그림이 남아 보이는 걸 막는다."""
    p = brand_path(kind)
    if not os.path.exists(p):
        return ""
    return "%s/%s.png?v=%d" % (BRAND_URL_PREFIX, kind, int(os.path.getmtime(p)))


def brand_all():
    return {k: brand_url(k) for k in KINDS}


def _fit(img, box, square):
    """가로세로 비율은 지키면서 box 안에 넣는다. 정사각형은 가운데를 남기고 자른다."""
    from PIL import Image

    if square:
        w, h = img.size
        side = min(w, h)
        img = img.crop(((w - side) // 2, (h - side) // 2,
                        (w - side) // 2 + side, (h - side) // 2 + side))
    img.thumbnail(box, Image.LANCZOS)
    return img


def save_brand(kind, fp):
    """올린 파일을 줄여 저장한다. 성공하면 (True, 주소) 실패하면 (False, 사유)."""
    if kind not in KINDS:
        return False, "종류가 올바르지 않습니다."
    cfg = KINDS[kind]
    try:
        from PIL import Image

        img = Image.open(fp)
        img.load()
        # 투명한 로고를 흰 배경 위에 얹으면 테두리가 지저분해진다. 투명은 그대로 둔다.
        img = img.convert("RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB")
        img = _fit(img, cfg["max"], cfg["square"])
        tmp = brand_path(kind) + ".tmp"
        img.save(tmp, "PNG", optimize=True)
        os.replace(tmp, brand_path(kind))
        return True, brand_url(kind)
    except Exception as e:                      # 그림이 아닌 파일·깨진 파일 등
        return False, "이미지를 읽을 수 없습니다. (%s)" % str(e)[:120]


def delete_brand(kind):
    p = brand_path(kind)
    if os.path.exists(p):
        try:
            os.remove(p)
        except OSError:
            pass
