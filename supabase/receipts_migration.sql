-- Receipt uploads feature: metadata table, private bucket, and RLS policies.
-- Run this complete script in the Supabase SQL editor. It is safe to rerun.

create table if not exists public.receipts (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    period text not null,
    bill text not null,
    storage_path text not null,
    file_name text,
    uploaded_at timestamptz not null default now(),
    unique (user_id, period, bill)
);

alter table public.receipts enable row level security;

insert into storage.buckets (id, name, public)
values ('receipts', 'receipts', false)
on conflict (id) do update set public = excluded.public;

drop policy if exists "Users manage their own receipts" on public.receipts;
create policy "Users manage their own receipts"
    on public.receipts
    for all
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

-- Storage policies restrict each user to objects under their own user_id/ prefix.
drop policy if exists "Users read own receipt objects" on storage.objects;
create policy "Users read own receipt objects"
    on storage.objects
    for select
    using (
        bucket_id = 'receipts'
        and auth.uid()::text = (storage.foldername(name))[1]
    );

drop policy if exists "Users upload own receipt objects" on storage.objects;
create policy "Users upload own receipt objects"
    on storage.objects
    for insert
    with check (
        bucket_id = 'receipts'
        and auth.uid()::text = (storage.foldername(name))[1]
    );

drop policy if exists "Users update own receipt objects" on storage.objects;
create policy "Users update own receipt objects"
    on storage.objects
    for update
    using (
        bucket_id = 'receipts'
        and auth.uid()::text = (storage.foldername(name))[1]
    );

drop policy if exists "Users delete own receipt objects" on storage.objects;
create policy "Users delete own receipt objects"
    on storage.objects
    for delete
    using (
        bucket_id = 'receipts'
        and auth.uid()::text = (storage.foldername(name))[1]
    );
