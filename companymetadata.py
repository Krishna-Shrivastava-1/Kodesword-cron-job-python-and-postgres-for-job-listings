
# import asyncio
# import difflib
# import logging
# import os
# import re
# from urllib.parse import quote, urlparse
# from curl_cffi.requests import AsyncSession
# from database import get_existing_company_names, upsert_companies

# logger = logging.getLogger(__name__)

# # Your API Token for full profile data
# THECOMPANIESAPI_TOKEN = os.getenv("THECOMPANIESAPI_TOKEN", "nPXevluBvHSw7tSi5bBtTurwfMT4NQ9C")

# STEALTH_HEADERS = {
#     "Accept": "application/json, text/plain, */*",
#     "Accept-Language": "en-US,en;q=0.9",
#     "Sec-Ch-Ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
#     "Sec-Ch-Ua-Mobile": "?0",
#     "Sec-Ch-Ua-Platform": '"Windows"',
#     "Sec-Fetch-Dest": "empty",
#     "Sec-Fetch-Mode": "cors",
#     "Sec-Fetch-Site": "same-origin",
# }


# def to_clean_str(val) -> str | None:
#     """Safely flattens nested dicts, lists, and primitives into a clean scalar string."""
#     if val is None:
#         return None
#     if isinstance(val, str):
#         cleaned = val.strip()
#         return cleaned if cleaned else None
#     if isinstance(val, (int, float)):
#         return str(val)
#     if isinstance(val, dict):
#         for key in ("name", "value", "title", "text", "url", "formatted", "street", "label"):
#             if key in val and val[key]:
#                 return to_clean_str(val[key])
#         sub_vals = [to_clean_str(v) for v in val.values() if v and not isinstance(v, (dict, list))]
#         sub_vals = [s for s in sub_vals if s]
#         return ", ".join(sub_vals) if sub_vals else None
#     if isinstance(val, list):
#         items = [to_clean_str(x) for x in val if x is not None]
#         items = [x for x in items if x]
#         return ", ".join(items) if items else None
#     return str(val).strip() or None


# def clean_company_name(name: str) -> str:
#     """Strips legal designations (Inc, LLC, Corp) so searches find canonical entities."""
#     if not name:
#         return ""
#     cleaned = re.sub(r"\(.*?\)", "", name)
#     pattern = r"(?i)\b(inc|incorporated|corp|corporation|llc|ltd|limited|technologies|technology|tech|holdings|group|plc|co|lp|gmbh)\b[.,]?"
#     cleaned = re.sub(pattern, "", cleaned)
#     cleaned = re.sub(r"[,\.\-\–\|\/]+$", "", cleaned).strip()
#     return cleaned if cleaned else name.strip()


# def extract_root_domain(url: str) -> str:
#     """Normalizes any URL or domain to its root (e.g. 'https://www.openai.com/about' -> 'openai.com')."""
#     if not url:
#         return ""
#     try:
#         url = url.strip().lower()
#         if not url.startswith("http://") and not url.startswith("https://"):
#             url = f"https://{url}"
#         netloc = urlparse(url).netloc
#         netloc = re.sub(r"^www\.", "", netloc)
#         parts = netloc.split(".")
#         if len(parts) >= 2:
#             return ".".join(parts[-2:])
#         return netloc
#     except Exception:
#         return ""


# async def resolve_canonical_domain(session: AsyncSession, company_name: str, fallback_website: str = None) -> tuple[str | None, str | None]:
#     """Resolves company domain and logo via Clearbit autocomplete API."""
#     if fallback_website:
#         domain = extract_root_domain(fallback_website)
#         if domain:
#             return domain, f"https://logo.clearbit.com/{domain}"

#     clean_name = clean_company_name(company_name)
#     url = f"https://autocomplete.clearbit.com/v1/companies/suggest?query={quote(clean_name)}"
    
#     try:
#         resp = await session.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=6.0)
#         if resp.status_code == 200:
#             suggestions = resp.json()
#             if suggestions and isinstance(suggestions, list):
#                 top = suggestions[0]
#                 domain = top.get("domain")
#                 logo = top.get("logo")
#                 if domain:
#                     return extract_root_domain(domain), logo
#     except Exception as e:
#         logger.debug(f"Domain lookup failed for '{company_name}': {e}")

#     return None, None


# async def resolve_ticker_strict(session: AsyncSession, company_name: str) -> dict | None:
#     """Strictly checks if a company is publicly traded on Yahoo Finance."""
#     clean_name = clean_company_name(company_name).lower()
#     url = "https://query2.finance.yahoo.com/v1/finance/search"
#     params = {
#         "q": clean_name,
#         "quotesCount": 6,
#         "newsCount": 0,
#         "listsCount": 0,
#         "enableFuzzyQuery": "false",
#     }

#     try:
#         resp = await session.get(url, params=params, headers=STEALTH_HEADERS, timeout=8.0)
#         if resp.status_code != 200:
#             return None

#         quotes = resp.json().get("quotes", [])
#         for q in quotes:
#             if q.get("quoteType", "").upper() not in ("EQUITY", "ETF"):
#                 continue

#             raw_name = (q.get("longname") or q.get("shortname") or "").lower()
#             q_clean = clean_company_name(raw_name).lower()

#             c_token = clean_name.split()[0]
#             q_tokens = re.findall(r"\b[a-z0-9]+\b", q_clean)
#             if not q_tokens or c_token != q_tokens[0]:
#                 continue

#             similarity = difflib.SequenceMatcher(None, clean_name, q_clean).ratio()
#             if similarity < 0.70 and clean_name not in q_clean:
#                 continue

#             raw_symbol = q.get("symbol")
#             if raw_symbol:
#                 return {
#                     "symbol": raw_symbol.split(".")[0],
#                     "exchange": q.get("exchDisp") or q.get("exchange"),
#                     "industry": q.get("industry"),
#                     "sector": q.get("sector"),
#                 }
#     except Exception as e:
#         logger.debug(f"Yahoo ticker search error for '{company_name}': {e}")

#     return None


# async def fetch_perplexity_public(session: AsyncSession, symbol: str, company_name: str) -> dict | None:
#     """Fetches profile metadata from Perplexity API for verified public tickers."""
#     url = f"https://www.perplexity.ai/rest/finance/profile/{symbol.upper()}"
#     try:
#         resp = await session.get(url, headers=STEALTH_HEADERS, timeout=9.0)
#         if resp.status_code == 200:
#             data = resp.json()
#             if not data or not data.get("companyName"):
#                 return None

#             return {
#                 "company_name": company_name,
#                 "symbol": symbol.upper(),
#                 "exchange": data.get("exchange") or data.get("exchangeShortName"),
#                 "industry": data.get("industry"),
#                 "sector": data.get("sector"),
#                 "website": data.get("website"),
#                 "description": data.get("description"),
#                 "ceo": data.get("ceo"),
#                 "country": data.get("country"),
#                 "full_time_employees": str(data.get("fullTimeEmployees", "")) if data.get("fullTimeEmployees") else None,
#                 "phone": data.get("phone"),
#                 "address": data.get("address"),
#                 "city": data.get("city"),
#                 "state": data.get("state"),
#                 "zip": data.get("zip"),
#                 "logo_url": data.get("image") or data.get("imageDark"),
#                 "source": "perplexity",
#             }
#     except Exception as e:
#         logger.debug(f"Perplexity fetch failed for {symbol}: {e}")
#     return None


# async def fetch_thecompaniesapi(session: AsyncSession, company_name: str, domain: str | None, logo_url: str | None) -> dict | None:
#     """
#     Fetches FULL company profile from TheCompaniesAPI using your paid API token.
#     Retrieves complete description, CEO, employees, phone, address, and industries.
#     """
#     headers = {
#         "Authorization": f"Bearer {THECOMPANIESAPI_TOKEN}",
#         "Accept": "application/json",
#     }

#     # Query by domain first, then fallback to clean name query
#     candidates = []
#     if domain:
#         candidates.append(domain)
    
#     clean_name = clean_company_name(company_name)
#     slug = re.sub(r"[^a-zA-Z0-9]+", "", clean_name).lower()
#     if slug and slug != domain:
#         candidates.append(slug)

#     data = None
#     for target in candidates:
#         # Full profile endpoint (WITHOUT ?simplified=true to get 100% of data)
#         url = f"https://api.thecompaniesapi.com/v1/companies/{quote(target)}"
#         try:
#             resp = await session.get(url, headers=headers, timeout=10.0)
#             if resp.status_code == 200:
#                 payload = resp.json()
#                 data = payload[0] if isinstance(payload, list) and payload else payload
#                 if isinstance(data, dict) and "companies" in data:
#                     c_list = data.get("companies", [])
#                     data = c_list[0] if c_list else {}

#                 if data and isinstance(data, dict):
#                     break
#             elif resp.status_code in (401, 403):
#                 logger.error(f"TheCompaniesAPI Authentication Failed (Status {resp.status_code}). Check your API token.")
#                 return None
#             elif resp.status_code in (402, 429):
#                 logger.warning(f"TheCompaniesAPI rate/credit limit reached (Status {resp.status_code}).")
#                 return None
#             else:
#                 logger.debug(f"TheCompaniesAPI: '{target}' returned status {resp.status_code}")
#         except Exception as e:
#             logger.debug(f"TheCompaniesAPI request error for '{target}': {e}")

#     if not data or not isinstance(data, dict):
#         return None

#     # Comprehensive description extraction
#     description = (
#         data.get("description")
#         or data.get("about")
#         or data.get("overview")
#         or data.get("summary")
#         or data.get("shortDescription")
#         or data.get("longDescription")
#     )

#     # Industry / Sector extraction
#     industry = (
#         data.get("industry")
#         or data.get("industries")
#         or data.get("categories")
#         or data.get("category")
#     )

#     # Location extraction
#     location = data.get("location") or data.get("headquarters") or {}
#     if not isinstance(location, dict):
#         location = {}

#     # CEO / Leadership extraction
#     ceo = (
#         data.get("ceo")
#         or data.get("founder")
#         or data.get("founderName")
#         or data.get("leadership")
#     )

#     # Headcount extraction
#     employees = (
#         data.get("employees")
#         or data.get("employeeRange")
#         or data.get("headcount")
#         or data.get("companySize")
#     )

#     company_domain = to_clean_str(data.get("domain")) or domain
#     raw_web = data.get("website") or data.get("url")
#     resolved_website = to_clean_str(raw_web) or (f"https://{company_domain}" if company_domain else None)
#     resolved_logo = to_clean_str(data.get("logo") or data.get("icon") or data.get("logoUrl")) or logo_url

#     return {
#         "company_name": company_name,
#         "symbol": None,
#         "exchange": None,
#         "industry": to_clean_str(industry),
#         "sector": to_clean_str(data.get("sector")),
#         "website": resolved_website,
#         "description": to_clean_str(description),
#         "ceo": to_clean_str(ceo),
#         "country": to_clean_str(data.get("country")) or to_clean_str(location.get("country")),
#         "full_time_employees": to_clean_str(employees),
#         "phone": to_clean_str(data.get("phone")),
#         "address": to_clean_str(location.get("street")) or to_clean_str(data.get("address")),
#         "city": to_clean_str(location.get("city")) or to_clean_str(data.get("city")),
#         "state": to_clean_str(location.get("state")) or to_clean_str(data.get("state")),
#         "zip": to_clean_str(location.get("postalCode")) or to_clean_str(data.get("zip")),
#         "logo_url": resolved_logo,
#         "source": "thecompaniesapi",
#     }


# def sanitize_company_record(record: dict) -> dict:
#     """Clamps fields to PostgreSQL column sizes and guarantees no dicts remain."""
#     limits = {
#         "company_name": 255,
#         "symbol": 50,
#         "exchange": 100,
#         "industry": 150,
#         "sector": 100,
#         "ceo": 150,
#         "country": 100,
#         "full_time_employees": 50,
#         "phone": 50,
#         "city": 100,
#         "state": 100,
#         "zip": 50,
#         "source": 50,
#     }

#     # Flatten all fields to clean strings
#     for k in list(record.keys()):
#         record[k] = to_clean_str(record.get(k))

#     raw_emp = record.get("full_time_employees")
#     if raw_emp:
#         num_match = re.search(r"[\d,]+(?:\s*\+)?(?:\s*(?:k|m|million|billion))?", raw_emp, re.IGNORECASE)
#         if num_match and len(raw_emp) > 50:
#             record["full_time_employees"] = num_match.group(0).strip()
#         else:
#             record["full_time_employees"] = raw_emp[:50]

#     for col, limit in limits.items():
#         val = record.get(col)
#         if val is not None:
#             record[col] = val[:limit]

#     return record


# def is_record_meaningful(record: dict) -> bool:
#     """Requires at least meaningful metadata (description, industry, or ticker) to save."""
#     return bool(
#         record.get("symbol")
#         or record.get("description")
#         or record.get("industry")
#         or record.get("ceo")
#     )


# async def process_and_save_company_metadata(unique_companies: list[dict], concurrency: int = 3):
#     """
#     Pure API Pipeline:
#     1. Skips companies already in Postgres.
#     2. Resolves company domain.
#     3. Checks Yahoo Finance for public ticker -> Perplexity profile.
#     4. If private -> Calls TheCompaniesAPI FULL profile.
#     5. Saves only validated, rich records to Postgres.
#     """
#     if not unique_companies:
#         return

#     # Deduplicate companies list
#     deduped = {}
#     for item in unique_companies:
#         name = item.get("company_name")
#         if name and name.strip():
#             key = name.strip().lower()
#             if key not in deduped:
#                 deduped[key] = item

#     # PostgreSQL check: Skip companies that already exist
#     try:
#         existing_in_db = get_existing_company_names()
#     except Exception as e:
#         logger.warning(f"Could not check existing companies in DB ({e}).")
#         existing_in_db = set()

#     to_process = [item for key, item in deduped.items() if key not in existing_in_db]

#     if not to_process:
#         logger.info("All companies already exist in DB. Skipping calls.")
#         return

#     logger.info(f"Processing metadata for {len(to_process)} new companies...")

#     sem = asyncio.Semaphore(concurrency)
#     results = []

#     async with AsyncSession(impersonate="chrome120") as session:

#         async def handle_company(item):
#             c_name = item.get("company_name", "").strip()
#             website = item.get("website")

#             async with sem:
#                 await asyncio.sleep(0.3)

#                 # STAGE 1: Discover Ground-Truth Domain and Logo
#                 canonical_domain, logo_url = await resolve_canonical_domain(session, c_name, fallback_website=website)

#                 # STAGE 2: Public vs Private validation (Yahoo Finance)
#                 resolved = await resolve_ticker_strict(session, c_name)
#                 data = None

#                 # STAGE 3: If Public, try Perplexity
#                 if resolved and resolved.get("symbol"):
#                     data = await fetch_perplexity_public(session, resolved["symbol"], c_name)
#                     if data:
#                         if not data.get("industry") and resolved.get("industry"):
#                             data["industry"] = resolved["industry"]
#                         if not data.get("sector") and resolved.get("sector"):
#                             data["sector"] = resolved["sector"]

#                 # STAGE 4: If Private or Perplexity failed, use FULL TheCompaniesAPI
#                 if not data:
#                     data = await fetch_thecompaniesapi(session, c_name, canonical_domain, logo_url)

#                 # STAGE 5: Save only records with actual profile data
#                 if data and is_record_meaningful(data):
#                     clean_data = sanitize_company_record(data)
#                     results.append(clean_data)
#                 else:
#                     logger.debug(f"Skipping '{c_name}': No rich profile data available.")

#         await asyncio.gather(*(handle_company(item) for item in to_process))

#     if results:
#         upsert_companies(results)
#         logger.info(f"Successfully saved {len(results)} new company records.")
#     else:
#         logger.info("No new company metadata found to insert.")




















import asyncio
import difflib
import logging
import os
import re
from urllib.parse import quote, urlparse
from curl_cffi.requests import AsyncSession
from database import get_existing_company_names, upsert_companies

logger = logging.getLogger(__name__)

THECOMPANIESAPI_TOKEN = os.getenv("THECOMPANIESAPI_TOKEN", "nPXevluBvHSw7tSi5bBtTurwfMT4NQ9C")

STEALTH_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Ch-Ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

# Major private tech giants that frequently collide with random microcap tickers/funds
KNOWN_PRIVATE_COMPANIES = {
    "openai", "stripe", "spacex", "anthropic", "databricks", "bytedance", 
    "epic games", "canva", "revolut", "discord", "valve", "figma", "klarna"
}


def to_clean_str(val) -> str | None:
    """Safely flattens nested dicts, lists, and primitives into a clean scalar string."""
    if val is None:
        return None
    if isinstance(val, str):
        cleaned = val.strip()
        return cleaned if cleaned else None
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, dict):
        for key in ("name", "value", "title", "text", "url", "formatted", "street", "label"):
            if key in val and val[key]:
                return to_clean_str(val[key])
        sub_vals = [to_clean_str(v) for v in val.values() if v and not isinstance(v, (dict, list))]
        sub_vals = [s for s in sub_vals if s]
        return ", ".join(sub_vals) if sub_vals else None
    if isinstance(val, list):
        items = [to_clean_str(x) for x in val if x is not None]
        items = [x for x in items if x]
        return ", ".join(items) if items else None
    return str(val).strip() or None


def clean_company_name(name: str) -> str:
    """Strips legal designations (Inc, LLC, Corp) so searches find canonical entities."""
    if not name:
        return ""
    cleaned = re.sub(r"\(.*?\)", "", name)
    pattern = r"(?i)\b(inc|incorporated|corp|corporation|llc|ltd|limited|technologies|technology|tech|holdings|group|plc|co|lp|gmbh|asset management|capital)\b[.,]?"
    cleaned = re.sub(pattern, "", cleaned)
    cleaned = re.sub(r"[,\.\-\–\|\/]+$", "", cleaned).strip()
    return cleaned if cleaned else name.strip()


def normalize_slug(name: str) -> str:
    """Normalizes string to alphanumeric only for robust comparison (e.g., 'Open AI' -> 'openai')."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def extract_root_domain(url: str) -> str:
    """Normalizes any URL or domain to its root (e.g. 'https://www.openai.com/about' -> 'openai.com')."""
    if not url:
        return ""
    try:
        url = url.strip().lower()
        if not url.startswith("http://") and not url.startswith("https://"):
            url = f"https://{url}"
        netloc = urlparse(url).netloc
        netloc = re.sub(r"^www\.", "", netloc)
        parts = netloc.split(".")
        if len(parts) >= 2:
            return ".".join(parts[-2:])
        return netloc
    except Exception:
        return ""


async def resolve_canonical_domain(session: AsyncSession, company_name: str, fallback_website: str = None) -> tuple[str | None, str | None]:
    """Resolves company domain and logo via Clearbit autocomplete API."""
    if fallback_website:
        domain = extract_root_domain(fallback_website)
        if domain:
            return domain, f"https://logo.clearbit.com/{domain}"

    clean_name = clean_company_name(company_name)
    url = f"https://autocomplete.clearbit.com/v1/companies/suggest?query={quote(clean_name)}"
    
    try:
        resp = await session.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=6.0)
        if resp.status_code == 200:
            suggestions = resp.json()
            if suggestions and isinstance(suggestions, list):
                top = suggestions[0]
                domain = top.get("domain")
                logo = top.get("logo")
                if domain:
                    return extract_root_domain(domain), logo
    except Exception as e:
        logger.debug(f"Domain lookup failed for '{company_name}': {e}")

    return None, None


async def resolve_ticker_strict(session: AsyncSession, company_name: str, canonical_domain: str | None = None) -> dict | None:
    """
    Strictly checks if a company is publicly traded on Yahoo Finance.
    Enforces exact normalized slug matching and excludes known private firms.
    """
    clean_name = clean_company_name(company_name).lower()
    norm_input = normalize_slug(clean_name)

    # 1. Immediate veto for known private giants
    if norm_input in KNOWN_PRIVATE_COMPANIES or clean_name in KNOWN_PRIVATE_COMPANIES:
        logger.info(f"Skipping ticker search for known private company: {company_name}")
        return None

    url = "https://query2.finance.yahoo.com/v1/finance/search"
    params = {
        "q": clean_name,
        "quotesCount": 6,
        "newsCount": 0,
        "listsCount": 0,
        "enableFuzzyQuery": "false",
    }

    try:
        resp = await session.get(url, params=params, headers=STEALTH_HEADERS, timeout=8.0)
        if resp.status_code != 200:
            return None

        quotes = resp.json().get("quotes", [])
        for q in quotes:
            if q.get("quoteType", "").upper() not in ("EQUITY",):
                continue

            raw_name = (q.get("longname") or q.get("shortname") or "").lower()
            q_clean = clean_company_name(raw_name).lower()
            norm_quote = normalize_slug(q_clean)

            # Strict name checks:
            # - Exact slug equality ('openai' == 'openai')
            # - Or sequence similarity ratio >= 0.92 (prevents 'openai' vs 'openair')
            slug_equal = (norm_input == norm_quote)
            similarity = difflib.SequenceMatcher(None, clean_name, q_clean).ratio()

            if not slug_equal and similarity < 0.92:
                continue

            raw_symbol = q.get("symbol")
            if raw_symbol:
                return {
                    "symbol": raw_symbol.split(".")[0],
                    "exchange": q.get("exchDisp") or q.get("exchange"),
                    "industry": q.get("industry"),
                    "sector": q.get("sector"),
                }
    except Exception as e:
        logger.debug(f"Yahoo ticker search error for '{company_name}': {e}")

    return None


async def fetch_perplexity_public(session: AsyncSession, symbol: str, company_name: str, expected_domain: str | None = None) -> dict | None:
    """
    Fetches profile metadata from Perplexity API.
    Crucial fix: Rejects the profile if the reported website domain contradicts the canonical domain.
    """
    url = f"https://www.perplexity.ai/rest/finance/profile/{symbol.upper()}"
    try:
        resp = await session.get(url, headers=STEALTH_HEADERS, timeout=9.0)
        if resp.status_code == 200:
            data = resp.json()
            if not data or not data.get("companyName"):
                return None

            reported_website = data.get("website")
            # Domain Cross-Verification Guard
            if expected_domain and reported_website:
                public_domain = extract_root_domain(reported_website)
                if public_domain and public_domain != expected_domain:
                    logger.warning(
                        f"Domain mismatch for {company_name}: Expected '{expected_domain}', got '{public_domain}' from ticker {symbol}. Rejecting public match."
                    )
                    return None

            return {
                "company_name": company_name,
                "symbol": symbol.upper(),
                "exchange": data.get("exchange") or data.get("exchangeShortName"),
                "industry": data.get("industry"),
                "sector": data.get("sector"),
                "website": reported_website,
                "description": data.get("description"),
                "ceo": data.get("ceo"),
                "country": data.get("country"),
                "full_time_employees": str(data.get("fullTimeEmployees", "")) if data.get("fullTimeEmployees") else None,
                "phone": data.get("phone"),
                "address": data.get("address"),
                "city": data.get("city"),
                "state": data.get("state"),
                "zip": data.get("zip"),
                "logo_url": data.get("image") or data.get("imageDark"),
                "source": "perplexity",
            }
    except Exception as e:
        logger.debug(f"Perplexity fetch failed for {symbol}: {e}")
    return None


async def fetch_thecompaniesapi(session: AsyncSession, company_name: str, domain: str | None, logo_url: str | None) -> dict | None:
    """Fetches full private company profile from TheCompaniesAPI."""
    headers = {
        "Authorization": f"Bearer {THECOMPANIESAPI_TOKEN}",
        "Accept": "application/json",
    }

    candidates = []
    if domain:
        candidates.append(domain)
    
    clean_name = clean_company_name(company_name)
    slug = normalize_slug(clean_name)
    if slug and slug != domain:
        candidates.append(slug)

    data = None
    for target in candidates:
        url = f"https://api.thecompaniesapi.com/v1/companies/{quote(target)}"
        try:
            resp = await session.get(url, headers=headers, timeout=10.0)
            if resp.status_code == 200:
                payload = resp.json()
                data = payload[0] if isinstance(payload, list) and payload else payload
                if isinstance(data, dict) and "companies" in data:
                    c_list = data.get("companies", [])
                    data = c_list[0] if c_list else {}

                if data and isinstance(data, dict):
                    break
            elif resp.status_code in (401, 403):
                logger.error(f"TheCompaniesAPI Authentication Failed (Status {resp.status_code}).")
                return None
            elif resp.status_code in (402, 429):
                logger.warning(f"TheCompaniesAPI limit reached (Status {resp.status_code}).")
                return None
        except Exception as e:
            logger.debug(f"TheCompaniesAPI error for '{target}': {e}")

    if not data or not isinstance(data, dict):
        return None

    description = (
        data.get("description")
        or data.get("about")
        or data.get("overview")
        or data.get("summary")
        or data.get("shortDescription")
        or data.get("longDescription")
    )

    industry = (
        data.get("industry")
        or data.get("industries")
        or data.get("categories")
        or data.get("category")
    )

    location = data.get("location") or data.get("headquarters") or {}
    if not isinstance(location, dict):
        location = {}

    ceo = data.get("ceo") or data.get("founder") or data.get("founderName") or data.get("leadership")
    employees = data.get("employees") or data.get("employeeRange") or data.get("headcount") or data.get("companySize")

    company_domain = to_clean_str(data.get("domain")) or domain
    raw_web = data.get("website") or data.get("url")
    resolved_website = to_clean_str(raw_web) or (f"https://{company_domain}" if company_domain else None)
    resolved_logo = to_clean_str(data.get("logo") or data.get("icon") or data.get("logoUrl")) or logo_url

    return {
        "company_name": company_name,
        "symbol": None,
        "exchange": None,
        "industry": to_clean_str(industry),
        "sector": to_clean_str(data.get("sector")),
        "website": resolved_website,
        "description": to_clean_str(description),
        "ceo": to_clean_str(ceo),
        "country": to_clean_str(data.get("country")) or to_clean_str(location.get("country")),
        "full_time_employees": to_clean_str(employees),
        "phone": to_clean_str(data.get("phone")),
        "address": to_clean_str(location.get("street")) or to_clean_str(data.get("address")),
        "city": to_clean_str(location.get("city")) or to_clean_str(data.get("city")),
        "state": to_clean_str(location.get("state")) or to_clean_str(data.get("state")),
        "zip": to_clean_str(location.get("postalCode")) or to_clean_str(data.get("zip")),
        "logo_url": resolved_logo,
        "source": "thecompaniesapi",
    }


def sanitize_company_record(record: dict) -> dict:
    """Clamps fields to PostgreSQL column sizes and guarantees no dicts remain."""
    limits = {
        "company_name": 255,
        "symbol": 50,
        "exchange": 100,
        "industry": 150,
        "sector": 100,
        "ceo": 150,
        "country": 100,
        "full_time_employees": 50,
        "phone": 50,
        "city": 100,
        "state": 100,
        "zip": 50,
        "source": 50,
    }

    for k in list(record.keys()):
        record[k] = to_clean_str(record.get(k))

    raw_emp = record.get("full_time_employees")
    if raw_emp:
        num_match = re.search(r"[\d,]+(?:\s*\+)?(?:\s*(?:k|m|million|billion))?", raw_emp, re.IGNORECASE)
        if num_match and len(raw_emp) > 50:
            record["full_time_employees"] = num_match.group(0).strip()
        else:
            record["full_time_employees"] = raw_emp[:50]

    for col, limit in limits.items():
        val = record.get(col)
        if val is not None:
            record[col] = val[:limit]

    return record


def is_record_meaningful(record: dict) -> bool:
    """Requires at least meaningful metadata to save."""
    return bool(
        record.get("symbol")
        or record.get("description")
        or record.get("industry")
        or record.get("ceo")
    )


async def process_and_save_company_metadata(unique_companies: list[dict], concurrency: int = 3):
    """
    Pure API Pipeline:
    1. Skips companies already in Postgres.
    2. Resolves canonical domain.
    3. Checks Yahoo Finance with strict token validation & domain cross-check.
    4. If private -> Calls TheCompaniesAPI with the exact domain.
    5. Saves only validated records.
    """
    if not unique_companies:
        return

    deduped = {}
    for item in unique_companies:
        name = item.get("company_name")
        if name and name.strip():
            key = name.strip().lower()
            if key not in deduped:
                deduped[key] = item

    try:
        existing_in_db = get_existing_company_names()
    except Exception as e:
        logger.warning(f"Could not check existing companies in DB ({e}).")
        existing_in_db = set()

    to_process = [item for key, item in deduped.items() if key not in existing_in_db]

    if not to_process:
        logger.info("All companies already exist in DB. Skipping calls.")
        return

    logger.info(f"Processing metadata for {len(to_process)} new companies...")

    sem = asyncio.Semaphore(concurrency)
    results = []

    async with AsyncSession(impersonate="chrome120") as session:

        async def handle_company(item):
            c_name = item.get("company_name", "").strip()
            website = item.get("website")

            async with sem:
                await asyncio.sleep(0.3)

                # STAGE 1: Discover Ground-Truth Domain and Logo first
                canonical_domain, logo_url = await resolve_canonical_domain(session, c_name, fallback_website=website)

                # STAGE 2: Public vs Private validation (Strict + Guarded)
                resolved = await resolve_ticker_strict(session, c_name, canonical_domain=canonical_domain)
                data = None

                # STAGE 3: If Public, fetch Perplexity and enforce domain equality
                if resolved and resolved.get("symbol"):
                    data = await fetch_perplexity_public(
                        session=session,
                        symbol=resolved["symbol"],
                        company_name=c_name,
                        expected_domain=canonical_domain,
                    )
                    if data:
                        if not data.get("industry") and resolved.get("industry"):
                            data["industry"] = resolved["industry"]
                        if not data.get("sector") and resolved.get("sector"):
                            data["sector"] = resolved["sector"]

                # STAGE 4: Fallback to TheCompaniesAPI using domain/name
                if not data:
                    data = await fetch_thecompaniesapi(session, c_name, canonical_domain, logo_url)

                # STAGE 5: Clean and save
                if data and is_record_meaningful(data):
                    clean_data = sanitize_company_record(data)
                    results.append(clean_data)
                else:
                    logger.debug(f"Skipping '{c_name}': No rich profile data available.")

        await asyncio.gather(*(handle_company(item) for item in to_process))

    if results:
        upsert_companies(results)
        logger.info(f"Successfully saved {len(results)} new company records.")
    else:
        logger.info("No new company metadata found to insert.")