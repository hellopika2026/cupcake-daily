# Cupcake Daily

Tờ báo nhỏ của Cupcake, mỗi sáng một lần. Robot quét các nguồn bạn chọn, lấy nguyên bản **tiêu đề và mô tả** của từng bài (không dùng AI), rồi hiện thành thẻ trên web. Bấm "Đọc bài gốc" để sang trang của tác giả.

- Web: https://cupcake-daily.pages.dev (chặn Google lập chỉ mục, ai có link thì xem được)
- Quản trị: https://cupcake-daily.pages.dev/admin (cần mật khẩu)

## Hệ thống chạy thế nào

```
Nguồn (RSS / sitemap / trang web)
        │  robot Python quét mỗi ngày
        ▼
   Supabase (cơ sở dữ liệu)  ◄── trang quản trị (thêm nguồn, cài đặt, lấy bài cũ)
        │  trang đọc lấy bài qua khóa "anon" (chỉ đọc)
        ▼
   Web tĩnh trong site/  ──►  Cloudflare Pages  (tự đăng khi đẩy code lên main)
```

| Phần | Ở đâu | Làm gì |
|---|---|---|
| Trang đọc, trang quản trị | `site/` | Web tĩnh (HTML/CSS/JS thuần), gọi thẳng Supabase |
| Robot quét bài | `robot/cupcake_robot.py` | Lấy bài mới, ghi vào Supabase |
| Lịch chạy trên máy bạn | `windows/` | Windows tự chạy robot mỗi ngày |
| Lịch chạy dự phòng | `.github/workflows/backup.yml` | GitHub chạy robot nếu hôm đó máy bạn chưa quét |
| Tự đăng web | `.github/workflows/cloudflare.yml` | Đẩy `site/` lên Cloudflare Pages |
| Dữ liệu và hàm quản trị | `supabase/schema.sql` | Bảng, quyền, hàm kiểm mật khẩu |
| Hàm phụ cho trang quản trị | `supabase/functions/` | `check-link` (kiểm tra link, quét thử) và `backfill` (lấy bài cũ) |

## Cách robot lấy bài

1. Mỗi nguồn đọc bằng RSS trước. Không có RSS thì thử sitemap, rồi đọc trực tiếp trang danh sách bài.
2. Bài chỉ được lấy nếu mới trong cửa sổ thời gian (mặc định 48 giờ) và chưa từng xử lý.
3. **Tiêu đề**: giữ nguyên bản gốc, không dịch, không viết lại.
4. **Mô tả**: ưu tiên mô tả tác giả viết trong feed. Nếu không có, dùng mô tả meta của trang. Nếu vẫn không có (hoặc trùng tiêu đề), lấy vài câu đầu của bài.
5. Bài thiếu tiêu đề hoặc mô tả quá ngắn thì bỏ qua. Có thể lọc thêm theo từ khóa.
6. Bài bạn đã xóa không bao giờ được lấy lại. Cùng một link ở hai nguồn chỉ giữ một bài.
7. Mỗi ngày tối đa N bài (mặc định 15). Bài "Lấy bài cũ" không tính vào hạn mức này.

Nút "Lấy bài cũ" ở trang quản trị làm giống hệt cách trên nhưng cho bài đã đăng trước đó của một nguồn (chạy bằng hàm `backfill` trên Supabase, đưa lên web ngay).

## Dùng hằng ngày

**Trang đọc**
- Lọc "Hôm nay / 7 ngày / Tất cả", tìm theo từ khóa, "Xem các ngày trước".
- **Lưu bài**: bấm biểu tượng đánh dấu ở góc dưới thẻ. Xem lại ở mục "Đã lưu". Danh sách này nằm trong trình duyệt đang dùng (đổi máy hoặc xóa dữ liệu trình duyệt là mất).
- **Xóa bài**: bấm biểu tượng thùng rác ở góc dưới thẻ, nhập mật khẩu quản trị trong cửa sổ hiện ra. Bài bị xóa sẽ không bị robot lấy lại.

**Trang quản trị**
- Thêm nguồn: dán link, bấm "Kiểm tra link", rồi "Thêm nguồn này".
- Bật/tắt nguồn, "Quét thử" một nguồn, "Lấy bài cũ" (1 đến 20 bài), xóa nguồn.
- Cài đặt: giờ quét trên máy và giờ dự phòng, số bài tối đa mỗi ngày, từ khóa lọc, có lấy ảnh đại diện không, nhạc nền YouTube.
- Xem các lần quét gần đây.

## Sửa web rồi đưa lên mạng

1. Sửa file trong `site/`. Xem thử bằng cách mở `site/index.html` trong trình duyệt (đã có sẵn địa chỉ Supabase và khóa chỉ-đọc trong `site/config.js`).
2. Commit và đẩy lên nhánh `main`. Nếu có thay đổi trong `site/`, GitHub tự chạy "Đăng web (Cloudflare)" và web cập nhật sau chừng một phút. Xem tiến độ ở tab **Actions**.
3. Muốn thử trước khi đăng: làm việc trên một nhánh riêng (ví dụ `thu-nghiem`), chỉ gộp vào `main` khi ưng.

Đăng tay, không qua GitHub: `npx wrangler pages deploy site --project-name cupcake-daily` (cần đã chạy `npx wrangler login` trên máy).

## Cài đặt từ đầu

### 1. Supabase
1. Tạo dự án. Mở **SQL Editor**, dán toàn bộ `supabase/schema.sql` rồi Run (chạy lại nhiều lần vẫn an toàn).
2. Trong ô SQL mới, bỏ dấu `--` ở 2 dòng cuối phần đặt mật khẩu, đổi `ĐỔI_MẬT_KHẨU_Ở_ĐÂY` thành mật khẩu quản trị của bạn, Run.
3. **Edge Functions**: tạo 2 hàm tên đúng `check-link` và `backfill`, dán nội dung các file trong `supabase/functions/` (với `backfill` có thêm `feed.ts`), Deploy.
4. **Project Settings > API**: ghi lại Project URL, khóa `anon public` và khóa `service_role`.
5. Điền URL và khóa `anon public` vào `site/config.js`. Khóa `anon` được thiết kế để công khai, còn `service_role` thì không.

### 2. GitHub
1. Đẩy toàn bộ thư mục này lên một repo (nhánh `main`).
2. Repo > Settings > **Secrets and variables > Actions** > Secrets, thêm:
   - `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` (service_role): cho robot dự phòng.
   - `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`: cho việc tự đăng web.

### 3. Cloudflare Pages
1. Tạo tài khoản Cloudflare, rồi tạo dự án Pages tên `cupcake-daily`: `npx wrangler pages project create cupcake-daily --production-branch main --force`.
2. Tạo API token tại `dash.cloudflare.com/profile/api-tokens` > Custom token: quyền **Account > Cloudflare Pages > Edit**, cấp cho đúng tài khoản của bạn. Dán vào secret `CLOUDFLARE_API_TOKEN`. `CLOUDFLARE_ACCOUNT_ID` lấy ở trang tổng quan tài khoản.
3. Vào tab Actions, chạy "Đăng web (Cloudflare)" một lần.

Tên dự án Pages không đổi được sau khi tạo. Tên miền riêng gắn trong Cloudflare > dự án > Custom domains.

### 4. Máy Windows
1. Cài Python. Chép `robot\.env.example` thành `robot\.env`, điền `SUPABASE_URL` và `SUPABASE_SERVICE_KEY`.
2. Chuột phải `windows\setup.ps1` > Run with PowerShell (làm một lần: cài thư viện và đặt lịch chạy mỗi ngày).
3. Bấm đúp `windows\quet_ngay.bat` để quét thử ngay. Kết quả ghi ở `logs\robot.log`.

Robot trên máy chạy theo giờ cài trong trang quản trị (mặc định 12:10). Lỡ giờ vì tắt máy thì quét bù khi bật máy. Robot dự phòng trên GitHub chạy 15:00 đến 17:00 giờ Việt Nam và tự bỏ qua nếu hôm đó đã quét xong.

## Thử và gỡ lỗi

- Quét thử, không ghi gì vào dữ liệu: `python robot/cupcake_robot.py --dry-run`
- Xem robot đã làm gì: `logs/robot.log` và mục "Các lần quét gần đây" ở trang quản trị.
- Web báo "Chưa kết nối được dữ liệu": kiểm tra `site/config.js` (URL và khóa `anon`).
- Workflow đăng web báo thiếu secret: thêm `CLOUDFLARE_API_TOKEN` và `CLOUDFLARE_ACCOUNT_ID`, rồi bấm Re-run.
- Hôm nay không thấy bài mới: nguồn chưa có bài mới trong 48 giờ, hoặc đã đủ hạn mức ngày, hoặc bị lọc từ khóa. Xem dòng ghi chú ở lần quét.
- GitHub tự tắt lịch chạy nếu repo im lặng 60 ngày. Workflow dự phòng đã có bước giữ lịch luôn bật.

## Bảo mật

- Khóa `service_role` và file `robot/.env` không bao giờ đưa lên GitHub hay gửi vào chat. `.env` đã nằm trong `.gitignore`.
- Mọi hàm quản trị kiểm mật khẩu ở phía máy chủ Supabase. Người không có mật khẩu không thể thêm, sửa hay xóa dữ liệu, kể cả khi gọi API trực tiếp.
- Hệ thống **không giới hạn số lần nhập sai mật khẩu** (đã bỏ theo lựa chọn của chủ dự án), nên hãy đặt mật khẩu quản trị dài và khó đoán.
- Web có `noindex` nên Google không lập chỉ mục. Ảnh bài lấy trực tiếp từ nguồn gốc.

## Cấu trúc thư mục

```
site/            Trang đọc (index.html), trang quản trị (admin.html), style.css, config.js, _headers
robot/           cupcake_robot.py, requirements.txt, .env.example
supabase/        schema.sql và các hàm trong functions/
windows/         setup.ps1 (đặt lịch), quet_ngay.bat, run_scheduled.bat
.github/workflows/  backup.yml (robot dự phòng), cloudflare.yml (đăng web)
```
