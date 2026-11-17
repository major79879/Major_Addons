# -*- coding: utf-8 -*-
"""مشغّل ملفات الترجمة الخارجية (SRT / VTT / ASS / SSA) مع مزامنة تلقائية."""
import bisect
import difflib
import gc
import html
import os
import re
import time

_TIME_RE = re.compile(r"^(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[,.](\d{1,3})$")
_ARROW_RE = re.compile(r"(\S+)\s*-->\s*(\S+)")
_TAG_RE = re.compile(r"<[^>]+>|\{[^}]*\}")
_NORM_RE = re.compile(r"[^\w\s]", re.UNICODE)
_ASS_TIME_RE = re.compile(r"^(\d+):(\d{2}):(\d{2})[.:](\d{1,2})$")
_ASS_DRAWING_RE = re.compile(r"\\p[1-9]")


def normalize_text(text):
    """تنظيف النص للمقارنة: إزالة الترقيم وتوحيد الحالة (يدعم العربية)."""
    return " ".join(_NORM_RE.sub("", text).lower().split())


def arabic_ratio(text):
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    arabic = sum(1 for c in letters if "\u0600" <= c <= "\u06ff")
    return arabic / float(len(letters))


class ExternalSubtitlePlayer:
    def __init__(self, file_path=""):
        self.file_path = file_path
        self.subtitles = []      # [(start_ms, end_ms, text)]
        self.start_times = []
        self._norm = []          # النص المنظف لكل سطر (للمزامنة)
        self.offset_ms = 0
        self.current_index = 0
        self.is_arabic = False
        self.last_sync_ratio = 0.0
        self._clock_epoch = time.time()
        self._ext_ms = None      # موضع تشغيل خارجي (للملفات المسجلة)
        self._ext_stamp = 0.0

    # ------------------------------------------------------------------
    # التحميل
    # ------------------------------------------------------------------
    @staticmethod
    def _decode(raw):
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            try:
                return raw.decode("utf-16")
            except UnicodeDecodeError:
                pass
        try:
            return raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            pass
        text = raw.decode("cp1256", errors="replace")
        non_ascii = [c for c in text if ord(c) > 127]
        if non_ascii:
            arabic = sum(1 for c in non_ascii if "\u0600" <= c <= "\u06ff")
            if arabic / float(len(non_ascii)) < 0.5:
                # الملف غالباً أوروبي (cp1252) وليس عربياً
                return raw.decode("cp1252", errors="replace")
        return text

    def load_file(self, file_path):
        self.file_path = file_path
        self.subtitles = []
        self.start_times = []
        self._norm = []
        self.offset_ms = 0
        self.current_index = 0
        self.is_arabic = False
        self.last_sync_ratio = 0.0
        self.reset_clock()

        if not file_path or not os.path.isfile(file_path):
            return False
        try:
            with open(file_path, "rb") as f:
                raw = f.read()
        except OSError:
            return False

        content = self._decode(raw)
        del raw
        if not content.strip():
            return False

        ext = os.path.splitext(file_path)[1].lower()
        if ext in (".ass", ".ssa"):
            self._parse_ass(content)
        else:
            self._parse_srt_vtt(content)

        self.subtitles.sort(key=lambda x: x[0])
        self.start_times = [item[0] for item in self.subtitles]
        self._norm = [normalize_text(item[2]) for item in self.subtitles]

        sample = " ".join(item[2] for item in self.subtitles[:200])
        self.is_arabic = arabic_ratio(sample) > 0.5

        del content
        gc.collect()
        return len(self.subtitles) > 0

    # ------------------------------------------------------------------
    # المحللات
    # ------------------------------------------------------------------
    @staticmethod
    def _time_to_ms(h, m, s, ms):
        return (int(h) * 3600 + int(m) * 60 + int(s)) * 1000 + int(ms)

    def _parse_time_token(self, token):
        m = _TIME_RE.match(token.strip())
        if not m:
            return None
        h = m.group(1) or 0
        ms = int(m.group(4).ljust(3, "0"))
        return self._time_to_ms(h, m.group(2), m.group(3), ms)

    def _parse_srt_vtt(self, content):
        content = content.replace("\r\n", "\n").replace("\r", "\n")
        for block in re.split(r"\n\s*\n", content):
            lines = block.strip("\n").split("\n")
            arrow_idx = None
            for i, line in enumerate(lines):
                if "-->" in line:
                    arrow_idx = i
                    break
            if arrow_idx is None:
                continue  # WEBVTT header, NOTE, STYLE ...
            m = _ARROW_RE.search(lines[arrow_idx])
            if not m:
                continue
            start_ms = self._parse_time_token(m.group(1))
            end_ms = self._parse_time_token(m.group(2))
            if start_ms is None or end_ms is None:
                continue
            text = "\n".join(lines[arrow_idx + 1:])
            text = html.unescape(_TAG_RE.sub("", text)).strip()
            if text:
                self.subtitles.append((start_ms, end_ms, text))

    @staticmethod
    def _ass_time(token):
        m = _ASS_TIME_RE.match(token.strip())
        if not m:
            return None
        cs = int(m.group(4).ljust(2, "0")) * 10
        return (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))) * 1000 + cs

    def _parse_ass(self, content):
        section = ""
        fields = None
        idx_start, idx_end, idx_text = 1, 2, 9
        for line in content.splitlines():
            s = line.strip()
            if s.startswith("["):
                section = s.lower()
                continue
            if section != "[events]":
                continue
            low = s.lower()
            if low.startswith("format:"):
                fields = [f.strip().lower() for f in s[7:].split(",")]
                try:
                    idx_start = fields.index("start")
                    idx_end = fields.index("end")
                    idx_text = fields.index("text")
                except ValueError:
                    fields = None
                continue
            if not low.startswith("dialogue:"):
                continue
            n_fields = len(fields) if fields else 10
            parts = s[9:].lstrip().split(",", n_fields - 1)
            if len(parts) < n_fields:
                continue
            start_ms = self._ass_time(parts[idx_start])
            end_ms = self._ass_time(parts[idx_end])
            if start_ms is None or end_ms is None:
                continue
            raw_text = parts[idx_text]
            if _ASS_DRAWING_RE.search(raw_text):
                continue  # رسومات vector وليست نصاً
            text = raw_text.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
            text = _TAG_RE.sub("", text).strip()
            if text:
                self.subtitles.append((start_ms, end_ms, text))

    # ------------------------------------------------------------------
    # الساعة
    # ------------------------------------------------------------------
    def reset_clock(self):
        self._clock_epoch = time.time()
        self._ext_ms = None

    def update_external_position(self, position_ms):
        """موضع التشغيل الفعلي (ms) عند تشغيل ملف/تسجيل. يُستدعى من الخيط الرئيسي."""
        self._ext_ms = int(position_ms)
        self._ext_stamp = time.time()

    def get_current_time_ms(self):
        ext = self._ext_ms
        if ext is not None:
            base = ext + min((time.time() - self._ext_stamp) * 1000.0, 500.0)
        else:
            base = (time.time() - self._clock_epoch) * 1000.0
        return int(base) + self.offset_ms

    def get_current_line(self):
        if not self.subtitles:
            return ""
        now_ms = self.get_current_time_ms()
        idx = bisect.bisect_right(self.start_times, now_ms) - 1
        if idx < 0:
            return ""
        active = []
        for i in range(idx, max(-1, idx - 6), -1):
            start_ms, end_ms, text = self.subtitles[i]
            if start_ms <= now_ms <= end_ms and text not in [a[1] for a in active]:
                active.append((start_ms, text))
        if not active:
            return ""
        self.current_index = idx
        active.sort(key=lambda a: a[0])
        return "\n".join(a[1] for a in active)

    def adjust_offset(self, delta_ms):
        self.offset_ms += delta_ms

    # ------------------------------------------------------------------
    # المزامنة التلقائية
    # ------------------------------------------------------------------
    def auto_sync_with_live_sample(self, samples, window_ms=None, min_ratio=0.45):
        """
        samples: نص، أو قائمة نصوص، أو قائمة (نص, أدنى_نسبة_تشابه).
        تُرجع True عند نجاح المزامنة (أو عندما يكون التوقيت صحيحاً أصلاً).
        """
        if not self.subtitles or not samples:
            return False
        if isinstance(samples, str):
            samples = [samples]

        prepared = []
        for item in samples:
            if isinstance(item, (tuple, list)):
                text, thr = item[0], item[1]
            else:
                text, thr = item, min_ratio
            norm = normalize_text(text or "")
            if len(norm) >= 5:
                prepared.append((norm, thr))
        if not prepared:
            return False

        now_ms = self.get_current_time_ms()
        best_ratio = 0.0
        best_start = None
        hits = []  # (ratio, start_ms)

        for sample, thr in prepared:
            matcher = difflib.SequenceMatcher(None, autojunk=False)
            matcher.set_seq2(sample)
            for i, cand in enumerate(self._norm):
                if not cand:
                    continue
                start_ms = self.subtitles[i][0]
                if window_ms is not None and abs(start_ms - now_ms) > window_ms:
                    continue
                matcher.set_seq1(cand)
                if matcher.real_quick_ratio() < thr or matcher.quick_ratio() < thr:
                    continue
                ratio = matcher.ratio()
                if ratio < thr:
                    continue
                hits.append((ratio, start_ms))
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_start = start_ms

        if best_start is None:
            return False

        # بحث شامل: إذا وُجد تطابق مشابه جداً في مكان بعيد (سطر مكرر مثل "Yes.") فهو غامض
        if window_ms is None:
            for ratio, start_ms in hits:
                if abs(start_ms - best_start) > 10000 and ratio >= best_ratio - 0.03:
                    return False

        self.last_sync_ratio = best_ratio
        diff = best_start - now_ms
        if window_ms is not None and abs(diff) < 400:
            return True
        self.offset_ms += diff
        return True
