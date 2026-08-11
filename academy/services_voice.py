"""등하원 안내 음성 파일 만들기.

브라우저 음성 읽기(TTS)에 기대지 않고 미리 만든 mp3 를 재생한다. 키오스크 태블릿에
한국어 음성이 없어도, 기기·브라우저가 무엇이든 똑같이 들리게 하기 위함이다.

파일은 /data/public/voice/ 에 두며 이 경로는 호스트에 연결돼 있어 컨테이너를 다시
만들어도 남는다. 만들 때만 인터넷이 필요하고, 재생은 우리 서버 파일로 한다.
"""
import asyncio
import os

from django.conf import settings

# 학생 성별에 맞춰 목소리를 고른다(남학생은 남자 목소리, 여학생은 여자 목소리).
VOICE_MALE = "ko-KR-HyunsuMultilingualNeural"    # 현수
VOICE_FEMALE = "ko-KR-SunHiNeural"               # 선히
VOICE_DEFAULT = VOICE_FEMALE                     # 성별 미입력 시

KINDS = {
    "in": "%s님 등원하였습니다",
    "out": "%s님 하원하였습니다",
}

VOICE_SUBDIR = "voice"
VOICE_URL_PREFIX = "/public/voice"


def voice_dir():
    d = os.path.join(settings.DATA_DIR, "public", VOICE_SUBDIR)
    os.makedirs(d, exist_ok=True)
    return d


def voice_path(student_id, kind):
    return os.path.join(voice_dir(), "%s_%s.mp3" % (student_id, kind))


def voice_url(student_id, kind):
    return "%s/%s_%s.mp3" % (VOICE_URL_PREFIX, student_id, kind)


def voice_for(gender):
    return {"M": VOICE_MALE, "F": VOICE_FEMALE}.get(gender or "", VOICE_DEFAULT)


def has_voice(student_id):
    return all(os.path.exists(voice_path(student_id, k)) for k in KINDS)


def voice_status(student_id):
    return {k: os.path.exists(voice_path(student_id, k)) for k in KINDS}


def _speak_to_file(text, voice, path):
    import edge_tts

    async def run():
        c = edge_tts.Communicate(text, voice)
        await c.save(path)

    # 만들다 실패하면 반쪽짜리 파일이 남아 재생이 깨진다. 임시로 만든 뒤 옮긴다.
    tmp = path + ".tmp"
    asyncio.run(run())
    if os.path.exists(tmp):
        os.replace(tmp, path)


def build_student_voice(student, name=None, gender=None, kinds=None):
    """한 학생의 안내 음성을 만든다. 성공하면 (True, "") 실패하면 (False, 사유).

    등록·이름 변경 때 자동으로 부르지만, 실패해도 등록 자체는 막지 않는다
    (인터넷이 잠깐 끊겨도 학생은 등록돼야 하고, 음성은 나중에 다시 만들면 된다)."""
    try:
        sp = getattr(student, "student_profile", None)
        nm = (name or "").strip()
        if not nm:
            try:
                nm = student.userprofile.real_name or student.username
            except Exception:
                nm = student.username
        if not nm:
            return False, "이름이 없습니다."
        g = gender if gender is not None else (sp.gender if sp else "")
        voice = voice_for(g)
        made = []
        for kind in (kinds or list(KINDS)):
            tpl = KINDS.get(kind)
            if not tpl:
                continue
            _speak_to_file(tpl % nm, voice, voice_path(student.id, kind))
            made.append(kind)
        return True, ""
    except Exception as e:                      # 네트워크 오류·패키지 없음 등
        return False, str(e)[:200]


def delete_student_voice(student_id):
    for kind in KINDS:
        p = voice_path(student_id, kind)
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
