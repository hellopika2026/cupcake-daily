// Hàm "Lấy bài cũ" cho trang quản trị Cupcake Daily.
// Kiểm tra mật khẩu admin -> ghi 1 yêu cầu vào bảng backfill_jobs -> kích hoạt workflow GitHub "backfill.yml".
// Token GitHub chỉ nằm ở đây (secret của Supabase), không bao giờ nằm trong trang web.
//
// Secrets cần đặt (Supabase > Edge Functions > Secrets, hoặc `supabase secrets set`):
//   GH_TOKEN  : fine-grained token của riêng repo này, quyền "Actions: Read and write"
//   GH_REPO   : (tùy chọn) mặc định hellopika2026/cupcake-daily
//   GH_REF    : (tùy chọn) nhánh chứa workflow, mặc định main
// SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY được Supabase tự cung cấp.

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};
const json = (o: unknown, status = 200) =>
  new Response(JSON.stringify(o), { status, headers: { ...CORS, "Content-Type": "application/json" } });
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_COUNT = 20;

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  try {
    const { password, source_id, count } = await req.json();
    const base = Deno.env.get("SUPABASE_URL")!;
    const anon = Deno.env.get("SUPABASE_ANON_KEY")!;
    const svc = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
    const ghToken = Deno.env.get("GH_TOKEN");
    const ghRepo = Deno.env.get("GH_REPO") || "hellopika2026/cupcake-daily";
    const ghRef = Deno.env.get("GH_REF") || "main";

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
    if (!ghToken) return json({ error: "Chưa cài GH_TOKEN trong Supabase (xem hướng dẫn)." }, 500);

    const H = { apikey: svc, Authorization: "Bearer " + svc, "Content-Type": "application/json" };

    // 3) Nguồn phải tồn tại
    const src = await (await fetch(`${base}/rest/v1/sources?select=id&id=eq.${source_id}`, { headers: H })).json();
    if (!Array.isArray(src) || !src.length) return json({ error: "Không tìm thấy nguồn này" }, 404);

    // 4) Mỗi lúc chỉ 1 lượt (tránh bấm dồn làm tốn lượt AI). Yêu cầu treo quá 30 phút coi như hỏng.
    const since = new Date(Date.now() - 30 * 60 * 1000).toISOString();
    const busy = await (await fetch(
      `${base}/rest/v1/backfill_jobs?select=id&status=in.(queued,running)&created_at=gte.${since}&limit=1`, { headers: H })).json();
    if (Array.isArray(busy) && busy.length) return json({ error: "Đang có một lượt lấy bài chạy. Đợi xong rồi bấm tiếp nhé." }, 409);

    // 5) Ghi yêu cầu
    const ins = await fetch(`${base}/rest/v1/backfill_jobs`, {
      method: "POST", headers: { ...H, Prefer: "return=representation" },
      body: JSON.stringify([{ source_id, requested: n }]),
    });
    const rows = await ins.json();
    if (!ins.ok || !rows?.[0]?.id) return json({ error: "Không tạo được yêu cầu. Đã chạy lại file schema.sql chưa?" }, 500);
    const jobId = rows[0].id as string;

    // 6) Kích hoạt workflow trên GitHub (chỉ gửi mã yêu cầu, robot tự đọc phần còn lại từ cơ sở dữ liệu)
    const gh = await fetch(`https://api.github.com/repos/${ghRepo}/actions/workflows/backfill.yml/dispatches`, {
      method: "POST",
      headers: {
        Authorization: "Bearer " + ghToken, Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "cupcake-daily-backfill",
      },
      body: JSON.stringify({ ref: ghRef, inputs: { job_id: jobId } }),
    });
    if (gh.status !== 204) {
      const why = `GitHub từ chối (${gh.status}). Kiểm tra GH_TOKEN, GH_REPO và file backfill.yml đã được đẩy lên nhánh ${ghRef}.`;
      await fetch(`${base}/rest/v1/backfill_jobs?id=eq.${jobId}`, {
        method: "PATCH", headers: H, body: JSON.stringify({ status: "failed", note: why, finished_at: new Date().toISOString() }),
      });
      return json({ error: why }, 502);
    }
    return json({ ok: true, job_id: jobId });
  } catch (e) {
    return json({ error: "Lỗi: " + (e as Error).message }, 500);
  }
});
