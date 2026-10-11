-- Operator-only verification after manifest-recovery.sql.
-- Requires approval for temporary fixture writes. Run the ENTIRE script.
-- No models, images, existing request IDs, or existing admissions are used.
-- All fixture writes roll back; no DELETE grants are needed.
begin;
set local lock_timeout = '5s';
set local statement_timeout = '20s';

do $$
declare
  browser_role text;
begin
  if not exists (
    select 1 from pg_trigger
    where tgrelid = 'public.ventures'::regclass
      and tgname = 'fence_retired_manifest'
      and tgfoid = 'public.fence_retired_manifest()'::regprocedure
      and tgenabled = 'O' and not tgisinternal
  ) then
    raise exception 'Retirement insert fence is missing or disabled.';
  end if;
  if not (select relrowsecurity from pg_class
          where oid = 'public.manifest_requests'::regclass) then
    raise exception 'Request row-level security is disabled.';
  end if;
  foreach browser_role in array array['anon', 'authenticated'] loop
    if has_table_privilege(browser_role, 'public.manifest_requests',
         'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
       or has_any_column_privilege(browser_role, 'public.manifest_requests',
         'SELECT,INSERT,UPDATE,REFERENCES')
       or has_function_privilege(browser_role,
         'public.retire_manifest_request(text,text)', 'EXECUTE')
       or has_function_privilege(browser_role,
         'public.fence_retired_manifest()', 'EXECUTE') then
      raise exception 'Browser role % has recovery access.', browser_role;
    end if;
  end loop;
end $$;

set local role service_role;

do $$
declare
  owner_id text := 'retirement-check-' || gen_random_uuid()::text;
  request_id_1 text := gen_random_uuid()::text;
  request_id_2 text := gen_random_uuid()::text;
  venture_id_1 uuid := gen_random_uuid();
  venture_id_2 uuid := gen_random_uuid();
  admission_id text := gen_random_uuid()::text;
  result jsonb;
  blocked boolean;
  row_data jsonb;
begin
  insert into public.manifest_requests
    (owner_token, request_id, fingerprint, venture_id, lease_until)
  values
    (owner_id, request_id_1, 'temporary-check', venture_id_1, clock_timestamp() + interval '5 minutes'),
    (owner_id, request_id_2, 'temporary-check', venture_id_2, clock_timestamp() + interval '5 minutes');

  if public.retire_manifest_request(owner_id || '-other', request_id_1) is not null
     or public.retire_manifest_request(owner_id, gen_random_uuid()::text) is not null then
    raise exception 'Creator or missing-request checks failed.';
  end if;
  result := public.retire_manifest_request(owner_id, request_id_1);
  if (result->>'retired')::boolean is distinct from false then
    raise exception 'Live request was retired.';
  end if;

  update public.manifest_requests set lease_until = clock_timestamp() - interval '1 minute'
    where owner_token = owner_id and request_id = request_id_1;
  result := public.retire_manifest_request(owner_id, request_id_1);
  if (result->>'retired')::boolean is distinct from true
     or (public.retire_manifest_request(owner_id, request_id_1)->>'retired')::boolean
        is distinct from true then
    raise exception 'Expired request retirement or repeat retirement failed.';
  end if;

  row_data := jsonb_build_object(
    'id', venture_id_1, 'slug', 'retirement-check-' || venture_id_1::text,
    'owner_token', owner_id, 'idea_raw', 'Temporary check',
    'idea_hash', 'temporary-check', 'provenance_code', 'temporary-check'
  );
  blocked := false;
  begin
    insert into public.ventures (id, slug, owner_token, idea_raw, idea_hash, provenance_code)
      values (venture_id_1, row_data->>'slug', owner_id,
              'Temporary check', 'temporary-check', 'temporary-check');
  exception when raise_exception then
    if sqlerrm <> 'Manifest request retired.' then raise; end if;
    blocked := true;
  end;
  if not blocked then raise exception 'Unlimited late save was not fenced.'; end if;

  insert into public.generation_admissions
    (token, caller, global_slot, admitted_at, expires_at, consumed)
    values (admission_id, owner_id, true, clock_timestamp(),
            clock_timestamp() + interval '5 minutes', false);
  blocked := false;
  begin
    perform public.create_reserved_venture(admission_id, row_data);
  exception when raise_exception then
    if sqlerrm <> 'Manifest request retired.' then raise; end if;
    blocked := true;
  end;
  if not blocked then raise exception 'Reserved late save was not fenced.'; end if;
  if exists (select 1 from public.ventures where id = venture_id_1)
     or (select consumed from public.generation_admissions where token = admission_id)
        is distinct from false then
    raise exception 'Retirement changed a saved result or admission.';
  end if;

  insert into public.ventures (id, slug, owner_token, idea_raw, idea_hash, provenance_code)
    values (venture_id_2, 'retirement-check-' || venture_id_2::text, owner_id,
            'Temporary check', 'temporary-check', 'temporary-check');
  update public.manifest_requests set lease_until = clock_timestamp() - interval '1 minute'
    where owner_token = owner_id and request_id = request_id_2;
  result := public.retire_manifest_request(owner_id, request_id_2);
  if (result->>'retired')::boolean is distinct from false
     or not exists (select 1 from public.ventures where id = venture_id_2) then
    raise exception 'Already-saved result did not win.';
  end if;
end $$;

rollback;
select 'PASS — browser roles blocked, retirement fenced, saved result preserved, test records rolled back' as result;
