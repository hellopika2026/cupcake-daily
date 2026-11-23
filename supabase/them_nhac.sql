-- Thêm cài đặt "nhạc nền YouTube". Dán vào Supabase > SQL Editor > Run (chạy một lần).
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

-- Cho trang đọc lấy link nhạc (chỉ trả đúng một giá trị này, không lộ cài đặt khác)
create or replace function public_music() returns text
language sql security definer set search_path = public as $$
  select coalesce((select value #>> '{}' from settings where key = 'music_url'), '');
$$;
grant execute on function public_music() to anon, authenticated;
