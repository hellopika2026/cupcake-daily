-- Lịch sử cập nhật tin (trang quản trị): mỗi lần hàm update-news chạy ghi 1 dòng vào bảng runs; chỉ giữ 7 ngày gần nhất.
create or replace function admin_news_history(pw text) returns json
language plpgsql security definer set search_path = public, extensions as $$
begin
  if not _check_pw(pw) then raise exception 'Sai mật khẩu'; end if;
  return coalesce((select json_agg(t) from (
    select coalesce(r.finished_at, r.started_at) as at, r.by_whom, r.ok, r.added, r."found", r.note
    from runs r
    where r.by_whom in ('manual', 'auto') and coalesce(r.finished_at, r.started_at) > now() - interval '7 days'
    order by coalesce(r.finished_at, r.started_at) desc
    limit 200) t), '[]'::json);
end $$;
grant execute on function admin_news_history(text) to anon, authenticated;
