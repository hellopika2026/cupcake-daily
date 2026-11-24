#!/usr/bin/env python3
"""Cupcake Daily - robot quét nguồn, lấy nguyên bản tiêu đề + mô tả của bài, ghi vào Supabase (không dùng AI).

Chạy:
  python cupcake_robot.py --mode local  --skip-if-done-today     (trên máy bạn)
  python cupcake_robot.py --mode backup --skip-if-done-today     (dự phòng trên GitHub)
  python cupcake_robot.py --dry-run                               (quét thử, không ghi gì)
"""
import argparse, datetime as dt, html, json, os, re, sys
from urllib.parse import urljoin, urlparse

import feedparser, requests, trafilatura
from bs4 import BeautifulSoup

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

for _s in (sys.stdout, sys.stderr):  # tránh lỗi ký tự lạ trên Windows
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

VN = dt.timezone(dt.timedelta(hours=7))
UA = "Mozilla/5.0 (compatible; CupcakeDailyBot/1.0; +personal-reader)"
HTTP_TIMEOUT = 25


def log(*a):
    print(dt.datetime.now(VN).strftime("%H:%M:%S"), *a, flush=True)


# ------------------------------------------------------------------ Supabase
class DB:
    def __init__(self):
        self.url = os.environ.get("SUPABASE_URL", "").rstrip("/")
        self.key = os.environ.get("SUPABASE_SERVICE_KEY", "")
        if not self.url or not self.key:
            sys.exit("Thiếu SUPABASE_URL hoặc SUPABASE_SERVICE_KEY trong .env")
        self.h = {"apikey": self.key, "Authorization": "Bearer " + self.key,
                  "Content-Type": "application/json"}

    def _r(self, method, table, params=None, body=None, prefer=None, extra=None):
        h = dict(self.h)
        if prefer:
            h["Prefer"] = prefer
        if extra:
            h.update(extra)
        r = requests.request(method, f"{self.url}/rest/v1/{table}", params=params,
                             data=json.dumps(body) if body is not None else None,
                             headers=h, timeout=30)
        if r.status_code >= 300:
            raise RuntimeError(f"Supabase {method} {table}: {r.status_code} {r.text[:300]}")
        return r.json() if r.text else None

    def select(self, table, params=None):
        out, start = [], 0
        while True:
            rows = self._r("GET", table, params, extra={"Range": f"{start}-{start + 999}"})
            out += rows
            if len(rows) < 1000:
                return out
            start += 1000

    def insert(self, table, rows, ignore_dups=False, on_conflict=None):
        prefer = "return=representation"
        params = None
        if ignore_dups:
            prefer = "resolution=ignore-duplicates,return=minimal"
            params = {"on_conflict": on_conflict} if on_conflict else None
        return self._r("POST", table, params, rows, prefer)

    def patch(self, table, match, body):
        return self._r("PATCH", table, match, body, "return=minimal")


# ------------------------------------------------------------------ Mạng
def http_get(url, binary=False):
    r = requests.get(url, headers={"User-Agent": UA, "Accept": "*/*",
                                   "Accept-Language": "en,vi;q=0.8"},
                     timeout=HTTP_TIMEOUT, allow_redirects=True)
    r.raise_for_status()
    if binary:
        return r.content
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def to_dt(struct):
    if not struct:
        return None
    try:
        return dt.datetime(*struct[:6], tzinfo=dt.timezone.utc)
    except Exception:
        return None


def parse_iso(s):
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None


def strip_html(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


# ------------------------------------------------------------------ Lấy danh sách bài từ 1 nguồn
COMMON_FEEDS = ["/feed", "/feed/", "/rss", "/rss.xml", "/feed.xml", "/atom.xml", "/index.xml", "/blog/feed"]
SKIP_PATH = re.compile(r"/(tag|tags|category|categories|author|about|contact|privacy|terms|login|"
                       r"subscribe|search|page|topics?)(/|$)", re.I)


def entries_from_feed_text(text):
    d = feedparser.parse(text)
    out = []
    for e in d.entries:
        link = e.get("link")
        if not link:
            continue
        img = None
        # Ưu tiên ảnh thumbnail. media:content bị loại khi là video/trình phát: với YouTube nó là link trình phát video (Flash), không phải ảnh.
        for key in ("media_thumbnail", "media_content"):
            for m in e.get(key, []) or []:
                kind = (m.get("type") or m.get("medium") or "").lower()
                if m.get("url") and (key == "media_thumbnail" or not kind.startswith(("video", "application", "audio"))):
                    img = m["url"]
                    break
            if img:
                break
        if not img:
            for en in e.get("enclosures", []) or []:
                if str(en.get("type", "")).startswith("image") and en.get("href"):
                    img = en["href"]
                    break
        body = ""
        if e.get("content"):
            body = e["content"][0].get("value", "")
        body = body or e.get("summary", "")
        if not img and body:
            m = re.search(r'<img[^>]+src=["\']([^"\']+)', body)
            if m:
                img = urljoin(link, m.group(1))
        out.append({"link": link, "title": strip_html(e.get("title", "")),
                    "published": to_dt(e.get("published_parsed") or e.get("updated_parsed")),
                    "description": strip_html(e.get("summary", "")),
                    "snippet": strip_html(body)[:4000], "image": img})
    return out


def find_feed_links(page_html, base):
    soup = BeautifulSoup(page_html, "html.parser")
    links = []
    for l in soup.find_all("link", rel=lambda v: v and "alternate" in v):
        t = (l.get("type") or "").lower()
        if ("rss" in t or "atom" in t) and l.get("href"):
            links.append(urljoin(base, l["href"]))
    return links


def entries_from_sitemap(base):
    root = f"{urlparse(base).scheme}://{urlparse(base).netloc}"
    try:
        xml = http_get(root + "/sitemap.xml")
    except Exception:
        return []
    def urls(x):
        return re.findall(r"<url>\s*<loc>(.*?)</loc>(?:.*?<lastmod>(.*?)</lastmod>)?", x, re.S)
    if "<sitemapindex" in xml:
        subs = re.findall(r"<loc>(.*?)</loc>", xml)
        pref = [s for s in subs if re.search(r"post|blog|article|news", s, re.I)] or subs[-2:]
        items = []
        for s in pref[:3]:
            try:
                items += urls(http_get(s.strip()))
            except Exception:
                pass
    else:
        items = urls(xml)
    path = urlparse(base).path.rstrip("/")
    out = []
    for loc, lm in items:
        loc = html.unescape(loc.strip())
        p = urlparse(loc).path
        if path and not p.startswith(path):
            continue
        if SKIP_PATH.search(p) or p.strip("/") == "":
            continue
        out.append({"link": loc, "title": "", "published": parse_iso(lm), "snippet": "", "image": None})
    return out


def entries_from_listing(page_html, base):
    soup = BeautifulSoup(page_html, "html.parser")
    host = urlparse(base).netloc
    seen, out = set(), []
    for a in soup.find_all("a", href=True):
        link = urljoin(base, a["href"]).split("#")[0]
        pu = urlparse(link)
        text = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
        if pu.netloc != host or link in seen or SKIP_PATH.search(pu.path):
            continue
        segs = [s for s in pu.path.split("/") if s]
        if len(text) < 25 or not (len(segs) >= 2 or (segs and "-" in segs[-1])):
            continue
        seen.add(link)
        out.append({"link": link, "title": text[:200], "published": None, "snippet": "", "image": None})
    return out[:15]


def get_entries(src):
    """Trả về (danh sách bài, cách đã dùng, feed_url đã tìm thấy)."""
    tried = []
    first = src.get("feed_url") or src["url"]
    try:
        text = http_get(first)
        es = entries_from_feed_text(text)
        if es:
            return es, "rss", first
        # trang web thường: tìm RSS ẩn trong HTML
        for fl in find_feed_links(text, first):
            try:
                es = entries_from_feed_text(http_get(fl))
                if es:
                    return es, "rss", fl
            except Exception as e:
                tried.append(str(e))
        page_html = text
    except Exception as e:
        tried.append(str(e))
        page_html = None
    root = f"{urlparse(src['url']).scheme}://{urlparse(src['url']).netloc}"
    for p in COMMON_FEEDS:
        try:
            es = entries_from_feed_text(http_get(root + p))
            if es:
                return es, "rss", root + p
        except Exception:
            pass
    es = entries_from_sitemap(src["url"])
    if es:
        return es, "sitemap", None
    if page_html:
        es = entries_from_listing(page_html, src["url"])
        if es:
            return es, "listing", None
    raise RuntimeError("Không đọc được bài nào từ nguồn này. " + "; ".join(tried)[:200])


# ------------------------------------------------------------------ Lấy nội dung 1 bài
def fetch_article(entry, want_image):
    page = None
    try:
        page = http_get(entry["link"])
    except Exception as e:
        log("   không mở được trang bài:", str(e)[:100])
    text = ""
    title = entry.get("title") or ""
    image = entry.get("image")
    meta_desc = ""
    if page:
        try:
            text = trafilatura.extract(page, include_comments=False, include_tables=False) or ""
        except Exception:
            text = ""
        soup = BeautifulSoup(page, "html.parser")
        for key in (("property", "og:description"), ("name", "description"), ("name", "twitter:description")):
            m = soup.find("meta", attrs={key[0]: key[1]})
            if m and m.get("content"):
                meta_desc = strip_html(m["content"]); break
        if not title:
            og = soup.find("meta", property="og:title")
            title = (og.get("content") if og else "") or (soup.title.string if soup.title else "") or ""
        if want_image and not image:
            for prop in ("og:image", "twitter:image"):
                m = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
                if m and m.get("content"):
                    image = urljoin(entry["link"], m["content"])
                    break
    if len(text) < 300:
        text = entry.get("snippet") or text
    if not want_image:
        image = None
    return {"title": title.strip(), "text": text.strip(), "image": image, "meta_desc": meta_desc}


# ------------------------------------------------------------------ Dựng tóm tắt từ mô tả gốc (không AI)
GREETING = re.compile(r"^(hi|hello|hey|dear|xin chào|chào)\b", re.I)


def _norm(x):
    return re.sub(r"[\W_]+", "", (x or "").lower())


def clip(s, mx=500):
    s = re.sub(r"\s+", " ", s or "").strip()
    if len(s) <= mx:
        return s
    cut = s[:mx]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[:end + 1] if end > mx * 0.5 else re.sub(r"\s+\S*$", "", cut) + "…").strip()


def build_summary(title, desc, text, target=320):
    """Ưu tiên mô tả do tác giả viết; nếu không có hoặc trùng tiêu đề/đầu bài thì lấy vài câu đầu của bài."""
    d = (desc or "").strip()
    if len(d) >= 40 and _norm(d) != _norm(title) and not _norm(text).startswith(_norm(d)[:60]):
        return clip(d)
    out = ""
    for sn in re.split(r"[\r\n]+|(?<=[.!?…])\s+", text or ""):
        sn = sn.strip()
        if len(sn) < 25 or GREETING.search(sn) or (sn.endswith(",") and len(sn) < 80):  # dòng chào đầu thư
            continue
        out += (" " if re.search(r"[.!?…:]$", out) else ". " if out else "") + sn
        if len(out) >= target:
            break
    return clip(out)


# ------------------------------------------------------------------ Chạy chính
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["local", "backup"], default="local")
    ap.add_argument("--skip-if-done-today", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    db = DB()
    now = dt.datetime.now(dt.timezone.utc)
    today0 = dt.datetime.now(VN).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(dt.timezone.utc)

    st = {r["key"]: r["value"] for r in db.select("settings")}

    if a.skip_if_done_today:  # chế độ chạy theo lịch: chạy đúng giờ cài đặt và chỉ 1 lần mỗi ngày
        key = "scan_time_local" if a.mode == "local" else "scan_time_backup"
        hhmm = str(st.get(key, "12:10" if a.mode == "local" else "14:00"))
        nowvn = dt.datetime.now(VN)
        try:
            h, m = [int(x) for x in hhmm.split(":")[:2]]
        except Exception:
            h, m = 12, 10
        if (nowvn.hour, nowvn.minute) < (h, m):
            log(f"Chưa tới giờ quét ({hhmm}), bỏ qua."); return
        done = db.select("runs", {"select": "id", "ok": "eq.true", "started_at": "gte." + today0.isoformat(), "limit": "1"})
        if done:
            log("Hôm nay đã quét xong rồi, bỏ qua."); return

    max_per_day = int(st.get("max_per_day", 15))
    window = dt.timedelta(hours=int(st.get("window_hours", 48)))
    kws = [k.strip().lower() for k in str(st.get("keyword_filter", "")).split(",") if k.strip()]
    want_image = bool(st.get("fetch_images", True))

    run_id = None
    if not a.dry_run:
        run_id = db.insert("runs", [{"by_whom": a.mode}])[0]["id"]

    sources = db.select("sources", {"select": "*", "active": "eq.true"})
    seen_since = (now - dt.timedelta(days=120)).isoformat()
    seen = {r["link"] for r in db.select("seen", {"select": "link", "created_at": "gte." + seen_since})}
    try:  # bài bạn đã xóa thì không lấy lại
        seen |= {r["link"] for r in db.select("deleted_articles", {"select": "link"})}
    except RuntimeError:  # chưa chạy schema.sql mới (chưa có bảng deleted_articles)
        pass
    try:  # bài "lấy bài cũ" không tính vào hạn mức mỗi ngày
        already_today = len(db.select("articles", {"select": "id", "created_at": "gte." + today0.isoformat(),
                                                   "backfilled": "eq.false"}))
    except RuntimeError:  # chưa chạy schema.sql mới (chưa có cột backfilled)
        already_today = len(db.select("articles", {"select": "id", "created_at": "gte." + today0.isoformat()}))
    budget = max(0, max_per_day - already_today)
    log(f"{len(sources)} nguồn đang bật; hôm nay còn được thêm {budget} bài.")

    candidates, mark_seen, failed = [], [], 0
    item_errors = 0
    for s in sources:
        try:
            entries, method, feed = get_entries(s)
            first_time = not any(True for e in entries if e["link"] in seen)
            new = [e for e in entries if e["link"] not in seen]
            fresh = []
            for e in new:
                old = e["published"] and (now - e["published"]) > window
                undated_first = (e["published"] is None) and first_time  # nguồn mới, không có ngày: chỉ ghi nhận
                if old or undated_first:
                    mark_seen.append({"link": e["link"], "source_id": s["id"]})
                else:
                    e["source"] = s; fresh.append(e)
            candidates += fresh
            log(f"[{s['name']}] {method}: {len(entries)} bài, mới {len(fresh)}")
            if not a.dry_run:
                db.patch("sources", {"id": "eq." + s["id"]},
                         {"fail_count": 0, "last_error": None, "last_ok_at": now.isoformat(),
                          "method": method, **({"feed_url": feed} if feed else {})})
        except Exception as ex:
            failed += 1
            log(f"[{s['name']}] LỖI:", str(ex)[:200])
            if not a.dry_run:
                db.patch("sources", {"id": "eq." + s["id"]},
                         {"fail_count": (s.get("fail_count") or 0) + 1, "last_error": str(ex)[:300]})

    if mark_seen and not a.dry_run:
        db.insert("seen", mark_seen, ignore_dups=True, on_conflict="link")

    uniq = {}
    for e in candidates:  # cùng một link xuất hiện ở 2 nguồn thì chỉ giữ 1
        uniq.setdefault(e["link"], e)
    candidates = list(uniq.values())
    candidates.sort(key=lambda e: e["published"] or now, reverse=True)
    log(f"Tổng {len(candidates)} bài mới, sẽ xử lý tối đa {budget}.")
    added = 0
    for e in candidates:
        if added >= budget:
            break
        s = e["source"]
        log(f"- {e['title'][:70] or e['link']}")
        try:
            art = fetch_article(e, want_image)
            title = art["title"] or e["title"]
            summary = build_summary(title, e.get("description") or art["meta_desc"], art["text"])
            if len(title) < 3 or len(summary) < 20:
                log("   thiếu tiêu đề hoặc mô tả, bỏ qua"); mark_seen.append({"link": e["link"], "source_id": s["id"]})
                if not a.dry_run: db.insert("seen", [{"link": e["link"], "source_id": s["id"]}], ignore_dups=True, on_conflict="link")
                continue
            if kws:
                hay = (title + " " + art["text"][:3000]).lower()
                if not any(k in hay for k in kws):
                    log("   không khớp từ khóa lọc, bỏ qua")
                    if not a.dry_run: db.insert("seen", [{"link": e["link"], "source_id": s["id"]}], ignore_dups=True, on_conflict="link")
                    continue
            words = len(art["text"].split())
            row = {"source_id": s["id"], "link": e["link"], "title_vi": title[:200],
                   "summary_vi": summary, "tags": [], "image_url": art["image"],
                   "read_minutes": max(1, round(words / 200)),
                   "published_at": (e["published"] or now).isoformat()}
            if a.dry_run:
                print(json.dumps(row, ensure_ascii=False, indent=1))
            else:
                db.insert("articles", [row], ignore_dups=True, on_conflict="link")
                db.insert("seen", [{"link": e["link"], "source_id": s["id"]}], ignore_dups=True, on_conflict="link")
            added += 1
            log("   xong")
        except Exception as ex:
            log("   LỖI, sẽ thử lại lần quét sau:", str(ex)[:200])
            item_errors += 1

    ok = not (sources and failed == len(sources)) and item_errors == 0
    note = f"{failed} nguồn lỗi" if failed else None
    if run_id:
        db.patch("runs", {"id": "eq." + run_id},
                 {"finished_at": dt.datetime.now(dt.timezone.utc).isoformat(), "ok": ok,
                  "found": len(candidates), "added": added, "note": note})
    log(f"Hoàn tất: thêm {added} bài." + ("" if ok else " (chưa trọn vẹn, sẽ thử lại)"))
    if not ok and a.skip_if_done_today:
        sys.exit(1)  # báo lỗi để lịch tự thử lại sau 1 giờ


if __name__ == "__main__":
    main()
