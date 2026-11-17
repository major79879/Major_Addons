# -*- coding: utf-8 -*-
import glob
import os
import re
import time

from enigma import eEPGCache, eServiceReference
from ServiceReference import ServiceReference

BOUQUET_DIR = "/etc/enigma2"
_TAGS = ("hd", "fhd", "uhd", "4k", "sd", "ar", "hevc", "h265", "sports", "sport")
_TAG_RE = re.compile(r"\b(" + "|".join(_TAGS) + r")\b")
_TASHKEEL_RE = re.compile(r"[\u064B-\u065F\u0670\u0640]")
_TAIL_DIGITS = re.compile(r"(\d+)$")
_ARABIC_MAP = {ord("أ"): "ا", ord("إ"): "ا", ord("آ"): "ا", ord("ة"): "ه", ord("ى"): "ي"}
NO_EPG = "لا يوجد دليل برامج (حدّد قناة EPG في إعدادات الرابط)"


class SatelliteEPGManager:
    def __init__(self, session):
        self.session = session
        self.epg_cache = eEPGCache.getInstance()
        self._entries = []       # [(اسم_مطبّع, مرجع, اسم_معروض)]
        self._exact = {}         # اسم_مطبّع -> (مرجع, اسم_معروض)
        self._match_cache = {}   # اسم_مطبّع -> (مرجع, اسم_معروض)
        self._picon_cache = {}
        self._built = False

    # ---------- التطبيع ----------
    def normalize_name(self, name):
        if not name:
            return ""
        name = _TASHKEEL_RE.sub("", name.lower()).translate(_ARABIC_MAP)
        name = _TAG_RE.sub("", name)
        return re.sub(r"[^a-z0-9\u0600-\u06FF]", "", name)

    # ---------- الفهرس ----------
    def _add(self, name, ref):
        norm = self.normalize_name(name)
        if norm:
            self._entries.append((norm, ref, name))
            self._exact.setdefault(norm, (ref, name))

    def build_bouquet_index(self):
        self._entries, self._exact = [], {}
        self._match_cache, self._picon_cache = {}, {}
        for b_file in sorted(glob.glob(os.path.join(BOUQUET_DIR, "userbouquet.*.tv"))):
            try:
                with open(b_file, "r", encoding="utf-8", errors="ignore") as f:
                    last_ref = ""
                    for line in f:
                        line = line.strip()
                        if line.startswith("#SERVICE "):
                            ref = line[len("#SERVICE "):].strip()
                            low = ref.lower()
                            if not ref.startswith("1:0:") or "http" in low or "%3a" in low:
                                last_ref = ""
                                continue
                            last_ref = ref
                            try:
                                self._add(ServiceReference(ref).getServiceName(), ref)
                            except Exception:
                                pass
                        elif line.startswith("#DESCRIPTION ") and last_ref:
                            self._add(line[len("#DESCRIPTION "):].strip(), last_ref)
                            last_ref = ""
            except Exception:
                continue
        self._built = True

    # ---------- المطابقة ----------
    def resolve(self, name_or_ref):
        """يرجع (المرجع، الاسم المعروض) أو ("", "")."""
        key = (name_or_ref or "").strip()
        if key.startswith("1:0:"):
            try:
                return key, ServiceReference(key).getServiceName() or key
            except Exception:
                return key, key
        norm = self.normalize_name(key)
        if not norm:
            return "", ""
        if not self._built:
            self.build_bouquet_index()
        if norm in self._match_cache:
            return self._match_cache[norm]

        found = self._exact.get(norm, ("", ""))
        if not found[0] and len(norm) >= 4:
            m_t = _TAIL_DIGITS.search(norm)
            tail = m_t.group(1) if m_t else ""
            best, best_diff = ("", ""), 10 ** 6
            for b_norm, b_ref, b_disp in self._entries:
                if len(b_norm) < 4:
                    continue
                if not (b_norm.startswith(norm) or norm.startswith(b_norm)):
                    continue
                m = _TAIL_DIGITS.search(b_norm)
                if (m.group(1) if m else "") != tail:
                    continue  # منع الخلط بين beIN 1 و beIN 2
                diff = abs(len(b_norm) - len(norm))
                if diff < best_diff:
                    best, best_diff = (b_ref, b_disp), diff
            found = best
        self._match_cache[norm] = found
        return found

    def describe(self, name_or_ref):
        return self.resolve(name_or_ref)[1]

    # ---------- البرامج ----------
    def _event_text(self, ref_str, direction):
        try:
            ev = self.epg_cache.lookupEventTime(eServiceReference(ref_str), -1, direction)
            if ev and ev.getEventName():
                b = ev.getBeginTime()
                e = b + ev.getDuration()
                return "[%s-%s] %s" % (time.strftime("%H:%M", time.localtime(b)),
                                       time.strftime("%H:%M", time.localtime(e)),
                                       ev.getEventName())
        except Exception:
            pass
        return ""

    def get_now_next(self, name_or_ref):
        ref, _ = self.resolve(name_or_ref)
        if not ref or not self.epg_cache:
            return NO_EPG, ""
        now = self._event_text(ref, 0) or "بانتظار تحديث EPG القناة..."
        nxt = self._event_text(ref, 1)
        return now, nxt

    def get_now_playing(self, name_or_ref):
        return self.get_now_next(name_or_ref)[0]

    # ---------- بيكون تلقائي من قناة الستالايت ----------
    def get_picon(self, name_or_ref):
        ref, _ = self.resolve(name_or_ref)
        if not ref:
            return ""
        if ref not in self._picon_cache:
            path = ""
            try:
                from Components.Renderer.Picon import getPiconName
                path = getPiconName(ref) or ""
            except Exception:
                path = ""
            self._picon_cache[ref] = path
        return self._picon_cache[ref]
