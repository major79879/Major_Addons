# -*- coding: utf-8 -*-
import os
import signal
import shutil
import subprocess
import time

from Components.config import config
from enigma import eTimer, iPlayableService

PID_FILE = "/tmp/majoripaudio.pid"
ALLOWED_SCHEMES = ("http://", "https://", "rtsp://", "rtmp://")
WATCH_INTERVAL_MS = 1500
KNOWN_BINARIES = ("exteplayer3", "gst-launch", "ffmpeg")


def _tool(name):
    return shutil.which(name) is not None


def _in_standby():
    try:
        import Screens.Standby as sb
        return bool(sb.inStandby)
    except Exception:
        return False


def kill_stale_process():
    """إنهاء أي مشغل صوت بقي يعمل بعد إعادة تشغيل الإنيجما أو تعطله."""
    try:
        with open(PID_FILE, "r") as f:
            pid = int(f.read().strip())
    except Exception:
        return
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            cmdline = f.read().decode("utf-8", "ignore")
        if any(b in cmdline for b in KNOWN_BINARIES):
            os.killpg(pid, signal.SIGTERM)
    except Exception:
        pass
    try:
        os.remove(PID_FILE)
    except Exception:
        pass


class ExternalAudioEngine:
    """محرك واحد مشترك (Singleton) يبقى حياً حتى بعد إغلاق واجهة البلاجن."""

    def __init__(self, session):
        self.session = session
        self.process = None
        self.is_playing = False
        self.current_url = ""
        self.engine_name = ""
        self._service_ref = ""
        self._we_muted = False
        self._listeners = []
        self._stop_hooks = []
        self.label = ""

        self._timer = eTimer()
        self._timer.callback.append(self._watch)
        try:
            self.session.nav.event.append(self._on_nav_event)
        except Exception:
            pass

    # ---------- مستمعون للأحداث (للواجهة) ----------
    def add_listener(self, cb):
        if cb not in self._listeners:
            self._listeners.append(cb)

    def remove_listener(self, cb):
        if cb in self._listeners:
            self._listeners.remove(cb)

    def add_stop_hook(self, cb):
        if cb not in self._stop_hooks:
            self._stop_hooks.append(cb)

    def _notify(self, msg):
        for cb in list(self._listeners):
            try:
                cb(msg)
            except Exception:
                pass

    # ---------- أدوات ----------
    def is_tool_available(self, name):
        return _tool(name)

    @staticmethod
    def valid_url(url):
        # منع تمرير قيمة تبدأ بـ "-" كخيار للمشغل (Option Injection)
        return isinstance(url, str) and url.strip().lower().startswith(ALLOWED_SCHEMES)

    def mute_original_audio(self, mute=True):
        try:
            from enigma import eDVBVolumecontrol
            vc = eDVBVolumecontrol.getInstance()
            if not vc:
                return
            if mute:
                if not vc.isMuted():
                    vc.volumeMute()
                    self._we_muted = True
            elif self._we_muted:
                vc.volumeUnMute()
                self._we_muted = False
        except Exception:
            pass

    def _current_service_ref(self):
        try:
            ref = self.session.nav.getCurrentlyPlayingServiceReference()
            return ref.toString() if ref else ""
        except Exception:
            return ""

    def _select_engine(self):
        try:
            choice = config.plugins.majoripaudio.audio_engine.value
        except Exception:
            choice = "auto"
        if choice != "auto":
            return choice
        if _tool("exteplayer3"):
            return "exteplayer3"
        if _tool("gst-launch-1.0"):
            return "gstreamer"
        if _tool("ffmpeg"):
            return "ffmpeg"
        return ""

    def _build_cmd(self, engine, url):
        if engine == "exteplayer3":
            if not _tool("exteplayer3"):
                return None, "مشغل ExtePlayer3 غير مثبت على الصورة"
            return ["exteplayer3", "-a", "0", url], ""
        if engine == "gstreamer":
            if not _tool("gst-launch-1.0"):
                return None, "مشغل GStreamer غير متاح"
            return ["gst-launch-1.0", "-q", "playbin", "uri=" + url, "video-sink=fakesink"], ""
        if engine == "ffmpeg":
            if not _tool("ffmpeg"):
                return None, "محرك FFmpeg غير مثبت على الصورة"
            cmd = ["ffmpeg", "-nostdin", "-loglevel", "quiet",
                   "-fflags", "nobuffer", "-flags", "low_delay"]
            if url.lower().startswith(("http://", "https://")):
                cmd += ["-reconnect", "1", "-reconnect_streamed", "1",
                        "-reconnect_delay_max", "5"]
            cmd += ["-i", url, "-vn", "-f", "alsa", "default"]
            return cmd, ""
        return None, "لا يوجد مشغل صوتي متاح (ثبت exteplayer3 أو ffmpeg)"

    # ---------- التشغيل ----------
    def play_audio(self, url, label=""):
        self.stop_audio(keep_mute=True)

        def fail(msg):
            self.mute_original_audio(False)
            return False, msg

        if not url:
            return fail("رابط الصوت فارغ")
        if not self.valid_url(url):
            return fail("رابط غير مدعوم (يجب أن يبدأ بـ http/https/rtsp/rtmp)")

        engine = self._select_engine()
        cmd, err = self._build_cmd(engine, url.strip())
        if not cmd:
            return fail(err)

        try:
            # exteplayer3 يقرأ الأوامر من stdin، لذلك نترك الأنبوب مفتوحاً
            stdin = subprocess.PIPE if engine == "exteplayer3" else subprocess.DEVNULL
            self.process = subprocess.Popen(
                cmd,
                stdin=stdin,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                with open(PID_FILE, "w") as f:
                    f.write(str(self.process.pid))
            except Exception:
                pass

            self.mute_original_audio(True)
            self.is_playing = True
            self.current_url = url
            self.engine_name = engine
            self.label = label
            self._service_ref = self._current_service_ref()
            self._timer.start(WATCH_INTERVAL_MS, False)
            return True, "يعمل الآن عبر: %s" % engine.upper()
        except Exception as e:
            self.process = None
            self.mute_original_audio(False)
            return fail("خطأ في التشغيل: %s" % str(e))

    def stop_audio(self, keep_mute=False):
        was_playing = self.is_playing
        self._timer.stop()
        proc, self.process = self.process, None
        if proc:
            try:
                if proc.stdin:
                    try:
                        proc.stdin.write(b"q\n")
                        proc.stdin.flush()
                    except Exception:
                        pass
                    try:
                        proc.stdin.close()
                    except Exception:
                        pass
                os.killpg(proc.pid, signal.SIGTERM)
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass
            for _ in range(10):
                if proc.poll() is not None:
                    break
                time.sleep(0.05)
            else:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
                except Exception:
                    pass
        try:
            os.remove(PID_FILE)
        except Exception:
            pass

        if not keep_mute and (self.is_playing or self._we_muted):
            self.mute_original_audio(False)
        self.is_playing = False
        self.current_url = ""
        self.engine_name = ""
        self.label = ""
        self._service_ref = ""
        if was_playing and not keep_mute:
            for cb in list(self._stop_hooks):
                try:
                    cb()
                except Exception:
                    pass

    # ---------- المراقبة ----------
    def _auto_stop(self, msg):
        self.stop_audio()
        self._notify(msg)

    def _service_changed(self):
        cur = self._current_service_ref()
        return bool(self._service_ref and cur and cur != self._service_ref)

    def _on_nav_event(self, event):
        if event == iPlayableService.evStart and self.is_playing and self._service_changed():
            self._auto_stop("تم تغيير القناة: أُوقف الصوت الخارجي وعاد صوت القناة")

    def _watch(self):
        if not self.process:
            self._timer.stop()
            return
        if self.process.poll() is not None:
            self._auto_stop("انقطع البث الصوتي: عاد صوت القناة الأصلي")
        elif _in_standby():
            self._auto_stop("وضع الاستعداد: أُوقف الصوت الخارجي")
        elif self._service_changed():
            self._auto_stop("تم تغيير القناة: أُوقف الصوت الخارجي وعاد صوت القناة")
