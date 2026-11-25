-- ClipHeist: who may touch the tables through the Supabase API.
-- Paste into Supabase -> SQL Editor -> Run (after schema.sql and 002_early_access.sql). Safe to run more than once.
--
-- Newer Supabase projects no longer give the API roles access to new tables by themselves, so it is stated here.
-- service_role  = the server (clipping app, Polar webhook) with the secret key: reads and writes everything.
-- authenticated = a signed-in person in the browser: may only read, and row level security limits that to their own rows.
-- anon          = a visitor who is not signed in: nothing (the spots counter is a function, granted in 002).

grant usage on schema public to anon, authenticated, service_role;
grant select, insert, update, delete on public.profiles, public.orders, public.waitlist to service_role;
grant select on public.profiles, public.orders, public.waitlist to authenticated;
