-- ClipHeist: early-access spots + the plan people are interested in.
-- Paste into Supabase -> SQL Editor -> Run (after schema.sql). Safe to run more than once.

-- The plan a person picks when joining (what they're interested in, not what they paid for).
alter table public.waitlist add column if not exists interested_plan text
  check (interested_plan in ('free', 'starter', 'pro', 'premium'));

-- Early access is limited to the first 100 people. Public, read-only numbers for the website counter.
create or replace function public.early_access_spots()
returns json language sql stable security definer set search_path = public as $$
  select json_build_object(
    'total', 100,
    'taken', least(100, (select count(*) from waitlist)),
    'left',  greatest(0, 100 - (select count(*) from waitlist))
  );
$$;
revoke all on function public.early_access_spots() from public;
grant execute on function public.early_access_spots() to anon, authenticated;

-- A signed-in person saves the plan they picked (only on their own waitlist row).
create or replace function public.set_interested_plan(p_plan text)
returns void language plpgsql security definer set search_path = public as $$
begin
  if p_plan not in ('free', 'starter', 'pro', 'premium') then
    raise exception 'unknown plan %', p_plan;
  end if;
  update waitlist set interested_plan = p_plan where user_id = auth.uid();
end $$;
revoke all on function public.set_interested_plan(text) from public;
grant execute on function public.set_interested_plan(text) to authenticated;
