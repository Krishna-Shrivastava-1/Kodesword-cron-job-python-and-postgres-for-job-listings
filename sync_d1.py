# import os
# import re
# import json
# import time
# import urllib.request
# import psycopg2
# import psycopg2.extras

# DATABASE_URL = os.environ["DATABASE_URL"]
# CF_ACCOUNT_ID = os.environ["CLOUDFLARE_ACCOUNT_ID"]
# CF_D1_DATABASE_ID = os.environ["CLOUDFLARE_D1_DATABASE_ID"]
# CF_API_TOKEN = os.environ["CLOUDFLARE_API_TOKEN"]


# def norm(v):
#     return re.sub(r"\s+", " ", str(v or "").strip()).lower()


# def compute_is_remote(workplace_norm):
#     if (
#         workplace_norm == "remote"
#         or workplace_norm.startswith("fully remote")
#         or "remote only" in workplace_norm
#         or workplace_norm.startswith("work from home")
#         or workplace_norm == "wfh"
#     ):
#         return 1
#     if (
#         workplace_norm in ("on-site", "onsite", "in-office", "office", "hybrid")
#         or "hybrid" in workplace_norm
#         or "on-site" in workplace_norm
#         or "onsite" in workplace_norm
#     ):
#         return 2
#     return 0


# def sql_str(v):
#     if v is None:
#         return "NULL"
#     s = str(v).replace("\x00", "").replace("'", "''")
#     return f"'{s}'"


# def sql_int(v):
#     return str(int(v or 0))


# def to_ms(dt):
#     if dt is None:
#         return 0
#     return int(dt.timestamp() * 1000)


# def fetch_d1_existing_ids():
#     url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/d1/database/{CF_D1_DATABASE_ID}/query"
#     req = urllib.request.Request(
#         url,
#         data=json.dumps({"sql": "SELECT id FROM jobs;"}).encode("utf-8"),
#         headers={
#             "Authorization": f"Bearer {CF_API_TOKEN}",
#             "Content-Type": "application/json",
#         },
#         method="POST",
#     )
#     try:
#         with urllib.request.urlopen(req, timeout=30) as resp:
#             data = json.loads(resp.read().decode("utf-8"))
#             rows = data["result"][0].get("results", [])
#             return {r["id"] for r in rows if r.get("id")}
#     except Exception as e:
#         print(f"Warning: could not fetch existing D1 IDs ({e}), doing full replace.")
#         return None


# def main():
#     conn = psycopg2.connect(DATABASE_URL)
#     cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

#     # 1. Get active table name from Neon
#     cur.execute("SELECT value FROM metadata WHERE key = 'active_table' LIMIT 1;")
#     meta_row = cur.fetchone()
#     active_table = meta_row["value"] if meta_row else "job_listings_a"
#     if active_table not in ("job_listings_a", "job_listings_b"):
#         raise ValueError(f"Unexpected active_table: {active_table}")

#     print(f"Syncing from Neon table: {active_table}")

#     # 2. Load company metadata
#     cur.execute("""
#         SELECT company_name, logo_url, website, description, ceo,
#                full_time_employees, industry, city, state, country
#         FROM companies;
#     """)
#     companies_meta = {}
#     for c in cur.fetchall():
#         companies_meta[norm(c["company_name"])] = c

#     # 3. Load active jobs
#     cur.execute(f"""
#         SELECT id, generated_unique_id, title, company_name, department,
#                employment_type, role_category, experience_level, workplace_type,
#                city, state, country, country_code, continent, location_display,
#                description_snippet, apply_url, ats_source, published_at, created_at
#         FROM {active_table}
#         WHERE status IS NULL OR UPPER(status) = 'ACTIVE';
#     """)
#     neon_jobs = cur.fetchall()
#     cur.close()
#     conn.close()

#     print(f"Loaded {len(neon_jobs)} active jobs from Neon")

#     now_ms = int(time.time() * 1000)
#     neon_by_id = {}
#     company_agg = {}
#     all_countries = set()

#     for j in neon_jobs:
#         jid = str(j["id"])
#         gen_uid = j["generated_unique_id"] or jid
#         short_id = gen_uid.replace("-", "")[:10]
#         w_norm = norm(j["workplace_type"])
#         c_norm = norm(j["company_name"])
#         country_val = (j["country"] or "").strip()
#         pub_ms = to_ms(j["published_at"]) or to_ms(j["created_at"]) or now_ms
#         latest_ms = max(pub_ms, to_ms(j["created_at"]) or pub_ms)

#         if country_val:
#             all_countries.add(country_val)

#         neon_by_id[jid] = {
#             "id": jid,
#             "generated_unique_id": gen_uid,
#             "short_id": short_id,
#             "title": j["title"] or "",
#             "title_norm": norm(j["title"]),
#             "company_name": j["company_name"] or "",
#             "company_norm": c_norm,
#             "department": j["department"],
#             "employment_type": j["employment_type"],
#             "employment_norm": norm(j["employment_type"]),
#             "role_category": j["role_category"],
#             "role_norm": norm(j["role_category"]),
#             "experience_level": j["experience_level"],
#             "experience_norm": norm(j["experience_level"]),
#             "workplace_type": j["workplace_type"],
#             "workplace_norm": w_norm,
#             "is_remote": compute_is_remote(w_norm),
#             "city": j["city"],
#             "state": j["state"],
#             "country": country_val or None,
#             "country_norm": norm(country_val),
#             "country_code": j["country_code"],
#             "continent": j["continent"],
#             "location_display": j["location_display"],
#             "description_snippet": j["description_snippet"],
#             "apply_url": j["apply_url"],
#             "ats_source": j["ats_source"],
#             "published_at": j["published_at"].isoformat() if j["published_at"] else None,
#             "published_ms": pub_ms,
#             "latest_ms": latest_ms,
#         }

#         if c_norm not in company_agg:
#             company_agg[c_norm] = {
#                 "company_name": j["company_name"] or "",
#                 "total_openings": 0,
#                 "latest_opening": 0,
#                 "countries": set(),
#             }
#         agg = company_agg[c_norm]
#         agg["total_openings"] += 1
#         if latest_ms > agg["latest_opening"]:
#             agg["latest_opening"] = latest_ms
#         if country_val:
#             agg["countries"].add(country_val)

#     # 4. Compute diff against D1
#     d1_ids = fetch_d1_existing_ids()
#     lines = [
#         "DROP TRIGGER IF EXISTS trg_jobs_auto_cols;",
#     ]

#     if d1_ids is None:
#         lines.append("DELETE FROM jobs;")
#         to_insert = list(neon_by_id.values())
#     else:
#         to_delete = list(d1_ids - set(neon_by_id.keys()))
#         to_insert = [row for jid, row in neon_by_id.items() if jid not in d1_ids]
#         print(f"Diff: {len(to_delete)} to delete, {len(to_insert)} to insert")
#         for i in range(0, len(to_delete), 100):
#             chunk = ",".join(sql_str(x) for x in to_delete[i : i + 100])
#             lines.append(f"DELETE FROM jobs WHERE id IN ({chunk});")

#     # Insert new jobs in chunks of 50 rows per statement
#     cols = (
#         "id, generated_unique_id, short_id, title, title_norm, company_name, company_norm, "
#         "department, employment_type, employment_norm, role_category, role_norm, "
#         "experience_level, experience_norm, workplace_type, workplace_norm, is_remote, "
#         "city, state, country, country_norm, country_code, continent, location_display, "
#         "description_snippet, apply_url, ats_source, published_at, published_ms, latest_ms"
#     )
#     for i in range(0, len(to_insert), 50):
#         batch = to_insert[i : i + 50]
#         values_sql = []
#         for r in batch:
#             values_sql.append(
#                 "("
#                 + ", ".join([
#                     sql_str(r["id"]),
#                     sql_str(r["generated_unique_id"]),
#                     sql_str(r["short_id"]),
#                     sql_str(r["title"]),
#                     sql_str(r["title_norm"]),
#                     sql_str(r["company_name"]),
#                     sql_str(r["company_norm"]),
#                     sql_str(r["department"]),
#                     sql_str(r["employment_type"]),
#                     sql_str(r["employment_norm"]),
#                     sql_str(r["role_category"]),
#                     sql_str(r["role_norm"]),
#                     sql_str(r["experience_level"]),
#                     sql_str(r["experience_norm"]),
#                     sql_str(r["workplace_type"]),
#                     sql_str(r["workplace_norm"]),
#                     sql_int(r["is_remote"]),
#                     sql_str(r["city"]),
#                     sql_str(r["state"]),
#                     sql_str(r["country"]),
#                     sql_str(r["country_norm"]),
#                     sql_str(r["country_code"]),
#                     sql_str(r["continent"]),
#                     sql_str(r["location_display"]),
#                     sql_str(r["description_snippet"]),
#                     sql_str(r["apply_url"]),
#                     sql_str(r["ats_source"]),
#                     sql_str(r["published_at"]),
#                     sql_int(r["published_ms"]),
#                     sql_int(r["latest_ms"]),
#                 ])
#                 + ")"
#             )
#         lines.append(f"INSERT OR REPLACE INTO jobs ({cols}) VALUES " + ",\n".join(values_sql) + ";")

#     # 5. Refresh companies_summary (small ~650 rows)
#     lines.append("DELETE FROM companies_summary;")
#     cs_cols = (
#         "company_norm, company_match, company_name, logo_url, website, description, "
#         "ceo, full_time_employees, industry, city, state, country, countries_json, "
#         "total_openings, latest_opening"
#     )
#     cs_items = list(company_agg.items())
#     for i in range(0, len(cs_items), 50):
#         batch = cs_items[i : i + 50]
#         values_sql = []
#         for c_norm, agg in batch:
#             meta = companies_meta.get(c_norm, {})
#             c_match = re.sub(r"[-_]+", " ", c_norm)
#             countries_json = json.dumps(sorted(agg["countries"]))
#             values_sql.append(
#                 "("
#                 + ", ".join([
#                     sql_str(c_norm),
#                     sql_str(c_match),
#                     sql_str(agg["company_name"]),
#                     sql_str(meta.get("logo_url")),
#                     sql_str(meta.get("website")),
#                     sql_str(meta.get("description")),
#                     sql_str(meta.get("ceo")),
#                     sql_str(meta.get("full_time_employees")),
#                     sql_str(meta.get("industry")),
#                     sql_str(meta.get("city")),
#                     sql_str(meta.get("state")),
#                     sql_str(meta.get("country")),
#                     sql_str(countries_json),
#                     sql_int(agg["total_openings"]),
#                     sql_int(agg["latest_opening"]),
#                 ])
#                 + ")"
#             )
#         lines.append(
#             f"INSERT INTO companies_summary ({cs_cols}) VALUES " + ",\n".join(values_sql) + ";"
#         )

#     # 6. Refresh meta_stats
#     all_countries_json = json.dumps(sorted(all_countries))
#     lines.append(
#         f"INSERT OR REPLACE INTO meta_stats (id, total_jobs, total_companies, countries_json, updated_at) "
#         f"VALUES (1, {len(neon_by_id)}, {len(company_agg)}, {sql_str(all_countries_json)}, {now_ms});"
#     )

#     with open("sync.sql", "w", encoding="utf-8") as f:
#         f.write("\n".join(lines))

#     print("Generated sync.sql successfully!")


# if __name__ == "__main__":
#     main()





import os
import re
import json
import time
import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ["DATABASE_URL"]


def norm(v):
    return re.sub(r"\s+", " ", str(v or "").strip()).lower()


def compute_is_remote(workplace_norm):
    if (
        workplace_norm == "remote"
        or workplace_norm.startswith("fully remote")
        or "remote only" in workplace_norm
        or workplace_norm.startswith("work from home")
        or workplace_norm == "wfh"
    ):
        return 1
    if (
        workplace_norm in ("on-site", "onsite", "in-office", "office", "hybrid")
        or "hybrid" in workplace_norm
        or "on-site" in workplace_norm
        or "onsite" in workplace_norm
    ):
        return 2
    return 0


def sql_str(v, max_len=None):
    if v is None:
        return "NULL"
    s = str(v).replace("\x00", "")
    if max_len and len(s) > max_len:
        s = s[:max_len]
    s = s.replace("'", "''")
    return f"'{s}'"


def sql_int(v):
    return str(int(v or 0))


def to_ms(dt):
    if dt is None:
        return 0
    return int(dt.timestamp() * 1000)


def append_batched_inserts(lines, table_name, cols_sql, value_strings, max_bytes=50000, max_rows=20):
    prefix = f"INSERT INTO {table_name} ({cols_sql}) VALUES "
    batch = []
    batch_bytes = len(prefix)

    for v in value_strings:
        v_bytes = len(v.encode("utf-8")) + 2
        if batch and (len(batch) >= max_rows or batch_bytes + v_bytes > max_bytes):
            lines.append(prefix + ",\n".join(batch) + ";")
            batch = []
            batch_bytes = len(prefix)
        batch.append(v)
        batch_bytes += v_bytes

    if batch:
        lines.append(prefix + ",\n".join(batch) + ";")


def main():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # 1. Read active Neon table
    cur.execute("SELECT value FROM metadata WHERE key = 'active_table' LIMIT 1;")
    meta_row = cur.fetchone()
    active_table = meta_row["value"] if meta_row else "job_listings_a"
    if active_table not in ("job_listings_a", "job_listings_b"):
        raise ValueError(f"Unexpected active_table: {active_table}")

    print(f"Syncing from Neon table: {active_table}")

    # 2. Load companies metadata
    cur.execute("""
        SELECT company_name, logo_url, website, description, ceo,
               full_time_employees, industry, city, state, country
        FROM companies;
    """)
    companies_meta = {norm(c["company_name"]): c for c in cur.fetchall()}

    # 3. Load active jobs
    cur.execute(f"""
        SELECT id, generated_unique_id, title, company_name, department,
               employment_type, role_category, experience_level, workplace_type,
               city, state, country, country_code, continent, location_display,
               description_snippet, apply_url, ats_source, published_at, created_at
        FROM {active_table}
        WHERE status IS NULL OR UPPER(status) = 'ACTIVE';
    """)
    neon_jobs = cur.fetchall()
    cur.close()
    conn.close()

    if len(neon_jobs) == 0:
        raise RuntimeError("Neon returned 0 active jobs — aborting D1 sync to protect live data!")

    print(f"Loaded {len(neon_jobs)} active jobs from Neon")

    now_ms = int(time.time() * 1000)
    job_values = []
    company_agg = {}
    all_countries = set()

    for j in neon_jobs:
        jid = str(j["id"])
        gen_uid = j["generated_unique_id"] or jid
        short_id = gen_uid.replace("-", "")[:10]
        w_norm = norm(j["workplace_type"])
        c_norm = norm(j["company_name"])
        country_val = (j["country"] or "").strip()
        pub_ms = to_ms(j["published_at"]) or to_ms(j["created_at"]) or now_ms
        latest_ms = max(pub_ms, to_ms(j["created_at"]) or pub_ms)

        if country_val:
            all_countries.add(country_val)

        job_values.append(
            "("
            + ", ".join([
                sql_str(jid),
                sql_str(gen_uid),
                sql_str(short_id),
                sql_str(j["title"] or ""),
                sql_str(norm(j["title"])),
                sql_str(j["company_name"] or ""),
                sql_str(c_norm),
                sql_str(j["department"]),
                sql_str(j["employment_type"]),
                sql_str(norm(j["employment_type"])),
                sql_str(j["role_category"]),
                sql_str(norm(j["role_category"])),
                sql_str(j["experience_level"]),
                sql_str(norm(j["experience_level"])),
                sql_str(j["workplace_type"]),
                sql_str(w_norm),
                sql_int(compute_is_remote(w_norm)),
                sql_str(j["city"]),
                sql_str(j["state"]),
                sql_str(country_val or None),
                sql_str(norm(country_val)),
                sql_str(j["country_code"]),
                sql_str(j["continent"]),
                sql_str(j["location_display"]),
                sql_str(j["description_snippet"], max_len=20000),
                sql_str(j["apply_url"]),
                sql_str(j["ats_source"]),
                sql_str(j["published_at"].isoformat() if j["published_at"] else None),
                sql_int(pub_ms),
                sql_int(latest_ms),
            ])
            + ")"
        )

        if c_norm not in company_agg:
            company_agg[c_norm] = {
                "company_name": j["company_name"] or "",
                "total_openings": 0,
                "latest_opening": 0,
                "countries": set(),
            }
        agg = company_agg[c_norm]
        agg["total_openings"] += 1
        if latest_ms > agg["latest_opening"]:
            agg["latest_opening"] = latest_ms
        if country_val:
            agg["countries"].add(country_val)

    # 4. Build SQL: First remove old data, then insert new data (max 50 KB per statement)
    lines = [
        "DROP TRIGGER IF EXISTS trg_jobs_auto_cols;",
        "DELETE FROM jobs;",
        "DELETE FROM companies_summary;",
    ]

    job_cols = (
        "id, generated_unique_id, short_id, title, title_norm, company_name, company_norm, "
        "department, employment_type, employment_norm, role_category, role_norm, "
        "experience_level, experience_norm, workplace_type, workplace_norm, is_remote, "
        "city, state, country, country_norm, country_code, continent, location_display, "
        "description_snippet, apply_url, ats_source, published_at, published_ms, latest_ms"
    )
    append_batched_inserts(lines, "jobs", job_cols, job_values, max_bytes=50000, max_rows=20)

    cs_cols = (
        "company_norm, company_match, company_name, logo_url, website, description, "
        "ceo, full_time_employees, industry, city, state, country, countries_json, "
        "total_openings, latest_opening"
    )
    cs_values = []
    for c_norm, agg in company_agg.items():
        meta = companies_meta.get(c_norm, {})
        c_match = re.sub(r"[-_]+", " ", c_norm)
        countries_json = json.dumps(sorted(agg["countries"]))
        cs_values.append(
            "("
            + ", ".join([
                sql_str(c_norm),
                sql_str(c_match),
                sql_str(agg["company_name"]),
                sql_str(meta.get("logo_url")),
                sql_str(meta.get("website")),
                sql_str(meta.get("description"), max_len=20000),
                sql_str(meta.get("ceo")),
                sql_str(meta.get("full_time_employees")),
                sql_str(meta.get("industry")),
                sql_str(meta.get("city")),
                sql_str(meta.get("state")),
                sql_str(meta.get("country")),
                sql_str(countries_json),
                sql_int(agg["total_openings"]),
                sql_int(agg["latest_opening"]),
            ])
            + ")"
        )
    append_batched_inserts(lines, "companies_summary", cs_cols, cs_values, max_bytes=50000, max_rows=20)

    all_countries_json = json.dumps(sorted(all_countries))
    lines.append(
        f"INSERT OR REPLACE INTO meta_stats (id, total_jobs, total_companies, countries_json, updated_at) "
        f"VALUES (1, {len(job_values)}, {len(company_agg)}, {sql_str(all_countries_json)}, {now_ms});"
    )

    with open("sync.sql", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"Generated sync.sql successfully ({len(lines)} SQL statements)!")


if __name__ == "__main__":
    main()