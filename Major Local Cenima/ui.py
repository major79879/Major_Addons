# -*- coding: utf-8 -*-
from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from Screens.InfoBar import MoviePlayer
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from Components.ScrollLabel import ScrollLabel
from Components.MenuList import MenuList
from Components.ConfigList import ConfigListScreen
from Components.config import config, configfile, getConfigListEntry
from enigma import ePicLoad, eServiceReference, eTimer, getDesktop

from . import scanner
from .common import local_img, cert_text

# All coordinates below are written for 1920x1080 and scaled to the real screen
_SX = getDesktop(0).size().width() / 1920.0


def sc(v):
    return int(round(v * _SX))


def W(name, x, y, w, h, extra=""):
    return '<widget name="%s" position="%d,%d" size="%d,%d" %s />' % (name, sc(x), sc(y), sc(w), sc(h), extra)


def E(x, y, w, h, color, z=1):
    return '<eLabel position="%d,%d" size="%d,%d" backgroundColor="%s" zPosition="%d" />' % (
        sc(x), sc(y), sc(w), sc(h), color, z)


def F(size):
    return 'font="Regular;%d"' % sc(size)


def SCREEN(body):
    return '<screen position="0,0" size="%d,%d" flags="wfNoBorder" backgroundColor="#101418">%s</screen>' % (
        sc(1920), sc(1080), "".join(body))


TXT = ' transparent="1" zPosition="3" '
GOLD = ' foregroundColor="#e5a00d" '


class PixLoader(object):
    """Decodes a jpg/png into a Pixmap widget at the widget's size."""

    def __init__(self, widget, w, h):
        self.widget = widget
        self.size = (sc(w), sc(h))
        self.pl = ePicLoad()
        try:
            self.pl.PictureData.get().append(self.done)
        except AttributeError:
            self._conn = self.pl.PictureData.connect(self.done)

    def load(self, path):
        self.pl.setPara((self.size[0], self.size[1], 1, 1, 0, 1, "#00000000"))
        self.pl.startDecode(path)

    def done(self, info=None):
        ptr = self.pl.getData()
        if ptr is not None and self.widget.instance:
            self.widget.instance.setPixmap(ptr)


def show_image(loader, widget, path):
    if path:
        widget.show()
        loader.load(path)
    else:
        widget.hide()


def play(session, path, title):
    ref = eServiceReference(4097, 0, path)
    ref.setName(title)
    session.open(MoviePlayer, ref)


def meta_line(it, is_series):
    parts = []
    if it.get("rating"):
        parts.append(u"★ %.1f" % it["rating"])
    parts.append(cert_text(it.get("cert")))
    if is_series:
        parts.append(u"%d موسم" % len(it.get("seasons", {})))
    elif it.get("runtime"):
        parts.append(u"%d د" % it["runtime"])
    if it.get("genres"):
        parts.append(u"، ".join(it["genres"][:3]))
    return u"   |   ".join(parts)


# =========================================================================
# Main screen: poster carousel (movies / series)
# =========================================================================
# x, y, w, h of the 7 poster slots; slot 3 is the selected one
SLOTS = [(155, 267, 200, 300), (375, 267, 200, 300), (595, 267, 200, 300),
         (815, 200, 290, 435),
         (1125, 267, 200, 300), (1345, 267, 200, 300), (1565, 267, 200, 300)]
CENTER = 3


def main_skin():
    b = [W("backdrop", 0, 0, 1920, 600, 'zPosition="0" scale="1"'),
         E(0, 0, 1920, 600, "#99101418", 1),
         E(809, 194, 302, 447, "#e5a00d", 1)]
    for i, s in enumerate(SLOTS):
        b.append(W("poster%d" % i, s[0], s[1], s[2], s[3], 'zPosition="2" scale="1"'))
    b += [
        W("tab", 60, 30, 900, 60, F(42) + GOLD + TXT),
        W("count", 1460, 30, 400, 60, F(32) + 'halign="right"' + TXT),
        W("status", 60, 100, 1800, 40, F(28) + TXT),
        W("title", 80, 655, 1760, 60, F(44) + 'foregroundColor="#ffffff"' + TXT),
        W("meta", 80, 722, 1760, 44, F(30) + GOLD + TXT),
        W("cast", 80, 770, 1760, 44, F(28) + 'foregroundColor="#b0b8c0"' + TXT),
        W("overview", 80, 825, 1760, 170, F(30) + 'foregroundColor="#e6e6e6" transparent="1"'),
    ]
    for i, (name, color) in enumerate((("key_red", "#9f1313"), ("key_green", "#1f771f"),
                                       ("key_yellow", "#a08500"), ("key_blue", "#18188b"))):
        b.append(W(name, 80 + i * 420, 1025, 400, 40,
                   F(26) + 'halign="center" valign="center" foregroundColor="#ffffff" backgroundColor="%s" zPosition="3"' % color))
    return SCREEN(b)


class MLMain(Screen):
    def __init__(self, session):
        self.skin = main_skin()
        Screen.__init__(self, session)
        self.lib = scanner.load()
        self.mode = "movies"
        self.items = []
        self.idx = 0
        self.builder = None

        self["backdrop"] = Pixmap()
        for i in range(len(SLOTS)):
            self["poster%d" % i] = Pixmap()
        for n in ("tab", "count", "status", "title", "meta", "cast"):
            self[n] = Label()
        self["overview"] = ScrollLabel()
        self["key_red"] = Label(u"الأفلام")
        self["key_green"] = Label(u"المسلسلات")
        self["key_yellow"] = Label(u"تحديث المكتبة")
        self["key_blue"] = Label(u"الإعدادات")

        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions", "MenuActions", "ChannelSelectBaseActions"],
            {
                "ok": self.ok, "cancel": self.close,
                "left": lambda: self.move(-1), "right": lambda: self.move(1),
                "up": self["overview"].pageUp, "down": self["overview"].pageDown,
                "nextBouquet": lambda: self.move(7), "prevBouquet": lambda: self.move(-7),
                "red": lambda: self.set_mode("movies"), "green": lambda: self.set_mode("series"),
                "yellow": self.scan, "blue": self.settings, "menu": self.settings,
            }, -1)

        self.timer = eTimer()
        self.timer.callback.append(self.poll)
        self.onClose.append(self.timer.stop)
        self.onLayoutFinish.append(self.setup)

    def setup(self):
        self.bd = PixLoader(self["backdrop"], 1920, 600)
        self.px = [PixLoader(self["poster%d" % i], s[2], s[3]) for i, s in enumerate(SLOTS)]
        self.set_mode("movies")
        if not self.lib["movies"] and not self.lib["series"]:
            self.scan()

    # ---- display ----------------------------------------------------------
    def set_mode(self, mode):
        self.mode = mode
        self.items = sorted(self.lib["movies" if mode == "movies" else "series"],
                            key=lambda x: (x.get("title") or "").lower())
        self.idx = 0
        self["tab"].setText(u"الأفلام" if mode == "movies" else u"المسلسلات")
        self.refresh()

    def move(self, step):
        n = len(self.items)
        new = max(0, min(n - 1, self.idx + step)) if n else 0
        if new != self.idx:
            self.idx = new
            self.refresh()

    def refresh(self):
        n = len(self.items)
        self["count"].setText(u"%d / %d" % (self.idx + 1 if n else 0, n))
        for i in range(len(SLOTS)):
            j = self.idx + i - CENTER
            path = local_img(self.items[j].get("poster"), "w500") if 0 <= j < n else ""
            show_image(self.px[i], self["poster%d" % i], path)
        if not n:
            self["title"].setText(u"لا توجد عناصر - اضغط الأصفر لتحديث المكتبة")
            for k in ("meta", "cast"):
                self[k].setText("")
            self["overview"].setText("")
            self["backdrop"].hide()
            return
        it = self.items[self.idx]
        title = it.get("title") or ""
        self["title"].setText(u"%s (%s)" % (title, it["year"]) if it.get("year") else title)
        self["meta"].setText(meta_line(it, self.mode == "series"))
        self["cast"].setText(u"الأبطال: " + u"، ".join(it.get("cast", [])[:7]) if it.get("cast") else "")
        self["overview"].setText(it.get("overview") or u"لا يوجد ملخص")
        show_image(self.bd, self["backdrop"], local_img(it.get("backdrop"), "w1280"))

    def ok(self):
        if not self.items:
            return
        it = self.items[self.idx]
        if self.mode == "movies":
            play(self.session, it["path"], it["title"])
        else:
            self.session.open(MLSeries, it)

    # ---- scanning ---------------------------------------------------------
    def scan(self):
        if self.builder and not self.builder.finished:
            return
        cfg = config.plugins.medialibrary
        if not cfg.tmdb_key.value.strip():
            self.session.open(
                MessageBox,
                u"أدخل مفتاح TMDB أولاً من الإعدادات (الزر الأزرق).\n"
                u"احصل عليه مجاناً من themoviedb.org > Settings > API",
                MessageBox.TYPE_INFO)
            return
        self.builder = scanner.Builder(cfg.tmdb_key.value, cfg.lang.value, cfg.country.value, cfg.paths.value)
        self.builder.start()
        self.timer.start(700, False)

    def poll(self):
        b = self.builder
        self["status"].setText(b.status)
        if b.finished:
            self.timer.stop()
            if b.error:
                self["status"].setText(u"خطأ: " + b.error)
            self.lib = scanner.load()
            self.set_mode(self.mode)

    def settings(self):
        self.session.open(MLSettings)


# =========================================================================
# Series screen: seasons list + numbered episodes
# =========================================================================
def series_skin():
    list_attr = 'scrollbarMode="showOnDemand" ' + F(30) + ' itemHeight="%d"' % sc(50)
    b = [
        W("backdrop", 0, 0, 1920, 1080, 'zPosition="0" scale="1"'),
        E(0, 0, 1920, 1080, "#d9101418", 1),
        W("poster", 60, 70, 380, 570, 'zPosition="2" scale="1"'),
        W("rate", 60, 660, 380, 40, F(28) + GOLD + TXT),
        W("cert", 60, 705, 380, 40, F(26) + TXT),
        W("cast", 60, 755, 380, 220, F(24) + 'foregroundColor="#b0b8c0"' + TXT),
        W("name", 480, 20, 1380, 60, F(44) + 'foregroundColor="#ffffff"' + TXT),
        W("hdr_s", 480, 100, 340, 40, F(28) + GOLD + TXT),
        W("seasons", 480, 145, 340, 700, list_attr + ' zPosition="3"'),
        W("hdr_e", 850, 100, 1010, 40, F(28) + GOLD + TXT),
        W("episodes", 850, 145, 1010, 450, list_attr + ' zPosition="3"'),
        W("still", 850, 655, 400, 225, 'zPosition="2" scale="1"'),
        W("ep_title", 1280, 610, 580, 40, F(28) + 'foregroundColor="#ffffff"' + TXT),
        W("overview", 1280, 655, 580, 300, F(26) + 'foregroundColor="#e6e6e6" transparent="1"'),
        W("hint", 480, 1020, 1380, 40, F(24) + 'foregroundColor="#8a939c"' + TXT),
    ]
    return SCREEN(b)


class MLSeries(Screen):
    def __init__(self, session, series):
        self.skin = series_skin()
        Screen.__init__(self, session)
        self.series = series
        self.snums = sorted(series.get("seasons", {}).keys(), key=int)
        self.focus = 0
        self.cur_season = None
        self.eps = []

        for n in ("backdrop", "poster", "still"):
            self[n] = Pixmap()
        for n in ("rate", "cert", "cast", "name", "hdr_s", "hdr_e", "ep_title", "hint"):
            self[n] = Label()
        self["overview"] = ScrollLabel()
        self["seasons"] = MenuList([
            u"الموسم %d   (%d)" % (int(n), len(series["seasons"][n]["episodes"])) for n in self.snums])
        self["episodes"] = MenuList([])

        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ChannelSelectBaseActions"],
            {
                "ok": self.ok, "cancel": self.close,
                "left": lambda: self.set_focus(0), "right": lambda: self.set_focus(1),
                "up": lambda: self.move(-1), "down": lambda: self.move(1),
                "nextBouquet": self["overview"].pageDown, "prevBouquet": self["overview"].pageUp,
            }, -1)
        self.onLayoutFinish.append(self.setup)

    def setup(self):
        self.bd = PixLoader(self["backdrop"], 1920, 1080)
        self.pp = PixLoader(self["poster"], 380, 570)
        self.sl = PixLoader(self["still"], 400, 225)
        s = self.series
        show_image(self.bd, self["backdrop"], local_img(s.get("backdrop"), "w1280"))
        show_image(self.pp, self["poster"], local_img(s.get("poster"), "w500"))
        self["name"].setText(u"%s (%s)" % (s["title"], s["year"]) if s.get("year") else s["title"])
        self["rate"].setText(u"★ %.1f" % s["rating"] if s.get("rating") else "")
        self["cert"].setText(cert_text(s.get("cert")))
        self["cast"].setText(u"الأبطال:\n" + u"\n".join(s.get("cast", [])[:6]) if s.get("cast") else "")
        self["hdr_s"].setText(u"المواسم")
        self["hdr_e"].setText(u"الحلقات")
        self["hint"].setText(u"◄ ► تنقل بين المواسم والحلقات   |   OK تشغيل   |   EXIT رجوع")
        self.set_focus(0)
        self.update()

    def set_focus(self, f):
        self.focus = f
        self["episodes"].instance.setSelectionEnable(1 if f == 1 else 0)

    def move(self, d):
        lst = self["seasons"] if self.focus == 0 else self["episodes"]
        (lst.up if d < 0 else lst.down)()
        self.update()

    def update(self):
        if not self.snums:
            return
        si = self["seasons"].getSelectedIndex()
        if si != self.cur_season:
            self.cur_season = si
            self.eps = self.series["seasons"][self.snums[si]]["episodes"]
            self["episodes"].setList([u"%02d     %s" % (e["num"], e.get("title") or "") for e in self.eps])
        if not self.eps:
            return
        e = self.eps[self["episodes"].getSelectedIndex()]
        self["ep_title"].setText(u"الحلقة %d" % e["num"])
        self["overview"].setText(e.get("overview") or self.series.get("overview") or "")
        show_image(self.sl, self["still"], local_img(e.get("still"), "w300"))

    def ok(self):
        if self.focus == 0:
            self.set_focus(1)
            return
        if not self.eps:
            return
        e = self.eps[self["episodes"].getSelectedIndex()]
        season = int(self.snums[self.cur_season])
        play(self.session, e["path"], u"%s S%02dE%02d" % (self.series["title"], season, e["num"]))


# =========================================================================
# Settings
# =========================================================================
class MLSettings(ConfigListScreen, Screen):
    def __init__(self, session):
        self.skin = (
            '<screen position="center,center" size="%d,%d" title="Media Library" backgroundColor="#101418">'
            '%s%s</screen>' % (
                sc(1140), sc(560),
                W("config", 20, 20, 1100, 400, 'itemHeight="%d" scrollbarMode="showOnDemand"' % sc(60)),
                W("hint", 20, 450, 1100, 90, F(26) + 'foregroundColor="#b0b8c0" transparent="1"')))
        Screen.__init__(self, session)
        cfg = config.plugins.medialibrary
        entries = [
            getConfigListEntry(u"مفتاح TMDB (API Key)", cfg.tmdb_key),
            getConfigListEntry(u"لغة البيانات", cfg.lang),
            getConfigListEntry(u"بلد التصنيف الأبوي", cfg.country),
            getConfigListEntry(u"مسارات البحث (مفصولة بفاصلة)", cfg.paths),
        ]
        ConfigListScreen.__init__(self, entries, session=session)
        self["hint"] = Label(u"OK لتعديل القيمة   |   الأخضر: حفظ   |   الأحمر: إلغاء\n"
                             u"بعد التعديل اضغط الأصفر في الشاشة الرئيسية لتحديث المكتبة")
        self["setupActions"] = ActionMap(
            ["SetupActions", "ColorActions"],
            {"green": self.save, "save": self.save, "red": self.cancel, "cancel": self.cancel}, -2)

    def save(self):
        for x in self["config"].list:
            x[1].save()
        configfile.save()
        self.close()

    def cancel(self):
        for x in self["config"].list:
            x[1].cancel()
        self.close()
