# -*- coding: utf-8 -*-
import os
import queue
import subprocess
import threading
import time
import traceback
import gc

from Plugins.Plugin import PluginDescriptor
from Screens.Screen import Screen
from Components.Label import Label
from Components.ActionMap import ActionMap
from Components.ConfigList import ConfigListScreen
from Components.FileList import FileList
from Components.config import (config, configfile, ConfigSubsection, ConfigSelection, ConfigText,
                               ConfigPassword, ConfigInteger, ConfigYesNo, getConfigListEntry)
from Components.ServiceEventTracker import ServiceEventTracker
from enigma import eTimer, iPlayableService, gFont, gRGB, ePoint, getDesktop

try:
    from skin import fonts as SKIN_FONTS
except Exception:
    SKIN_FONTS = {}

try:
    from PIL import Image
except ImportError:
    Image = None

from .translator_engine import LiveTranslatorEngine, TEMP_AUDIO, arabic_ratio
from .subtitle_extractor import (DVBTextExtractor, GRAB_LOCK, TEMP_FILES, NONE, SAME, NEW)
from .srt_player import ExternalSubtitlePlayer

# ----------------------------------------------------------------------
# أدوات مساعدة
# ----------------------------------------------------------------------
DESIGN_W, DESIGN_H = 1920, 1080          # الإحداثيات مصممة لدقة 1080p ثم تُحجَّم
OVERLAY_W, OVERLAY_H = 1500, 175
AUDIO_FALLBACK_AFTER = 10                # ثوانٍ بدون ترجمة ظاهرة قبل اللجوء للصوت (وضع auto)
BRIGHT_TMP = "/tmp/bright_check.png"


def log(msg):
    print("[MajorSubtitlePro] %s" % msg)


def log_exc(where):
    log("%s: %s" % (where, traceback.format_exc()))


def desktop_size():
    try:
        s = getDesktop(0).size()
        return int(s.width()), int(s.height())
    except Exception:
        return DESIGN_W, DESIGN_H


def ui_scale():
    w, h = desktop_size()
    return min(w / float(DESIGN_W), h / float(DESIGN_H))


def sc(v):
    return int(round(v * ui_scale()))


def resolve_font(name):
    """إن لم يكن الخط معرّفاً في السكن نرجع إلى Regular بدل خط مجهول."""
    if SKIN_FONTS and name not in SKIN_FONTS:
        return "Regular"
    return name


def hex_to_int(value, default=0):
    try:
        return int(value.lstrip("#"), 16)
    except Exception:
        return default


THEMES = {
    "cyberpunk": {"bg": "#0a0710", "card": "#140e24", "accent": "#ff007f", "glow": "#00f0ff", "text": "#ffffff", "muted": "#b8b0d0"},
    "aurora": {"bg": "#061012", "card": "#0c1e22", "accent": "#00ff87", "glow": "#9d4edd", "text": "#ffffff", "muted": "#a8c8cc"},
    "miami": {"bg": "#120814", "card": "#1e0f24", "accent": "#ff5e00", "glow": "#d500f9", "text": "#ffffff", "muted": "#d4b4dc"},
    "electric": {"bg": "#080c14", "card": "#101828", "accent": "#ffd60a", "glow": "#00b4d8", "text": "#ffffff", "muted": "#b0c4de"},
    "toxic": {"bg": "#08100a", "card": "#102014", "accent": "#ccff00", "glow": "#00f5d4", "text": "#ffffff", "muted": "#b4d4bc"},
    "cyber": {"bg": "#0b0e14", "card": "#131822", "accent": "#00e5ff", "glow": "#0088cc", "text": "#ffffff", "muted": "#9aa5b1"},
    "gold": {"bg": "#0a0a0a", "card": "#161616", "accent": "#ffd700", "glow": "#b8860b", "text": "#ffffff", "muted": "#b0a890"},
    "emerald": {"bg": "#071310", "card": "#0e221d", "accent": "#00e676", "glow": "#00a152", "text": "#ffffff", "muted": "#98b8ae"},
    "nebula": {"bg": "#110b1a", "card": "#1c132b", "accent": "#d500f9", "glow": "#8e24aa", "text": "#ffffff", "muted": "#b5a6c9"},
    "crimson": {"bg": "#121212", "card": "#1e1e1e", "accent": "#ff1744", "glow": "#b71c1c", "text": "#ffffff", "muted": "#b0b0b0"},
}

# ----------------------------------------------------------------------
# الإعدادات
# ----------------------------------------------------------------------
config.plugins.majorsubtitlepro = ConfigSubsection()
cfg0 = config.plugins.majorsubtitlepro
cfg0.theme = ConfigSelection(
    default="cyberpunk",
    choices=[
        ("cyberpunk", "سايبر بانك (وردي نيون + سماوي)"),
        ("aurora", "شفق الشمال (زمردي + بنفسجي نيون)"),
        ("miami", "غروب ميامي (برتقالي + أرجواني نيون)"),
        ("electric", "الذهب الكهربائي (ذهبي + أزرق نيون)"),
        ("toxic", "الماتريكس النيون (ليموني + فيروزي)"),
        ("cyber", "السايبر الأزرق السماوي"),
        ("gold", "الذهبي الملكي السينمائي"),
        ("emerald", "الزمردي الأنيق"),
        ("nebula", "البنفسجي الفضائي"),
        ("crimson", "الياقوتي الدافئ"),
    ],
)
cfg0.mode = ConfigSelection(
    default="auto",
    choices=[
        ("auto", "تلقائي (Text ثم DVB ثم الصوت)"),
        ("dvb", "ترجمة صور DVB فقط (OCR)"),
        ("txt", "ترجمة نصوص Teletext فقط"),
        ("audio", "ترجمة الصوت المباشر فقط (Audio)"),
        ("srt", "تشغيل ملف ترجمة خارجي (SRT/ASS)"),
    ],
)
cfg0.lang = ConfigSelection(
    default="eng",
    choices=[
        ("eng", "الإنجليزية (English)"),
        ("fra", "الفرنسية (French)"),
        ("deu", "الألمانية (German)"),
        ("spa", "الإسبانية (Spanish)"),
        ("ita", "الإيطالية (Italian)"),
        ("rus", "الروسية (Russian)"),
        ("tur", "التركية (Turkish)"),
    ],
)
cfg0.timeshift_delay = ConfigSelection(
    default="5",
    choices=[
        ("0", "بدون تأخير (0 ثانية)"),
        ("3", "3 ثوانٍ"),
        ("4", "4 ثوانٍ"),
        ("5", "5 ثوانٍ (موصى به)"),
        ("6", "6 ثوانٍ"),
        ("8", "8 ثوانٍ"),
        ("10", "10 ثوانٍ"),
    ],
)
cfg0.resync_interval = ConfigSelection(
    default="180",
    choices=[
        ("0", "إيقاف المزامنة الدورية"),
        ("60", "كل 1 دقيقة"),
        ("180", "كل 3 دقائق (موصى به)"),
        ("300", "كل 5 دقائق"),
        ("600", "كل 10 دقائق"),
    ],
)
cfg0.hide_on_silence = ConfigYesNo(default=True)
cfg0.silence_timeout = ConfigSelection(
    default="4",
    choices=[
        ("2", "2 ثانية"),
        ("3", "3 ثوانٍ"),
        ("4", "4 ثوانٍ (موصى به)"),
        ("6", "6 ثوانٍ"),
        ("8", "8 ثوانٍ"),
    ],
)
cfg0.auto_contrast = ConfigYesNo(default=True)
cfg0.pos_x = ConfigInteger(default=210, limits=(0, 420))
cfg0.pos_y = ConfigInteger(default=840, limits=(50, 900))
cfg0.font_color = ConfigSelection(
    default="#ffd700",
    choices=[
        ("#ffd700", "أصفر ذهبي"),
        ("#ffffff", "أبيض ناصع"),
        ("#00f0ff", "سماوي نيون"),
        ("#00ff87", "أخضر فاتح"),
        ("#ff9100", "برتقالي"),
        ("#ff4081", "وردي"),
    ],
)
cfg0.font_size = ConfigSelection(
    default="38",
    choices=[
        ("28", "صغير (28)"),
        ("32", "متوسط صغير (32)"),
        ("36", "متوسط (36)"),
        ("38", "قياسي (38)"),
        ("42", "كبير (42)"),
        ("48", "كبير جداً (48)"),
        ("54", "ضخم (54)"),
    ],
)
cfg0.font_family = ConfigSelection(
    default="Regular",
    choices=[
        ("Regular", "الخط الافتراضي (Regular)"),
        ("Bold", "خط عريض (Bold)"),
        ("Arabic", "خط عربي (Arabic) - إن وُجد في السكن"),
        ("LCD", "رقمي (LCD) - إن وُجد في السكن"),
        ("Console", "واضح (Console) - إن وُجد في السكن"),
    ],
)
cfg0.shadow_size = ConfigSelection(
    default="2",
    choices=[
        ("0", "بدون ظل (0)"),
        ("1", "ظل خفيف (1px)"),
        ("2", "ظل متوسط (2px)"),
        ("3", "ظل قوي (3px)"),
        ("4", "ظل سميك جداً (4px)"),
    ],
)
cfg0.shadow_color = ConfigSelection(
    default="#000000",
    choices=[
        ("#000000", "أسود"),
        ("#222222", "رمادي داكن"),
        ("#001f3f", "أزرق داكن"),
        ("#2c003e", "بنفسجي داكن"),
        ("#4a0000", "أحمر داكن"),
    ],
)
# ملاحظة: في Enigma2 قيمة alpha معكوسة (00 = معتم تماماً، ff = شفاف تماماً)
cfg0.bg_style = ConfigSelection(
    default="#80000000",
    choices=[
        ("#ff000000", "بدون خلفية (شفاف بالكامل)"),
        ("#b5000000", "خلفية خفيفة (عتمة ~30%)"),
        ("#80000000", "خلفية شبه شفافة (عتمة 50%)"),
        ("#40000000", "خلفية داكنة (عتمة ~75%)"),
        ("#00000000", "خلفية سوداء غير شفافة (100%)"),
        ("#000a1a33", "خلفية كحلية غير شفافة (100%)"),
    ],
)
cfg0.srt_path = ConfigText(default="", fixed_size=False)
cfg0.apikey = ConfigPassword(default="", fixed_size=False)


def save_all_config(entries=None):
    try:
        if entries:
            for x in entries:
                x[1].save()
        config.plugins.majorsubtitlepro.save()
        configfile.save()
    except Exception:
        log_exc("save_all_config")


# ----------------------------------------------------------------------
# شاشة اختيار ملف الترجمة (تعتمد على FileList المتوفر في كل الصور)
# ----------------------------------------------------------------------
class SubtitleFileSelector(Screen):
    def __init__(self, session, start_dir="/"):
        k = sc
        self.skin = f"""
            <screen name="SubtitleFileSelector" position="center,center" size="{k(1000)},{k(640)}" backgroundColor="#101418" flags="wfNoBorder">
                <widget name="path" position="{k(20)},{k(15)}" size="{k(960)},{k(36)}" font="Regular;{k(24)}" foregroundColor="#ffd700" transparent="1" />
                <widget name="filelist" position="{k(20)},{k(60)}" size="{k(960)},{k(510)}" font="Regular;{k(26)}" itemHeight="{k(40)}" scrollbarMode="showOnDemand" transparent="1" />
                <widget name="hint" position="{k(20)},{k(585)}" size="{k(960)},{k(36)}" font="Regular;{k(22)}" foregroundColor="#b0b0b0" transparent="1" />
            </screen>
        """
        Screen.__init__(self, session)
        self["path"] = Label("")
        self["hint"] = Label("OK: فتح مجلد / اختيار ملف     Exit: إلغاء")
        self["filelist"] = FileList(start_dir, matchingPattern=r"(?i)^.*\.(srt|ass|ssa|vtt)$")
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions"], {
            "ok": self.on_ok,
            "cancel": self.cancel,
            "up": lambda: self["filelist"].up(),
            "down": lambda: self["filelist"].down(),
            "left": lambda: self["filelist"].pageUp(),
            "right": lambda: self["filelist"].pageDown(),
        }, -1)
        self.onLayoutFinish.append(self.update_path)

    def update_path(self):
        try:
            self["path"].setText(self["filelist"].getCurrentDirectory() or "/")
        except Exception:
            pass

    def on_ok(self):
        fl = self["filelist"]
        try:
            if fl.canDescent():
                fl.descent()
                self.update_path()
                return
            name = fl.getFilename()
            if name:
                if not name.startswith("/"):
                    name = os.path.join(fl.getCurrentDirectory() or "/", name)
                self.close(name)
        except Exception:
            log_exc("SubtitleFileSelector.on_ok")

    def cancel(self):
        self.close(None)


# ----------------------------------------------------------------------
# شاشة الإعدادات
# ----------------------------------------------------------------------
class MajorSubtitleSetupScreen(ConfigListScreen, Screen):
    def __init__(self, session):
        cfg = config.plugins.majorsubtitlepro
        t = THEMES.get(cfg.theme.value, THEMES["cyberpunk"])
        k = sc
        self.skin = f"""
            <screen name="MajorSubtitleSetupScreen" position="center,center" size="{k(1180)},{k(760)}" backgroundColor="{t['bg']}" flags="wfNoBorder">
                <eLabel position="0,0" size="{k(1180)},{k(75)}" backgroundColor="{t['card']}" />
                <eLabel position="0,{k(73)}" size="{k(590)},{k(3)}" backgroundColor="{t['accent']}" />
                <eLabel position="{k(590)},{k(73)}" size="{k(590)},{k(3)}" backgroundColor="{t['glow']}" />
                <widget name="header_title" position="{k(30)},{k(15)}" size="{k(750)},{k(45)}" font="Bold;{k(32)}" foregroundColor="{t['accent']}" transparent="1" />
                <widget name="device_badge" position="{k(800)},{k(20)}" size="{k(350)},{k(35)}" font="Regular;{k(22)}" halign="right" foregroundColor="{t['glow']}" transparent="1" />

                <eLabel position="{k(25)},{k(95)}" size="{k(1130)},{k(420)}" backgroundColor="{t['card']}" cornerRadius="16" />
                <widget name="config" position="{k(45)},{k(110)}" size="{k(1090)},{k(390)}" itemHeight="{k(38)}" font="Regular;{k(24)}" scrollbarMode="showOnDemand" transparent="1" />

                <eLabel position="{k(25)},{k(530)}" size="{k(1130)},{k(130)}" backgroundColor="{t['card']}" cornerRadius="16" />
                <widget name="preview_title" position="{k(45)},{k(538)}" size="{k(1090)},{k(26)}" font="Regular;{k(20)}" foregroundColor="{t['muted']}" transparent="1" />
                <widget name="preview_box" position="{k(60)},{k(570)}" size="{k(1060)},{k(78)}" font="Regular;{k(36)}" halign="center" valign="center" />

                <widget name="key_green" position="{k(30)},{k(685)}" size="{k(265)},{k(50)}" font="Bold;{k(23)}" halign="center" valign="center" backgroundColor="#00897b" foregroundColor="#ffffff" cornerRadius="12" />
                <widget name="key_yellow" position="{k(315)},{k(685)}" size="{k(265)},{k(50)}" font="Bold;{k(23)}" halign="center" valign="center" backgroundColor="#f57c00" foregroundColor="#ffffff" cornerRadius="12" />
                <widget name="key_blue" position="{k(600)},{k(685)}" size="{k(265)},{k(50)}" font="Bold;{k(23)}" halign="center" valign="center" backgroundColor="#1976d2" foregroundColor="#ffffff" cornerRadius="12" />
                <widget name="key_red" position="{k(885)},{k(685)}" size="{k(265)},{k(50)}" font="Bold;{k(23)}" halign="center" valign="center" backgroundColor="#c62828" foregroundColor="#ffffff" cornerRadius="12" />
            </screen>
        """
        Screen.__init__(self, session)
        self.session = session

        self["header_title"] = Label("MAJOR SUBTITLE PRO")
        self["device_badge"] = Label("ترجمة فورية • v2.0")
        self["preview_title"] = Label("معاينة حية لشكل الترجمة (تتغير تلقائياً مع إعداداتك):")
        self["preview_box"] = Label("هذه معاينة حية لشكل ولون وحجم الترجمة على الشاشة")

        self["key_green"] = Label("تشغيل الترجمة")
        self["key_yellow"] = Label("اختر ملف SRT")
        self["key_blue"] = Label("حفظ وتطبيق الثيم")
        self["key_red"] = Label("إغلاق")

        list_entries = [
            getConfigListEntry("ثيم واجهة البلاجن (UI Theme):", cfg.theme),
            getConfigListEntry("وضع الترجمة:", cfg.mode),
            getConfigListEntry("مسار ملف الترجمة (SRT/ASS) - OK للتعديل:", cfg.srt_path),
            getConfigListEntry("المزامنة الدورية التلقائية:", cfg.resync_interval),
            getConfigListEntry("الإخفاء التلقائي عند الصمت:", cfg.hide_on_silence),
            getConfigListEntry("مدة الانتظار قبل الإخفاء:", cfg.silence_timeout),
            getConfigListEntry("التباين الذكي للسطوع:", cfg.auto_contrast),
            getConfigListEntry("موضع الترجمة الرأسي (Y):", cfg.pos_y),
            getConfigListEntry("موضع الترجمة الأفقي (X):", cfg.pos_x),
            getConfigListEntry("لغة القناة المصدر (OCR والصوت):", cfg.lang),
            getConfigListEntry("لون خط الترجمة:", cfg.font_color),
            getConfigListEntry("حجم خط الترجمة:", cfg.font_size),
            getConfigListEntry("نوع الخط:", cfg.font_family),
            getConfigListEntry("حجم ظلال الترجمة:", cfg.shadow_size),
            getConfigListEntry("لون ظلال الترجمة:", cfg.shadow_color),
            getConfigListEntry("نمط خلفية الترجمة:", cfg.bg_style),
            getConfigListEntry("إيقاف الصورة مؤقتاً لمزامنة الصوت (Pause):", cfg.timeshift_delay),
            getConfigListEntry("مفتاح OpenAI API (للصوت) - OK للتعديل:", cfg.apikey),
        ]
        ConfigListScreen.__init__(self, list_entries, on_change=self.update_live_preview)

        # اسم مختلف عن "actions" حتى لا نستبدل خرائط ConfigListScreen الداخلية
        self["setup_actions"] = ActionMap(["SetupActions", "ColorActions"], {
            "green": self.start_translation,
            "yellow": self.open_file_browser,
            "blue": self.save_and_apply_theme,
            "cancel": self.exit_setup,
            "red": self.exit_setup,
        }, -1)

        self.onLayoutFinish.append(self.update_live_preview)

    def update_live_preview(self, *args):
        try:
            cfg = config.plugins.majorsubtitlepro
            t = THEMES.get(cfg.theme.value, THEMES["cyberpunk"])
            if self["header_title"].instance:
                self["header_title"].instance.setForegroundColor(gRGB(hex_to_int(t["accent"])))
            if self["device_badge"].instance:
                self["device_badge"].instance.setForegroundColor(gRGB(hex_to_int(t["glow"])))

            lbl = self["preview_box"].instance
            if not lbl:
                return
            shadow_size = int(cfg.shadow_size.value)
            lbl.setFont(gFont(resolve_font(cfg.font_family.value), sc(int(cfg.font_size.value))))
            lbl.setForegroundColor(gRGB(hex_to_int(cfg.font_color.value)))
            lbl.setShadowColor(gRGB(hex_to_int(cfg.shadow_color.value)))
            lbl.setShadowOffset(ePoint(shadow_size, shadow_size))

            bg_hex = cfg.bg_style.value.lstrip("#")
            if bg_hex == "ff000000":
                lbl.setTransparent(1)
            else:
                lbl.setTransparent(0)
                lbl.setBackgroundColor(gRGB(int(bg_hex, 16)))
            lbl.invalidate()
        except Exception:
            log_exc("update_live_preview")

    def save_and_apply_theme(self):
        save_all_config(self["config"].list)
        self.close(True)   # main() يعيد فتح الشاشة بالثيم الجديد

    def open_file_browser(self):
        start_dir = "/"
        for d in ("/media/hdd/", "/media/usb/", "/tmp/"):
            if os.path.isdir(d):
                start_dir = d
                break
        self.session.openWithCallback(self.file_selected, SubtitleFileSelector, start_dir)

    def file_selected(self, selected_file):
        if selected_file:
            config.plugins.majorsubtitlepro.srt_path.value = selected_file
            config.plugins.majorsubtitlepro.mode.value = "srt"
            self["config"].invalidateAll()

    def start_translation(self):
        save_all_config(self["config"].list)
        self.session.open(MajorSubtitleOverlayScreen)

    def exit_setup(self):
        # تراجع عن أي تعديل لم يُحفظ
        try:
            for x in self["config"].list:
                x[1].cancel()
        except Exception:
            pass
        self.close(False)


# ----------------------------------------------------------------------
# شاشة عرض الترجمة
# ----------------------------------------------------------------------
class MajorSubtitleOverlayScreen(Screen):
    def __init__(self, session):
        cfg = config.plugins.majorsubtitlepro
        t = THEMES.get(cfg.theme.value, THEMES["cyberpunk"])
        k = sc
        self.current_x = cfg.pos_x.value
        self.current_y = cfg.pos_y.value
        self.ow, self.oh = k(OVERLAY_W), k(OVERLAY_H)
        font_family = resolve_font(cfg.font_family.value)
        font_size = k(int(cfg.font_size.value))
        bg_style = cfg.bg_style.value

        self.skin = f"""
            <screen name="MajorSubtitleOverlayScreen" position="{k(self.current_x)},{k(self.current_y)}" size="{self.ow},{self.oh}" backgroundColor="{bg_style}" flags="wfNoBorder" cornerRadius="20">
                <eLabel position="0,0" size="{self.ow // 2},{max(2, k(3))}" backgroundColor="{t['accent']}" />
                <eLabel position="{self.ow // 2},0" size="{self.ow // 2},{max(2, k(3))}" backgroundColor="{t['glow']}" />
                <widget name="sub_text" position="{k(25)},{k(8)}" size="{self.ow - k(50)},{k(128)}" font="{font_family};{font_size}" foregroundColor="{cfg.font_color.value}" shadowColor="{cfg.shadow_color.value}" shadowOffset="{cfg.shadow_size.value},{cfg.shadow_size.value}" halign="center" valign="center" transparent="1" />
                <widget name="status_bar" position="{k(25)},{k(140)}" size="{self.ow - k(50)},{k(28)}" font="Regular;{k(19)}" foregroundColor="{t['accent']}" halign="center" valign="center" transparent="1" />
            </screen>
        """
        Screen.__init__(self, session)
        self.session = session
        self.is_overlay_visible = True

        self["sub_text"] = Label("Major Subtitle Pro: جاري التهيئة...")
        self["status_bar"] = Label("")

        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions", "ColorActions", "NumberActions"], {
            "cancel": self.exit_overlay,
            "left": self.sync_backward_small,
            "right": self.sync_forward_small,
            "up": self.sync_backward_large,
            "down": self.sync_forward_large,
            "blue": self.trigger_manual_resync,
            "2": self.move_up,
            "8": self.move_down,
            "4": self.move_left,
            "6": self.move_right,
            "5": self.reset_position,
        }, -1)

        self.mode = cfg.mode.value
        self.lang = cfg.lang.value
        self.srt_path = cfg.srt_path.value
        self.ts_delay = int(cfg.timeshift_delay.value)
        self.resync_interval = int(cfg.resync_interval.value)
        self.auto_contrast_enabled = cfg.auto_contrast.value
        self.hide_on_silence = cfg.hide_on_silence.value
        self.silence_timeout_ms = int(cfg.silence_timeout.value) * 1000
        self.default_bg_hex = bg_style.lstrip("#")
        try:
            self.default_alpha = int(self.default_bg_hex[:2], 16) if len(self.default_bg_hex) == 8 else 0
        except ValueError:
            self.default_alpha = 0
        self.is_darkened_by_contrast = False

        # --- مكونات العمل ---
        self.engine = LiveTranslatorEngine(api_key=cfg.apikey.value, language=self.lang)
        self.extractor = DVBTextExtractor(session=self.session, source_lang=self.lang)
        self.srt_player = ExternalSubtitlePlayer()

        # --- حالة مشتركة بين الخيوط ---
        self.ui_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.sync_lock = threading.Lock()
        self.contrast_busy = False
        self.last_posted = ""
        self.last_sub_seen_ts = time.time()
        self.last_error_posted = ("", 0.0)
        self.current_service_ref = ""
        self.current_is_file = False
        self.file_mode = False
        self.paused_by_us = False
        self.is_running = True
        self.overlap_ocr = False   # هل تغطي نافذتنا منطقة ترجمة القناة؟
        self._cleaned = False

        # --- المؤقتات (كلها تعمل في الخيط الرئيسي) ---
        self.ui_timer = eTimer()
        self.ui_timer.callback.append(self.poll_ui_queue)
        self.srt_timer = eTimer()
        self.srt_timer.callback.append(self.update_srt_display)
        self.periodic_resync_timer = eTimer()
        self.periodic_resync_timer.callback.append(self.trigger_periodic_resync)
        self.contrast_timer = eTimer()
        self.contrast_timer.callback.append(self.check_screen_brightness)
        self.silence_timer = eTimer()
        self.silence_timer.callback.append(self.auto_hide_overlay)
        self.resume_timer = eTimer()
        self.resume_timer.callback.append(self.resume_playback)
        self.nudge_timer = eTimer()
        self.nudge_timer.callback.append(self.resume_playback)

        self.event_tracker = ServiceEventTracker(screen=self, eventmap={
            iPlayableService.evStart: self.on_channel_changed,
            iPlayableService.evUpdatedInfo: self.on_info_updated,
        })

        self.onClose.append(self.cleanup)
        self.onLayoutFinish.append(self.after_layout)

    # ------------------------------------------------------------------
    # بدء التشغيل
    # ------------------------------------------------------------------
    def after_layout(self):
        self.update_extractor_exclusion()
        self.refresh_service_info()
        self.ui_timer.start(150, False)

        if self.mode == "srt":
            self.start_srt_playback()
        else:
            self["status_bar"].setText("● %s MODE  |  2/8/4/6: تحريك  |  Exit: إغلاق" % self.mode.upper())
            self.reset_silence_timer()
            if self.mode in ("audio", "auto") and self.ts_delay > 0:
                self.start_pause_sync()
            threading.Thread(target=self.process_loop, daemon=True).start()
            self.ocr_overlap_hint()

        if self.auto_contrast_enabled:
            self.contrast_timer.start(2500, False)

    def refresh_service_info(self):
        """الخيط الرئيسي فقط: نقرأ مرجع القناة وPID ثم نمررها للخيوط كقيم عادية."""
        try:
            ref = self.session.nav.getCurrentlyPlayingServiceReference()
            self.current_service_ref = ref.toString() if ref else ""
            self.current_is_file = bool(ref and ref.getPath())
        except Exception:
            log_exc("refresh_service_info")
        self.extractor.refresh_service_info()

    def update_extractor_exclusion(self):
        w, h = desktop_size()
        l, t = sc(self.current_x), sc(self.current_y)
        self.extractor.set_exclusion((l, t, l + self.ow, t + self.oh), (w, h))
        # إذا نزلت النافذة إلى منطقة قص OCR فهي تغطي ترجمة القناة نفسها
        self.overlap_ocr = (t + self.oh) > h * self.extractor.CROP_TOP_RATIO

    def ocr_overlap_hint(self):
        if self.overlap_ocr and self.mode in ("dvb", "auto"):
            self["status_bar"].setText("⚠ النافذة تغطي ترجمة القناة: اضغط 2 لرفعها حتى تُقرأ الترجمة")

    # ------------------------------------------------------------------
    # الاتصال بين الخيوط والواجهة
    # ------------------------------------------------------------------
    def post(self, kind, value):
        if not self.stop_event.is_set():
            self.ui_queue.put((kind, value))

    def poll_ui_queue(self):
        if not self.is_running:
            return
        latest_text = None
        try:
            while True:
                kind, value = self.ui_queue.get_nowait()
                if kind == "text":
                    latest_text = value
                elif kind == "status":
                    self["status_bar"].setText(value)
                elif kind == "contrast":
                    self.apply_contrast(value)
        except queue.Empty:
            pass
        if latest_text is not None:
            self.show_text(latest_text)

    def show_text(self, text):
        self["sub_text"].setText(text)
        if text:
            self.show_overlay_if_hidden()

    # ------------------------------------------------------------------
    # الظهور والإخفاء
    # ------------------------------------------------------------------
    def show_overlay_if_hidden(self):
        if not self.is_overlay_visible:
            self.show()
            self.is_overlay_visible = True
        self.reset_silence_timer()

    def reset_silence_timer(self):
        self.silence_timer.stop()
        if self.hide_on_silence:
            self.silence_timer.start(self.silence_timeout_ms, True)

    def auto_hide_overlay(self):
        if self.hide_on_silence and self.is_overlay_visible:
            self.hide()
            self.is_overlay_visible = False

    # ------------------------------------------------------------------
    # تحريك النافذة
    # ------------------------------------------------------------------
    def move_up(self):
        self.current_y = max(50, self.current_y - 25)
        self.apply_new_position()

    def move_down(self):
        self.current_y = min(900, self.current_y + 25)
        self.apply_new_position()

    def move_left(self):
        self.current_x = max(0, self.current_x - 30)
        self.apply_new_position()

    def move_right(self):
        self.current_x = min(420, self.current_x + 30)
        self.apply_new_position()

    def reset_position(self):
        self.current_x = 210
        self.current_y = 840
        self.apply_new_position()

    def apply_new_position(self):
        self.show_overlay_if_hidden()
        try:
            if self.instance:
                self.instance.move(ePoint(sc(self.current_x), sc(self.current_y)))
        except Exception:
            log_exc("apply_new_position")
        config.plugins.majorsubtitlepro.pos_x.value = self.current_x
        config.plugins.majorsubtitlepro.pos_y.value = self.current_y
        self.update_extractor_exclusion()
        self["status_bar"].setText("الموضع: X=%d, Y=%d" % (self.current_x, self.current_y))
        self.ocr_overlap_hint()

    # ------------------------------------------------------------------
    # التباين الذكي (يعمل في خيط ولا يحجب الواجهة)
    # ------------------------------------------------------------------
    def check_screen_brightness(self):
        if (not self.is_running or not self.auto_contrast_enabled or not self.is_overlay_visible
                or self.contrast_busy or Image is None):
            return
        self.contrast_busy = True
        threading.Thread(target=self._brightness_worker, args=(self.current_y,), daemon=True).start()

    def _brightness_worker(self, y_design):
        try:
            if not GRAB_LOCK.acquire(timeout=1):
                return
            try:
                subprocess.run(["grab", "-v", "-r", "16", "-p", BRIGHT_TMP],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
            finally:
                GRAB_LOCK.release()
            if not os.path.isfile(BRIGHT_TMP):
                return
            img = Image.open(BRIGHT_TMP).convert("L")
            w, h = img.size
            center = (y_design + OVERLAY_H / 2.0) / float(DESIGN_H)
            y_sample = min(h - 1, max(0, int(center * h)))
            pixels = [img.getpixel((int(w * p), y_sample)) for p in (0.25, 0.4, 0.5, 0.6, 0.75)]
            self.post("contrast", sum(pixels) / float(len(pixels)))
        except Exception:
            log_exc("_brightness_worker")
        finally:
            try:
                os.remove(BRIGHT_TMP)
            except OSError:
                pass
            self.contrast_busy = False

    def apply_contrast(self, avg_brightness):
        # نُعتّم الخلفية فقط إذا كانت خلفية المستخدم أخف من 0x50 (alpha معكوس)
        if self.default_alpha <= 0x50 or not self.instance:
            return
        try:
            if avg_brightness > 165 and not self.is_darkened_by_contrast:
                self.instance.setBackgroundColor(gRGB(0x50000000))
                self.instance.invalidate()
                self.is_darkened_by_contrast = True
            elif avg_brightness <= 165 and self.is_darkened_by_contrast:
                self.instance.setBackgroundColor(gRGB(int(self.default_bg_hex, 16)))
                self.instance.invalidate()
                self.is_darkened_by_contrast = False
        except Exception:
            log_exc("apply_contrast")

    # ------------------------------------------------------------------
    # وضع الملف الخارجي SRT/ASS
    # ------------------------------------------------------------------
    def start_srt_playback(self):
        if not self.srt_player.load_file(self.srt_path):
            self["sub_text"].setText("خطأ: تعذر قراءة ملف الترجمة • اختر ملفاً بالزر الأصفر من الإعدادات")
            return
        self.file_mode = self.current_is_file
        self.srt_timer.start(250, False)
        self.reset_silence_timer()
        if self.file_mode:
            self["sub_text"].setText("تم تحميل الملف • التوقيت من موضع التشغيل الفعلي")
            self["status_bar"].setText("● SRT (ملف)  |  أسهم: ضبط التوقيت  |  2/8: مكان")
        else:
            self["sub_text"].setText("تم تحميل الملف • جاري المزامنة التلقائية...")
            self["status_bar"].setText("● SRT (بث)  |  أسهم: توقيت  |  أزرق: مزامنة  |  2/8: مكان")
            if self.resync_interval > 0:
                self.periodic_resync_timer.start(self.resync_interval * 1000, False)
            threading.Thread(target=self.auto_sync_srt_thread, args=(None,), daemon=True).start()

    def poll_play_position(self):
        try:
            service = self.session.nav.getCurrentService()
            seek = service.seek() if service else None
            if seek:
                res = seek.getPlayPosition()
                if res and res[0] == 0:
                    self.srt_player.update_external_position(int(res[1]) // 90)
        except Exception:
            pass

    def trigger_manual_resync(self):
        if self.mode == "srt" and not self.file_mode:
            self.show_overlay_if_hidden()
            self["status_bar"].setText("جاري إعادة المزامنة التلقائية الشاملة...")
            threading.Thread(target=self.auto_sync_srt_thread, args=(None,), daemon=True).start()

    def trigger_periodic_resync(self):
        if self.mode == "srt" and self.is_running and not self.file_mode:
            threading.Thread(target=self.auto_sync_srt_thread, args=(45000,), daemon=True).start()

    def auto_sync_srt_thread(self, window_ms=None):
        if not self.sync_lock.acquire(False):
            return  # مزامنة أخرى جارية
        try:
            state, text = self.extractor.extract_teletext()
            if state == NONE:
                state, text = self.extractor.extract_dvb_bitmap()
            if state == NONE or not text:
                if window_ms is None:
                    self.post("status", "لا توجد ترجمة على الشاشة للمزامنة • استخدم الأسهم يدوياً")
                return

            samples = [(text, 0.45)]
            # ملف عربي + ترجمة القناة بلغة أخرى: نقارن بالترجمة العربية أيضاً
            if self.srt_player.is_arabic and arabic_ratio(text) < 0.5:
                translated = self.engine.translate_text_to_arabic(text)
                if translated:
                    samples.append((translated, 0.35))

            if self.srt_player.auto_sync_with_live_sample(samples, window_ms=window_ms):
                self.post("status", "تمت المزامنة تلقائياً (%+.1fs)" % (self.srt_player.offset_ms / 1000.0))
            elif window_ms is None:
                self.post("status", "تعذرت المزامنة التلقائية • استخدم الأسهم لضبط التوقيت")
        except Exception:
            log_exc("auto_sync_srt_thread")
        finally:
            self.sync_lock.release()
            gc.collect()

    def update_srt_display(self):
        if not self.is_running:
            return
        if self.file_mode:
            self.poll_play_position()
        line = self.srt_player.get_current_line()
        if line != self.last_posted:
            self.last_posted = line
            self["sub_text"].setText(line)
            if line:
                self.show_overlay_if_hidden()

    def show_offset(self):
        self["status_bar"].setText("توقيت الترجمة: %+.1f ثانية" % (self.srt_player.offset_ms / 1000.0))

    def sync_backward_small(self):
        if self.mode == "srt":
            self.show_overlay_if_hidden()
            self.srt_player.adjust_offset(-500)
            self.show_offset()
        else:
            self.manual_nudge_backward()

    def sync_forward_small(self):
        if self.mode == "srt":
            self.show_overlay_if_hidden()
            self.srt_player.adjust_offset(500)
            self.show_offset()
        else:
            self.manual_nudge_forward()

    def sync_backward_large(self):
        if self.mode == "srt":
            self.show_overlay_if_hidden()
            self.srt_player.adjust_offset(-5000)
            self.show_offset()

    def sync_forward_large(self):
        if self.mode == "srt":
            self.show_overlay_if_hidden()
            self.srt_player.adjust_offset(5000)
            self.show_offset()

    # ------------------------------------------------------------------
    # تغيير القناة
    # ------------------------------------------------------------------
    def on_info_updated(self):
        # معلومات القناة (مثل PID التيليتكست) قد تصل بعد evStart
        self.refresh_service_info()

    def on_channel_changed(self):
        if not self.is_running:
            return
        self.resume_timer.stop()
        self.nudge_timer.stop()
        self.paused_by_us = False          # الخدمة أُعيد تشغيلها، فلا حاجة لـ unpause
        self.refresh_service_info()
        self.extractor.reset()
        self.last_posted = ""
        self.last_sub_seen_ts = time.time()

        self.file_mode = self.mode == "srt" and self.current_is_file
        if self.mode == "srt":
            if not self.file_mode:
                self.srt_player.reset_clock()
        else:
            self.show_overlay_if_hidden()
            self["sub_text"].setText("تم تغيير القناة • جاري إعادة المزامنة...")
            if self.mode in ("audio", "auto") and self.ts_delay > 0:
                self.start_pause_sync()

    # ------------------------------------------------------------------
    # إيقاف الصورة مؤقتاً لإعطاء ترجمة الصوت وقتاً (Pause فقط، لا ملفات)
    # ------------------------------------------------------------------
    def start_pause_sync(self):
        try:
            service = self.session.nav.getCurrentService()
            pauseable = service.pause() if service else None
            if pauseable:
                pauseable.pause()
                self.paused_by_us = True
                self["status_bar"].setText("إيقاف الصورة %d ثوانٍ لمزامنة الصوت..." % self.ts_delay)
                self.resume_timer.start(self.ts_delay * 1000, True)
        except Exception:
            log_exc("start_pause_sync")

    def resume_playback(self):
        if not self.paused_by_us:
            return
        try:
            service = self.session.nav.getCurrentService()
            pauseable = service.pause() if service else None
            if pauseable:
                pauseable.unpause()
            self.paused_by_us = False
            self["status_bar"].setText("2/8/4/6: تحريك | أسهم: ضبط | Exit: إغلاق")
        except Exception:
            log_exc("resume_playback")

    def manual_nudge_backward(self):
        try:
            service = self.session.nav.getCurrentService()
            pauseable = service.pause() if service else None
            if pauseable and not self.paused_by_us:
                self.show_overlay_if_hidden()
                pauseable.pause()
                self.paused_by_us = True
                self["status_bar"].setText("تأخير البث 1 ثانية...")
                self.nudge_timer.start(1000, True)
        except Exception:
            log_exc("manual_nudge_backward")

    def manual_nudge_forward(self):
        try:
            service = self.session.nav.getCurrentService()
            seek = service.seek() if service else None
            if seek:
                self.show_overlay_if_hidden()
                seek.seekRelative(1, 90000)
                self["status_bar"].setText("تقديم البث 1 ثانية...")
        except Exception:
            log_exc("manual_nudge_forward")

    # ------------------------------------------------------------------
    # حلقة العمل (خيط خلفي: لا تلمس أي كائن enigma هنا)
    # ------------------------------------------------------------------
    def process_loop(self):
        self.last_sub_seen_ts = time.time()
        while not self.stop_event.is_set():
            try:
                self.process_once()
            except Exception:
                log_exc("process_loop")
            self.report_errors()
            self.stop_event.wait(0.5)

    def process_once(self):
        mode = self.mode
        if mode == "txt":
            self.handle_extraction(self.extractor.extract_teletext())
        elif mode == "dvb":
            self.handle_extraction(self.extractor.extract_dvb_bitmap())
        elif mode == "audio":
            self.run_audio_mode()
        elif mode == "auto":
            result = self.extractor.extract_teletext()
            if result[0] == NONE:
                result = self.extractor.extract_dvb_bitmap()
            state = self.handle_extraction(result)
            # نلجأ للصوت (مدفوع) فقط إذا غابت أي ترجمة ظاهرة فترة كافية
            if state == NONE and time.time() - self.last_sub_seen_ts > AUDIO_FALLBACK_AFTER:
                self.run_audio_mode()

    def handle_extraction(self, result):
        state, text = result
        if state == NEW:
            self.last_sub_seen_ts = time.time()
            self.emit_translation(self.engine.translate_text_to_arabic(text))
        elif state == SAME:
            self.last_sub_seen_ts = time.time()
        elif self.last_posted and not self.overlap_ocr:
            # اختفت الترجمة من المصدر: نمسح ترجمتنا أيضاً (ما لم تكن نافذتنا تغطيه، فيبدو غياباً كاذباً)
            self.last_posted = ""
            self.post("text", "")
        return state

    def emit_translation(self, text):
        if text and text != self.last_posted:
            self.last_posted = text
            self.post("text", text)

    def run_audio_mode(self):
        ref = self.current_service_ref
        if ref and self.engine.capture_audio_chunk(ref, duration=4):
            self.emit_translation(self.engine.translate_audio_via_whisper())

    def report_errors(self):
        err = self.engine.pop_error() or self.extractor.pop_error()
        if err:
            last_msg, last_ts = self.last_error_posted
            now = time.time()
            if err != last_msg or now - last_ts > 15:
                self.last_error_posted = (err, now)
                self.post("status", "⚠ " + err)

    # ------------------------------------------------------------------
    # الإغلاق والتنظيف
    # ------------------------------------------------------------------
    def exit_overlay(self):
        self.close()   # cleanup() يُستدعى عبر onClose

    def cleanup(self):
        if self._cleaned:
            return
        self._cleaned = True
        self.is_running = False
        self.stop_event.set()
        for timer in (self.ui_timer, self.srt_timer, self.periodic_resync_timer, self.contrast_timer,
                      self.silence_timer, self.resume_timer, self.nudge_timer):
            try:
                timer.stop()
            except Exception:
                pass
        # لا نترك البث متجمداً إن خرجنا أثناء الإيقاف المؤقت
        if self.paused_by_us:
            try:
                service = self.session.nav.getCurrentService()
                pauseable = service.pause() if service else None
                if pauseable:
                    pauseable.unpause()
            except Exception:
                log_exc("cleanup unpause")
            self.paused_by_us = False
        # نحذف ملفاتنا المؤقتة فقط (لا نلمس أي ملفات Timeshift أو تسجيلات)
        for tmp_file in TEMP_FILES + [TEMP_AUDIO, BRIGHT_TMP]:
            try:
                os.remove(tmp_file)
            except OSError:
                pass
        save_all_config()
        gc.collect()


# ----------------------------------------------------------------------
def main(session, **kwargs):
    def reopen_cb(reopen=False):
        if reopen:
            main(session, **kwargs)
    session.openWithCallback(reopen_cb, MajorSubtitleSetupScreen)


def Plugins(**kwargs):
    return [
        PluginDescriptor(
            name="Major Subtitle Pro",
            description="ترجمة فورية احترافية للصوت و DVB و Text و SRT",
            where=[PluginDescriptor.WHERE_PLUGINMENU, PluginDescriptor.WHERE_EXTENSIONSMENU],
            icon="plugin.png",
            fnc=main,
        )
    ]
