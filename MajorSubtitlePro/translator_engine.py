# -*- coding: utf-8 -*-
"""محرك الترجمة: التقاط الصوت -> Whisper -> ترجمة إلى العربية."""
import array
import collections
import os
import subprocess
import sys
import traceback
import wave
from urllib.parse import quote

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

TEMP_AUDIO = "/tmp/live_chunk.wav"

# رمز اللغة في Tesseract (3 أحرف) -> رمز Whisper (حرفان)
WHISPER_LANGS = {
    "eng": "en", "fra": "fr", "deu": "de", "spa": "es",
    "ita": "it", "rus": "ru", "tur": "tr",
}

# عبارات يهلوس بها Whisper عادة عند الصمت أو الموسيقى
HALLUCINATIONS = (
    "thanks for watching", "thank you for watching", "subtitles by",
    "amara.org", "please subscribe", "like and subscribe",
    "subtitles made by", "translated by",
)

SILENCE_RMS = 250  # أقل من هذه القيمة يعتبر صمتاً (16-bit)


def _log(msg):
    print("[MajorSubtitlePro] %s" % msg)


def arabic_ratio(text):
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    arabic = sum(1 for c in letters if "\u0600" <= c <= "\u06ff")
    return arabic / float(len(letters))


class LiveTranslatorEngine:
    def __init__(self, api_key="", language="eng"):
        self.api_key = api_key
        self.language = WHISPER_LANGS.get(language, "")
        self.temp_audio = TEMP_AUDIO
        self.last_error = ""
        self._cache = collections.OrderedDict()
        self._session = requests.Session() if requests else None

    # ------------------------------------------------------------------
    def pop_error(self):
        msg, self.last_error = self.last_error, ""
        return msg

    def _remove_audio(self):
        try:
            os.remove(self.temp_audio)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # الصوت
    # ------------------------------------------------------------------
    def capture_audio_chunk(self, service_ref, duration=4):
        """service_ref: نص مرجع القناة (يُقرأ من الخيط الرئيسي ثم يُمرَّر هنا)."""
        if not service_ref:
            return False
        self._remove_audio()
        stream_url = "http://127.0.0.1:8001/" + quote(service_ref, safe=":%/")
        cmd = [
            "ffmpeg", "-y", "-nostdin", "-loglevel", "error",
            "-analyzeduration", "1000000", "-probesize", "1000000",
            "-i", stream_url,
            "-t", str(duration),
            "-vn", "-map", "0:a:0?",
            "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            self.temp_audio,
        ]
        try:
            proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  timeout=duration + 6)
            ok = proc.returncode == 0 and os.path.isfile(self.temp_audio) \
                and os.path.getsize(self.temp_audio) > 2000
            if not ok:
                self.last_error = "تعذر التقاط الصوت من البث (تحقق من ffmpeg)"
                self._remove_audio()
            return ok
        except FileNotFoundError:
            self.last_error = "الأداة ffmpeg غير مثبتة"
        except subprocess.TimeoutExpired:
            self.last_error = "انتهت مهلة التقاط الصوت"
        except Exception:
            _log("capture_audio_chunk: " + traceback.format_exc())
        self._remove_audio()
        return False

    def is_silent(self, path=None):
        """يفحص مستوى الصوت (RMS) دون الحاجة إلى audioop."""
        path = path or self.temp_audio
        try:
            with wave.open(path, "rb") as wf:
                if wf.getsampwidth() != 2:
                    return False
                frames = wf.readframes(wf.getnframes())
            samples = array.array("h")
            samples.frombytes(frames[: len(frames) - (len(frames) % 2)])
            if sys.byteorder == "big":
                samples.byteswap()
            if not samples:
                return True
            step = 4
            picked = samples[::step]
            rms = (sum(s * s for s in picked) / float(len(picked))) ** 0.5
            return rms < SILENCE_RMS
        except Exception:
            _log("is_silent: " + traceback.format_exc())
            return False

    def translate_audio_via_whisper(self):
        if not os.path.isfile(self.temp_audio):
            return ""
        try:
            if requests is None:
                self.last_error = "حزمة python3-requests غير مثبتة"
                return ""
            if not self.api_key:
                self.last_error = "ضع مفتاح OpenAI API في الإعدادات لترجمة الصوت"
                return ""
            if self.is_silent():
                return ""  # لا نرسل الصمت (توفير للتكلفة ومنع الهلوسة)

            url = "https://api.openai.com/v1/audio/transcriptions"
            headers = {"Authorization": "Bearer " + self.api_key}
            data = {"model": "whisper-1", "response_format": "json", "temperature": "0"}
            if self.language:
                data["language"] = self.language

            with open(self.temp_audio, "rb") as audio_file:
                files = {"file": ("chunk.wav", audio_file, "audio/wav")}
                response = self._session.post(url, headers=headers, files=files, data=data, timeout=15)

            if response.status_code == 200:
                raw_text = (response.json().get("text") or "").strip()
                low = raw_text.lower()
                if len(raw_text) < 2 or any(p in low for p in HALLUCINATIONS):
                    return ""
                return self.translate_text_to_arabic(raw_text)
            if response.status_code == 401:
                self.last_error = "مفتاح OpenAI غير صحيح"
            elif response.status_code == 429:
                self.last_error = "تجاوزت حد الطلبات أو الرصيد في OpenAI"
            else:
                self.last_error = "خطأ من OpenAI (HTTP %d)" % response.status_code
        except Exception:
            self.last_error = "تعذر الاتصال بخدمة Whisper"
            _log("translate_audio_via_whisper: " + traceback.format_exc())
        finally:
            self._remove_audio()
        return ""

    # ------------------------------------------------------------------
    # الترجمة النصية
    # ------------------------------------------------------------------
    def translate_text_to_arabic(self, text):
        """تُرجع الترجمة العربية، أو "" عند الفشل (ولا تُرجع النص الأصلي على أنه ترجمة)."""
        if not text or not text.strip():
            return ""
        text = text.strip()[:900]
        if arabic_ratio(text) > 0.5:
            return text
        if text in self._cache:
            self._cache.move_to_end(text)
            return self._cache[text]
        if requests is None:
            self.last_error = "حزمة python3-requests غير مثبتة"
            return ""

        url = "https://translate.googleapis.com/translate_a/single"
        params = {"client": "gtx", "sl": "auto", "tl": "ar", "dt": "t", "q": text}
        try:
            res = self._session.get(url, params=params, timeout=6)
            if res.status_code == 200:
                data = res.json()
                result = "".join(s[0] for s in data[0] if s and s[0]).strip()
                if result:
                    self._cache[text] = result
                    if len(self._cache) > 200:
                        self._cache.popitem(last=False)
                return result
            self.last_error = "فشلت الترجمة (HTTP %d)" % res.status_code
        except Exception:
            self.last_error = "تعذر الاتصال بخدمة الترجمة"
            _log("translate_text_to_arabic: " + traceback.format_exc())
        return ""
