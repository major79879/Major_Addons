# -*- coding: utf-8 -*-
import json
import os
import uuid

STORE_PATH = "/etc/enigma2/majoripaudio_links.json"
OLD_CHANNELS = "/media/hdd/MajorIPAudioPro/channels.json"
MAX_DELAY = 120.0
# خطوات 0.5 ثانية من 0 إلى 120
DELAY_CHOICES = [("%.1f" % (i / 2.0), "%.1f ثانية" % (i / 2.0)) for i in range(int(MAX_DELAY * 2) + 1)]


def clamp_delay(value):
    try:
        v = float(value)
    except Exception:
        v = 0.0
    return round(max(0.0, min(MAX_DELAY, v)) * 2) / 2.0


def make_link(name, url, epg="", delay=5.0):
    return {
        "id": uuid.uuid4().hex[:8],
        "name": (name or "").strip() or "صوتية",
        "url": (url or "").strip(),
        "epg": (epg or "").strip(),   # اسم قناة الستالايت (أو مرجعها 1:0:...) لعرض الـ EPG
        "delay": clamp_delay(delay),
    }


def _flatten(nodes, out, delay):
    if not isinstance(nodes, list):
        return
    for n in nodes:
        if not isinstance(n, dict):
            continue
        if "url" in n:
            src = n.get("epg_source", "auto")
            out.append(make_link(n.get("name") or n.get("title"), n.get("url"),
                                 "" if src in ("auto", "", None) else src, delay))
        else:
            _flatten(n.get("sub_menus", n.get("channels", [])), out, delay)


class LinkStore:
    def __init__(self):
        self.links = []
        self.load()

    def load(self):
        self.links = []
        try:
            with open(STORE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            for x in data.get("links", []):
                if isinstance(x, dict) and x.get("url"):
                    link = make_link(x.get("name"), x.get("url"), x.get("epg"), x.get("delay", 5))
                    link["id"] = str(x.get("id") or link["id"])
                    self.links.append(link)
            return
        except Exception:
            pass
        # أول تشغيل: استيراد من channels.json القديم إن وُجد
        try:
            if os.path.isfile(OLD_CHANNELS):
                with open(OLD_CHANNELS, "r", encoding="utf-8") as f:
                    data = json.load(f)
                _flatten(data.get("main_menus", data.get("channels", [])), self.links, 5.0)
                if self.links:
                    self.save()
        except Exception:
            self.links = []

    def save(self):
        tmp = STORE_PATH + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"links": self.links}, f, ensure_ascii=False, indent=1)
            os.replace(tmp, STORE_PATH)  # كتابة ذرية: لا يتلف الملف عند انقطاع الكهرباء
            return True
        except Exception:
            return False

    def get(self, link_id):
        for x in self.links:
            if x["id"] == link_id:
                return x
        return None

    def add(self, link):
        self.links.append(link)
        return self.save()

    def update(self, link_id, **kw):
        x = self.get(link_id)
        if not x:
            return False
        if "delay" in kw:
            kw["delay"] = clamp_delay(kw["delay"])
        x.update(kw)
        return self.save()

    def delete(self, link_id):
        self.links = [x for x in self.links if x["id"] != link_id]
        return self.save()

    def move(self, link_id, step):
        for i, x in enumerate(self.links):
            if x["id"] == link_id:
                j = i + step
                if 0 <= j < len(self.links):
                    self.links[i], self.links[j] = self.links[j], self.links[i]
                    self.save()
                return j
        return -1
