# Cupcake Daily

Tờ báo nhỏ của Cupcake, mỗi sáng một lần. Robot quét các nguồn bạn chọn, lấy nguyên bản tiêu đề và mô tả của từng bài (không dùng AI), hiện thành thẻ trên web.

## Gồm những gì
- `supabase/schema.sql`: cấu trúc dữ liệu và mật khẩu quản trị (dán vào Supabase).
- `supabase/functions/check-link/index.ts`: nút "Kiểm tra link" và "Quét thử".
- `site/`: trang đọc (`index.html`) và trang quản trị (`admin.html`).
- `robot/`: robot Python. Chạy trên máy bạn, dự phòng bằng GitHub Actions.
- `windows/`: lịch chạy cho máy Windows.
- `.github/workflows/`: robot dự phòng.
- `.github/workflows/cloudflare.yml`: tự đăng thư mục `site/` lên Cloudflare Pages (https://cupcake-daily.pages.dev) mỗi khi đẩy code lên `main`.

## Cài đặt (làm theo thứ tự)
### 1. Supabase
1. Mở dự án > **SQL Editor** > dán toàn bộ `supabase/schema.sql` > Run.
2. Trong ô SQL mới, bỏ dấu `--` ở 2 dòng cuối file, đổi `ĐỔI_MẬT_KHẨU_Ở_ĐÂY` thành mật khẩu quản trị của bạn, Run.
3. **Edge Functions** > Create function, tên đúng `check-link` > dán nội dung `index.ts` > Deploy.
4. **Project Settings > API**: ghi lại 3 thứ: Project URL, khóa `anon public`, khóa `service_role`.

### 2. GitHub
1. Tạo repo mới (Public), tải toàn bộ thư mục này lên (nhánh `main`).
2. Repo > Settings > **Secrets and variables > Actions**:
   - Tab *Secrets*: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` (service_role).
3. Đăng web bằng **Cloudflare Pages**: tạo dự án Pages tên `cupcake-daily`, rồi thêm 2 secret `CLOUDFLARE_API_TOKEN` (token quyền *Cloudflare Pages: Edit*) và `CLOUDFLARE_ACCOUNT_ID`. Sau đó mỗi lần đẩy lên `main` web tự cập nhật. Đăng tay: `npx wrangler pages deploy site --project-name cupcake-daily`.

### 3. Máy Windows
1. Cài Python nếu chưa có. Chép `robot\.env.example` thành `robot\.env`, điền `SUPABASE_URL` và `SUPABASE_SERVICE_KEY`.
2. Chuột phải `windows\setup.ps1` > Run with PowerShell (làm một lần).
3. Bấm đúp `windows\quet_ngay.bat` để quét thử ngay.

## Lưu ý bảo mật
- Khóa `service_role` và file `.env` không bao giờ đưa lên GitHub hay gửi vào chat.
- Web có `noindex` nên Google không lập chỉ mục; ai có link thì xem được.
