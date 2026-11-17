# -*- coding: utf-8 -*-
import json
import os

try:
    from urllib.request import urlopen, Request
    from urllib.parse import urlencode
except ImportError:  # python2
    from urllib2 import urlopen, Request
    from urllib import urlencode

from .common import img_path

API = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/"
UA = {"User-Agent": "Enigma2-MediaLibrary/1.0"}


def fetch_img(tmdb_path, size):
    """Download an image once; returns local path or ''."""
    if not tmdb_path:
        return ""
    dst = img_path(tmdb_path, size)
    if os.path.exists(dst):
        return dst
    try:
        data = urlopen(Request(IMG + size + tmdb_path, headers=UA), timeout=20).read()
        with open(dst + ".tmp", "wb") as f:
            f.write(data)
        os.rename(dst + ".tmp", dst)
        return dst
    except Exception:
        return ""


class TMDB(object):
    def __init__(self, key, lang="ar", country="US"):
        self.key = key.strip()
        self.lang = lang
        self.country = country

    def _get(self, path, **params):
        params["api_key"] = self.key
        req = Request("%s%s?%s" % (API, path, urlencode(params)), headers=UA)
        return json.loads(urlopen(req, timeout=20).read().decode("utf-8"))

    # ---- parental rating -------------------------------------------------
    def _cert_movie(self, d):
        res = (d.get("release_dates") or {}).get("results", [])
        for cc in (self.country, "US"):
            for r in res:
                if r.get("iso_3166_1") == cc:
                    for rd in r.get("release_dates", []):
                        if rd.get("certification"):
                            return rd["certification"]
        return ""

    def _cert_tv(self, d):
        res = (d.get("content_ratings") or {}).get("results", [])
        for cc in (self.country, "US"):
            for r in res:
                if r.get("iso_3166_1") == cc and r.get("rating"):
                    return r["rating"]
        return ""

    # ---- details ---------------------------------------------------------
    def _detail(self, kind, tid):
        extra = "credits,release_dates" if kind == "movie" else "credits,content_ratings"
        d = self._get("/%s/%d" % (kind, tid), language=self.lang, append_to_response=extra)
        if self.lang != "en-US" and (not d.get("overview") or not d.get("poster_path")):
            try:  # fall back to English for missing summary / poster
                en = self._get("/%s/%d" % (kind, tid), language="en-US")
                for k in ("overview", "poster_path", "backdrop_path"):
                    if not d.get(k):
                        d[k] = en.get(k)
            except Exception:
                pass
        return d

    def _norm(self, d, cert):
        cast = [c["name"] for c in (d.get("credits") or {}).get("cast", [])[:8] if c.get("name")]
        runtime = d.get("runtime") or (d.get("episode_run_time") or [0])[0]
        return {
            "tmdb_id": d["id"],
            "title": d.get("title") or d.get("name") or "",
            "year": (d.get("release_date") or d.get("first_air_date") or "")[:4],
            "overview": d.get("overview") or "",
            "rating": round(d.get("vote_average") or 0, 1),
            "cert": cert,
            "genres": [g["name"] for g in d.get("genres", [])],
            "runtime": runtime or 0,
            "cast": cast,
            "poster": d.get("poster_path"),
            "backdrop": d.get("backdrop_path"),
        }

    def movie(self, title, year=""):
        p = {"query": title, "language": self.lang}
        if year:
            p["year"] = year
        res = self._get("/search/movie", **p).get("results")
        if not res and year:
            p.pop("year")
            res = self._get("/search/movie", **p).get("results")
        if not res:
            return None
        d = self._detail("movie", res[0]["id"])
        return self._norm(d, self._cert_movie(d))

    def tv(self, name):
        res = self._get("/search/tv", query=name, language=self.lang).get("results")
        if not res:
            return None
        d = self._detail("tv", res[0]["id"])
        return self._norm(d, self._cert_tv(d))

    def season(self, tid, number):
        d = self._get("/tv/%d/season/%d" % (tid, number), language=self.lang)
        out = {}
        for e in d.get("episodes", []):
            out[e["episode_number"]] = {
                "title": e.get("name") or "",
                "overview": e.get("overview") or "",
                "still": e.get("still_path"),
            }
        return out
