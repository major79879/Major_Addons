# -*- coding: utf-8 -*-
import json
import os
import re
from threading import Thread

from .common import LIB_FILE
from .tmdb import TMDB, fetch_img

VIDEO_EXT = (".mkv", ".mp4", ".avi", ".ts", ".m2ts", ".mov", ".wmv", ".mpg", ".mpeg", ".flv", ".m4v")
MIN_SIZE = 30 * 1024 * 1024            # skip samples / trailers
SKIP_DIRS = ("lost+found", "timeshift", "trash", "mediacenter")
REC_RX = re.compile(r"^\d{8} \d{4} - ")  # Enigma2 TV recordings: "20260101 2030 - Channel - Title"

SEP = r"(?:^|[\s._\-\[\(])"
SERIES_RX = [
    re.compile(r"^(?P<name>.*?)" + SEP + r"[Ss](?P<s>\d{1,2})[\s._\-]*[Ee](?P<e>\d{1,3})(?!\d)"),
    re.compile(r"^(?P<name>.*?)" + SEP + r"(?P<s>\d{1,2})[xX](?P<e>\d{2,3})(?!\d)"),
]
SEASON_DIR = re.compile(r"^(?:season|s|الموسم)[\s._\-]*(\d{1,2})$", re.I | re.U)
EP_ONLY = re.compile(r"(?:^|[\s._\-\[])(?:e|ep|episode|الحلقة)[\s._\-]*(\d{1,3})(?!\d)", re.I | re.U)
JUNK = re.compile(
    r"\b(480p|576p|720p|1080p|2160p|4k|uhd|bluray|brrip|bdrip|web-?dl|webrip|hdrip|dvdrip|hdtv|"
    r"x264|x265|h264|h265|hevc|aac|ac3|dts|atmos|10bit|remux|proper|extended|unrated|imax|"
    r"arabic|multi|dual|subbed|hdr)\b.*", re.I)


def clean_title(s):
    s = re.sub(r"[._]+", " ", s)
    s = re.sub(r"\[[^\]]*\]", " ", s)
    s = re.sub(r"\(\s*(19|20)\d{2}\s*\)", " ", s)
    s = JUNK.sub("", s)
    return " ".join(re.sub(r"[\-\(\)]+", " ", s).split())


def parse_movie(stem):
    s = re.sub(r"[._]+", " ", stem)
    s = re.sub(r"\[[^\]]*\]", " ", s)
    year, m = "", None
    for m in re.finditer(r"[\(\s\-](19\d{2}|20\d{2})(?=[\)\s\-]|$)", s):
        pass
    if m and m.start() > 0:
        year = m.group(1)
        s = s[:m.start()]
    return clean_title(s), year


def classify(path):
    """-> ('episode', (show, season, ep)) or ('movie', (title, year))"""
    stem = os.path.splitext(os.path.basename(path))[0]
    parent = os.path.basename(os.path.dirname(path))
    grand = os.path.basename(os.path.dirname(os.path.dirname(path)))
    for rx in SERIES_RX:
        m = rx.match(stem)
        if m:
            name = clean_title(m.group("name"))
            if not name:
                name = clean_title(grand if SEASON_DIR.match(parent) else parent)
            return "episode", (name, int(m.group("s")), int(m.group("e")))
    m = SEASON_DIR.match(parent)
    if m:
        e = EP_ONLY.search(stem) or re.match(r"^\s*(\d{1,3})(?!\d)", stem)
        if e:
            return "episode", (clean_title(grand), int(m.group(1)), int(e.group(1)))
    return "movie", parse_movie(stem)


def find_videos(paths):
    for root in paths:
        for d, dirs, files in os.walk(root):
            dirs[:] = [x for x in dirs if not x.startswith(".") and x.lower() not in SKIP_DIRS]
            for f in files:
                if f.startswith(".") or not f.lower().endswith(VIDEO_EXT) or REC_RX.match(f):
                    continue
                p = os.path.join(d, f)
                try:
                    if os.path.getsize(p) < MIN_SIZE:
                        continue
                except OSError:
                    continue
                yield p


def norm_key(name):
    return re.sub(r"\W+", "", name.lower(), flags=re.U)


def load():
    try:
        with open(LIB_FILE) as f:
            d = json.load(f)
        d.setdefault("movies", [])
        d.setdefault("series", [])
        return d
    except Exception:
        return {"movies": [], "series": []}


def save(lib):
    with open(LIB_FILE + ".tmp", "w") as f:
        json.dump(lib, f)
    os.rename(LIB_FILE + ".tmp", LIB_FILE)


def blank(title, year=""):
    return {"tmdb_id": 0, "title": title, "year": year, "overview": "", "rating": 0, "cert": "",
            "genres": [], "runtime": 0, "cast": [], "poster": None, "backdrop": None, "unmatched": True}


class Builder(Thread):
    """Scans the disk and fetches metadata in the background."""

    def __init__(self, key, lang, country, paths):
        Thread.__init__(self)
        self.daemon = True
        self.tm = TMDB(key, lang, country)
        self.paths = [p.strip() for p in paths.split(",") if p.strip()]
        self.status = u"جاري البحث عن الملفات..."
        self.finished = False
        self.error = ""

    def run(self):
        try:
            self.work()
        except Exception as e:
            self.error = str(e)
        self.finished = True

    def images(self, it):
        fetch_img(it.get("poster"), "w500")
        fetch_img(it.get("backdrop"), "w1280")

    def work(self):
        files = list(find_videos(self.paths))
        old = load()
        old_movies = dict((m["path"], m) for m in old["movies"])
        old_series = dict((s["key"], s) for s in old["series"])
        movies, groups = [], {}

        for i, f in enumerate(files):
            kind, info = classify(f)
            if kind == "episode":
                name, s, e = info
                g = groups.setdefault(norm_key(name), {"name": name, "eps": {}})
                g["eps"].setdefault(s, {})[e] = f
                continue
            om = old_movies.get(f)
            if om and not om.get("unmatched"):
                self.images(om)
                movies.append(om)
                continue
            title, year = info
            self.status = u"فيلم %d/%d: %s" % (i + 1, len(files), title)
            try:
                it = self.tm.movie(title, year)
            except Exception:
                it = None
            it = it or blank(title, year)
            it["path"] = f
            self.images(it)
            movies.append(it)

        series = []
        for key, g in groups.items():
            self.status = u"مسلسل: %s" % g["name"]
            s = old_series.get(key)
            if not s or s.get("unmatched"):
                try:
                    s = self.tm.tv(g["name"])
                except Exception:
                    s = None
                s = s or blank(g["name"])
                s["seasons"] = dict((k, v) for k, v in (old_series.get(key, {}).get("seasons") or {}).items())
            s["key"] = key
            self.images(s)
            seasons = {}
            for sn in sorted(g["eps"]):
                files_ = g["eps"][sn]
                old_season = (s.get("seasons") or {}).get(str(sn))
                old_eps = dict((e["num"], e) for e in old_season["episodes"]) if old_season else {}
                tm_eps = {}
                if any(n not in old_eps for n in files_) and s.get("tmdb_id"):
                    self.status = u"%s - الموسم %d" % (g["name"], sn)
                    try:
                        tm_eps = self.tm.season(s["tmdb_id"], sn)
                    except Exception:
                        tm_eps = {}
                episodes = []
                for n in sorted(files_):
                    e = dict(old_eps.get(n) or tm_eps.get(n) or {})
                    e.setdefault("overview", "")
                    e.setdefault("still", None)
                    if not e.get("title"):
                        e["title"] = u"الحلقة %d" % n
                    e["num"], e["path"] = n, files_[n]
                    fetch_img(e["still"], "w300")
                    episodes.append(e)
                seasons[str(sn)] = {"episodes": episodes}
            s["seasons"] = seasons
            series.append(s)

        save({"movies": movies, "series": series})
        self.status = u"تم: %d فيلم، %d مسلسل" % (len(movies), len(series))
