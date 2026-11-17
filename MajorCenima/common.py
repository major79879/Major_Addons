# -*- coding: utf-8 -*-
import os


def _data_dir():
    # Prefer a mounted disk so posters don't fill the receiver's flash memory
    for base, must_mount in (("/media/hdd", True), ("/media/usb", True), ("/etc/enigma2", False)):
        if must_mount and not os.path.ismount(os.path.realpath(base)):
            continue
        if os.path.isdir(base) and os.access(base, os.W_OK):
            return os.path.join(base, "MediaLibrary")
    return "/tmp/MediaLibrary"


DATA_DIR = _data_dir()
IMG_DIR = os.path.join(DATA_DIR, "img")
LIB_FILE = os.path.join(DATA_DIR, "library.json")

for _d in (DATA_DIR, IMG_DIR):
    if not os.path.isdir(_d):
        try:
            os.makedirs(_d)
        except OSError:
            pass


def img_path(tmdb_path, size):
    return os.path.join(IMG_DIR, size + tmdb_path.replace("/", "_"))


def local_img(tmdb_path, size):
    """Return the cached image file path, or '' if it isn't downloaded."""
    if not tmdb_path:
        return ""
    p = img_path(tmdb_path, size)
    return p if os.path.exists(p) else ""


AGE = {
    "G": u"للجميع", "U": u"للجميع", "TV-G": u"للجميع", "0": u"للجميع",
    "PG": u"بإشراف أبوي", "TV-PG": u"بإشراف أبوي",
    "TV-Y": u"للأطفال", "TV-Y7": u"+7",
    "PG-13": u"+13", "TV-14": u"+14", "12A": u"+12",
    "R": u"+17", "NC-17": u"+18", "TV-MA": u"+18",
}


def cert_text(cert):
    if not cert:
        return u"التصنيف: غير متوفر"
    age = AGE.get(cert.upper())
    if age is None and cert.isdigit():
        age = u"+" + cert
    return u"التصنيف: %s  %s" % (cert, age) if age else u"التصنيف: %s" % cert
