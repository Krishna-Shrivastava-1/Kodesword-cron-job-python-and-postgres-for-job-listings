# import os
# import psycopg2
# from psycopg2.extras import execute_values
# from dotenv import load_dotenv

# # Load variables from .env file for local development
# load_dotenv()

# DATABASE_URL = os.getenv("DATABASE_URL")

# INIT_TABLES_QUERY = """
# CREATE TABLE IF NOT EXISTS metadata (
#     key VARCHAR(50) PRIMARY KEY,
#     value VARCHAR(50) NOT NULL,
#     updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
# );

# INSERT INTO metadata (key, value) 
# VALUES ('active_table', 'job_listings_a') 
# ON CONFLICT (key) DO NOTHING;

# CREATE TABLE IF NOT EXISTS job_listings_a (
#     id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
#     generated_unique_id VARCHAR(255) UNIQUE NOT NULL,
#     title VARCHAR(255) NOT NULL,
#     company_name VARCHAR(255) NOT NULL,
#     department VARCHAR(255),
#     employment_type VARCHAR(100),
#     role_category VARCHAR(100),
#     experience_level VARCHAR(100),
#     workplace_type VARCHAR(100),
#     city VARCHAR(100),
#     state VARCHAR(100),
#     country VARCHAR(100),
#     country_code VARCHAR(10),
#     continent VARCHAR(50),
#     location_display TEXT,
#     description_snippet TEXT,
#     apply_url TEXT,
#     ats_source VARCHAR(100),
#     external_job_id VARCHAR(255),
#     status VARCHAR(50) DEFAULT 'ACTIVE',
#     published_at TIMESTAMPTZ,
#     created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
#     updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
# );

# CREATE TABLE IF NOT EXISTS job_listings_b (
#     id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
#     generated_unique_id VARCHAR(255) UNIQUE NOT NULL,
#     title VARCHAR(255) NOT NULL,
#     company_name VARCHAR(255) NOT NULL,
#     department VARCHAR(255),
#     employment_type VARCHAR(100),
#     role_category VARCHAR(100),
#     experience_level VARCHAR(100),
#     workplace_type VARCHAR(100),
#     city VARCHAR(100),
#     state VARCHAR(100),
#     country VARCHAR(100),
#     country_code VARCHAR(10),
#     continent VARCHAR(50),
#     location_display TEXT,
#     description_snippet TEXT,
#     apply_url TEXT,
#     ats_source VARCHAR(100),
#     external_job_id VARCHAR(255),
#     status VARCHAR(50) DEFAULT 'ACTIVE',
#     published_at TIMESTAMPTZ,
#     created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
#     updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
# );
# """

# def get_db_connection():
#     if not DATABASE_URL:
#         raise ValueError("DATABASE_URL environment variable is not set!")
#     return psycopg2.connect(DATABASE_URL)

# def init_db():
#     """Ensures metadata, job_listings_a, and job_listings_b tables exist."""
#     conn = get_db_connection()
#     try:
#         with conn.cursor() as cursor:
#             cursor.execute(INIT_TABLES_QUERY)
#         conn.commit()
#         print("Database initialized successfully.")
#     except Exception as e:
#         conn.rollback()
#         print(f"Error initializing database: {e}")
#         raise e
#     finally:
#         conn.close()

# def get_active_table_name():
#     """Returns the currently active table name from metadata."""
#     conn = get_db_connection()
#     try:
#         with conn.cursor() as cursor:
#             cursor.execute("SELECT value FROM metadata WHERE key = 'active_table';")
#             result = cursor.fetchone()
#             return result[0] if result else "job_listings_a"
#     finally:
#         conn.close()

# def save_jobs_alternating(jobs_list):
#     """
#     1. Identifies inactive table (if A is active, targets B, and vice-versa).
#     2. Clears target table (`TRUNCATE`).
#     3. Bulk inserts new 9k+ listings into target table.
#     4. Flips active_table pointer in metadata.
#     """
#     if not jobs_list:
#         print("No jobs provided to save.")
#         return

#     init_db()

#     current_active = get_active_table_name()
#     # Determine target table to receive fresh scrape
#     target_table = "job_listings_b" if current_active == "job_listings_a" else "job_listings_a"

#     print(f"Current active table is '{current_active}'. Writing fresh batch to '{target_table}'...")

#     tuples = []
#     for job in jobs_list:
#         tuples.append((
#             job["id"],                      # Maps to generated_unique_id
#             job["title"],
#             job["company_name"],
#             job.get("department"),
#             job.get("employment_type"),
#             job.get("role_category"),
#             job.get("experience_level"),
#             job.get("workplace_type"),
#             job.get("city"),
#             job.get("state"),
#             job.get("country"),
#             job.get("country_code"),
#             job.get("continent"),
#             job.get("location_display"),
#             job.get("description_snippet"),
#             job.get("apply_url"),
#             job.get("ats_source"),
#             job.get("external_job_id"),
#             job.get("status", "ACTIVE"),
#             job.get("published_at"),
#             job.get("created_at"),
#             job.get("updated_at")
#         ))

#     conn = get_db_connection()
#     try:
#         with conn.cursor() as cursor:
#             # 1. Clear target table completely (TRUNCATE is fast and resets state)
#             cursor.execute(f"TRUNCATE TABLE {target_table};")

#             # 2. Bulk insert into target table
#             insert_query = f"""
#                 INSERT INTO {target_table} (
#                     generated_unique_id, title, company_name, department, employment_type,
#                     role_category, experience_level, workplace_type, city, state, country,
#                     country_code, continent, location_display, description_snippet, apply_url,
#                     ats_source, external_job_id, status, published_at, created_at, updated_at
#                 )
#                 VALUES %s;
#             """
#             execute_values(cursor, insert_query, tuples, page_size=1000)

#             # 3. Swap active pointer in metadata
#             update_meta_query = """
#                 UPDATE metadata 
#                 SET value = %s, updated_at = CURRENT_TIMESTAMP 
#                 WHERE key = 'active_table';
#             """
#             cursor.execute(update_meta_query, (target_table,))

#         conn.commit()
#         print(f"Successfully loaded {len(jobs_list)} records into '{target_table}'. Swapped active table pointer in metadata!")

#     except Exception as e:
#         conn.rollback()
#         print(f"Error during alternating swap operation: {e}")
#         raise e
#     finally:
#         conn.close()
































import os
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

INIT_TABLES_QUERY = """
CREATE TABLE IF NOT EXISTS metadata (
    key VARCHAR(50) PRIMARY KEY,
    value VARCHAR(50) NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO metadata (key, value) 
VALUES ('active_table', 'job_listings_a') 
ON CONFLICT (key) DO NOTHING;

-- Company Metadata Table (historical, immutable records)
CREATE TABLE IF NOT EXISTS companies (
    company_name VARCHAR(255) PRIMARY KEY,
    symbol VARCHAR(50),
    exchange VARCHAR(100),
    industry VARCHAR(150),
    sector VARCHAR(100),
    website TEXT,
    description TEXT,
    ceo VARCHAR(150),
    country VARCHAR(100),
    full_time_employees VARCHAR(50),
    phone VARCHAR(50),
    address TEXT,
    city VARCHAR(100),
    state VARCHAR(100),
    zip VARCHAR(50),
    logo_url TEXT,
    source VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS job_listings_a (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    generated_unique_id VARCHAR(255) UNIQUE NOT NULL,
    title VARCHAR(255) NOT NULL,
    company_name VARCHAR(255) NOT NULL,
    department VARCHAR(255),
    employment_type VARCHAR(100),
    role_category VARCHAR(100),
    experience_level VARCHAR(100),
    workplace_type VARCHAR(100),
    city VARCHAR(100),
    state VARCHAR(100),
    country VARCHAR(100),
    country_code VARCHAR(10),
    continent VARCHAR(50),
    location_display TEXT,
    description_snippet TEXT,
    apply_url TEXT,
    ats_source VARCHAR(100),
    external_job_id VARCHAR(255),
    status VARCHAR(50) DEFAULT 'ACTIVE',
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS job_listings_b (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    generated_unique_id VARCHAR(255) UNIQUE NOT NULL,
    title VARCHAR(255) NOT NULL,
    company_name VARCHAR(255) NOT NULL,
    department VARCHAR(255),
    employment_type VARCHAR(100),
    role_category VARCHAR(100),
    experience_level VARCHAR(100),
    workplace_type VARCHAR(100),
    city VARCHAR(100),
    state VARCHAR(100),
    country VARCHAR(100),
    country_code VARCHAR(10),
    continent VARCHAR(50),
    location_display TEXT,
    description_snippet TEXT,
    apply_url TEXT,
    ats_source VARCHAR(100),
    external_job_id VARCHAR(255),
    status VARCHAR(50) DEFAULT 'ACTIVE',
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
"""

def get_db_connection():
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL environment variable is not set!")
    return psycopg2.connect(DATABASE_URL)

def init_db():
    """Ensures metadata, job_listings_a, job_listings_b, and companies tables exist."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(INIT_TABLES_QUERY)
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def get_existing_company_names() -> set[str]:
    """Returns a lowercase set of company names already stored in DB."""
    init_db()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT LOWER(company_name) FROM companies;")
            return {row[0] for row in cur.fetchall() if row[0]}
    finally:
        conn.close()

def upsert_companies(companies_list):
    """
    Inserts company metadata ONLY if the company doesn't already exist.
    Preserves historical company records without overwriting or updating.
    """
    if not companies_list:
        return

    init_db()
    
    tuples = []
    for c in companies_list:
        tuples.append((
            c["company_name"],
            c.get("symbol"),
            c.get("exchange"),
            c.get("industry"),
            c.get("sector"),
            c.get("website"),
            c.get("description"),
            c.get("ceo"),
            c.get("country"),
            c.get("full_time_employees"),
            c.get("phone"),
            c.get("address"),
            c.get("city"),
            c.get("state"),
            c.get("zip"),
            c.get("logo_url"),
            c.get("source", "perplexity")
        ))

    insert_query = """
        INSERT INTO companies (
            company_name, symbol, exchange, industry, sector, website,
            description, ceo, country, full_time_employees, phone,
            address, city, state, zip, logo_url, source
        )
        VALUES %s
        ON CONFLICT (company_name) DO NOTHING;
    """

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            execute_values(cursor, insert_query, tuples, page_size=500)
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def get_active_table_name():
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT value FROM metadata WHERE key = 'active_table';")
            result = cursor.fetchone()
            return result[0] if result else "job_listings_a"
    finally:
        conn.close()

def save_jobs_alternating(jobs_list):
    if not jobs_list:
        return

    init_db()

    current_active = get_active_table_name()
    target_table = "job_listings_b" if current_active == "job_listings_a" else "job_listings_a"

    tuples = [
        (
            job["id"],
            job["title"],
            job["company_name"],
            job.get("department"),
            job.get("employment_type"),
            job.get("role_category"),
            job.get("experience_level"),
            job.get("workplace_type"),
            job.get("city"),
            job.get("state"),
            job.get("country"),
            job.get("country_code"),
            job.get("continent"),
            job.get("location_display"),
            job.get("description_snippet"),
            job.get("apply_url"),
            job.get("ats_source"),
            job.get("external_job_id"),
            job.get("status", "ACTIVE"),
            job.get("published_at"),
            job.get("created_at"),
            job.get("updated_at")
        )
        for job in jobs_list
    ]

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(f"TRUNCATE TABLE {target_table};")
            insert_query = f"""
                INSERT INTO {target_table} (
                    generated_unique_id, title, company_name, department, employment_type,
                    role_category, experience_level, workplace_type, city, state, country,
                    country_code, continent, location_display, description_snippet, apply_url,
                    ats_source, external_job_id, status, published_at, created_at, updated_at
                )
                VALUES %s;
            """
            execute_values(cursor, insert_query, tuples, page_size=1000)

            update_meta_query = """
                UPDATE metadata 
                SET value = %s, updated_at = CURRENT_TIMESTAMP 
                WHERE key = 'active_table';
            """
            cursor.execute(update_meta_query, (target_table,))

        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()