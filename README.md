# Cupcake Daily

Tờ báo nhỏ của Cupcake, mỗi sáng một lần. Robot quét các nguồn bạn chọn, viết tiêu đề và tóm tắt tiếng Việt, hiện thành thẻ trên web.

## Gồm những gì
- `supabase/schema.sql`: cấu trúc dữ liệu và mật khẩu quản trị (dán vào Supabase).
- `supabase/functions/check-link/index.ts`: nút "Kiểm tra link" và "Quét thử".
- `site/`: trang đọc (`index.html`) và trang quản trị (`admin.html`).
- `robot/`: robot Python. Chạy trên máy bạn (ưu tiên `agy`), dự phòng bằng Gemini API trên GitHub.
- `windows/`: lịch chạy cho máy Windows.
- `.github/workflows/`: robot dự phòng và đăng web lên GitHub Pages.

## Cài đặt (làm theo thứ tự)
### 1. Supabase
1. Mở dự án > **SQL Editor** > dán toàn bộ `supabase/schema.sql` > Run.
2. Trong ô SQL mới, bỏ dấu `--` ở 2 dòng cuối file, đổi `ĐỔI_MẬT_KHẨU_Ở_ĐÂY` thành mật khẩu quản trị của bạn, Run.
3. **Edge Functions** > Create function, tên đúng `check-link` > dán nội dung `index.ts` > Deploy.
4. **Project Settings > API**: ghi lại 3 thứ: Project URL, khóa `anon public`, khóa `service_role`.

### 2. GitHub
1. Tạo repo mới (Public), tải toàn bộ thư mục này lên (nhánh `main`).
2. Repo > Settings > **Secrets and variables > Actions**:
   - Tab *Secrets*: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` (service_role), `GEMINI_API_KEY`.
   - Tab *Variables*: `SUPABASE_URL`, `SUPABASE_ANON_KEY` (anon public).
3. Settings > **Pages** > Source: **GitHub Actions**. Sau đó vào tab Actions, chạy "Đăng web". Link web sẽ hiện ở đó.

### 3. Máy Windows
1. Cài Python nếu chưa có. Chép `robot\.env.example` thành `robot\.env`, điền `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, và cách gọi `agy`.
2. Chuột phải `windows\setup.ps1` > Run with PowerShell (làm một lần).
3. Bấm đúp `windows\test_agy.bat` để thử phần AI. Rồi `windows\quet_ngay.bat` để quét thử ngay.

## Lưu ý bảo mật
- Khóa `service_role` và file `.env` không bao giờ đưa lên GitHub hay gửi vào chat.
- Web có `noindex` nên Google không lập chỉ mục; ai có link thì xem được.
