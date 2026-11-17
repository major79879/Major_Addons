# -*- coding: utf-8 -*-
"""استخراج نص الترجمة الحية: Teletext (محاولة تقريبية) و DVB Bitmap عبر OCR."""
import os
import re
import subprocess
import threading
import traceback

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

try:
    from enigma import iServiceInformation
except ImportError:  # للاختبار خارج الجهاز
    iServiceInformation = None

# حالات النتيجة
NONE = "none"   # لا توجد ترجمة ظاهرة
SAME = "same"   # نفس الترجمة السابقة (النص موجود في الحقل الثاني)
NEW = "new"     # ترجمة جديدة

TEMP_OSD = "/tmp/sub_osd.png"
TEMP_CROP = "/tmp/sub_crop.png"
TEMP_FILES = [TEMP_OSD, TEMP_CROP]

# قفل مشترك لأي استدعاء لأداة grab (حتى لا تتزاحم الخيوط)
GRAB_LOCK = threading.Lock()

_NOISE_RE = re.compile(
    r"\b(PID|PES|Stream|Packet|Data|Length|Field|identifier|Teletext|Magazine|Header|Table|Section)\b",
    re.IGNORECASE,
)


def _log(msg):
    print("[MajorSubtitlePro] %s" % msg)


class DVBTextExtractor:
    BRIGHT_THRESHOLD = 170      # سطوع النص (أبيض/أصفر) بعد الدمج على خلفية سوداء
    MIN_TEXT_PIXELS = 150       # أقل عدد بكسلات ساطعة لاعتبار أن هناك نصاً
    CROP_TOP_RATIO = 0.65       # نبدأ القص من 65% من ارتفاع الشاشة

    def __init__(self, session, source_lang="eng"):
        self.session = session
        self.source_lang = source_lang
        self.temp_osd = TEMP_OSD
        self.cropped_path = TEMP_CROP
        self.tessdata_dir = self.find_tessdata_dir()
        self.txt_pid = None
        self.last_error = ""
        self.exclude_rect = None   # (l, t, r, b) بإحداثيات الشاشة
        self.desktop_size = None   # (w, h)
        self._state_lock = threading.Lock()
        self._extract_lock = threading.Lock()
        self._last_text = {"txt": "", "bmp": ""}
        self._last_sig = None

    # ------------------------------------------------------------------
    # إعدادات
    # ------------------------------------------------------------------
    def find_tessdata_dir(self):
        possible_paths = [
            "/media/hdd/tessdata",
            "/media/mmcblk0p1/tessdata",
            "/media/mmcblk0p2/tessdata",
            "/media/sd/tessdata",
            "/usr/share/tessdata",
        ]
        for path in possible_paths:
            if os.path.isdir(path):
                return path
        return "/usr/share/tessdata"

    def set_exclusion(self, rect, desktop_size):
        """منطقة نافذة الترجمة الخاصة بنا، تُمسح قبل OCR حتى لا نقرأ ترجمتنا."""
        self.exclude_rect = rect
        self.desktop_size = desktop_size

    def reset(self):
        with self._state_lock:
            self._last_text = {"txt": "", "bmp": ""}
            self._last_sig = None

    def pop_error(self):
        msg, self.last_error = self.last_error, ""
        return msg

    def refresh_service_info(self, service=None):
        """يجب استدعاؤها من الخيط الرئيسي فقط (كائنات enigma ليست آمنة للخيوط)."""
        try:
            if service is None:
                service = self.session.nav.getCurrentService()
            info = service.info() if service else None
            pid = info.getInfo(iServiceInformation.sTXTPID) if (info and iServiceInformation) else -1
            self.txt_pid = pid if pid not in (None, -1) else None
        except Exception:
            self.txt_pid = None
            _log("refresh_service_info: " + traceback.format_exc())

    def get_teletext_pid(self):
        return hex(self.txt_pid) if self.txt_pid else None

    # ------------------------------------------------------------------
    def _classify(self, source, text):
        with self._state_lock:
            if not text:
                self._last_text[source] = ""
                return (NONE, "")
            if text == self._last_text[source]:
                return (SAME, text)
            self._last_text[source] = text
            return (NEW, text)

    def _same(self, source):
        with self._state_lock:
            text = self._last_text[source]
        return (SAME, text) if text else (NONE, "")

    # ------------------------------------------------------------------
    # Teletext (تقريبي: ما زال يعتمد على مخرجات dvbsnoop النصية)
    # ------------------------------------------------------------------
    def extract_teletext(self):
        pid = self.get_teletext_pid()
        if not pid:
            return self._classify("txt", "")

        cmd = ["dvbsnoop", "-n", "15", "-nph", pid]
        try:
            try:
                out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3).stdout
            except subprocess.TimeoutExpired as exc:
                out = exc.stdout or b""
            output = out.decode("utf-8", errors="ignore")
            lines = re.findall(r'[A-Za-z0-9 .,!?\'"-]{10,}', output)
            lines = [ln.strip() for ln in lines if not _NOISE_RE.search(ln)]
            clean_text = " ".join(dict.fromkeys(lines)).strip()  # يحافظ على الترتيب
            return self._classify("txt", clean_text)
        except FileNotFoundError:
            self.last_error = "الأداة dvbsnoop غير مثبتة"
        except Exception:
            _log("extract_teletext: " + traceback.format_exc())
        return self._same("txt")

    # ------------------------------------------------------------------
    # DVB Bitmap + OCR
    # ------------------------------------------------------------------
    def _cleanup_temp(self):
        for path in TEMP_FILES:
            try:
                os.remove(path)
            except OSError:
                pass

    def _grab_osd(self):
        if not GRAB_LOCK.acquire(timeout=3):
            return False
        try:
            try:
                os.remove(self.temp_osd)
            except OSError:
                pass
            subprocess.run(["grab", "-o", "-p", self.temp_osd],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
            return os.path.isfile(self.temp_osd)
        except FileNotFoundError:
            self.last_error = "الأداة grab غير متوفرة"
            return False
        except Exception:
            _log("grab: " + traceback.format_exc())
            return False
        finally:
            GRAB_LOCK.release()

    def _prepare_image(self):
        """يُرجع (صورة_ثنائية_للنص_الأبيض, عدد_البكسلات_الساطعة) أو None."""
        img = Image.open(self.temp_osd)
        img.load()
        img = img.convert("RGBA")
        w, h = img.size

        if self.exclude_rect and self.desktop_size and self.desktop_size[0] and self.desktop_size[1]:
            sx = w / float(self.desktop_size[0])
            sy = h / float(self.desktop_size[1])
            l, t, r, b = self.exclude_rect
            img.paste((0, 0, 0, 0), (int(l * sx), int(t * sy), int(r * sx) + 1, int(b * sy) + 1))

        crop = img.crop((0, int(h * self.CROP_TOP_RATIO), w, h))
        background = Image.new("RGBA", crop.size, (0, 0, 0, 255))
        gray = Image.alpha_composite(background, crop).convert("L")

        thr = self.BRIGHT_THRESHOLD
        bw = gray.point(lambda p: 255 if p > thr else 0)
        bright = bw.histogram()[255]
        return bw, bright

    def extract_dvb_bitmap(self):
        if Image is None:
            self.last_error = "مكتبة PIL غير مثبتة"
            return (NONE, "")
        if not self._extract_lock.acquire(timeout=5):
            return self._same("bmp")
        try:
            if not self._grab_osd():
                return self._same("bmp")

            prepared = self._prepare_image()
            bw, bright = prepared
            if bright < self.MIN_TEXT_PIXELS:
                self._last_sig = None
                return self._classify("bmp", "")

            sig = bw.resize((96, 24)).tobytes()
            if sig == self._last_sig:
                return self._same("bmp")
            self._last_sig = sig

            ocr_img = bw.point(lambda p: 255 - p)  # نص أسود على خلفية بيضاء
            ocr_img = ocr_img.resize((ocr_img.width * 2, ocr_img.height * 2))
            ocr_img.save(self.cropped_path)

            cmd = ["tesseract", self.cropped_path, "stdout",
                   "--tessdata-dir", self.tessdata_dir,
                   "-l", self.source_lang, "--psm", "6"]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=8)
            if proc.returncode != 0:
                self.last_error = "فشل Tesseract (تحقق من حزمة اللغة %s)" % self.source_lang
                return self._same("bmp")

            text = proc.stdout.decode("utf-8", errors="replace")
            text = "\n".join(ln.strip() for ln in text.splitlines() if ln.strip())
            if len(re.findall(r"\w", text)) < 3:
                text = ""
            return self._classify("bmp", text)
        except FileNotFoundError:
            self.last_error = "الأداة tesseract غير مثبتة"
        except Exception:
            _log("extract_dvb_bitmap: " + traceback.format_exc())
        finally:
            self._cleanup_temp()
            self._extract_lock.release()
        return self._same("bmp")
