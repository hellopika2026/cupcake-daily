// Đọc RSS/Atom và lấy sẵn NỘI DUNG bài, để robot trên GitHub không phải gọi vào các trang (ví dụ Substack) đang chặn máy chủ GitHub.
// Chỉ dùng kiểu và hàm thuần của JS nên chạy được cả trên Deno lẫn Node (để thử).

export type FeedItem = { link: string; title: string; published: string | null; description: string; text: string; image: string | null };

const ENT: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " " };
const decode = (s: string) =>
  s.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (m, e: string) => {
    if (e[0] === "#") {
      const n = e[1].toLowerCase() === "x" ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10);
      return Number.isFinite(n) && n > 0 && n < 0x110000 ? String.fromCodePoint(n) : m;
    }
    return ENT[e.toLowerCase()] ?? m;
  });

// Lấy phần ruột của một thẻ. Nếu bọc trong CDATA thì mở ra; nếu bị mã hóa (&lt;p&gt;) thì giải mã để ra HTML thật.
function inner(block: string, tags: string[]): string {
  for (const t of tags) {
    const m = block.match(new RegExp(`<${t}(?:\\s[^>]*)?>([\\s\\S]*?)</${t}>`, "i"));
    if (!m) continue;
    const c = m[1].trim();
    const cd = c.match(/^<!\[CDATA\[([\s\S]*?)\]\]>$/);
    if (cd) return cd[1];
    return /&lt;|&#60;/i.test(c) ? decode(c) : c;
  }
  return "";
}

export function htmlToText(html: string): string {
  return decode(
    html.replace(/<(script|style)[\s\S]*?<\/\1>/gi, " ")
      .replace(/<\/(p|div|h[1-6]|li|blockquote|tr)>|<br\s*\/?>/gi, "\n")
      .replace(/<[^>]+>/g, " "),
  ).replace(/[ \t ]+/g, " ").replace(/ *\n */g, "\n").replace(/\n{3,}/g, "\n\n").trim();
}

function pickImage(block: string, html: string, base: string): string | null {
  // Ưu tiên thumbnail. media:content bị loại khi là video/trình phát: với YouTube nó là link trình phát video (Flash), không phải ảnh.
  const mc = block.match(/<media:content[^>]*>/i)?.[0] || "";
  const mcUrl = mc.match(/url=["']([^"']+)["']/i)?.[1];
  const mcIsImage = !/(type|medium)=["'](video|application|audio)/i.test(mc);
  const cands = [
    block.match(/<media:thumbnail[^>]+url=["']([^"']+)["']/i)?.[1],
    mcIsImage ? mcUrl : undefined,
    block.match(/<enclosure[^>]+type=["']image[^"']*["'][^>]*url=["']([^"']+)["']/i)?.[1],
    block.match(/<enclosure[^>]+url=["']([^"']+)["'][^>]*type=["']image/i)?.[1],
    html.match(/<img[^>]+src=["']([^"']+)["']/i)?.[1],
  ];
  for (const c of cands) {
    if (!c) continue;
    try { return new URL(decode(c), base).href; } catch { /* thử ứng viên kế tiếp */ }
  }
  return null;
}

/** Trả về các bài (mới nhất trước) hoặc null nếu không phải RSS/Atom. */
export function parseFeedItems(xml: string, base: string, maxChars = 12000): FeedItem[] | null {
  const isAtom = /<feed[\s>]/i.test(xml) && !/<rss[\s>]/i.test(xml);
  const blocks = xml.match(isAtom ? /<entry[\s>][\s\S]*?<\/entry>/gi : /<item[\s>][\s\S]*?<\/item>/gi) || [];
  if (!blocks.length) return null;
  const items: FeedItem[] = [];
  for (const b of blocks) {
    let link = "";
    if (isAtom) {
      link = (b.match(/<link[^>]+rel=["']alternate["'][^>]*href=["']([^"']+)/i) || b.match(/<link[^>]+href=["']([^"']+)/i) || [])[1] || "";
    } else {
      link = decode(inner(b, ["link"])).trim();
    }
    link = decode(link).trim();
    if (!link) continue;
    const mediaDesc = inner(b, ["media:description"]); // YouTube để mô tả video ở đây
    const html = inner(b, isAtom ? ["content", "summary"] : ["content:encoded", "description"]) || mediaDesc;
    const d = inner(b, ["pubDate", "published", "updated", "dc:date"]);
    const dt = d ? new Date(decode(d).trim()) : null;
    items.push({
      link,
      title: htmlToText(inner(b, ["title"])).replace(/\s+/g, " "),
      published: dt && !isNaN(+dt) ? dt.toISOString() : null,
      description: htmlToText(inner(b, isAtom ? ["summary"] : ["description"]) || mediaDesc).replace(/\s+/g, " "),
      text: htmlToText(html).slice(0, maxChars),
      image: pickImage(b, html, link),
    });
  }
  if (items.some((i) => i.published)) items.sort((a, b) => (b.published || "").localeCompare(a.published || ""));
  return items;
}

// ---------- Dựng tóm tắt KHÔNG dùng AI ----------
// Ưu tiên phần mô tả do chính tác giả viết trong feed. Thiếu thì lấy vài câu đầu của bài, bỏ qua lời chào.
const GREETING = /^(xin\s*ch[àa]o|ch[àa]o|hello|hi|hey|dear|k[íi]nh\s*g[ửu]i|th[âa]n\s*g[ửu]i|ch[úu]c|c[ảa]m\s*[ơo]n|hy\s*v[ọo]ng)/i;
const clip = (s: string, max: number) => {
  if (s.length <= max) return s;
  const cut = s.slice(0, max);
  const end = Math.max(cut.lastIndexOf(". "), cut.lastIndexOf("! "), cut.lastIndexOf("? "));
  return (end > max * 0.5 ? cut.slice(0, end + 1) : cut.replace(/\s+\S*$/, "") + "…").trim();
};

export function buildSummary(it: FeedItem, target = 320): string {
  const d = it.description.trim();
  const norm = (x: string) => x.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");
  const useful = d.length >= 40 && norm(d) !== norm(it.title) && !norm(it.text).startsWith(norm(d).slice(0, 60));
  if (useful) return clip(d, 500);
  const sentences = it.text.split(/\n+|(?<=[.!?…])\s+/).map((x) => x.trim()).filter((x) => x.length >= 25 && !GREETING.test(x) && !/^https?:\/\//i.test(x));
  let out = "";
  for (const sn of sentences) {
    out += (out ? (/[.!?…:]$/.test(out) ? " " : ". ") : "") + sn;
    if (out.length >= target) break;
  }
  return clip(out, 500);
}
