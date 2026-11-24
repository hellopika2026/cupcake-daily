// Hàm "Kiểm tra link" và "Quét thử" cho trang quản trị Cupcake Daily.
// Dán vào Supabase > Edge Functions > Create a new function, tên: check-link
import { DOMParser } from "https://deno.land/x/deno_dom@v0.1.45/deno-dom-wasm.ts";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};
const UA = "Mozilla/5.0 (compatible; CupcakeDailyBot/1.0; +personal-reader)";
const json = (o: unknown, status = 200) =>
  new Response(JSON.stringify(o), { status, headers: { ...CORS, "Content-Type": "application/json" } });

async function get(url: string): Promise<string> {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), 15000);
  try {
    const r = await fetch(url, { headers: { "User-Agent": UA, Accept: "*/*" }, signal: ctl.signal, redirect: "follow" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    return await r.text();
  } finally { clearTimeout(t); }
}
const strip = (s: string) =>
  s.replace(/<!\[CDATA\[|\]\]>/g, "").replace(/<[^>]+>/g, " ").replace(/&amp;/g, "&").replace(/&#39;|&apos;/g, "'")
    .replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/\s+/g, " ").trim();

type Item = { title: string; link: string; date: string | null };

function parseFeed(xml: string): { title: string; items: Item[] } | null {
  const isAtom = /<feed[\s>]/i.test(xml) && !/<rss[\s>]/i.test(xml);
  const blocks = xml.match(isAtom ? /<entry[\s>][\s\S]*?<\/entry>/gi : /<item[\s>][\s\S]*?<\/item>/gi) || [];
  if (!blocks.length) return null;
  const items: Item[] = blocks.slice(0, 10).map((b) => {
    const title = strip((b.match(/<title[^>]*>([\s\S]*?)<\/title>/i) || [])[1] || "");
    let link = "";
    if (isAtom) {
      const m = b.match(/<link[^>]+rel=["']alternate["'][^>]*href=["']([^"']+)/i) || b.match(/<link[^>]+href=["']([^"']+)/i);
      link = m ? m[1] : "";
    } else link = strip((b.match(/<link[^>]*>([\s\S]*?)<\/link>/i) || [])[1] || "");
    const d = (b.match(/<(?:pubDate|published|updated|dc:date)[^>]*>([\s\S]*?)</i) || [])[1];
    const dt = d ? new Date(strip(d)) : null;
    return { title, link, date: dt && !isNaN(+dt) ? dt.toISOString() : null };
  }).filter((i) => i.link);
  const head = xml.replace(/<(item|entry)[\s>][\s\S]*$/i, "");
  const title = strip((head.match(/<title[^>]*>([\s\S]*?)<\/title>/i) || [])[1] || "");
  return { title, items };
}

async function tryFeed(url: string) {
  try { const p = parseFeed(await get(url)); return p && p.items.length ? p : null; } catch { return null; }
}

async function discover(input: string) {
  const u = new URL(input);
  const root = u.origin;
  let html = "";
  try {
    html = await get(input);
    const p = parseFeed(html);
    if (p) return { method: "rss", feed_url: input, name: p.title, items: p.items, html: "" };
  } catch (e) { /* tiếp tục thử cách khác */ }

  let siteName = "";
  if (html) {
    const doc = new DOMParser().parseFromString(html, "text/html");
    siteName = doc?.querySelector("meta[property='og:site_name']")?.getAttribute("content") ||
      doc?.querySelector("title")?.textContent?.trim() || "";
    const links = [...(doc?.querySelectorAll("link[rel='alternate']") || [])]
      .filter((l) => /rss|atom/i.test(l.getAttribute("type") || "") && l.getAttribute("href"))
      .map((l) => new URL(l.getAttribute("href")!, input).href);
    for (const f of links) {
      const p = await tryFeed(f);
      if (p) return { method: "rss", feed_url: f, name: p.title || siteName, items: p.items, html };
    }
  }
  for (const path of ["/feed", "/feed/", "/rss", "/rss.xml", "/feed.xml", "/atom.xml", "/index.xml"]) {
    const p = await tryFeed(root + path);
    if (p) return { method: "rss", feed_url: root + path, name: p.title || siteName, items: p.items, html };
  }
  // sitemap
  try {
    let sm = await get(root + "/sitemap.xml");
    if (/<sitemapindex/i.test(sm)) {
      const subs = [...sm.matchAll(/<loc>(.*?)<\/loc>/g)].map((m) => m[1].trim());
      const pick = subs.find((s) => /post|blog|article|news/i.test(s)) || subs[subs.length - 1];
      if (pick) sm = await get(pick);
    }
    const items: Item[] = [...sm.matchAll(/<url>\s*<loc>(.*?)<\/loc>(?:[\s\S]*?<lastmod>(.*?)<\/lastmod>)?/g)]
      .map((m) => ({ title: "", link: m[1].trim(), date: m[2] ? new Date(m[2]).toISOString() : null }))
      .filter((i) => new URL(i.link).pathname.length > 1)
      .sort((a, b) => (b.date || "").localeCompare(a.date || "")).slice(0, 10);
    if (items.length) return { method: "sitemap", feed_url: null, name: siteName, items, html };
  } catch { /* bỏ qua */ }
  // danh sách bài trên trang
  if (html) {
    const doc = new DOMParser().parseFromString(html, "text/html");
    const seen = new Set<string>();
    const items: Item[] = [];
    for (const a of doc?.querySelectorAll("a[href]") || []) {
      const text = (a.textContent || "").replace(/\s+/g, " ").trim();
      let link = "";
      try { link = new URL(a.getAttribute("href")!, input).href.split("#")[0]; } catch { continue; }
      const pu = new URL(link);
      const segs = pu.pathname.split("/").filter(Boolean);
      if (pu.host !== u.host || seen.has(link) || text.length < 25) continue;
      if (/\/(tag|tags|category|author|about|contact|page|topics?)(\/|$)/i.test(pu.pathname)) continue;
      if (!(segs.length >= 2 || (segs.length && segs[segs.length - 1].includes("-")))) continue;
      seen.add(link); items.push({ title: text.slice(0, 200), link, date: null });
      if (items.length >= 10) break;
    }
    if (items.length) return { method: "listing", feed_url: null, name: siteName, items, html };
  }
  return null;
}

async function pageInfo(link: string) {
  try {
    const doc = new DOMParser().parseFromString(await get(link), "text/html");
    const img = doc?.querySelector("meta[property='og:image']")?.getAttribute("content") ||
      doc?.querySelector("meta[name='twitter:image']")?.getAttribute("content") || null;
    const title = doc?.querySelector("meta[property='og:title']")?.getAttribute("content") ||
      doc?.querySelector("title")?.textContent?.trim() || "";
    return { image: img ? new URL(img, link).href : null, title };
  } catch { return { image: null, title: "" }; }
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  try {
    const { password, url } = await req.json();
    const base = Deno.env.get("SUPABASE_URL")!;
    const anon = Deno.env.get("SUPABASE_ANON_KEY")!;
    const auth = await fetch(`${base}/rest/v1/rpc/admin_login`, {
      method: "POST",
      headers: { apikey: anon, Authorization: "Bearer " + anon, "Content-Type": "application/json" },
      body: JSON.stringify({ pw: password || "" }),
    });
    if (!auth.ok || (await auth.json()) !== true) return json({ error: "Sai mật khẩu" }, 401);

    let target = String(url || "").trim();
    if (!target) return json({ error: "Chưa có link" }, 400);
    if (!/^https?:\/\//i.test(target)) target = "https://" + target;
    new URL(target);

    const found = await discover(target);
    if (!found) return json({ ok: false, message: "Không tìm thấy bài nào ở link này. Có thể trang chặn robot hoặc cần RSS riêng (ví dụ qua rss.app)." });
    const latest = found.items[0];
    let meta = { image: null as string | null, title: latest.title };
    if (latest.link) meta = await pageInfo(latest.link);
    return json({
      ok: true, method: found.method, feed_url: found.feed_url, name: found.name || new URL(target).hostname,
      count: found.items.length,
      latest: { title: latest.title || meta.title, link: latest.link, date: latest.date },
      has_image: !!meta.image, image: meta.image,
      recent: found.items.slice(0, 5),
    });
  } catch (e) {
    return json({ ok: false, message: "Không kiểm tra được: " + (e as Error).message });
  }
});
