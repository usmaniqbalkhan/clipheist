-- ClipHeist database schema. Paste into Supabase -> SQL Editor -> New query -> Run. Safe to run more than once.
--
-- profiles : one row per signed-in user (Google). Holds the plan and clip credits.
-- orders   : one row per Polar order, written only by the server (Polar webhook).
-- waitlist : everyone who signed in from the website. Joining now requires Google sign-in.
--
-- Users can READ their own profile and orders. Nobody can change plans or credits from the browser:
-- only the Polar webhook (server, secret key) writes orders, and a trigger recalculates the profile.

-- ---------------------------------------------------------------- tables
create table if not exists public.profiles (
  id          uuid primary key references auth.users(id) on delete cascade,
  email       text not null unique,
  full_name   text,
  avatar_url  text,
  plan        text not null default 'free' check (plan in ('free', 'starter', 'pro', 'premium')),
  clips_total integer not null default 3,      -- free trial: 3 clips
  clips_used  integer not null default 0,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create table if not exists public.orders (
  id                text primary key,           -- Polar order id
  email             text not null,
  plan              text not null check (plan in ('starter', 'pro', 'premium')),
  clips             integer not null check (clips > 0),
  amount            integer,                    -- in cents, after discount
  currency          text,
  status            text not null default 'paid' check (status in ('paid', 'refunded')),
  product_id        text,
  polar_customer_id text,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);
create index if not exists orders_email_idx on public.orders (lower(email));

-- waitlist already exists from the first setup; make sure it has the columns we use
create table if not exists public.waitlist (
  id bigint generated always as identity primary key,
  email text not null unique,
  source text default 'landing',
  created_at timestamptz not null default now()
);
alter table public.waitlist add column if not exists user_id uuid references auth.users(id) on delete set null;

-- ---------------------------------------------------------------- plan + credits from orders
create or replace function public.recalc_profile(p_email text)
returns void language plpgsql security definer set search_path = public as $$
declare
  v_clips integer;
  v_plan  text;
begin
  select coalesce(sum(clips), 0),
         coalesce((array_agg(plan order by case plan when 'premium' then 3 when 'pro' then 2 else 1 end desc))[1], 'free')
    into v_clips, v_plan
    from orders
   where lower(email) = lower(p_email) and status = 'paid';
  update profiles
     set plan = v_plan,
         clips_total = 3 + v_clips,
         updated_at = now()
   where lower(email) = lower(p_email);
end $$;

create or replace function public.on_order_change()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  perform recalc_profile(new.email);
  if tg_op = 'UPDATE' and lower(old.email) <> lower(new.email) then
    perform recalc_profile(old.email);
  end if;
  return new;
end $$;

drop trigger if exists orders_recalc on public.orders;
create trigger orders_recalc after insert or update on public.orders
  for each row execute function public.on_order_change();

-- ---------------------------------------------------------------- new Google user -> profile + waitlist
create or replace function public.on_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into profiles (id, email, full_name, avatar_url)
  values (new.id, lower(new.email),
          coalesce(new.raw_user_meta_data->>'full_name', new.raw_user_meta_data->>'name'),
          new.raw_user_meta_data->>'avatar_url')
  on conflict (id) do nothing;

  insert into waitlist (email, source, user_id)
  values (lower(new.email), 'google', new.id)
  on conflict (email) do update set user_id = excluded.user_id;

  perform recalc_profile(new.email);          -- paid before signing in? plan applies now
  return new;
end $$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.on_new_user();

-- ---------------------------------------------------------------- row level security
alter table public.profiles enable row level security;
alter table public.orders   enable row level security;
alter table public.waitlist enable row level security;

drop policy if exists "read own profile" on public.profiles;
create policy "read own profile" on public.profiles
  for select to authenticated using (auth.uid() = id);

drop policy if exists "read own orders" on public.orders;
create policy "read own orders" on public.orders
  for select to authenticated using (lower(email) = lower(auth.jwt() ->> 'email'));

-- Joining the waitlist now happens only through Google sign-in (the trigger above).
drop policy if exists "anyone can join the waitlist" on public.waitlist;
drop policy if exists "read own waitlist row" on public.waitlist;
create policy "read own waitlist row" on public.waitlist
  for select to authenticated using (user_id = auth.uid());

-- Clip usage is counted by the clipping app on the server (secret key), never from the browser.
revoke insert, update, delete on public.profiles from anon, authenticated;
revoke insert, update, delete on public.orders   from anon, authenticated;
revoke insert, update, delete on public.waitlist from anon, authenticated;
