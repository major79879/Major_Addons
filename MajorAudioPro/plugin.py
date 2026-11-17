# -*- coding: utf-8 -*-
import math
import time

from Plugins.Plugin import PluginDescriptor
from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from Screens.ChoiceBox import ChoiceBox
from Components.Label import Label
from Components.ActionMap import ActionMap
from Components.MenuList import MenuList
from Components.ConfigList import ConfigListScreen
from Components.config import (
    config, configfile, ConfigSubsection, ConfigSelection, ConfigText,
    getConfigListEntry, KEY_OK
)
from Components.MultiContent import MultiContentEntryText, MultiContentEntryPixmapAlphaBlend
from enigma import (
    eTimer, eListboxPythonMultiContent, gFont,
    RT_HALIGN_LEFT, RT_HALIGN_RIGHT, RT_VALIGN_CENTER
)
from Tools.LoadPixmap import LoadPixmap

from .audio_engine import ExternalAudioEngine, kill_stale_process
from .epg_manager import SatelliteEPGManager
from .store import LinkStore, make_link, clamp_delay, DELAY_CHOICES

PTS_PER_SEC = 90000
NUDGE_SMALL = 0.5
NUDGE_BIG = 2.0
HUD_AUTOHIDE_MS = 8000

# ====================== الإعدادات ======================
config.plugins.majoripaudio = ConfigSubsection()
config.plugins.majoripaudio.theme = ConfigSelection(
    default="cyber_neon",
    choices=[
        ("cyber_neon", "1. السايبر نيون (Cyber Neon Blue & Pink)"),
        ("royal_gold", "2. الذهب الملكي الفاخر (Royal Gold & Onyx)"),
        ("aurora_emerald", "3. شفق الشمال الزمردي (Aurora Emerald & Violet)"),
        ("stadium_crimson", "4. الاستاد الياقوتي (Stadium Crimson & Amber)"),
        ("arctic_frost", "5. الجليد الأزرق النقي (Arctic Frost & Cobalt)"),
        ("synthwave_sunset", "6. غروب ميامي المضيء (Synthwave Orange & Purple)"),
    ]
)
config.plugins.majoripaudio.audio_engine = ConfigSelection(
    default="auto",
    choices=[
        ("auto", "تلقائي ذكي (ExtePlayer3 / GStreamer / FFmpeg)"),
        ("exteplayer3", "مشغل ExtePlayer3 (الأفضل للبث المباشر)"),
        ("gstreamer", "مشغل GStreamer 1.0"),
        ("ffmpeg", "محرك FFmpeg (أقل تأخير ممكن)"),
    ]
)
config.plugins.majoripaudio.default_delay = ConfigSelection(default="5.0", choices=DELAY_CHOICES)

THEMES = {
    "cyber_neon": {"bg": "#0b0e14", "card": "#131822", "accent": "#00e5ff", "neon2": "#ff007f", "text": "#ffffff", "subtext": "#8fa3bf"},
    "royal_gold": {"bg": "#0a0a0a", "card": "#161410", "accent": "#ffd700", "neon2": "#ff9100", "text": "#ffffff", "subtext": "#b8a98a"},
    "aurora_emerald": {"bg": "#071310", "card": "#0e221d", "accent": "#00ff87", "neon2": "#9d4edd", "text": "#ffffff", "subtext": "#8cb8ab"},
    "stadium_crimson": {"bg": "#12090b", "card": "#1f1014", "accent": "#ff1744", "neon2": "#ffab00", "text": "#ffffff", "subtext": "#bfa0a7"},
    "arctic_frost": {"bg": "#09121d", "card": "#112033", "accent": "#40c4ff", "neon2": "#536dfe", "text": "#ffffff", "subtext": "#9ab3cc"},
    "synthwave_sunset": {"bg": "#13091f", "card": "#201033", "accent": "#ff5e00", "neon2": "#d500f9", "text": "#ffffff", "subtext": "#b89ec9"},
}


def current_theme():
    return THEMES.get(config.plugins.majoripaudio.theme.value, THEMES["cyber_neon"])


# ====================== كائنات مشتركة (Singletons) ======================
_engine = None
_store = None
_epg = None
_late = {"timer": None}
# حالة المزامنة الحالية: التأخير الأساسي + التعديلات اليدوية (nudge)
_sync = {"link_id": None, "delay": 0.0, "nudge": 0.0, "ts": False}
_ts = {"by_us": False, "paused": False}


def get_store():
    global _store
    if _store is None:
        _store = LinkStore()
    return _store


def get_epg(session):
    global _epg
    if _epg is None:
        _epg = SatelliteEPGManager(session)
    return _epg


def get_engine(session):
    global _engine
    if _engine is None:
        _engine = ExternalAudioEngine(session)
        _engine.add_stop_hook(lambda: on_audio_stopped(session))
    return _engine


def run_later(fn, ms=100):
    """تنفيذ دالة بعد إغلاق الشاشة الحالية (لفتح شاشة أخرى بأمان)."""
    t = eTimer()
    t.callback.append(fn)
    _late["timer"] = t
    t.start(ms, True)


# ====================== التايم شيفت عبر InfoBar الرسمي ======================
def _infobar():
    try:
        from Screens.InfoBar import InfoBar
        return InfoBar.instance
    except Exception:
        return None


def video_pause():
    """يبدأ تايم شيفت حقيقي ويوقف الصورة. يرجع True فقط إذا كان التايم شيفت فعّالاً."""
    _ts["by_us"] = False
    _ts["paused"] = False
    ib = _infobar()
    if ib is None:
        return False
    try:
        if ib.timeshiftEnabled():
            ib.pauseService()
        else:
            ib.startTimeshift()  # يبدأ التسجيل المؤقت ويوقف الصورة
        if ib.timeshiftEnabled():
            _ts["by_us"] = True   # البلاجن هو المسؤول عن إنهائه عند إيقاف الصوت
            _ts["paused"] = True
            return True
    except Exception:
        pass
    return False


def video_resume():
    if not _ts["paused"]:
        return
    _ts["paused"] = False
    ib = _infobar()
    try:
        if ib is not None:
            ib.unPauseService()
    except Exception:
        pass


def timeshift_stop(session):
    if not _ts["by_us"]:
        return
    _ts["by_us"] = False
    ib = _infobar()
    try:
        if ib is not None and ib.timeshiftEnabled():
            if hasattr(ib, "stopTimeshiftConfirmed"):
                ib.stopTimeshiftConfirmed(True)
            else:
                svc = session.nav.getCurrentService()
                ts = svc and svc.timeshift()
                if ts:
                    ts.stopTimeshift()
    except Exception:
        pass


def nudge_sync(d):
    """ضبط دقيق للمزامنة أثناء التشغيل. d>0 يزيد تأخير الصورة، d<0 يقلله."""
    if not _sync["ts"] or not d:
        return False
    total = _sync["delay"] + _sync["nudge"]
    if total + d < 0:
        d = -total
    if abs(d) < 0.01:
        return False
    ib = _infobar()
    try:
        ib.doSeekRelative(int(-d * PTS_PER_SEC))  # الرجوع للخلف في البافر = تأخير أكبر
        _sync["nudge"] += d
        return True
    except Exception:
        return False


def on_audio_stopped(session):
    """يُستدعى عند أي إيقاف للصوت (يدوي/تلقائي): حفظ المزامنة، وإيقاف التايم شيفت."""
    lid = _sync["link_id"]
    if lid and _sync["ts"] and abs(_sync["nudge"]) >= 0.25:
        get_store().update(lid, delay=_sync["delay"] + _sync["nudge"])  # تذكّر المزامنة المثلى
    _sync.update(link_id=None, delay=0.0, nudge=0.0, ts=False)
    video_resume()
    timeshift_stop(session)


# ====================== شاشة العد التنازلي ======================
class SyncCountdownOverlay(Screen):
    def __init__(self, session, link):
        t = current_theme()
        self.skin = f"""
            <screen name="SyncCountdownOverlay" position="center,center" size="680,260" backgroundColor="{t['bg']}" flags="wfNoBorder">
                <eLabel position="0,0" size="680,6" backgroundColor="{t['accent']}" />
                <eLabel position="0,254" size="680,6" backgroundColor="{t['neon2']}" />
                <widget name="title_lbl" position="20,25" size="640,40" font="Regular;28" halign="center" foregroundColor="{t['accent']}" transparent="1" />
                <widget name="count_lbl" position="20,75" size="640,100" font="Regular;72" halign="center" valign="center" foregroundColor="{t['text']}" transparent="1" />
                <widget name="hint_lbl" position="20,190" size="640,40" font="Regular;22" halign="center" foregroundColor="{t['subtext']}" transparent="1" />
            </screen>
        """
        Screen.__init__(self, session)
        self.session = session
        self.link = link
        self.delay = clamp_delay(link.get("delay", 5))
        self.engine = get_engine(session)
        self.note = ""
        self.t0 = 0.0
        self.ts_active = False

        self["title_lbl"] = Label("مزامنة: %s" % link.get("name", ""))
        self["count_lbl"] = Label("%.1fs" % self.delay)
        self["hint_lbl"] = Label("الصورة متوقفة مؤقتاً لمطابقة الصوت... (EXIT للإلغاء)")
        self["actions"] = ActionMap(["OkCancelActions"], {"cancel": self.cancel_sync}, -1)

        self.timer = eTimer()
        self.timer.callback.append(self.tick)
        self.onClose.append(self.timer.stop)
        self.onLayoutFinish.append(self.begin)  # لا نغلق الشاشة داخل __init__

    def begin(self):
        if self.delay <= 0:
            self.finish_sync()
            return
        self.ts_active = video_pause()
        if not self.ts_active:
            self.note = " (بدون تأخير: التايم شيفت غير متاح، تحقق من وجود قرص)"
            self.finish_sync()
            return
        self.t0 = time.monotonic()  # القياس بالساعة الفعلية يلغي تراكم تأخر المؤقت
        self.tick()

    def tick(self):
        left = self.delay - (time.monotonic() - self.t0)
        if left <= 0.02:
            self.finish_sync()
            return
        self["count_lbl"].setText("%ds" % int(math.ceil(left)))
        self.timer.start(int(min(1.0, left) * 1000), True)

    def finish_sync(self):
        self.timer.stop()
        # نكتم صوت القناة قبل استئناف الصورة حتى لا يُسمع صوتها الأصلي لحظة
        self.engine.mute_original_audio(True)
        video_resume()
        ok, msg = self.engine.play_audio(self.link.get("url", ""), self.link.get("name", ""))
        if ok:
            _sync.update(link_id=self.link["id"], delay=self.delay if self.ts_active else 0.0,
                         nudge=0.0, ts=self.ts_active)
        else:
            on_audio_stopped(self.session)
        self.close(ok, msg + self.note)

    def cancel_sync(self):
        self.timer.stop()
        on_audio_stopped(self.session)
        self.close(False, "تم إلغاء المزامنة")


# ====================== شريط الضبط السريع فوق الصورة ======================
class SyncHud(Screen):
    def __init__(self, session):
        t = current_theme()
        a = "#18" + t["card"][1:]  # خلفية شبه شفافة
        self.skin = f"""
            <screen name="SyncHud" position="center,e-230" size="1100,200" backgroundColor="{a}" flags="wfNoBorder">
                <eLabel position="0,0" size="1100,4" backgroundColor="{t['accent']}" />
                <widget name="name_lbl" position="25,12" size="700,40" font="Regular;30" halign="left" foregroundColor="{t['accent']}" transparent="1" />
                <widget name="sync_lbl" position="720,12" size="355,40" font="Regular;26" halign="right" foregroundColor="{t['neon2']}" transparent="1" />
                <widget name="now_lbl" position="25,58" size="1050,34" font="Regular;24" halign="left" foregroundColor="{t['text']}" transparent="1" />
                <widget name="next_lbl" position="25,92" size="1050,30" font="Regular;21" halign="left" foregroundColor="{t['subtext']}" transparent="1" />
                <widget name="hint_lbl" position="25,140" size="1050,50" font="Regular;21" halign="center" foregroundColor="{t['subtext']}" transparent="1" />
            </screen>
        """
        Screen.__init__(self, session)
        self.session = session
        self.engine = get_engine(session)
        self.epg = get_epg(session)

        for n in ("name_lbl", "sync_lbl", "now_lbl", "next_lbl", "hint_lbl"):
            self[n] = Label("")
        self["hint_lbl"].setText("► الصوت متأخر عن الصورة (يزيد التأخير)   ◄ الصوت يسبق الصورة   ▲▼ خطوة كبيرة\n"
                                 "أحمر: إيقاف الصوت   أصفر: القائمة   OK: إخفاء")
        self["actions"] = ActionMap(["OkCancelActions", "ColorActions", "DirectionActions"], {
            "ok": self.close, "cancel": self.close,
            "right": lambda: self.nudge(NUDGE_SMALL), "left": lambda: self.nudge(-NUDGE_SMALL),
            "up": lambda: self.nudge(NUDGE_BIG), "down": lambda: self.nudge(-NUDGE_BIG),
            "red": self.stop_audio, "yellow": self.open_list,
        }, -1)

        self.hide_timer = eTimer()
        self.hide_timer.callback.append(self.close)
        self.engine.add_listener(self.on_engine_event)
        self.onClose.append(self.cleanup)
        self.onLayoutFinish.append(self.refresh)

    def cleanup(self):
        self.hide_timer.stop()
        self.engine.remove_listener(self.on_engine_event)

    def on_engine_event(self, msg):
        self.close()

    def refresh(self):
        link = get_store().get(_sync["link_id"]) if _sync["link_id"] else None
        name = (link or {}).get("name") or self.engine.label or "صوتية خارجية"
        self["name_lbl"].setText("● %s" % name)
        if _sync["ts"]:
            self["sync_lbl"].setText("التأخير: %.1fs" % (_sync["delay"] + _sync["nudge"]))
        else:
            self["sync_lbl"].setText("بدون تايم شيفت")
        epg_name = (link or {}).get("epg") or (link or {}).get("name") or ""
        now, nxt = self.epg.get_now_next(epg_name)
        self["now_lbl"].setText(now)
        self["next_lbl"].setText(("التالي: " + nxt) if nxt else "")
        self.hide_timer.start(HUD_AUTOHIDE_MS, True)

    def nudge(self, d):
        if not _sync["ts"]:
            self["sync_lbl"].setText("لا مزامنة: التايم شيفت غير فعّال")
        else:
            nudge_sync(d)
        self.refresh()

    def stop_audio(self):
        self.engine.stop_audio()
        self.close()

    def open_list(self):
        self.close()
        run_later(lambda: open_main(self.session))


# ====================== محرر الرابط ======================
class LinkEditScreen(ConfigListScreen, Screen):
    def __init__(self, session, link=None):
        t = current_theme()
        self.skin = f"""
            <screen name="LinkEditScreen" position="center,center" size="1050,560" backgroundColor="{t['bg']}" title="رابط صوتي">
                <eLabel position="0,0" size="1050,80" backgroundColor="{t['card']}" />
                <eLabel position="0,80" size="1050,4" backgroundColor="{t['accent']}" />
                <widget name="header_title" position="30,18" size="990,45" font="Regular;32" halign="left" foregroundColor="{t['accent']}" transparent="1" />
                <widget name="config" position="30,105" size="990,240" itemHeight="48" scrollbarMode="showOnDemand" backgroundColor="{t['bg']}" foregroundColor="{t['text']}" />
                <widget name="match_lbl" position="30,360" size="990,80" font="Regular;22" halign="center" valign="center" foregroundColor="{t['subtext']}" transparent="1" />
                <widget name="key_green" position="30,480" size="300,50" font="Regular;24" halign="center" valign="center" backgroundColor="#008000" foregroundColor="#ffffff" />
                <widget name="key_red" position="350,480" size="300,50" font="Regular;24" halign="center" valign="center" backgroundColor="#900000" foregroundColor="#ffffff" />
            </screen>
        """
        Screen.__init__(self, session)
        self.session = session
        self.link = link
        self.epg = get_epg(session)

        self["header_title"] = Label("تعديل الرابط" if link else "إضافة رابط صوتي")
        self["match_lbl"] = Label("")
        self["key_green"] = Label("حفظ")
        self["key_red"] = Label("إلغاء")

        l = link or {}
        self.c_name = ConfigText(default=l.get("name", ""), fixed_size=False)
        self.c_url = ConfigText(default=l.get("url", "http://"), fixed_size=False)
        self.c_epg = ConfigText(default=l.get("epg", ""), fixed_size=False)
        self.c_delay = ConfigSelection(
            default="%.1f" % clamp_delay(l.get("delay", config.plugins.majoripaudio.default_delay.value)),
            choices=DELAY_CHOICES)
        entries = [
            getConfigListEntry("اسم الرابط (مثال: بين سبورت 1):", self.c_name),
            getConfigListEntry("رابط الصوت (URL):", self.c_url),
            getConfigListEntry("قناة الـ EPG على الستالايت:", self.c_epg),
            getConfigListEntry("مدة التايم شيفت:", self.c_delay),
        ]
        ConfigListScreen.__init__(self, entries, session=session, on_change=self.update_preview)
        self["setup_actions"] = ActionMap(["OkCancelActions", "ColorActions"], {
            "ok": lambda: self["config"].handleKey(KEY_OK),
            "green": self.save, "red": self.cancel, "cancel": self.cancel,
        }, -1)
        try:
            self["config"].onSelectionChanged.append(self.update_preview)
        except Exception:
            pass
        self.onLayoutFinish.append(self.update_preview)

    def update_preview(self):
        key = self.c_epg.value.strip() or self.c_name.value.strip()
        disp = self.epg.describe(key) if key else ""
        if disp:
            now, _ = self.epg.get_now_next(key)
            self["match_lbl"].setText("قناة EPG المطابقة: %s\n%s" % (disp, now))
        else:
            self["match_lbl"].setText("لا توجد قناة مطابقة على الستالايت. اكتب اسم القناة كما في قائمتك (مثل: beIN Sports 1)")

    def save(self):
        name, url = self.c_name.value.strip(), self.c_url.value.strip()
        if not name:
            self.session.open(MessageBox, "اكتب اسماً للرابط", MessageBox.TYPE_ERROR, timeout=4)
            return
        if not ExternalAudioEngine.valid_url(url):
            self.session.open(MessageBox, "الرابط يجب أن يبدأ بـ http:// أو https:// أو rtsp:// أو rtmp://",
                              MessageBox.TYPE_ERROR, timeout=5)
            return
        data = dict(name=name, url=url, epg=self.c_epg.value.strip(), delay=float(self.c_delay.value))
        self.close(data)

    def cancel(self):
        self.close(None)


# ====================== الإعدادات العامة ======================
class MajorIPAudioSetupScreen(ConfigListScreen, Screen):
    def __init__(self, session):
        t = current_theme()
        self.skin = f"""
            <screen name="MajorIPAudioSetupScreen" position="center,center" size="1050,420" backgroundColor="{t['bg']}" title="الإعدادات">
                <eLabel position="0,0" size="1050,80" backgroundColor="{t['card']}" />
                <eLabel position="0,80" size="1050,4" backgroundColor="{t['accent']}" />
                <widget name="header_title" position="30,18" size="990,45" font="Regular;32" halign="left" foregroundColor="{t['accent']}" transparent="1" />
                <widget name="config" position="30,105" size="990,150" itemHeight="48" scrollbarMode="showOnDemand" backgroundColor="{t['bg']}" foregroundColor="{t['text']}" />
                <widget name="helper_lbl" position="30,270" size="990,40" font="Regular;21" halign="center" foregroundColor="{t['subtext']}" transparent="1" />
                <widget name="key_green" position="30,345" size="300,50" font="Regular;24" halign="center" valign="center" backgroundColor="#008000" foregroundColor="#ffffff" />
                <widget name="key_red" position="350,345" size="300,50" font="Regular;24" halign="center" valign="center" backgroundColor="#900000" foregroundColor="#ffffff" />
            </screen>
        """
        Screen.__init__(self, session)
        self.session = session
        self["header_title"] = Label("MAJOR IP AUDIO PRO  |  الإعدادات")
        self["helper_lbl"] = Label("تغيير الثيم يظهر عند إعادة فتح البلاجن")
        self["key_green"] = Label("حفظ")
        self["key_red"] = Label("إلغاء")
        entries = [
            getConfigListEntry("ثيم الواجهة:", config.plugins.majoripaudio.theme),
            getConfigListEntry("محرك تشغيل الصوت:", config.plugins.majoripaudio.audio_engine),
            getConfigListEntry("مدة التايم شيفت الافتراضية للروابط الجديدة:", config.plugins.majoripaudio.default_delay),
        ]
        ConfigListScreen.__init__(self, entries, session=session)
        self["setup_actions"] = ActionMap(["OkCancelActions", "ColorActions"], {
            "green": self.save, "red": self.cancel, "cancel": self.cancel,
        }, -1)

    def save(self):
        for x in self["config"].list:
            x[1].save()
        configfile.save()
        self.close(True)

    def cancel(self):
        for x in self["config"].list:
            x[1].cancel()
        self.close(False)


# ====================== الشاشة الرئيسية ======================
class MajorIPAudioMainScreen(Screen):
    def __init__(self, session):
        t = current_theme()
        self.skin = f"""
            <screen name="MajorIPAudioMainScreen" position="center,center" size="1200,720" backgroundColor="{t['bg']}" title="MajorIPAudioPro">
                <eLabel position="0,0" size="1200,90" backgroundColor="{t['card']}" />
                <eLabel position="0,90" size="1200,4" backgroundColor="{t['accent']}" />
                <widget name="app_title" position="30,15" size="700,40" font="Regular;32" halign="left" foregroundColor="{t['accent']}" transparent="1" />
                <widget name="breadcrumb" position="30,55" size="700,30" font="Regular;22" halign="left" foregroundColor="{t['subtext']}" transparent="1" />
                <widget name="status_badge" position="640,12" size="530,70" font="Regular;21" halign="right" valign="center" foregroundColor="{t['neon2']}" transparent="1" />
                <widget name="menu_list" position="30,110" size="1140,480" itemHeight="72" scrollbarMode="showOnDemand" backgroundColor="{t['bg']}" foregroundColor="{t['text']}" />
                <widget name="hint_lbl" position="30,598" size="1140,34" font="Regular;21" halign="center" foregroundColor="{t['subtext']}" transparent="1" />
                <eLabel position="0,640" size="1200,80" backgroundColor="{t['card']}" />
                <widget name="key_green" position="30,655" size="260,50" font="Regular;24" halign="center" valign="center" backgroundColor="#008000" foregroundColor="#ffffff" />
                <widget name="key_red" position="310,655" size="260,50" font="Regular;24" halign="center" valign="center" backgroundColor="#900000" foregroundColor="#ffffff" />
                <widget name="key_yellow" position="590,655" size="280,50" font="Regular;24" halign="center" valign="center" backgroundColor="#b58900" foregroundColor="#ffffff" />
                <widget name="key_blue" position="890,655" size="280,50" font="Regular;24" halign="center" valign="center" backgroundColor="#005f87" foregroundColor="#ffffff" />
            </screen>
        """
        Screen.__init__(self, session)
        self.session = session
        self.engine = get_engine(session)
        self.store = get_store()
        self.epg = get_epg(session)

        self["app_title"] = Label("MAJOR IP AUDIO PRO")
        self["breadcrumb"] = Label("")
        self["status_badge"] = Label("")
        self["hint_lbl"] = Label("◄ ► تغيير مدة التايم شيفت للرابط المحدد (±0.5 ثانية)   |   OK تشغيل")
        self["key_green"] = Label("تشغيل")
        self["key_red"] = Label("إيقاف الصوت")
        self["key_yellow"] = Label("إضافة رابط")
        self["key_blue"] = Label("خيارات")

        self["menu_list"] = MenuList([], enableWrapAround=True, content=eListboxPythonMultiContent)
        self["menu_list"].l.setFont(0, gFont("Regular", 28))
        self["menu_list"].l.setFont(1, gFont("Regular", 22))
        self["menu_list"].l.setItemHeight(72)
        self.pixmaps = {}

        self["actions"] = ActionMap(["OkCancelActions", "ColorActions", "DirectionActions"], {
            "ok": self.on_ok, "green": self.on_ok, "cancel": self.close,
            "red": self.stop_external_audio, "yellow": self.add_link, "blue": self.show_options,
            "up": self["menu_list"].up, "down": self["menu_list"].down,
            "left": lambda: self.adjust_delay(-NUDGE_SMALL), "right": lambda: self.adjust_delay(NUDGE_SMALL),
        }, -1)

        self.epg_timer = eTimer()
        self.epg_timer.callback.append(self.refresh_keep)
        self.engine.add_listener(self.on_engine_event)
        self.onClose.append(self.cleanup)
        self.onLayoutFinish.append(self.first_show)

    # ---------- دورة الحياة ----------
    def first_show(self):
        self.render()
        self.update_status()
        self.epg_timer.start(60000, False)

    def cleanup(self):
        self.epg_timer.stop()
        self.engine.remove_listener(self.on_engine_event)
        self.pixmaps.clear()

    def on_engine_event(self, msg):
        self.render_keep()
        self["status_badge"].setText(msg)

    def update_status(self, extra=""):
        if self.engine.is_playing:
            txt = "يعمل الآن: %s  (%s)" % (self.engine.label, self.engine.engine_name.upper())
        else:
            txt = "الصوت الخارجي متوقف"
        self["status_badge"].setText(extra or txt)

    # ---------- العرض ----------
    def _pix(self, path):
        if not path:
            return None
        if path not in self.pixmaps:
            self.pixmaps[path] = LoadPixmap(path)
        return self.pixmaps[path]

    def render(self):
        links = self.store.links
        self["breadcrumb"].setText("%d رابط صوتي" % len(links) if links else "لا توجد روابط: اضغط الأصفر لإضافة رابط")
        rows = []
        for link in links:
            playing = self.engine.is_playing and _sync["link_id"] == link["id"]
            epg_name = link.get("epg") or link["name"]
            now, _ = self.epg.get_now_next(epg_name)
            row = [link]
            pix = self._pix(self.epg.get_picon(epg_name))
            if pix:
                row.append(MultiContentEntryPixmapAlphaBlend(pos=(15, 8), size=(90, 54), png=pix))
            row.append(MultiContentEntryText(
                pos=(120, 6), size=(850, 34), font=0, flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                text=link["name"], color=0x0000ff87 if playing else 0x00ffffff))
            row.append(MultiContentEntryText(
                pos=(980, 6), size=(150, 34), font=1, flags=RT_HALIGN_RIGHT | RT_VALIGN_CENTER,
                text="%.1fs" % link.get("delay", 0), color=0x00ffab00))
            row.append(MultiContentEntryText(
                pos=(120, 40), size=(1010, 26), font=1, flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                text=now, color=0x0000e5ff))
            rows.append(tuple(row))
        self["menu_list"].setList(rows)

    def render_keep(self):
        idx = self["menu_list"].getSelectionIndex()
        self.render()
        self["menu_list"].moveToIndex(idx)

    def refresh_keep(self):
        self.render_keep()

    def selected(self):
        cur = self["menu_list"].getCurrent()
        return cur[0] if cur else None

    # ---------- الإجراءات ----------
    def adjust_delay(self, d):
        link = self.selected()
        if not link:
            return
        self.store.update(link["id"], delay=link.get("delay", 0) + d)
        self.render_keep()

    def on_ok(self):
        link = self.selected()
        if not link:
            return
        if not ExternalAudioEngine.valid_url(link["url"]):
            self.update_status("رابط غير صالح: عدّله من الخيارات")
            return
        if self.engine.is_playing:
            self.engine.stop_audio()
        self.session.openWithCallback(self.on_sync_finished, SyncCountdownOverlay, link)

    def on_sync_finished(self, ok=False, msg=""):
        if ok:
            self.close()  # نعود للصورة ونفتح شريط الضبط السريع فوقها
            run_later(lambda: self.session.open(SyncHud))
        else:
            self.update_status(msg)

    def stop_external_audio(self):
        self.engine.stop_audio()
        self.render_keep()
        self.update_status("تم إيقاف الصوت الخارجي وعاد صوت القناة")

    def add_link(self):
        self.session.openWithCallback(self.on_link_edited, LinkEditScreen, None)

    def on_link_edited(self, data, link_id=None):
        if not data:
            return
        if link_id:
            self.store.update(link_id, **data)
        else:
            self.store.add(make_link(data["name"], data["url"], data["epg"], data["delay"]))
        self.render_keep()

    def show_options(self):
        opts = [("إضافة رابط جديد", "add")]
        if self.selected():
            opts += [("تعديل الرابط المحدد", "edit"), ("حذف الرابط المحدد", "delete"),
                     ("نقل لأعلى", "up"), ("نقل لأسفل", "down")]
        opts += [("تحديث فهرس القنوات والـ EPG", "epg"), ("الإعدادات", "settings")]
        self.session.openWithCallback(self.option_chosen, ChoiceBox, title="خيارات", list=opts)

    def option_chosen(self, choice):
        if not choice:
            return
        key, link = choice[1], self.selected()
        if key == "add":
            self.add_link()
        elif key == "edit" and link:
            self.session.openWithCallback(lambda d: self.on_link_edited(d, link["id"]), LinkEditScreen, link)
        elif key == "delete" and link:
            self.session.openWithCallback(
                lambda yes: self.do_delete(link, yes), MessageBox,
                "حذف الرابط «%s»؟" % link["name"], MessageBox.TYPE_YESNO)
        elif key in ("up", "down") and link:
            j = self.store.move(link["id"], -1 if key == "up" else 1)
            self.render()
            if j >= 0:
                self["menu_list"].moveToIndex(j)
        elif key == "epg":
            self.epg.build_bouquet_index()
            self.render_keep()
            self.update_status("تم تحديث فهرس القنوات والـ EPG")
        elif key == "settings":
            self.session.open(MajorIPAudioSetupScreen)

    def do_delete(self, link, yes):
        if yes:
            if self.engine.is_playing and _sync["link_id"] == link["id"]:
                self.engine.stop_audio()
            self.store.delete(link["id"])
            self.render()


# ====================== نقاط الدخول ======================
def open_main(session):
    session.open(MajorIPAudioMainScreen)


def main(session, **kwargs):
    open_main(session)


def quick(session, **kwargs):
    """من قائمة الإضافات (الأزرق): شريط الضبط السريع إن كان الصوت يعمل، وإلا القائمة."""
    if get_engine(session).is_playing:
        session.open(SyncHud)
    else:
        open_main(session)


def sessionstart(reason, session=None, **kwargs):
    if reason == 0 and session is not None:
        kill_stale_process()  # تنظيف أي مشغل يتيم من جلسة سابقة
        get_engine(session)


def Plugins(**kwargs):
    return [
        PluginDescriptor(where=PluginDescriptor.WHERE_SESSIONSTART, fnc=sessionstart),
        PluginDescriptor(
            name="Major IP Audio Pro",
            description="تشغيل صوتيات IP بدل صوت قناة الستالايت مع مزامنة تايم شيفت دقيقة",
            where=PluginDescriptor.WHERE_PLUGINMENU, fnc=main),
        PluginDescriptor(
            name="IP Audio: ضبط المزامنة / إيقاف سريع",
            description="شريط ضبط المزامنة فوق الصورة",
            where=PluginDescriptor.WHERE_EXTENSIONSMENU, fnc=quick),
    ]
