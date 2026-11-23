-- =====================================================================
-- CUPCAKE DAILY - cấu trúc dữ liệu (dán toàn bộ file này vào Supabase > SQL Editor > Run)
-- Chạy lại nhiều lần vẫn an toàn.
-- =====================================================================
create extension if not exists pgcrypto;

-- ---------- Bảng dữ liệu ----------
create table if not exists sources (
  id          uuid primary key default gen_random_uuid(),
  name        text not null,
  url         text not null,              -- link bạn dán vào (trang web hoặc RSS)
  feed_url    text,                       -- link RSS tìm được (nếu có)
  method      text not null default 'rss',-- rss | sitemap | listing
  color       text not null default '#3B5BFF',
  active      boolean not null default true,
  fail_count  int not null default 0,     -- số lần quét lỗi liên tiếp
  last_error  text,
  last_ok_at  timestamptz,
  created_at  timestamptz not null default now()
);

create table if not exists articles (
  id           uuid primary key default gen_random_uuid(),
  source_id    uuid references sources(id) on delete cascade,
  link         text not null unique,
  title_vi     text not null,
  summary_vi   text not null,
  tags         text[] not null default '{}',
  image_url    text,
  read_minutes int,
  published_at timestamptz not null default now(),
  created_at   timestamptz not null default now()
);
create index if not exists articles_pub_idx on articles (published_at desc);

-- Sổ ghi các link đã xem (kể cả link bị bỏ qua) để không xử lý lại
create table if not exists seen (
  link       text primary key,
  source_id  uuid references sources(id) on delete cascade,
  created_at timestamptz not null default now()
);

create table if not exists settings (
  key   text primary key,
  value jsonb not null
);
insert into settings(key, value) values
  ('max_per_day',     '15'::jsonb),
  ('keyword_filter',  '""'::jsonb),
  ('fetch_images',    'true'::jsonb),
  ('window_hours',    '48'::jsonb),
  ('scan_time_local', '"12:10"'::jsonb),
  ('scan_time_backup','"14:00"'::jsonb)
on conflict (key) do nothing;

-- Mỗi lần robot quét ghi 1 dòng (robot dự phòng dựa vào đây để biết hôm nay đã quét chưa)
create table if not exists runs (
  id          uuid primary key default gen_random_uuid(),
  started_at  timestamptz not null default now(),
  finished_at timestamptz,
  by_whom     text,            -- local | backup
  ok          boolean not null default false,
  found       int not null default 0,
  added       int not null default 0,
  note        text
);

create table if not exists admin_config (
  id            int primary key default 1 check (id = 1),
  password_hash text not null
);

create table if not exists admin_attempts (
  id         bigserial primary key,
  at         timestamptz not null default now()
);

-- ---------- Quyền đọc công khai ----------
alter table sources       enable row level security;
alter table articles      enable row level security;
alter table seen          enable row level security;
alter table settings      enable row level security;
alter table runs          enable row level security;
alter table admin_config  enable row level security;
alter table admin_attempts enable row level security;

-- Ai có link web đều đọc được bài (chỉ đọc)
drop policy if exists "public read articles" on articles;
create policy "public read articles" on articles for select to anon, authenticated using (true);

-- Danh sách nguồn công khai chỉ gồm tên + màu (không lộ link RSS)
create or replace view public_sources as
  select id, name, color from sources where active;
grant select on public_sources to anon, authenticated;

-- Giờ cập nhật gần nhất, cho web hiển thị "Cập nhật lần cuối"
create or replace function public_last_run() returns timestamptz
language sql security definer set search_path = public as $$
  select max(finished_at) from runs where ok;
$$;
grant execute on function public_last_run() to anon, authenticated;

-- Link nhạc nền YouTube do chủ trang đặt trong quản trị
create or replace function public_music() returns text
language sql security definer set search_path = public as $$
  select coalesce((select value #>> '{}' from settings where key = 'music_url'), '');
$$;
grant execute on function public_music() to anon, authenticated;

-- Các bảng khác: KHÔNG có policy nào cho anon => người ngoài không đọc/ghi được.
-- Robot dùng khóa service_role nên vượt qua RLS.

-- ---------- Hàm quản trị: mọi hàm đều kiểm mật khẩu ở phía máy chủ ----------
create or replace function _check_pw(pw text) returns boolean
language plpgsql security definer set search_path = public, extensions as $$
declare ok boolean; recent int;
begin
  delete from admin_attempts where at < now() - interval '1 day';
  select count(*) into recent from admin_attempts where at > now() - interval '10 minutes';
  if recent >= 8 then
    raise exception 'Nhập sai quá nhiều lần, thử lại sau 10 phút.';
  end if;
  select (password_hash = crypt(pw, password_hash)) into ok from admin_config where id = 1;
  if ok is not true then
    insert into admin_attempts default values;
    return false;
  end if;
  return true;
end $$;
revoke all on function _check_pw(text) from public, anon, authenticated;

create or replace function admin_login(pw text) returns boolean
language plpgsql security definer set search_path = public, extensions as $$
begin return _check_pw(pw); end $$;

create or replace function admin_list_sources(pw text) returns json
language plpgsql security definer set search_path = public, extensions as $$
begin
  if not _check_pw(pw) then raise exception 'Sai mật khẩu'; end if;
  return coalesce((
    select json_agg(row_to_json(t) order by t.created_at) from (
      select s.*, (select count(*) from articles a where a.source_id = s.id) as article_count
      from sources s) t), '[]'::json);
end $$;

create or replace function admin_add_source(pw text, p_name text, p_url text, p_feed text, p_method text, p_color text)
returns uuid language plpgsql security definer set search_path = public, extensions as $$
declare nid uuid;
begin
  if not _check_pw(pw) then raise exception 'Sai mật khẩu'; end if;
  insert into sources(name, url, feed_url, method, color)
  values (p_name, p_url, nullif(p_feed,''), coalesce(nullif(p_method,''),'rss'), coalesce(nullif(p_color,''),'#3B5BFF'))
  returning id into nid;
  return nid;
end $$;

create or replace function admin_toggle_source(pw text, p_id uuid, p_active boolean) returns void
language plpgsql security definer set search_path = public, extensions as $$
begin
  if not _check_pw(pw) then raise exception 'Sai mật khẩu'; end if;
  update sources set active = p_active, fail_count = case when p_active then 0 else fail_count end where id = p_id;
end $$;

create or replace function admin_delete_source(pw text, p_id uuid) returns void
language plpgsql security definer set search_path = public, extensions as $$
begin
  if not _check_pw(pw) then raise exception 'Sai mật khẩu'; end if;
  delete from sources where id = p_id;
end $$;

create or replace function admin_get_settings(pw text) returns json
language plpgsql security definer set search_path = public, extensions as $$
begin
  if not _check_pw(pw) then raise exception 'Sai mật khẩu'; end if;
  return (select json_object_agg(key, value) from settings);
end $$;

create or replace function admin_set_setting(pw text, p_key text, p_value jsonb) returns void
language plpgsql security definer set search_path = public, extensions as $$
begin
  if not _check_pw(pw) then raise exception 'Sai mật khẩu'; end if;
  if p_key not in ('max_per_day','keyword_filter','fetch_images','window_hours','scan_time_local','scan_time_backup','music_url') then
    raise exception 'Cài đặt không hợp lệ';
  end if;
  insert into settings(key, value) values (p_key, p_value)
  on conflict (key) do update set value = excluded.value;
end $$;

create or replace function admin_recent_runs(pw text) returns json
language plpgsql security definer set search_path = public, extensions as $$
begin
  if not _check_pw(pw) then raise exception 'Sai mật khẩu'; end if;
  return coalesce((select json_agg(r) from (select * from runs order by started_at desc limit 10) r), '[]'::json);
end $$;

grant execute on function admin_login(text), admin_list_sources(text),
  admin_add_source(text,text,text,text,text,text), admin_toggle_source(text,uuid,boolean),
  admin_delete_source(text,uuid), admin_get_settings(text), admin_set_setting(text,text,jsonb),
  admin_recent_runs(text) to anon, authenticated;

-- ---------- Lấy bài cũ (nút "Lấy bài cũ" trong trang quản trị, hàm Edge "backfill", không dùng AI) ----------
-- Bài lấy thêm được đánh dấu để KHÔNG bị tính vào hạn mức "số bài tối đa mỗi ngày" của lần quét thường.
alter table articles add column if not exists backfilled boolean not null default false;

create table if not exists backfill_jobs (
  id          uuid primary key default gen_random_uuid(),
  source_id   uuid references sources(id) on delete cascade,
  requested   int  not null check (requested between 1 and 20),
  status      text not null default 'queued' check (status in ('queued','running','done','failed')),
  added       int  not null default 0,
  note        text,
  created_at  timestamptz not null default now(),
  started_at  timestamptz,
  finished_at timestamptz
);
alter table backfill_jobs drop column if exists items;  -- cột này từng dùng ở bản cũ (đã bỏ)
alter table backfill_jobs enable row level security;  -- không có policy: chỉ hàm bên dưới và khóa service_role đụng được
grant select, insert, update, delete on backfill_jobs to service_role;  -- hàm "backfill" dùng role này

create or replace function admin_backfill_jobs(pw text) returns json
language plpgsql security definer set search_path = public, extensions as $$
begin
  if not _check_pw(pw) then raise exception 'Sai mật khẩu'; end if;
  return coalesce((select json_agg(t) from (
    select j.id, j.requested, j.status, j.added, j.note, j.created_at, j.finished_at, s.name as source_name
    from backfill_jobs j left join sources s on s.id = j.source_id
    order by j.created_at desc limit 8) t), '[]'::json);
end $$;
grant execute on function admin_backfill_jobs(text) to anon, authenticated;

-- ---------- Xóa bài ----------
-- Sổ ghi link các bài đã xóa: robot quét hằng ngày và nút "Lấy bài cũ" đều bỏ qua các link này, nên bài đã xóa không tự quay lại.
create table if not exists deleted_articles (
  link       text primary key,
  deleted_at timestamptz not null default now()
);
alter table deleted_articles enable row level security;  -- không có policy: chỉ hàm bên dưới và service_role đụng được
grant select, insert, update, delete on deleted_articles to service_role;

create or replace function admin_list_articles(pw text, p_source uuid default null, p_q text default '', p_limit int default 20, p_offset int default 0)
returns json language plpgsql security definer set search_path = public, extensions as $$
declare total int; items json;
begin
  if not _check_pw(pw) then raise exception 'Sai mật khẩu'; end if;
  select count(*) into total from articles a
    where (p_source is null or a.source_id = p_source)
      and (coalesce(p_q,'') = '' or a.title_vi ilike '%' || p_q || '%');
  select coalesce(json_agg(t), '[]'::json) into items from (
    select a.id, a.title_vi, a.link, a.created_at, a.backfilled, s.name as source_name
    from articles a left join sources s on s.id = a.source_id
    where (p_source is null or a.source_id = p_source)
      and (coalesce(p_q,'') = '' or a.title_vi ilike '%' || p_q || '%')
    order by a.created_at desc
    limit greatest(1, least(coalesce(p_limit,20), 100)) offset greatest(0, coalesce(p_offset,0))) t;
  return json_build_object('total', total, 'items', items);
end $$;

create or replace function admin_delete_article(pw text, p_id uuid) returns boolean
language plpgsql security definer set search_path = public, extensions as $$
declare l text;
begin
  if not _check_pw(pw) then raise exception 'Sai mật khẩu'; end if;
  delete from articles where id = p_id returning link into l;
  if l is null then return false; end if;
  insert into deleted_articles(link) values (l) on conflict (link) do nothing;
  insert into seen(link) values (l) on conflict (link) do nothing;
  return true;
end $$;
grant execute on function admin_list_articles(text, uuid, text, int, int), admin_delete_article(text, uuid) to anon, authenticated;

-- =====================================================================
-- ĐẶT MẬT KHẨU QUẢN TRỊ: sửa chữ ĐỔI_MẬT_KHẨU_Ở_ĐÂY thành mật khẩu của bạn rồi chạy MỘT LẦN.
-- (Đổi mật khẩu sau này: chạy lại đúng dòng này với mật khẩu mới.)
-- =====================================================================
-- insert into admin_config(id, password_hash) values (1, crypt('ĐỔI_MẬT_KHẨU_Ở_ĐÂY', gen_salt('bf')))
-- on conflict (id) do update set password_hash = excluded.password_hash;
