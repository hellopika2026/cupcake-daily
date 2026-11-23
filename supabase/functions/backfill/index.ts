// Hàm "Lấy bài cũ" cho trang quản trị Cupcake Daily.
// Kiểm tra mật khẩu admin -> đọc feed của nguồn -> lấy N bài gần nhất -> ghi thẳng vào bảng articles.
// KHÔNG dùng AI: tiêu đề và phần tóm tắt lấy từ chính feed (mô tả do tác giả viết). Bài lên trang ngay.
// Quét hằng ngày (robot) vẫn dùng AI như cũ.
//
// SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY được Supabase tự cung cấp.
import { buildSummary, type FeedItem, parseFeedItems } from "./feed.ts";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};
const json = (o: unknown, status = 200) =>
  new Response(JSON.stringify(o), { status, headers: { ...CORS, "Content-Type": "application/json" } });
const UA = "Mozilla/5.0 (compatible; CupcakeDailyBot/1.0; +personal-reader)";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_COUNT = 20;

async function getText(url: string): Promise<string> {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), 20000);
  try {
    const r = await fetch(url, { headers: { "User-Agent": UA, Accept: "*/*" }, signal: ctl.signal, redirect: "follow" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    return await r.text();
  } finally { clearTimeout(t); }
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  try {
    const { password, source_id, count } = await req.json();
    const base = Deno.env.get("SUPABASE_URL")!;
    const anon = Deno.env.get("SUPABASE_ANON_KEY")!;
    const svc = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;

    // 1) Mật khẩu admin (dùng chính hàm admin_login, có giới hạn thử sai)
    const auth = await fetch(`${base}/rest/v1/rpc/admin_login`, {
      method: "POST",
      headers: { apikey: anon, Authorization: "Bearer " + anon, "Content-Type": "application/json" },
      body: JSON.stringify({ pw: password || "" }),
    });
    if (!auth.ok || (await auth.json()) !== true) return json({ error: "Sai mật khẩu" }, 401);

    // 2) Kiểm tra dữ liệu gửi lên
    const n = Number(count);
    if (!UUID.test(String(source_id || ""))) return json({ error: "Nguồn không hợp lệ" }, 400);
    if (!Number.isInteger(n) || n < 1 || n > MAX_COUNT) return json({ error: `Số bài phải từ 1 đến ${MAX_COUNT}` }, 400);

    const H = { apikey: svc, Authorization: "Bearer " + svc, "Content-Type": "application/json" };

    // 3) Nguồn phải tồn tại và là RSS/Atom
    const src = await (await fetch(`${base}/rest/v1/sources?select=id,url,feed_url,method&id=eq.${source_id}`, { headers: H })).json();
    if (!Array.isArray(src) || !src.length) return json({ error: "Không tìm thấy nguồn này" }, 404);
    if (!src[0].feed_url && src[0].method !== "rss") {
      return json({ error: "Nguồn này không có RSS nên chưa lấy bài cũ bằng nút này được." }, 422);
    }

    // 4) Đọc feed, lấy N bài mới nhất
    const feedUrl: string = src[0].feed_url || src[0].url;
    let all: FeedItem[] | null;
    try {
      all = parseFeedItems(await getText(feedUrl), feedUrl);
    } catch (e) {
      return json({ error: `Không đọc được nguồn này lúc này (${(e as Error).message}). Thử lại sau nhé.` }, 502);
    }
    if (!all || !all.length) return json({ error: "Nguồn này không có bài nào để lấy." }, 422);
    // Bỏ qua các bài bạn đã xóa (không tính vào N)
    const gone = await (await fetch(`${base}/rest/v1/deleted_articles?select=link&limit=10000`, { headers: H })).json();
    const goneSet = new Set<string>(Array.isArray(gone) ? gone.map((d: { link: string }) => d.link) : []);
    const top = all.filter((it) => !goneSet.has(it.link)).slice(0, n);
    if (!top.length) return json({ ok: true, read: all.length, requested: n, added: 0, existing: 0, skipped: 0 });

    // 5) Dựng bài (không AI) và ghi vào database
    const set = await (await fetch(`${base}/rest/v1/settings?select=value&key=eq.fetch_images`, { headers: H })).json();
    const wantImage = !(Array.isArray(set) && set[0] && set[0].value === false);
    const now = new Date().toISOString();
    const rows = top.map((it) => ({
      source_id, link: it.link, title_vi: it.title, summary_vi: buildSummary(it), tags: [] as string[],
      image_url: wantImage ? it.image : null,
      read_minutes: Math.max(1, Math.round(it.text.split(/\s+/).filter(Boolean).length / 200)),
      published_at: it.published || now, backfilled: true,
    })).filter((r) => r.title_vi.length >= 3 && r.summary_vi.length >= 20);
    const skippedEmpty = top.length - rows.length;

    let added = 0;
    if (rows.length) {
      // ignore-duplicates: bài đã có (trùng link) bị bỏ qua, chỉ trả về những dòng thật sự mới
      const ins = await fetch(`${base}/rest/v1/articles?on_conflict=link`, {
        method: "POST", headers: { ...H, Prefer: "resolution=ignore-duplicates,return=representation" }, body: JSON.stringify(rows),
      });
      const inserted = await ins.json();
      if (!ins.ok || !Array.isArray(inserted)) return json({ error: "Không ghi được bài vào cơ sở dữ liệu." }, 500);
      added = inserted.length;
      await fetch(`${base}/rest/v1/seen?on_conflict=link`, { // để robot quét hằng ngày không xử lý lại các bài này
        method: "POST", headers: { ...H, Prefer: "resolution=ignore-duplicates,return=minimal" },
        body: JSON.stringify(rows.map((r) => ({ link: r.link, source_id }))),
      });
    }
    const existing = rows.length - added;

    // 6) Ghi nhật ký để trang quản trị hiện trong "Lấy bài cũ gần đây"
    const parts = [`đọc được ${all.length} bài, lấy ${top.length}`, `thêm ${added}`];
    if (existing) parts.push(`${existing} bài đã có sẵn`);
    if (skippedEmpty) parts.push(`${skippedEmpty} bài thiếu nội dung`);
    await fetch(`${base}/rest/v1/backfill_jobs`, {
      method: "POST", headers: { ...H, Prefer: "return=minimal" },
      body: JSON.stringify([{ source_id, requested: n, status: "done", added, note: parts.join(", "), started_at: now, finished_at: now }]),
    });
    return json({ ok: true, read: all.length, requested: n, added, existing, skipped: skippedEmpty });
  } catch (e) {
    return json({ error: "Lỗi: " + (e as Error).message }, 500);
  }
});
