#!/usr/bin/env python3
"""Cupcake Daily - robot quét nguồn, viết tóm tắt tiếng Việt, ghi vào Supabase.

Chạy:
  python cupcake_robot.py --mode local  --skip-if-done-today     (trên máy bạn, ưu tiên agy)
  python cupcake_robot.py --mode backup --skip-if-done-today     (trên GitHub, dùng Gemini API)
  python cupcake_robot.py --test-llm                              (thử riêng phần AI)
  python cupcake_robot.py --dry-run                               (quét thử, không ghi gì)
"""
import argparse, datetime as dt, html, json, os, re, shlex, subprocess, sys, tempfile, time
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
        for key in ("media_content", "media_thumbnail"):
            for m in e.get(key, []) or []:
                if m.get("url"):
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
    if entry.get("prefetched"):  # nội dung đã có sẵn, không gọi lại vào trang bài
        return {"title": (entry.get("title") or "").strip(), "text": (entry.get("snippet") or "").strip(),
                "image": entry.get("image") if want_image else None}
    page = None
    try:
        page = http_get(entry["link"])
    except Exception as e:
        log("   không mở được trang bài:", str(e)[:100])
    text = ""
    title = entry.get("title") or ""
    image = entry.get("image")
    if page:
        try:
            text = trafilatura.extract(page, include_comments=False, include_tables=False) or ""
        except Exception:
            text = ""
        soup = BeautifulSoup(page, "html.parser")
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
    return {"title": title.strip(), "text": text.strip(), "image": image}


# ------------------------------------------------------------------ AI viết tóm tắt tiếng Việt
PROMPT = """Bạn là biên tập viên của một tờ báo cá nhân tên Cupcake Daily, độc giả là người Việt Nam làm trong ngành công nghệ/kinh doanh nhưng không phải kỹ sư.
Đọc bài dưới đây (có thể bằng tiếng Anh) và trả về DUY NHẤT một đối tượng JSON, không thêm chữ nào khác, với đúng 3 khóa:
- "title_vi": tiêu đề tiếng Việt tự nhiên, rõ ý, tối đa 90 ký tự, không giật tít.
- "summary_vi": tóm tắt tiếng Việt 3 đến 5 câu (khoảng 60 đến 110 từ): bài nói gì, ý chính hoặc con số quan trọng, và vì sao đáng đọc. Chỉ dùng thông tin có trong bài, không bịa. Giữ nguyên tên riêng và thuật ngữ quốc tế quen thuộc (ví dụ API, GPT, agent).
- "tags": danh sách 1 đến 3 nhãn chủ đề ngắn (1 đến 3 từ), viết thường, ưu tiên tiếng Việt.

Nguồn: {source}
Tiêu đề gốc: {title}
Nội dung:
{text}
"""


def parse_llm_json(raw):
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError("AI không trả về JSON")
    d = json.loads(m.group(0))
    t, s = str(d.get("title_vi", "")).strip(), str(d.get("summary_vi", "")).strip()
    tags = [str(x).strip().lower() for x in (d.get("tags") or []) if str(x).strip()][:3]
    if len(t) < 5 or len(s) < 40:
        raise ValueError("AI trả về quá ngắn")
    return {"title_vi": t[:200], "summary_vi": s, "tags": tags}


def llm_agy(prompt):
    cmd = os.environ.get("AGY_COMMAND", "agy")
    timeout = int(os.environ.get("AGY_TIMEOUT", "300"))
    tmp = None
    use_stdin = True
    if "{prompt_file}" in cmd:
        f = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
        f.write(prompt); f.close(); tmp = f.name
        cmd = cmd.replace("{prompt_file}", tmp); use_stdin = False
    elif "{prompt}" in cmd:
        cmd = cmd.replace("{prompt}", subprocess.list2cmdline([prompt])); use_stdin = False
    try:
        p = subprocess.run(cmd, shell=True, input=prompt if use_stdin else None, text=True,
                           encoding="utf-8", capture_output=True, timeout=timeout)
    finally:
        if tmp:
            try: os.unlink(tmp)
            except Exception: pass
    if p.returncode != 0:
        raise RuntimeError(f"agy lỗi ({p.returncode}): {(p.stderr or p.stdout)[:200]}")
    return parse_llm_json(p.stdout)


def llm_gemini(prompt):
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("Chưa có GEMINI_API_KEY")
    model = os.environ.get("GEMINI_MODEL") or "gemini-flash-latest"
    r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                      headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                      json={"contents": [{"parts": [{"text": prompt}]}],
                            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.3}},
                      timeout=90)
    if r.status_code != 200:
        raise RuntimeError(f"Gemini {r.status_code}: {r.text[:200]}")
    parts = r.json()["candidates"][0]["content"]["parts"]
    return parse_llm_json("".join(p.get("text", "") for p in parts))


def summarize(source, title, text, mode):
    prompt = PROMPT.format(source=source, title=title, text=text[:12000])
    order = ["agy", "gemini"] if mode == "local" else ["gemini"]
    if mode == "local" and not os.environ.get("GEMINI_API_KEY"):
        order = ["agy"]
    last = None
    for name in order:
        for attempt in (1, 2):
            try:
                return (llm_agy if name == "agy" else llm_gemini)(prompt), name
            except Exception as e:
                last = f"{name}: {e}"
                log(f"   AI ({name}) lần {attempt} lỗi: {str(e)[:150]}")
                time.sleep(2)
    raise RuntimeError(last or "AI lỗi")


# ------------------------------------------------------------------ Lấy bài cũ theo yêu cầu
def run_backfill(db, job_id, mode):
    """Lấy N bài gần nhất của 1 nguồn (không phân biệt hôm nay hay cũ), tóm tắt và đưa lên trang chủ.
    Bỏ qua: cửa sổ giờ, hạn mức mỗi ngày, bộ lọc từ khóa, 'hôm nay đã quét'. Bài đã có trên web thì không lấy lại."""
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", job_id or ""):
        sys.exit("Mã yêu cầu không hợp lệ")
    jobs = db.select("backfill_jobs", {"select": "*", "id": "eq." + job_id})
    if not jobs:
        sys.exit("Không tìm thấy yêu cầu " + job_id)
    job = jobs[0]

    def upd(**kw):
        db.patch("backfill_jobs", {"id": "eq." + job_id}, kw)

    upd(status="running", started_at=dt.datetime.now(dt.timezone.utc).isoformat())
    added = errors = too_short = 0
    try:
        srcs = db.select("sources", {"select": "*", "id": "eq." + str(job["source_id"])})
        if not srcs:
            raise RuntimeError("Nguồn không còn tồn tại")
        s = srcs[0]
        count = max(1, min(int(job["requested"]), 20))
        st = {r["key"]: r["value"] for r in db.select("settings")}
        want_image = bool(st.get("fetch_images", True))

        if job.get("items"):  # hàm "backfill" trên Supabase đã đọc sẵn bài (GitHub bị nhiều trang chặn)
            entries = [{"link": i["link"], "title": i.get("title") or "", "published": parse_iso(i.get("published")),
                        "snippet": i.get("text") or "", "image": i.get("image"), "prefetched": True}
                       for i in job["items"] if i.get("link")]
            method = "đã đọc sẵn"
        else:
            entries, method, _feed = get_entries(s)
        if any(e["published"] for e in entries):  # có ngày thì xếp mới nhất lên đầu; không có ngày thì giữ thứ tự của nguồn
            entries.sort(key=lambda e: e["published"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
        top = entries[:count]
        log(f"[{s['name']}] {method}: đọc được {len(entries)} bài, lấy {len(top)} bài gần nhất.")

        existing = {r["link"] for r in db.select("articles", {"select": "link"})}
        todo = [e for e in top if e["link"] not in existing]
        have = len(top) - len(todo)
        if have:
            log(f"{have} bài đã có sẵn trên trang, bỏ qua.")

        now = dt.datetime.now(dt.timezone.utc)
        for e in todo:
            log(f"- {e['title'][:70] or e['link']}")
            try:
                art = fetch_article(e, want_image)
                if len(art["text"]) < 120:
                    log("   nội dung quá ngắn, bỏ qua"); too_short += 1
                    continue
                res, who = summarize(s["name"], art["title"], art["text"], mode)
                row = {"source_id": s["id"], "link": e["link"], "title_vi": res["title_vi"],
                       "summary_vi": res["summary_vi"], "tags": res["tags"], "image_url": art["image"],
                       "read_minutes": max(1, round(len(art["text"].split()) / 200)),
                       "published_at": (e["published"] or now).isoformat(), "backfilled": True}
                db.insert("articles", [row], ignore_dups=True, on_conflict="link")
                db.insert("seen", [{"link": e["link"], "source_id": s["id"]}], ignore_dups=True, on_conflict="link")
                added += 1
                log(f"   xong ({who})")
            except Exception as ex:
                errors += 1
                log("   LỖI:", str(ex)[:200])

        parts = [f"đọc được {len(entries)} bài, lấy {len(top)}", f"thêm {added}"]
        if have: parts.append(f"{have} bài đã có sẵn")
        if too_short: parts.append(f"{too_short} bài quá ngắn")
        if errors: parts.append(f"{errors} bài lỗi")
        failed = errors > 0 and added == 0
        upd(status="failed" if failed else "done", added=added, note=", ".join(parts),
            finished_at=dt.datetime.now(dt.timezone.utc).isoformat())
        log("Hoàn tất:", ", ".join(parts))
        if failed:
            sys.exit(1)
    except SystemExit:
        raise
    except Exception as ex:
        log("LỖI:", str(ex)[:300])
        upd(status="failed", added=added, note=str(ex)[:300], finished_at=dt.datetime.now(dt.timezone.utc).isoformat())
        sys.exit(1)


# ------------------------------------------------------------------ Chạy chính
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["local", "backup"], default="local")
    ap.add_argument("--skip-if-done-today", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--test-llm", action="store_true")
    ap.add_argument("--backfill-job", metavar="JOB_ID", help="xử lý 1 yêu cầu 'Lấy bài cũ' trong bảng backfill_jobs")
    a = ap.parse_args()

    if a.test_llm:
        sample = ("Researchers released a new open model that matches larger systems on reasoning "
                  "benchmarks while running on a single GPU. The team says training cost fell by 40 percent "
                  "thanks to better data filtering. " * 4)
        r, who = summarize("Nguồn thử", "A small open model rivals big ones", sample, a.mode)
        log("OK, dùng", who); print(json.dumps(r, ensure_ascii=False, indent=2)); return

    db = DB()
    if a.backfill_job:
        run_backfill(db, a.backfill_job, a.mode); return
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
            if len(art["text"]) < 120:
                log("   nội dung quá ngắn, bỏ qua"); mark_seen.append({"link": e["link"], "source_id": s["id"]})
                if not a.dry_run: db.insert("seen", [{"link": e["link"], "source_id": s["id"]}], ignore_dups=True, on_conflict="link")
                continue
            if kws:
                hay = (art["title"] + " " + art["text"][:3000]).lower()
                if not any(k in hay for k in kws):
                    log("   không khớp từ khóa lọc, bỏ qua")
                    if not a.dry_run: db.insert("seen", [{"link": e["link"], "source_id": s["id"]}], ignore_dups=True, on_conflict="link")
                    continue
            res, who = summarize(s["name"], art["title"], art["text"], a.mode)
            words = len(art["text"].split())
            row = {"source_id": s["id"], "link": e["link"], "title_vi": res["title_vi"],
                   "summary_vi": res["summary_vi"], "tags": res["tags"], "image_url": art["image"],
                   "read_minutes": max(1, round(words / 200)),
                   "published_at": (e["published"] or now).isoformat()}
            if a.dry_run:
                print(json.dumps(row, ensure_ascii=False, indent=1))
            else:
                db.insert("articles", [row], ignore_dups=True, on_conflict="link")
                db.insert("seen", [{"link": e["link"], "source_id": s["id"]}], ignore_dups=True, on_conflict="link")
            added += 1
            log(f"   xong ({who})")
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
