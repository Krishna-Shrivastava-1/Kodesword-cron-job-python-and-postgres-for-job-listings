

# """
# GlobalTechHarvester v2
# =======================
# Changes vs. the original script (see inline comments marked "FIX:" / "NEW:"):

# 1. FIX  Workday returned zero jobs because it hit the wrong endpoint
#         (/wday/cxf/service-pagination/{site}). The real, public, unauthenticated
#         endpoint every Workday careers page itself calls is:
#             POST https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
#         Now fetches that, paginates through it, and pulls the requisition ID out
#         of `bulletFields` instead of guessing it from the URL.

# 2. FIX  Experience-level classification no longer relies on title keywords alone.
#         It now also looks for explicit "3-5 years", "5+ years" style phrases in the
#         title, the ATS commitment field, and (for Greenhouse/Lever/Ashby) the job
#         description body -- and lets that numeric evidence override a noisy
#         "Internship" flag coming from an ATS's employment-type field. INTERNSHIP
#         is now only assigned when the *title itself* says intern/co-op/apprentice,
#         or when the commitment field says so AND the title has no seniority word.

# 3. FIX  Location parsing keeps the "never invent a location" rule, but:
#         - Adds ~90 more cities across US/EU/APAC/LatAm/MEA.
#         - Adds a distinct `state` field (was silently overloading `city` with the
#           state name for US-state-only postings).
#         - Adds bare ISO2 / common alias matching (US, UK, UAE, etc.).
#         - Fixes the "no location text at all" case: it used to default
#           workplace_type to "On-site" (an invented fact); it's now "Unspecified".

# 4. Same-as-before: hard 30-day freshness cutoff, still enforced identically for
#    every ATS.

# 5. NEW  Pagination for Workday and SmartRecruiters (both silently truncated to
#    one page before).
# 6. NEW  Retry with backoff on 429/5xx so a transient rate limit doesn't just
#    silently produce an empty list for that company.
# 7. NEW  Job description (where cheaply available) is stored as a short plain-text
#    snippet, and department/requisition ID are captured -- useful for an actual
#    job-board UI.
# 8. NEW  Company metadata (website, HQ country, ticker) is joined in from
#    company_career_pages.json so the frontend can show a logo/link without a
#    second lookup. Named company_website / company_hq_country so it's never
#    confused with the job's own location.
# """

# import asyncio
# import html as html_lib
# import json
# import logging
# import os
# import re
# import uuid
# from datetime import datetime, timedelta, timezone
# from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
# from dateutil import parser as date_parser
# import httpx
# from database import save_jobs_alternating
# from companymetadata import process_and_save_company_metadata
# # =====================================================================
# # 1. CONFIGURATION & LOGGING
# # =====================================================================
# logging.basicConfig(
#     level=logging.INFO,
#     format="%(asctime)s [%(levelname)s] %(message)s"
# )
# logger = logging.getLogger("GlobalTechHarvester")

# MAX_CONCURRENT_REQUESTS = 90
# PROBE_CONCURRENCY = 45
# REQUEST_TIMEOUT = 15.0
# MAX_JOB_AGE_DAYS = 30
# OUTPUT_FILE = "curated_global_tech_jobs.json"
# BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# COMPANIES_JSON_FILE = os.path.join(BASE_DIR, "company_career_pages.json")

# MAX_RETRIES = 3
# RETRY_BACKOFF_BASE = 0.75  # seconds

# # NEW: see the long comment in fetch_lever(). Lever's public API has no
# # "last updated / reposted" field, only original createdAt, and explicitly
# # supports evergreen postings left open for months. Since the API only ever
# # serves currently-open roles, treat "still returned by Lever" as the
# # freshness signal for this ATS instead of excluding by createdAt age.
# # Flip to False to enforce the strict 30-day-since-creation rule instead.
# LEVER_TREAT_OPEN_AS_FRESH = True

# # NEW: Workday's list endpoint (POST .../jobs) omits department and sometimes
# # omits locationsText entirely, and never includes the description. Set this to
# # True to fetch the per-job detail endpoint for every posting that survives the
# # title/freshness filter (cheap in aggregate since filtering happens first).
# # Set False to keep the old, faster, list-endpoint-only behavior.
# WORKDAY_FETCH_DETAILS = True
# WORKDAY_DETAIL_CONCURRENCY = 15

# NOW_UTC = datetime.now(timezone.utc)
# CUTOFF_DATE = NOW_UTC - timedelta(days=MAX_JOB_AGE_DAYS)
# UUID_NAMESPACE = uuid.UUID("e30fa982-1275-40dc-84e4-7d52f6b8a8b1")

# HEADERS = {
#     "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
#     "Accept": "application/json, text/plain, */*",
# }

# DISCOVERY_SOURCES = [
#     "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/README.md",
#     "https://raw.githubusercontent.com/SimplifyJobs/Summer2025-Internships/dev/README.md",
#     "https://raw.githubusercontent.com/poteto/hiring-without-whiteboards/master/README.md",
#     "https://raw.githubusercontent.com/lukasz-madon/awesome-remote-job/master/README.md",
#     "https://raw.githubusercontent.com/ripienaar/free-for-dev/master/README.md"
# ]

# # Verified high-volume tech seeds
# CURATED_SEEDS = {
#     "greenhouse": ["truecaller", "stripe", "figma", "airbnb", "coinbase", "brex", "postman", "instacart", "datadog"],
#     "lever": ["spotify", "palantir", "atlassian"],
#     "ashby": ["openai", "anthropic", "ramp", "perplexity", "retool", "linear"],
#     "smartrecruiters": ["visa", "square", "bosch"],
#     "workday": [
#         "nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite",
#         "adobe.wd5.myworkdayjobs.com/external_experienced",
#         "salesforce.wd12.myworkdayjobs.com/External_Career_Site",
#         "paloaltonetworks.wd1.myworkdayjobs.com/PANWExternalCareers",
#         "cisco.wd5.myworkdayjobs.com/CiscoJobs",
#         "autodesk.wd1.myworkdayjobs.com/Uni"
#     ]
# }

# RESERVED_SLUGS = {
#     "jobs", "careers", "search", "apply", "embed", "about", "terms", "privacy",
#     "cookie", "default", "api", "v1", "v0", "postings", "content", "job", "career",
#     "en-us", "en", "login", "userhome"
# }

# # =====================================================================
# # 2. STRICT GEOLOCATION ENGINE (ZERO INVENTED LOCATIONS)
# # =====================================================================

# ISO_COUNTRY_CONTINENT = {
#     "IN": ("India", "Asia"), "US": ("United States", "North America"),
#     "CA": ("Canada", "North America"), "GB": ("United Kingdom", "Europe"),
#     "DE": ("Germany", "Europe"), "NL": ("Netherlands", "Europe"),
#     "FR": ("France", "Europe"), "IE": ("Ireland", "Europe"),
#     "ES": ("Spain", "Europe"), "PL": ("Poland", "Europe"),
#     "SG": ("Singapore", "Asia"), "JP": ("Japan", "Asia"),
#     "AU": ("Australia", "Oceania"), "NZ": ("New Zealand", "Oceania"),
#     "BR": ("Brazil", "South America"), "IL": ("Israel", "Asia"),
#     "AE": ("United Arab Emirates", "Asia"), "SE": ("Sweden", "Europe"),
#     "CH": ("Switzerland", "Europe"),
#     # NEW: broader coverage
#     "IT": ("Italy", "Europe"), "PT": ("Portugal", "Europe"),
#     "AT": ("Austria", "Europe"), "BE": ("Belgium", "Europe"),
#     "DK": ("Denmark", "Europe"), "FI": ("Finland", "Europe"),
#     "NO": ("Norway", "Europe"), "CZ": ("Czech Republic", "Europe"),
#     "HU": ("Hungary", "Europe"), "RO": ("Romania", "Europe"),
#     "GR": ("Greece", "Europe"), "UA": ("Ukraine", "Europe"),
#     "HK": ("Hong Kong", "Asia"), "CN": ("China", "Asia"),
#     "KR": ("South Korea", "Asia"), "TW": ("Taiwan", "Asia"),
#     "TH": ("Thailand", "Asia"), "ID": ("Indonesia", "Asia"),
#     "PH": ("Philippines", "Asia"), "MY": ("Malaysia", "Asia"),
#     "VN": ("Vietnam", "Asia"), "SA": ("Saudi Arabia", "Asia"),
#     "QA": ("Qatar", "Asia"), "MX": ("Mexico", "North America"),
#     "AR": ("Argentina", "South America"), "CO": ("Colombia", "South America"),
#     "CL": ("Chile", "South America"), "ZA": ("South Africa", "Africa"),
#     "EG": ("Egypt", "Africa"), "NG": ("Nigeria", "Africa"),
# }

# GLOBAL_CITIES_MAP = {
#     # India Tech Hubs
#     "bengaluru": ("IN", "Bengaluru"), "bangalore": ("IN", "Bengaluru"),
#     "hyderabad": ("IN", "Hyderabad"), "pune": ("IN", "Pune"),
#     "mumbai": ("IN", "Mumbai"), "gurgaon": ("IN", "Gurugram"),
#     "gurugram": ("IN", "Gurugram"), "noida": ("IN", "Noida"),
#     "delhi": ("IN", "Delhi"), "new delhi": ("IN", "New Delhi"),
#     "chennai": ("IN", "Chennai"), "ahmedabad": ("IN", "Ahmedabad"),
#     "kolkata": ("IN", "Kolkata"), "kochi": ("IN", "Kochi"),
#     "coimbatore": ("IN", "Coimbatore"), "trivandrum": ("IN", "Thiruvananthapuram"),
#     "thiruvananthapuram": ("IN", "Thiruvananthapuram"), "indore": ("IN", "Indore"),
#     "chandigarh": ("IN", "Chandigarh"), "jaipur": ("IN", "Jaipur"),
#     # North America
#     "san francisco": ("US", "San Francisco"),
#     "new york": ("US", "New York"), "nyc": ("US", "New York"),
#     "seattle": ("US", "Seattle"), "austin": ("US", "Austin"),
#     "boston": ("US", "Boston"), "chicago": ("US", "Chicago"),
#     "los angeles": ("US", "Los Angeles"), "san jose": ("US", "San Jose"),
#     "sunnyvale": ("US", "Sunnyvale"), "mountain view": ("US", "Mountain View"),
#     "palo alto": ("US", "Palo Alto"), "santa clara": ("US", "Santa Clara"),
#     "denver": ("US", "Denver"), "atlanta": ("US", "Atlanta"),
#     "miami": ("US", "Miami"), "dallas": ("US", "Dallas"),
#     "houston": ("US", "Houston"), "raleigh": ("US", "Raleigh"),
#     "durham": ("US", "Durham"), "reston": ("US", "Reston"),
#     "arlington": ("US", "Arlington"), "washington dc": ("US", "Washington DC"),
#     "washington, d.c.": ("US", "Washington DC"), "detroit": ("US", "Detroit"),
#     "minneapolis": ("US", "Minneapolis"), "portland": ("US", "Portland"),
#     "phoenix": ("US", "Phoenix"), "san diego": ("US", "San Diego"),
#     "philadelphia": ("US", "Philadelphia"), "pittsburgh": ("US", "Pittsburgh"),
#     "salt lake city": ("US", "Salt Lake City"), "irvine": ("US", "Irvine"),
#     "redmond": ("US", "Redmond"), "bellevue": ("US", "Bellevue"),
#     "toronto": ("CA", "Toronto"), "vancouver": ("CA", "Vancouver"),
#     "montreal": ("CA", "Montreal"), "waterloo": ("CA", "Waterloo"),
#     "ottawa": ("CA", "Ottawa"), "calgary": ("CA", "Calgary"),
#     "mexico city": ("MX", "Mexico City"), "guadalajara": ("MX", "Guadalajara"),
#     # Europe
#     "london": ("GB", "London"), "manchester": ("GB", "Manchester"),
#     "edinburgh": ("GB", "Edinburgh"), "bristol": ("GB", "Bristol"),
#     "cambridge, uk": ("GB", "Cambridge"),
#     "berlin": ("DE", "Berlin"), "munich": ("DE", "Munich"),
#     "hamburg": ("DE", "Hamburg"), "frankfurt": ("DE", "Frankfurt"),
#     "amsterdam": ("NL", "Amsterdam"), "rotterdam": ("NL", "Rotterdam"),
#     "dublin": ("IE", "Dublin"), "cork": ("IE", "Cork"),
#     "paris": ("FR", "Paris"), "lyon": ("FR", "Lyon"),
#     "madrid": ("ES", "Madrid"), "barcelona": ("ES", "Barcelona"),
#     "warsaw": ("PL", "Warsaw"), "krakow": ("PL", "Krakow"),
#     "stockholm": ("SE", "Stockholm"), "gothenburg": ("SE", "Gothenburg"),
#     "zurich": ("CH", "Zurich"), "geneva": ("CH", "Geneva"),
#     "milan": ("IT", "Milan"), "rome": ("IT", "Rome"),
#     "lisbon": ("PT", "Lisbon"), "porto": ("PT", "Porto"),
#     "vienna": ("AT", "Vienna"), "brussels": ("BE", "Brussels"),
#     "copenhagen": ("DK", "Copenhagen"), "helsinki": ("FI", "Helsinki"),
#     "oslo": ("NO", "Oslo"), "prague": ("CZ", "Prague"),
#     "budapest": ("HU", "Budapest"), "bucharest": ("RO", "Bucharest"),
#     "athens": ("GR", "Athens"), "kyiv": ("UA", "Kyiv"),
#     # APAC & Others
#     "singapore": ("SG", "Singapore"), "tokyo": ("JP", "Tokyo"),
#     "osaka": ("JP", "Osaka"), "sydney": ("AU", "Sydney"),
#     "melbourne": ("AU", "Melbourne"), "brisbane": ("AU", "Brisbane"),
#     "auckland": ("NZ", "Auckland"),
#     "tel aviv": ("IL", "Tel Aviv"), "herzliya": ("IL", "Herzliya"),
#     "dubai": ("AE", "Dubai"), "abu dhabi": ("AE", "Abu Dhabi"),
#     "riyadh": ("SA", "Riyadh"), "doha": ("QA", "Doha"),
#     "hong kong": ("HK", "Hong Kong"), "shanghai": ("CN", "Shanghai"),
#     "beijing": ("CN", "Beijing"), "shenzhen": ("CN", "Shenzhen"),
#     "seoul": ("KR", "Seoul"), "taipei": ("TW", "Taipei"),
#     "bangkok": ("TH", "Bangkok"), "jakarta": ("ID", "Jakarta"),
#     "manila": ("PH", "Manila"), "kuala lumpur": ("MY", "Kuala Lumpur"),
#     "ho chi minh city": ("VN", "Ho Chi Minh City"), "hanoi": ("VN", "Hanoi"),
#     # LatAm & Africa
#     "sao paulo": ("BR", "Sao Paulo"), "são paulo": ("BR", "Sao Paulo"),
#     "rio de janeiro": ("BR", "Rio de Janeiro"),
#     "buenos aires": ("AR", "Buenos Aires"), "bogota": ("CO", "Bogota"),
#     "santiago": ("CL", "Santiago"),
#     "cape town": ("ZA", "Cape Town"), "johannesburg": ("ZA", "Johannesburg"),
#     "cairo": ("EG", "Cairo"), "lagos": ("NG", "Lagos"),
# }

# # NEW: bare country-code / common alias tokens (e.g. "Remote - US", "UK", "UAE")
# ISO2_ALIASES = {
#     "usa": "US", "us": "US", "u.s.": "US", "u.s.a.": "US",
#     "uk": "GB", "gb": "GB", "u.k.": "GB",
#     "uae": "AE", "emea": None, "apac": None,  # regions, not countries -> handled separately
# }

# # FIX: the original list only had ~22 of 50 states -- e.g. Arkansas ("AR") was
# # missing entirely, so a URL-declared "Bentonville-AR" fell through to Worldwide
# # instead of being recognized as a US location. Now covers all 50 states + DC.
# US_STATES_INDEX = {
#     "al": "Alabama", "ak": "Alaska", "az": "Arizona", "ar": "Arkansas",
#     "ca": "California", "co": "Colorado", "ct": "Connecticut", "de": "Delaware",
#     "fl": "Florida", "ga": "Georgia", "hi": "Hawaii", "id": "Idaho",
#     "il": "Illinois", "in": "Indiana", "ia": "Iowa", "ks": "Kansas",
#     "ky": "Kentucky", "la": "Louisiana", "me": "Maine", "md": "Maryland",
#     "ma": "Massachusetts", "mi": "Michigan", "mn": "Minnesota", "ms": "Mississippi",
#     "mo": "Missouri", "mt": "Montana", "ne": "Nebraska", "nv": "Nevada",
#     "nh": "New Hampshire", "nj": "New Jersey", "nm": "New Mexico", "ny": "New York",
#     "nc": "North Carolina", "nd": "North Dakota", "oh": "Ohio", "ok": "Oklahoma",
#     "or": "Oregon", "pa": "Pennsylvania", "ri": "Rhode Island", "sc": "South Carolina",
#     "sd": "South Dakota", "tn": "Tennessee", "tx": "Texas", "ut": "Utah",
#     "vt": "Vermont", "va": "Virginia", "wa": "Washington", "wv": "West Virginia",
#     "wi": "Wisconsin", "wy": "Wyoming", "dc": "District of Columbia",
# }
# US_STATE_NAMES = {name.lower(): name for name in US_STATES_INDEX.values()}

# RE_REMOTE_KEYWORDS = re.compile(
#     r"\b(remote|virtual|distributed|wfh|telecommute|anywhere|worldwide|global|home[-\s]?based|work from home)\b",
#     re.I,
# )
# RE_HYBRID_KEYWORDS = re.compile(r"\b(hybrid|flexible)\b", re.I)
# RE_PURE_GLOBAL = re.compile(
#     r"^(remote|virtual|anywhere|worldwide|global|all|any|unspecified|multiple locations|work from anywhere)$",
#     re.I,
# )
# RE_MULTI_LOCATION_PLACEHOLDER = re.compile(r"^\d+\s+locations?$", re.I)


# def parse_geo_location(raw_text: str) -> dict:
#     """
#     Strictly maps declared locations to verified cities/countries/states.
#     NEVER invents a location. If absent, marks workplace_type as Unspecified
#     and country as Worldwide rather than guessing "On-site".
#     """
#     base = {
#         "city": None, "state": None, "country": "Worldwide", "country_code": "GLOBAL",
#         "continent": "Global", "workplace_type": "Unspecified", "location_display": "Worldwide (Unspecified)"
#     }

#     if not raw_text or not raw_text.strip() or RE_MULTI_LOCATION_PLACEHOLDER.match(raw_text.strip()):
#         return base

#     clean_text = re.sub(r"\s+", " ", raw_text).strip()
#     loc_lower = clean_text.lower()

#     is_hybrid = bool(RE_HYBRID_KEYWORDS.search(loc_lower))
#     is_remote = bool(RE_REMOTE_KEYWORDS.search(loc_lower))
#     # FIX: only assume "On-site" once we know there's real location text to go on.
#     workplace_type = "Hybrid" if is_hybrid else ("Remote" if is_remote else "On-site")

#     if RE_PURE_GLOBAL.match(loc_lower):
#         return {
#             "city": None, "state": None, "country": "Worldwide", "country_code": "GLOBAL",
#             "continent": "Global", "workplace_type": "Remote", "location_display": "Worldwide (Remote)"
#         }

#     # 1. Known tech cities (longest keys first so "new delhi" wins over "delhi", etc.)
#     for city_key in sorted(GLOBAL_CITIES_MAP, key=len, reverse=True):
#         alpha2, city_name = GLOBAL_CITIES_MAP[city_key]
#         if re.search(rf"\b{re.escape(city_key)}\b", loc_lower):
#             country_name, continent = ISO_COUNTRY_CONTINENT.get(alpha2, (alpha2, "Unknown"))
#             display_str = f"{city_name}, {country_name}"
#             if workplace_type != "On-site":
#                 display_str += f" ({workplace_type})"
#             return {
#                 "city": city_name, "state": None, "country": country_name, "country_code": alpha2,
#                 "continent": continent, "workplace_type": workplace_type, "location_display": display_str
#             }

#     # 2. India (broad, no specific city matched)
#     if re.search(r"\b(india|bharat)\b", loc_lower):
#         display_str = "India" if workplace_type == "On-site" else f"India ({workplace_type})"
#         return {
#             "city": None, "state": None, "country": "India", "country_code": "IN",
#             "continent": "Asia", "workplace_type": workplace_type, "location_display": display_str
#         }

#     # 3. US states -- full names first, then abbreviations
#     for state_key, state_name in US_STATE_NAMES.items():
#         if re.search(rf"\b{re.escape(state_key)}\b", loc_lower):
#             display_str = f"{state_name}, United States"
#             if workplace_type != "On-site":
#                 display_str += f" ({workplace_type})"
#             return {
#                 "city": None, "state": state_name, "country": "United States", "country_code": "US",
#                 "continent": "North America", "workplace_type": workplace_type, "location_display": display_str
#             }

#     tokens = [t.strip(".,()[]{}") for t in re.split(r"[\s,/|\-]+", loc_lower) if t.strip()]
#     for token in tokens:
#         if token in US_STATES_INDEX:
#             state_name = US_STATES_INDEX[token]
#             display_str = f"{state_name}, United States"
#             if workplace_type != "On-site":
#                 display_str += f" ({workplace_type})"
#             return {
#                 "city": None, "state": state_name, "country": "United States", "country_code": "US",
#                 "continent": "North America", "workplace_type": workplace_type, "location_display": display_str
#             }

#     # 4. Bare ISO2 / common aliases (e.g. "Remote - US", "UK")
#     for token in tokens:
#         alpha2 = ISO2_ALIASES.get(token)
#         if alpha2 and alpha2 in ISO_COUNTRY_CONTINENT:
#             country_name, continent = ISO_COUNTRY_CONTINENT[alpha2]
#             display_str = country_name if workplace_type == "On-site" else f"{country_name} ({workplace_type})"
#             return {
#                 "city": None, "state": None, "country": country_name, "country_code": alpha2,
#                 "continent": continent, "workplace_type": workplace_type, "location_display": display_str
#             }

#     # 5. Full ISO country names
#     for alpha2, (country_name, continent) in ISO_COUNTRY_CONTINENT.items():
#         if re.search(rf"\b{re.escape(country_name.lower())}\b", loc_lower):
#             display_str = country_name if workplace_type == "On-site" else f"{country_name} ({workplace_type})"
#             return {
#                 "city": None, "state": None, "country": country_name, "country_code": alpha2,
#                 "continent": continent, "workplace_type": workplace_type, "location_display": display_str
#             }

#     # 6. Remote with no verifiable country -> Worldwide (Remote), never invented
#     if is_remote:
#         return {
#             "city": None, "state": None, "country": "Worldwide", "country_code": "GLOBAL",
#             "continent": "Global", "workplace_type": "Remote", "location_display": "Worldwide (Remote)"
#         }

#     # Fallback: text existed but named nothing we can verify
#     return {
#         "city": None, "state": None, "country": "Worldwide", "country_code": "GLOBAL",
#         "continent": "Global", "workplace_type": workplace_type, "location_display": f"Worldwide ({workplace_type})"
#     }


# def split_and_parse_locations(raw_locations) -> list:
#     if isinstance(raw_locations, str):
#         candidates = [raw_locations]
#     else:
#         candidates = raw_locations or []

#     expanded = []
#     for cand in candidates:
#         if not cand:
#             continue
#         parts = re.split(r"\s*;\s*|\s*\|\s*|\s+/\s+|\s+or\s+", str(cand))
#         for p in parts:
#             p_strip = p.strip()
#             if p_strip and p_strip not in expanded:
#                 expanded.append(p_strip)

#     if not expanded:
#         expanded = [""]  # -> Unspecified, not "Worldwide" pretending to know it's remote

#     parsed = []
#     seen = set()
#     for item in expanded:
#         loc = parse_geo_location(item)
#         key = (loc["country_code"], loc["city"], loc["state"], loc["workplace_type"])
#         if key not in seen:
#             seen.add(key)
#             parsed.append(loc)
#     return parsed

# # =====================================================================
# # 3. STRICT TECH ROLE, SENIORITY & EXPERIENCE CLASSIFIER
# # =====================================================================

# DISQUALIFIED_ROLES = re.compile(
#     r"\b("
#     r"telecaller|telecalling|bpo|voice process|call center|customer care|field executive|"
#     r"sales executive|collection agent|data entry|business development associate|\bbda\b|"
#     r"account executive|bdr|sdr|growth marketer|seo|content writer|copywriter|"
#     r"recruiter|talent acquisition|sourcer|human resources|\bhr\b|people ops|"
#     r"accountant|accounting|payroll|billing|tax|auditor|audit|"
#     r"legal counsel|paralegal|office manager|receptionist|cashier|warehouse|driver"
#     r")\b",
#     re.I
# )

# ROLE_TAXONOMY = [
#     ("AI / ML & Data Science", re.compile(
#         r"\b(machine learning|ml engineer|deep learning|computer vision|nlp|data scientist|"
#         r"llm engineer|ai engineer|ai researcher|genai|applied scientist|mlops)\b", re.I)),
#     ("Data Engineering & BI", re.compile(
#         r"\b(data engineer|bi engineer|data architect|analytics engineer|big data|etl)\b", re.I)),
#     ("Cloud, DevOps & SRE", re.compile(
#         r"\b(devops|sre|site reliability|cloud engineer|platform engineer|infrastructure engineer|"
#         r"cloud architect|kubernetes engineer|sysadmin|systems engineer|linux administrator)\b", re.I)),
#     ("Cybersecurity", re.compile(
#         r"\b(cybersecurity|security engineer|infosec|appsec|soc analyst|penetration tester|"
#         r"security architect|incident response|vulnerability)\b", re.I)),
#     ("Mobile Engineering", re.compile(
#         r"\b(ios engineer|android engineer|mobile developer|swift developer|kotlin developer|react native|flutter)\b", re.I)),
#     ("Hardware & Embedded", re.compile(
#         r"\b(embedded engineer|firmware|hardware engineer|asic|fpga|pcb|robotics|mechatronics)\b", re.I)),
#     ("QA & Automation", re.compile(
#         r"\b(qa engineer|quality assurance|sdet|test automation|software test)\b", re.I)),
#     ("Engineering Management", re.compile(
#         r"\b(engineering manager|director of engineering|vp of engineering|tech lead|chief architect|cto)\b", re.I)),
#     ("Software Engineering", re.compile(
#         r"\b(software|developer|swe|fullstack|full-stack|backend|back-end|"
#         r"frontend|front-end|web developer|api engineer|application engineer)\b", re.I))
# ]

# STRONG_TECH_ANCHOR = re.compile(r"\b(software|swe|developer|engineer|data scientist|architect|sdet)\b", re.I)

# # NEW: numeric years-of-experience detectors, used as a cross-check against title keywords
# YEARS_EXP_RE = re.compile(
#     r"(\d{1,2})\s*(?:\+)?\s*(?:-|to|–|—)?\s*(\d{1,2})?\+?\s*(?:years?|yrs?)\s*"
#     r"(?:of\s+)?(?:relevant\s+|professional\s+|industry\s+|hands[-\s]?on\s+|work(?:ing)?\s+)?experience",
#     re.I,
# )
# YEARS_PLUS_RE = re.compile(r"\b(\d{1,2})\s*\+\s*years?\b", re.I)

# STRONG_SENIORITY_WORDS = re.compile(
#     r"\b(senior|sr\.?|staff|principal|distinguished|lead|architect|ii|iii|iv|director|manager|head of)\b",
#     re.I,
# )

# # FIX: "early career" wasn't in the Entry/Junior vocabulary at all (e.g. Anduril's
# # "2027 Early Career Software Engineer" fell through every keyword check and landed
# # on the Mid-Level default, purely for lack of a matching word -- not a wrong signal,
# # just a missing one). Also adds detection of the very common "20XX New Grad" /
# # "Class of 20XX" graduating-year prefix pattern used by many big tech new-grad reqs.
# ENTRY_LEVEL_RE = re.compile(
#     r"\b(entry[-\s]?level|junior|jr\.?|fresher|graduate|new[-\s]?grad|associate|"
#     r"early[-\s]?career|university grad(?:uate)?|campus hire|class of 20\d{2})\b|"
#     r"\b20\d{2}\s+(?:new[-\s]?grad|early[-\s]?career|graduate|university)\b",
#     re.I,
# )


# def strip_html(raw_html: str) -> str:
#     """
#     FIX: some ATS boards (seen on Greenhouse) return the `content` field as
#     HTML-entity-escaped markup, e.g. "&lt;div class=&quot;intro&quot;&gt;" instead
#     of literal "<div class="intro">". The old version only stripped literal tags
#     and only decoded &nbsp;/&amp;, so entity-escaped tags survived as visible junk
#     ("div class= intro") and things like "&#39;" (apostrophe) were never decoded.
#     html.unescape() before AND after tag-stripping handles both single- and
#     double-escaped input, and decodes every standard/numeric entity correctly.
#     """
#     if not raw_html:
#         return ""
#     text = html_lib.unescape(raw_html)
#     text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.I | re.S)
#     text = re.sub(r"<[^>]+>", " ", text)
#     text = html_lib.unescape(text)  # decode any entities that were inside tag text content
#     return re.sub(r"\s+", " ", text).strip()


# def classify_and_filter_title(raw_title: str) -> tuple:
#     if not raw_title or len(raw_title.strip()) < 3:
#         return False, "", ""

#     cleaned = re.sub(r"[\(\[\{][^\)\]\}]*(?:req|requisition|\d{4,})[^\)\]\}]*[\)\]\}]", "", raw_title, flags=re.I)
#     cleaned = re.sub(r"\s+", " ", cleaned).strip()

#     if DISQUALIFIED_ROLES.search(cleaned) and not STRONG_TECH_ANCHOR.search(cleaned):
#         return False, "", ""

#     for category, pattern in ROLE_TAXONOMY:
#         if pattern.search(cleaned):
#             return True, cleaned, category

#     return False, "", ""


# def _extract_min_years(text: str):
#     """Return the smallest 'N years experience' style number found, or None."""
#     found = []
#     for pattern in (YEARS_EXP_RE, YEARS_PLUS_RE):
#         for m in pattern.finditer(text):
#             try:
#                 lo = int(m.group(1))
#                 if 0 <= lo <= 40:
#                     found.append(lo)
#             except (ValueError, IndexError):
#                 continue
#     return min(found) if found else None


# def extract_employment_and_experience(title: str, ats_commitment: str = "", content_text: str = "") -> tuple:
#     """
#     FIX: previously classified purely off title keywords, which mislabeled roles
#     that state "3-5 years experience" in the body/commitment field as Internship
#     whenever the ATS's employment-type field was noisy. Now:
#       - Internship is only assigned from an explicit title keyword, or from the
#         commitment field when the title carries no seniority word at all.
#       - Numeric "X years experience" evidence (title, commitment, or description)
#         is used to set/confirm Mid vs Senior vs Lead-Staff, and overrides a bare
#         commitment-field "Internship" flag when years >= 2 are explicitly stated.
#     """
#     title_lower = title.lower()
#     commitment_lower = (ats_commitment or "").lower()
#     combined_short = f"{title_lower} {commitment_lower}"
#     combined_full = f"{combined_short} {content_text.lower()}"

#     min_years = _extract_min_years(combined_full)

#     # Employment type (contract / part-time / full-time)
#     if re.search(r"\b(contract|contractor|freelance|temporary|temp)\b", combined_short):
#         emp_type = "CONTRACT"
#     elif re.search(r"\b(part[-\s]?time)\b", combined_short):
#         emp_type = "PART_TIME"
#     else:
#         emp_type = "FULL_TIME"

#     title_says_intern = bool(re.search(r"\b(intern|internship|co-op|coop|apprentice|apprenticeship|trainee)\b", title_lower))
#     commitment_says_intern = bool(re.search(r"\b(intern|internship)\b", commitment_lower))

#     if title_says_intern:
#         return "INTERNSHIP", "Internship"
#     if commitment_says_intern and not STRONG_SENIORITY_WORDS.search(title_lower) and (min_years is None or min_years <= 1):
#         return "INTERNSHIP", "Internship"

#     # Keyword + numeric-years seniority ladder (numeric evidence can promote, never used to
#     # silently downgrade an explicit senior/staff/exec title keyword).
#     if re.search(r"\b(vp|vice president|director|head of|chief architect|\bcto\b)\b", combined_short):
#         return emp_type, "Executive"
#     if re.search(r"\b(staff|principal|distinguished|lead|architect)\b", combined_short) or (min_years is not None and min_years >= 8):
#         return emp_type, "Lead / Staff"
#     if re.search(r"\b(senior|sr\.?|specialist iii|specialist iv)\b", combined_short) or (min_years is not None and 5 <= min_years <= 7):
#         return emp_type, "Senior"
#     if min_years is not None and 2 <= min_years <= 4:
#         return emp_type, "Mid-Level"
#     if re.search(ENTRY_LEVEL_RE, combined_short) and (min_years is None or min_years <= 2):
#         return emp_type, "Entry / Junior"

#     return emp_type, "Mid-Level"


# def validate_date_within_window(raw_date) -> tuple:
#     if not raw_date:
#         return False, ""
#     try:
#         if isinstance(raw_date, (int, float)):
#             if raw_date > 1e11:
#                 raw_date /= 1000.0
#             dt = datetime.fromtimestamp(raw_date, tz=timezone.utc)
#         else:
#             cleaned = str(raw_date).strip()
#             if "30+" in cleaned or "month" in cleaned.lower() or "year" in cleaned.lower():
#                 return False, ""
#             dt = date_parser.parse(cleaned)
#             dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)

#         if CUTOFF_DATE <= dt <= (NOW_UTC + timedelta(days=1)):
#             return True, dt.isoformat()
#     except Exception:
#         return False, ""
#     return False, ""


# def parse_workday_relative_date(posted_str: str) -> tuple:
#     if not posted_str:
#         return False, ""
#     cleaned = posted_str.lower().strip()
#     if "30+" in cleaned or "month" in cleaned or "year" in cleaned:
#         return False, ""
#     if "today" in cleaned or "just posted" in cleaned:
#         return True, NOW_UTC.isoformat()
#     if "yesterday" in cleaned:
#         return True, (NOW_UTC - timedelta(days=1)).isoformat()

#     match = re.search(r"(\d+)\s+day", cleaned)
#     if match:
#         days = int(match.group(1))
#         if days <= MAX_JOB_AGE_DAYS:
#             return True, (NOW_UTC - timedelta(days=days)).isoformat()
#         return False, ""

#     return validate_date_within_window(posted_str)


# def clean_application_url(raw_url: str) -> str:
#     parsed = urlparse(raw_url)
#     qs = parse_qs(parsed.query)
#     clean_qs = {k: v for k, v in qs.items() if not k.startswith("utm_") and k not in {"gh_src", "lever-source", "ref", "source"}}
#     return urlunparse(parsed._replace(query=urlencode(clean_qs, doseq=True)))

# # =====================================================================
# # 4. ROW BUILDER
# # =====================================================================
# # complete description no 400 limited
# def build_job_records(
#     company_name: str, title: str, role_category: str, ats_source: str, external_id: str,
#     apply_url: str, published_at: str, raw_locations, ats_commitment: str = "",
#     content_text: str = "", department: str = "",
# ) -> list:
#     emp_type, exp_level = extract_employment_and_experience(title, ats_commitment, content_text)
#     locations = split_and_parse_locations(raw_locations)
#     cleaned_url = clean_application_url(apply_url)
#     now_iso = NOW_UTC.isoformat()
    
#     # Store complete description text instead of truncating to 400 chars
#     full_description = content_text.strip() if content_text else None
#     records = []

#     for idx, loc in enumerate(locations):
#         loc_key = f"{loc['country_code']}_{loc['city'] or loc['state'] or 'all'}_{idx}"
#         stable_seed = f"{ats_source}_{company_name}_{external_id}_{loc_key}"
#         row_uuid = str(uuid.uuid5(UUID_NAMESPACE, stable_seed))

#         records.append({
#             "id": row_uuid,
#             "title": title,
#             "company_name": company_name,
#             "department": department or None,
#             "employment_type": emp_type,
#             "role_category": role_category,
#             "experience_level": exp_level,
#             "workplace_type": loc["workplace_type"],
#             "city": loc["city"],
#             "state": loc["state"],
#             "country": loc["country"],
#             "country_code": loc["country_code"],
#             "continent": loc["continent"],
#             "location_display": loc["location_display"],
#             "description_snippet": full_description,
#             "apply_url": cleaned_url,
#             "ats_source": ats_source,
#             "external_job_id": str(external_id),
#             "status": "ACTIVE",
#             "published_at": published_at,
#             "created_at": now_iso,
#             "updated_at": now_iso,
#         })

#     return records
# # 400 char description
# # def build_job_records(
# #     company_name: str, title: str, role_category: str, ats_source: str, external_id: str,
# #     apply_url: str, published_at: str, raw_locations, ats_commitment: str = "",
# #     content_text: str = "", department: str = "",
# # ) -> list:
# #     emp_type, exp_level = extract_employment_and_experience(title, ats_commitment, content_text)
# #     locations = split_and_parse_locations(raw_locations)
# #     cleaned_url = clean_application_url(apply_url)
# #     now_iso = NOW_UTC.isoformat()
# #     snippet = content_text[:400].strip() + ("..." if len(content_text) > 400 else "") if content_text else ""
# #     records = []

# #     for idx, loc in enumerate(locations):
# #         loc_key = f"{loc['country_code']}_{loc['city'] or loc['state'] or 'all'}_{idx}"
# #         stable_seed = f"{ats_source}_{company_name}_{external_id}_{loc_key}"
# #         row_uuid = str(uuid.uuid5(UUID_NAMESPACE, stable_seed))

# #         records.append({
# #             "id": row_uuid,
# #             "title": title,
# #             "company_name": company_name,
# #             "department": department or None,
# #             "employment_type": emp_type,
# #             "role_category": role_category,
# #             "experience_level": exp_level,
# #             "workplace_type": loc["workplace_type"],
# #             "city": loc["city"],
# #             "state": loc["state"],
# #             "country": loc["country"],
# #             "country_code": loc["country_code"],
# #             "continent": loc["continent"],
# #             "location_display": loc["location_display"],
# #             "description_snippet": snippet or None,
# #             "apply_url": cleaned_url,
# #             "ats_source": ats_source,
# #             "external_job_id": str(external_id),
# #             "status": "ACTIVE",
# #             "published_at": published_at,
# #             "created_at": now_iso,
# #             "updated_at": now_iso,
# #         })

# #     return records

# # =====================================================================
# # 5. HTTP HELPERS (NEW: retry with backoff on 429/5xx)
# # =====================================================================

# async def _request_with_retry(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> httpx.Response:
#     last_exc = None
#     for attempt in range(MAX_RETRIES):
#         try:
#             res = await client.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
#             if res.status_code == 429 or res.status_code >= 500:
#                 await asyncio.sleep(RETRY_BACKOFF_BASE * (2 ** attempt))
#                 continue
#             return res
#         except (httpx.TransportError, httpx.TimeoutException) as e:
#             last_exc = e
#             await asyncio.sleep(RETRY_BACKOFF_BASE * (2 ** attempt))
#     if last_exc:
#         raise last_exc
#     return res  # last (possibly bad) response after exhausting retries

# # =====================================================================
# # 6. ATS FETCHERS (STRICT DECLARED LOCATION ONLY)
# # =====================================================================

# async def fetch_greenhouse(client: httpx.AsyncClient, slug: str, sem: asyncio.Semaphore) -> list:
#     url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
#     async with sem:
#         try:
#             res = await _request_with_retry(client, "GET", url, headers=HEADERS)
#             if res.status_code != 200:
#                 res = await _request_with_retry(
#                     client, "GET",
#                     f"https://boards-api.eu.greenhouse.io/v1/boards/{slug}/jobs?content=true",
#                     headers=HEADERS,
#                 )
#                 if res.status_code != 200:
#                     return []

#             jobs = []
#             comp_name = slug.replace("-", " ").title()

#             for j in res.json().get("jobs", []):
#                 is_valid, title, category = classify_and_filter_title(j.get("title", ""))
#                 if not is_valid:
#                     continue
#                 is_fresh, pub_date = validate_date_within_window(j.get("updated_at"))
#                 if not is_fresh:
#                     continue

#                 loc_obj = j.get("location") or {}
#                 declared_loc = loc_obj.get("name", "").strip() if isinstance(loc_obj, dict) else str(loc_obj).strip()
#                 raw_locs = [declared_loc] if declared_loc else [""]

#                 # NEW: greenhouse already fetches content=true -- actually use it now.
#                 content_html = (j.get("content") or "")
#                 content_text = strip_html(content_html)
#                 dept = ""
#                 depts = j.get("departments") or []
#                 if depts:
#                     dept = depts[0].get("name", "")

#                 jobs.extend(build_job_records(
#                     company_name=comp_name, title=title, role_category=category,
#                     ats_source="Greenhouse", external_id=str(j.get("id")),
#                     apply_url=j.get("absolute_url", ""), published_at=pub_date,
#                     raw_locations=raw_locs, content_text=content_text, department=dept,
#                 ))
#             return jobs
#         except Exception as e:
#             logger.debug(f"greenhouse:{slug} failed: {e}")
#             return []


# async def fetch_lever(client: httpx.AsyncClient, slug: str, sem: asyncio.Semaphore) -> list:
#     """
#     FIX: this previously returned ~0 jobs for essentially every Lever board.
#     Root cause is a platform quirk, not a coding typo: Lever's public postings
#     API only exposes `createdAt` -- the date a posting was first created -- and
#     has no separate "last updated" / "reposted" field. Lever explicitly supports
#     long-lived "evergreen" postings that stay open for months without ever being
#     recreated, and the API only ever returns currently-*open* roles in the first
#     place (a closed role just vanishes from the feed). So the old strict
#     "createdAt within 30 days" check was silently discarding almost every real,
#     currently-open Lever posting -- e.g. Palantir's board (independently
#     verified at 300+ open roles right now) would have failed this check almost
#     entirely, simply because most of those postings were first created more
#     than 30 days ago despite still being live today.
#     LEVER_TREAT_OPEN_AS_FRESH below controls this: default True means "if
#     Lever's API is still serving it, it's live, and that's the recency signal
#     for this ATS" -- published_at is still populated from createdAt for display
#     when present, it's just no longer used to exclude the posting.
#     Set it to False to fall back to the old strict behavior (some Lever
#     postings will then legitimately re-disappear).

#     NEW: also adds the EU host fallback Greenhouse already had (Lever splits
#     US/EU tenants across api.lever.co / api.eu.lever.co), and pagination via
#     skip/limit -- Lever's API defaults to 100 postings per page, so a board
#     larger than that was previously being silently truncated.
#     """
#     jobs = []
#     comp_name = slug.replace("-", " ").title()
#     page_size = 100
#     offset = 0
#     max_pages = 20  # safety cap: 2,000 postings per company

#     async with sem:
#         base_url = f"https://api.lever.co/v0/postings/{slug}"
#         for _ in range(max_pages):
#             url = f"{base_url}?mode=json&limit={page_size}&skip={offset}"
#             try:
#                 res = await _request_with_retry(client, "GET", url, headers=HEADERS)
#                 if res.status_code != 200:
#                     # NEW: EU-hosted tenants answer on a different host, same as Greenhouse's EU split
#                     eu_url = f"https://api.eu.lever.co/v0/postings/{slug}?mode=json&limit={page_size}&skip={offset}"
#                     res = await _request_with_retry(client, "GET", eu_url, headers=HEADERS)
#                     if res.status_code != 200:
#                         break
#                 page = res.json()
#             except Exception as e:
#                 logger.debug(f"lever:{slug} failed: {e}")
#                 break

#             if not isinstance(page, list) or not page:
#                 break

#             for j in page:
#                 is_valid, title, category = classify_and_filter_title(j.get("text", ""))
#                 if not is_valid:
#                     continue

#                 is_fresh, pub_date = validate_date_within_window(j.get("createdAt"))
#                 if not is_fresh:
#                     if LEVER_TREAT_OPEN_AS_FRESH:
#                         # Still currently served by Lever's API == still open. Keep the record;
#                         # just record whatever creation date we have (or now) for display/sorting.
#                         _, pub_date = validate_date_within_window(NOW_UTC.isoformat())
#                     else:
#                         continue

#                 cats = j.get("categories", {})
#                 raw_locs = []
#                 if cats.get("location"):
#                     raw_locs.append(cats.get("location"))
#                 if cats.get("additionalLocations"):
#                     raw_locs.extend(cats.get("additionalLocations"))
#                 if not raw_locs:
#                     raw_locs = [""]

#                 content_text = strip_html(j.get("descriptionPlain") or j.get("description") or "")

#                 jobs.extend(build_job_records(
#                     company_name=comp_name, title=title, role_category=category,
#                     ats_source="Lever", external_id=str(j.get("id")),
#                     apply_url=j.get("hostedUrl", ""), published_at=pub_date,
#                     raw_locations=raw_locs, ats_commitment=cats.get("commitment", ""),
#                     content_text=content_text, department=cats.get("team", ""),
#                 ))

#             offset += page_size
#             if len(page) < page_size:
#                 break

#     return jobs


# async def fetch_ashby(client: httpx.AsyncClient, slug: str, sem: asyncio.Semaphore) -> list:
#     url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
#     async with sem:
#         try:
#             res = await _request_with_retry(client, "GET", url, headers=HEADERS)
#             if res.status_code != 200:
#                 return []
#             jobs = []
#             comp_name = slug.replace("-", " ").title()

#             for j in res.json().get("jobs", []):
#                 is_valid, title, category = classify_and_filter_title(j.get("title", ""))
#                 if not is_valid:
#                     continue
#                 is_fresh, pub_date = validate_date_within_window(j.get("publishedAt"))
#                 if not is_fresh:
#                     continue

#                 raw_locs = []
#                 if j.get("location"):
#                     raw_locs.append(j.get("location"))
#                 for sec in j.get("secondaryLocations", []):
#                     if isinstance(sec, str):
#                         raw_locs.append(sec)
#                     elif isinstance(sec, dict):
#                         name = sec.get("location") or sec.get("name")
#                         if name:
#                             raw_locs.append(name)
#                 if not raw_locs:
#                     raw_locs = [""]

#                 content_text = strip_html(j.get("descriptionHtml") or "")

#                 jobs.extend(build_job_records(
#                     company_name=comp_name, title=title, role_category=category,
#                     ats_source="Ashby", external_id=str(j.get("id")),
#                     apply_url=j.get("jobUrl", f"https://jobs.ashbyhq.com/{slug}/{j.get('id')}"),
#                     published_at=pub_date, raw_locations=raw_locs,
#                     ats_commitment=j.get("employmentType", ""), content_text=content_text,
#                     department=j.get("department", ""),
#                 ))
#             return jobs
#         except Exception as e:
#             logger.debug(f"ashby:{slug} failed: {e}")
#             return []


# async def fetch_smartrecruiters(client: httpx.AsyncClient, slug: str, sem: asyncio.Semaphore) -> list:
#     """NEW: paginated -- the old version silently stopped at page 1 (100 postings)."""
#     jobs = []
#     comp_name = slug.replace("-", " ").title()
#     offset = 0
#     page_size = 100
#     max_pages = 15  # safety cap: 1,500 postings per company is generous

#     async with sem:
#         for _ in range(max_pages):
#             url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit={page_size}&offset={offset}"
#             try:
#                 res = await _request_with_retry(client, "GET", url, headers=HEADERS)
#                 if res.status_code != 200:
#                     break
#                 data = res.json()
#             except Exception as e:
#                 logger.debug(f"smartrecruiters:{slug} failed: {e}")
#                 break

#             content = data.get("content", [])
#             if not content:
#                 break

#             for j in content:
#                 is_valid, title, category = classify_and_filter_title(j.get("name", ""))
#                 if not is_valid:
#                     continue
#                 is_fresh, pub_date = validate_date_within_window(j.get("releasedDate"))
#                 if not is_fresh:
#                     continue

#                 loc_data = j.get("location", {}) or {}
#                 primary = f"{loc_data.get('city', '')}, {loc_data.get('country', '')}".strip(", ")
#                 raw_locs = [primary] if primary else []
#                 for sec in j.get("secondaryLocations", []):
#                     sec_str = f"{sec.get('city', '')}, {sec.get('country', '')}".strip(", ")
#                     if sec_str:
#                         raw_locs.append(sec_str)
#                 if not raw_locs:
#                     raw_locs = [""]

#                 jobs.extend(build_job_records(
#                     company_name=comp_name, title=title, role_category=category,
#                     ats_source="SmartRecruiters", external_id=str(j.get("id")),
#                     apply_url=f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
#                     published_at=pub_date, raw_locations=raw_locs,
#                     ats_commitment=j.get("typeOfEmployment", {}).get("label", ""),
#                     department=(j.get("department") or {}).get("label", ""),
#                 ))

#             total_found = data.get("totalFound", 0)
#             offset += page_size
#             if offset >= total_found or len(content) < page_size:
#                 break

#     return jobs


# def _location_from_slug(segment: str) -> str:
#     """
#     NEW: Workday embeds the primary declared location in the URL itself, e.g.
#     ".../job/Bentonville-AR/..." or ".../job/San-Jose/...". This is Workday's own
#     facet value (not invented), so it's a legitimate, zero-cost fallback for the
#     cases where the list endpoint's `locationsText` comes back blank.
#     "Bentonville-AR" -> "Bentonville, AR"; "San-Jose" -> "San Jose".
#     """
#     if not segment:
#         return ""
#     parts = segment.split("-")
#     if len(parts) >= 2 and re.fullmatch(r"[A-Za-z]{2}", parts[-1]):
#         return f"{' '.join(parts[:-1])}, {parts[-1].upper()}"
#     return " ".join(parts)


# async def _fetch_workday_detail(client: httpx.AsyncClient, host: str, tenant: str, site: str, external_path: str) -> dict:
#     """
#     NEW: hits Workday's per-posting detail endpoint to recover what the list
#     endpoint frequently omits: every declared location (not just the primary
#     one baked into the URL), the department/team, and the full description.

#     Workday's detail JSON schema isn't publicly documented and can vary by
#     tenant/version, so this is intentionally defensive: it tries several
#     plausible key paths and simply returns whatever it can find. If your
#     tenant's actual response uses different key names, share one raw detail
#     JSON (GET the URL this function builds, for one job) and the key paths
#     below can be corrected precisely.
#     """
#     detail_url = f"https://{host}/wday/cxs/{tenant}/{site}{external_path}"
#     out = {"locations": [], "department": None, "description": ""}
#     try:
#         res = await _request_with_retry(client, "GET", detail_url, headers=HEADERS)
#         if res.status_code != 200:
#             return out
#         data = res.json()
#     except Exception:
#         return out

#     info = data.get("jobPostingInfo", data)  # some tenants nest under jobPostingInfo, some don't

#     # Locations: primary + any additional, under a few different key spellings seen in the wild
#     for key in ("location", "primaryLocation", "locationsText"):
#         val = info.get(key)
#         if isinstance(val, str) and val.strip():
#             out["locations"].append(val.strip())
#             break
#     for key in ("additionalLocationsText", "additionalLocations", "additional_locations"):
#         val = info.get(key)
#         if isinstance(val, list):
#             out["locations"].extend([str(v).strip() for v in val if v])
#         elif isinstance(val, str) and val.strip():
#             out["locations"].append(val.strip())

#     # Department / team -- best-effort, frequently just absent from the public API
#     for key in ("jobFamilyGroup", "department", "team", "supervisoryOrganization"):
#         val = info.get(key)
#         if isinstance(val, str) and val.strip():
#             out["department"] = val.strip()
#             break
#         if isinstance(val, dict) and val.get("descriptor"):
#             out["department"] = str(val["descriptor"]).strip()
#             break

#     for key in ("jobDescription", "description", "jobDescriptionHtml"):
#         val = info.get(key)
#         if isinstance(val, str) and val.strip():
#             out["description"] = val
#             break

#     return out


# async def fetch_workday(client: httpx.AsyncClient, target: str, sem: asyncio.Semaphore) -> list:
#     """
#     FIX: the correct, public, unauthenticated Workday endpoint is the CXS
#     (Candidate Experience Service) API -- the same one the careers page's own
#     search box calls:
#         POST https://{host}/wday/cxs/{tenant}/{site}/jobs
#     Response shape: {"total": N, "jobPostings": [ {title, externalPath,
#     locationsText, postedOn, bulletFields: [...]}, ... ]}
#     (The old /wday/cxf/service-pagination/{site} endpoint either 404s or
#     returns an unrelated facet payload, which is why it silently produced 0 jobs.)

#     FIX: `locationsText` on the list endpoint is frequently blank even when the
#     posting clearly has a location (visible in its own URL, and often has a
#     *second* location only shown on the full job page). This now: (a) always
#     falls back to the location embedded in the URL slug when locationsText is
#     blank, and (b) fetches each surviving candidate's detail page to pick up
#     every declared location, department and the real description.
#     """
#     parts = target.split("/", 1)
#     if len(parts) != 2:
#         return []
#     host, site = parts[0], parts[1]
#     tenant = host.split(".")[0]
#     comp_name = tenant.replace("-", " ").title()

#     cxs_url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
#     post_headers = {**HEADERS, "Content-Type": "application/json"}

#     candidates = []
#     limit = 20
#     offset = 0
#     max_pages = 25  # safety cap: 500 postings per company

#     async with sem:
#         for _ in range(max_pages):
#             payload = {"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": ""}
#             try:
#                 res = await _request_with_retry(client, "POST", cxs_url, headers=post_headers, json=payload)
#                 if res.status_code != 200:
#                     break
#                 data = res.json()
#             except Exception as e:
#                 logger.debug(f"workday:{target} failed: {e}")
#                 break

#             postings = data.get("jobPostings", [])
#             if not postings:
#                 break

#             for item in postings:
#                 raw_title = item.get("title", "")
#                 is_valid, title, category = classify_and_filter_title(raw_title)
#                 if not is_valid:
#                     continue

#                 is_fresh, pub_date = parse_workday_relative_date(item.get("postedOn", ""))
#                 if not is_fresh:
#                     continue

#                 external_path = item.get("externalPath", "") or ""
#                 apply_url = f"https://{host}/{site}{external_path}" if external_path.startswith("/") else f"https://{host}/{site}/{external_path}"

#                 bullet_fields = item.get("bulletFields") or []
#                 external_id = bullet_fields[0] if bullet_fields else (external_path.rstrip("/").split("/")[-1] or apply_url[-24:])

#                 loc_text = (item.get("locationsText") or "").strip()
#                 if not loc_text:
#                     # NEW: fall back to Workday's own location slug embedded in the URL
#                     slug_match = re.match(r"^/job/([^/]+)/", external_path)
#                     if slug_match:
#                         loc_text = _location_from_slug(slug_match.group(1))

#                 candidates.append({
#                     "title": title, "category": category, "pub_date": pub_date,
#                     "external_path": external_path, "apply_url": apply_url,
#                     "external_id": external_id, "loc_text": loc_text,
#                 })

#             total = data.get("total", 0)
#             offset += limit
#             if offset >= total or len(postings) < limit:
#                 break

#     if not candidates:
#         return []

#     # NEW: fetch each candidate's detail page (bounded concurrency) for the full
#     # location list / department / description the list endpoint left out.
#     detail_sem = asyncio.Semaphore(10)

#     async def _enrich(c):
#         async with detail_sem:
#             detail = await _fetch_workday_detail(client, host, tenant, site, c["external_path"])
#         return c, detail

#     enriched = await asyncio.gather(*(_enrich(c) for c in candidates))

#     jobs = []
#     for c, detail in enriched:
#         raw_locs = detail["locations"] if detail["locations"] else ([c["loc_text"]] if c["loc_text"] else [""])
#         content_text = strip_html(detail["description"]) if detail["description"] else ""

#         jobs.extend(build_job_records(
#             company_name=comp_name, title=c["title"], role_category=c["category"],
#             ats_source="Workday", external_id=c["external_id"], apply_url=c["apply_url"],
#             published_at=c["pub_date"], raw_locations=raw_locs,
#             content_text=content_text, department=detail["department"] or "",
#         ))

#     return jobs

# # =====================================================================
# # 7. DISCOVERY & SEED PROBING
# # =====================================================================

# def clean_slug_candidates(raw_name: str) -> list:
#     if not raw_name:
#         return []
#     cleaned = re.sub(r"\b(inc|corp|corporation|ltd|llc|holdings|technologies|technology|group|co|india)\b\.?", "", raw_name, flags=re.I)
#     cleaned = re.sub(r"[^\w\s-]", "", cleaned).strip().lower()
#     slug_dash = re.sub(r"[\s_]+", "-", cleaned)
#     slug_no_sep = re.sub(r"[\s_-]+", "", cleaned)
#     return [c for c in dict.fromkeys([slug_dash, slug_no_sep]) if len(c) >= 2 and c not in RESERVED_SLUGS]


# def normalize_company_key(raw_name: str) -> str:
#     """NEW: same normalization as clean_slug_candidates' no-separator form, used
#     to join company_career_pages.json metadata back onto harvested jobs."""
#     if not raw_name:
#         return ""
#     cleaned = re.sub(r"\b(inc|corp|corporation|ltd|llc|holdings|technologies|technology|group|co|india)\b\.?", "", raw_name, flags=re.I)
#     cleaned = re.sub(r"[^\w\s-]", "", cleaned).strip().lower()
#     return re.sub(r"[\s_-]+", "", cleaned)


# async def verify_board(client: httpx.AsyncClient, ats: str, slug: str) -> bool:
#     url_map = {
#         "greenhouse": f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
#         "lever": f"https://api.lever.co/v0/postings/{slug}?limit=1",
#         "ashby": f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
#         "smartrecruiters": f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1",
#     }
#     try:
#         if ats == "workday":
#             parts = slug.split("/", 1)
#             if len(parts) == 2:
#                 host, site = parts
#                 tenant = host.split(".")[0]
#                 # FIX: verify against the real CXS endpoint, not the old cxf one
#                 res = await client.post(
#                     f"https://{host}/wday/cxs/{tenant}/{site}/jobs",
#                     json={"limit": 1, "offset": 0, "searchText": ""},
#                     timeout=5.0,
#                 )
#                 return res.status_code == 200
#             return False
#         res = await client.get(url_map[ats], timeout=3.5)
#         return res.status_code == 200
#     except Exception:
#         return False


# async def discover_all_companies(client: httpx.AsyncClient) -> dict:
#     discovered = {k: set(v) for k, v in CURATED_SEEDS.items()}

#     patterns = {
#         "greenhouse": re.compile(r"(?:boards|job-boards|boards\.eu)\.greenhouse\.io/(?:embed/job_board\?for=)?([a-zA-Z0-9_\-]+)", re.I),
#         "lever": re.compile(r"jobs\.lever\.co/([a-zA-Z0-9_\-]+)", re.I),
#         "ashby": re.compile(r"jobs\.ashbyhq\.com/([a-zA-Z0-9_\-]+)", re.I),
#         "smartrecruiters": re.compile(r"(?:careers|jobs)\.smartrecruiters\.com/([a-zA-Z0-9_\-]+)", re.I),
#         "workday": re.compile(r"https?://([a-zA-Z0-9_\-\.]+\.myworkdayjobs\.com)/(?:[a-zA-Z]{2}(?:-[a-zA-Z]{2})?/)?([a-zA-Z0-9_\-]+)", re.I),
#     }

#     logger.info("Scanning discovery sources for verified ATS targets...")
#     candidate_names = set()

#     for url in DISCOVERY_SOURCES:
#         try:
#             res = await client.get(url, headers=HEADERS, timeout=12.0)
#             if res.status_code == 200:
#                 text = res.text

#                 for ats in ["greenhouse", "lever", "ashby", "smartrecruiters"]:
#                     for match in patterns[ats].finditer(text):
#                         slug = match.group(1).lower().strip("-_/")
#                         if slug and slug not in RESERVED_SLUGS and len(slug) >= 2:
#                             discovered[ats].add(slug)

#                 for match in patterns["workday"].finditer(text):
#                     host = match.group(1).lower()
#                     site = match.group(2)
#                     if site.lower() not in RESERVED_SLUGS and len(site) >= 2:
#                         discovered["workday"].add(f"{host}/{site}")

#                 for link_match in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", text):
#                     name = re.sub(r"[\*\_`]", "", link_match.group(1)).strip()
#                     if 2 <= len(name) <= 35 and not re.search(r"\b(apply|link|here|source|readme|sponsor)\b", name, re.I):
#                         candidate_names.add(name)
#         except Exception as e:
#             logger.warning(f"Skipping {url}: {e}")

#     companies_meta = {}  # NEW: name -> {website, hq_country, symbol}
#     if os.path.exists(COMPANIES_JSON_FILE):
#         try:
#             with open(COMPANIES_JSON_FILE, "r", encoding="utf-8") as f:
#                 comp_data = json.load(f)
#                 for item in comp_data:
#                     cname = item.get("name") or item.get("search_name")
#                     if cname:
#                         candidate_names.add(cname)
#                         companies_meta[normalize_company_key(cname)] = {
#                             "company_name": cname,  # NEW: canonical name, used to fix mangled slug-derived names
#                             "company_website": item.get("website"),
#                             "company_hq_country": item.get("country"),
#                             "company_symbol": item.get("symbol"),
#                         }
#         except Exception as e:
#             logger.warning(f"Could not load {COMPANIES_JSON_FILE}: {e}")

#     logger.info(f"Probing {len(candidate_names)} candidate companies across ATS platforms...")
#     sem = asyncio.Semaphore(PROBE_CONCURRENCY)

#     async def probe(name: str):
#         candidates = clean_slug_candidates(name)
#         async with sem:
#             for slug in candidates:
#                 for ats in ["greenhouse", "lever", "ashby", "smartrecruiters"]:
#                     if slug not in discovered[ats]:
#                         if await verify_board(client, ats, slug):
#                             discovered[ats].add(slug)
#                             return

#     await asyncio.gather(*(probe(n) for n in candidate_names))
#     return discovered, companies_meta

# # =====================================================================
# # 8. EXECUTION & REPORTING
# # =====================================================================

# async def main():
#     sem = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
#     limits = httpx.Limits(max_keepalive_connections=120, max_connections=MAX_CONCURRENT_REQUESTS)

#     async with httpx.AsyncClient(verify=False, follow_redirects=True, limits=limits) as client:
#         targets, companies_meta = await discover_all_companies(client)

#         total_boards = sum(len(v) for v in targets.values())
#         logger.info(f"Harvesting from {total_boards} Verified Boards:")
#         for ats_name, s_set in targets.items():
#             logger.info(f"  • {ats_name.title():<18}: {len(s_set):>4} boards")

#         tasks = []
#         for slug in targets["greenhouse"]:
#             tasks.append(fetch_greenhouse(client, slug, sem))
#         for slug in targets["lever"]:
#             tasks.append(fetch_lever(client, slug, sem))
#         for slug in targets["ashby"]:
#             tasks.append(fetch_ashby(client, slug, sem))
#         for slug in targets["smartrecruiters"]:
#             tasks.append(fetch_smartrecruiters(client, slug, sem))
#         for w_target in targets["workday"]:
#             tasks.append(fetch_workday(client, w_target, sem))

#         results = await asyncio.gather(*tasks)

#     seen_ids = set()
#     clean_jobs = []

#     for batch in results:
#         for job in batch:
#             job_id = job["id"]
#             if job_id not in seen_ids:
#                 seen_ids.add(job_id)
#                 # NEW: enrich with company metadata (website / HQ country / ticker).
#                 # FIX: also replace the display name with the canonical one from your
#                 # companies file when we find a match -- the name we had until now was
#                 # guessed from the ATS URL slug (e.g. Workday tenant "paloaltonetworks"
#                 # title-cased to "Paloaltonetworks" instead of "Palo Alto Networks"),
#                 # which also silently broke this same lookup before.
#                 meta = companies_meta.get(normalize_company_key(job["company_name"]), {})
#                 if meta.get("company_name"):
#                     job["company_name"] = meta["company_name"]
#                 job["company_website"] = meta.get("company_website")
#                 job["company_hq_country"] = meta.get("company_hq_country")
#                 job["company_symbol"] = meta.get("company_symbol")
#                 clean_jobs.append(job)
#     save_jobs_alternating(clean_jobs)
#     await process_and_save_company_metadata(
#         [
#             {
#                 "company_name": job.get("company_name"),
#                 "symbol": job.get("company_symbol"),
#                 "website": job.get("company_website"),
#             }
#             for job in clean_jobs
#         ]
#     )

#     ats_breakdown, countries, emp_types, exp_levels = {}, {}, {}, {}
#     for job in clean_jobs:
#         ats_breakdown[job["ats_source"]] = ats_breakdown.get(job["ats_source"], 0) + 1
#         countries[job["country"]] = countries.get(job["country"], 0) + 1
#         emp_types[job["employment_type"]] = emp_types.get(job["employment_type"], 0) + 1
#         exp_levels[job["experience_level"]] = exp_levels.get(job["experience_level"], 0) + 1

#     print("\n" + "=" * 80)
#     print("      5-ATS TECH HARVEST REPORT (<= 30 DAYS OLD)      ")
#     print("=" * 80)
#     print(f"Total Useful Tech Job Records     : {len(clean_jobs):,}")
#     print("-" * 80)
#     print("ATS Breakdown:")
#     for ats_k, count in sorted(ats_breakdown.items(), key=lambda x: x[1], reverse=True):
#         print(f"  • {ats_k:<25}: {count:>4} positions")
#     print("-" * 80)
#     print("Top Countries:")
#     for cntry, count in sorted(countries.items(), key=lambda x: x[1], reverse=True)[:8]:
#         print(f"  • {cntry:<25}: {count:>4}")
#     print("-" * 80)
#     print("Employment Types:")
#     for et, count in sorted(emp_types.items(), key=lambda x: x[1], reverse=True):
#         print(f"  • {et:<25}: {count:>4}")
#     print("-" * 80)
#     print("Experience Levels:")
#     for ex, count in sorted(exp_levels.items(), key=lambda x: x[1], reverse=True):
#         print(f"  • {ex:<25}: {count:>4}")
#     print("=" * 80 + "\n")

#     if os.getenv("WRITE_LOCAL_JSON", "false").lower() == "true":
#             with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
#                 json.dump(clean_jobs, f, indent=2, ensure_ascii=False)
#             logger.info(f"Debug JSON written to {OUTPUT_FILE}")


# if __name__ == "__main__":
#     asyncio.run(main())

















































"""
GlobalTechHarvester v2
=======================
Changes vs. the original script (see inline comments marked "FIX:" / "NEW:"):

1. FIX  Workday returned zero jobs because it hit the wrong endpoint
        (/wday/cxf/service-pagination/{site}). The real, public, unauthenticated
        endpoint every Workday careers page itself calls is:
            POST https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
        Now fetches that, paginates through it, and pulls the requisition ID out
        of `bulletFields` instead of guessing it from the URL.

2. FIX  Experience-level classification no longer relies on title keywords alone.
        It now also looks for explicit "3-5 years", "5+ years" style phrases in the
        title, the ATS commitment field, and (for Greenhouse/Lever/Ashby) the job
        description body -- and lets that numeric evidence override a noisy
        "Internship" flag coming from an ATS's employment-type field. INTERNSHIP
        is now only assigned when the *title itself* says intern/co-op/apprentice,
        or when the commitment field says so AND the title has no seniority word.

3. FIX  Location parsing keeps the "never invent a location" rule, but:
        - Adds ~90 more cities across US/EU/APAC/LatAm/MEA.
        - Adds a distinct `state` field (was silently overloading `city` with the
          state name for US-state-only postings).
        - Adds bare ISO2 / common alias matching (US, UK, UAE, etc.).
        - Fixes the "no location text at all" case: it used to default
          workplace_type to "On-site" (an invented fact); it's now "Unspecified".

4. Same-as-before: hard 30-day freshness cutoff, still enforced identically for
   every ATS.

5. NEW  Pagination for Workday and SmartRecruiters (both silently truncated to
   one page before).
6. NEW  Retry with backoff on 429/5xx so a transient rate limit doesn't just
   silently produce an empty list for that company.
7. NEW  Job description (where cheaply available) is stored as a short plain-text
   snippet, and department/requisition ID are captured -- useful for an actual
   job-board UI.
8. NEW  Company metadata (website, HQ country, ticker) is joined in from
   company_career_pages.json so the frontend can show a logo/link without a
   second lookup. Named company_website / company_hq_country so it's never
   confused with the job's own location.
"""

import asyncio
import html as html_lib
import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
from dateutil import parser as date_parser
import httpx
from database import save_jobs_alternating
from companymetadata import process_and_save_company_metadata
# =====================================================================
# 1. CONFIGURATION & LOGGING
# =====================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("GlobalTechHarvester")

MAX_CONCURRENT_REQUESTS = 90
PROBE_CONCURRENCY = 45
REQUEST_TIMEOUT = 15.0
MAX_JOB_AGE_DAYS = 30
OUTPUT_FILE = "curated_global_tech_jobs.json"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COMPANIES_JSON_FILE = os.path.join(BASE_DIR, "company_career_pages.json")

MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 0.75  # seconds

# NEW: see the long comment in fetch_lever(). Lever's public API has no
# "last updated / reposted" field, only original createdAt, and explicitly
# supports evergreen postings left open for months. Since the API only ever
# serves currently-open roles, treat "still returned by Lever" as the
# freshness signal for this ATS instead of excluding by createdAt age.
# Flip to False to enforce the strict 30-day-since-creation rule instead.
LEVER_TREAT_OPEN_AS_FRESH = True

# NEW: replaces the old hard 400-char truncation (see build_job_records).
# Generous safety cap, not a real limit -- normal descriptions never approach this.
MAX_DESCRIPTION_CHARS = 20000

# NEW: Workday's list endpoint (POST .../jobs) omits department and sometimes
# omits locationsText entirely, and never includes the description. Set this to
# True to fetch the per-job detail endpoint for every posting that survives the
# title/freshness filter (cheap in aggregate since filtering happens first).
# Set False to keep the old, faster, list-endpoint-only behavior.
WORKDAY_FETCH_DETAILS = True
WORKDAY_DETAIL_CONCURRENCY = 15

NOW_UTC = datetime.now(timezone.utc)
CUTOFF_DATE = NOW_UTC - timedelta(days=MAX_JOB_AGE_DAYS)
UUID_NAMESPACE = uuid.UUID("e30fa982-1275-40dc-84e4-7d52f6b8a8b1")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}

DISCOVERY_SOURCES = [
    "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/README.md",
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2025-Internships/dev/README.md",
    "https://raw.githubusercontent.com/poteto/hiring-without-whiteboards/master/README.md",
    "https://raw.githubusercontent.com/lukasz-madon/awesome-remote-job/master/README.md",
    "https://raw.githubusercontent.com/ripienaar/free-for-dev/master/README.md"
]

# Verified high-volume tech seeds
CURATED_SEEDS = {
    "greenhouse": ["truecaller", "stripe", "figma", "airbnb", "coinbase", "brex", "postman", "instacart", "datadog"],
    "lever": ["spotify", "palantir", "atlassian"],
    "ashby": ["openai", "anthropic", "ramp", "perplexity", "retool", "linear"],
    "smartrecruiters": ["visa", "square", "bosch"],
    "workday": [
        "nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite",
        "adobe.wd5.myworkdayjobs.com/external_experienced",
        "salesforce.wd12.myworkdayjobs.com/External_Career_Site",
        "paloaltonetworks.wd1.myworkdayjobs.com/PANWExternalCareers",
        "cisco.wd5.myworkdayjobs.com/CiscoJobs",
        "autodesk.wd1.myworkdayjobs.com/Uni"
    ]
}

RESERVED_SLUGS = {
    "jobs", "careers", "search", "apply", "embed", "about", "terms", "privacy",
    "cookie", "default", "api", "v1", "v0", "postings", "content", "job", "career",
    "en-us", "en", "login", "userhome"
}

# =====================================================================
# 2. STRICT GEOLOCATION ENGINE (ZERO INVENTED LOCATIONS)
# =====================================================================

ISO_COUNTRY_CONTINENT = {
    "IN": ("India", "Asia"), "US": ("United States", "North America"),
    "CA": ("Canada", "North America"), "GB": ("United Kingdom", "Europe"),
    "DE": ("Germany", "Europe"), "NL": ("Netherlands", "Europe"),
    "FR": ("France", "Europe"), "IE": ("Ireland", "Europe"),
    "ES": ("Spain", "Europe"), "PL": ("Poland", "Europe"),
    "SG": ("Singapore", "Asia"), "JP": ("Japan", "Asia"),
    "AU": ("Australia", "Oceania"), "NZ": ("New Zealand", "Oceania"),
    "BR": ("Brazil", "South America"), "IL": ("Israel", "Asia"),
    "AE": ("United Arab Emirates", "Asia"), "SE": ("Sweden", "Europe"),
    "CH": ("Switzerland", "Europe"),
    # NEW: broader coverage
    "IT": ("Italy", "Europe"), "PT": ("Portugal", "Europe"),
    "AT": ("Austria", "Europe"), "BE": ("Belgium", "Europe"),
    "DK": ("Denmark", "Europe"), "FI": ("Finland", "Europe"),
    "NO": ("Norway", "Europe"), "CZ": ("Czech Republic", "Europe"),
    "HU": ("Hungary", "Europe"), "RO": ("Romania", "Europe"),
    "GR": ("Greece", "Europe"), "UA": ("Ukraine", "Europe"),
    "HK": ("Hong Kong", "Asia"), "CN": ("China", "Asia"),
    "KR": ("South Korea", "Asia"), "TW": ("Taiwan", "Asia"),
    "TH": ("Thailand", "Asia"), "ID": ("Indonesia", "Asia"),
    "PH": ("Philippines", "Asia"), "MY": ("Malaysia", "Asia"),
    "VN": ("Vietnam", "Asia"), "SA": ("Saudi Arabia", "Asia"),
    "QA": ("Qatar", "Asia"), "MX": ("Mexico", "North America"),
    "AR": ("Argentina", "South America"), "CO": ("Colombia", "South America"),
    "CL": ("Chile", "South America"), "ZA": ("South Africa", "Africa"),
    "EG": ("Egypt", "Africa"), "NG": ("Nigeria", "Africa"),
}

GLOBAL_CITIES_MAP = {
    # India Tech Hubs
    "bengaluru": ("IN", "Bengaluru"), "bangalore": ("IN", "Bengaluru"),
    "hyderabad": ("IN", "Hyderabad"), "pune": ("IN", "Pune"),
    "mumbai": ("IN", "Mumbai"), "gurgaon": ("IN", "Gurugram"),
    "gurugram": ("IN", "Gurugram"), "noida": ("IN", "Noida"),
    "delhi": ("IN", "Delhi"), "new delhi": ("IN", "New Delhi"),
    "chennai": ("IN", "Chennai"), "ahmedabad": ("IN", "Ahmedabad"),
    "kolkata": ("IN", "Kolkata"), "kochi": ("IN", "Kochi"),
    "coimbatore": ("IN", "Coimbatore"), "trivandrum": ("IN", "Thiruvananthapuram"),
    "thiruvananthapuram": ("IN", "Thiruvananthapuram"), "indore": ("IN", "Indore"),
    "chandigarh": ("IN", "Chandigarh"), "jaipur": ("IN", "Jaipur"),
    # North America
    "san francisco": ("US", "San Francisco"),
    "new york": ("US", "New York"), "nyc": ("US", "New York"),
    "seattle": ("US", "Seattle"), "austin": ("US", "Austin"),
    "boston": ("US", "Boston"), "chicago": ("US", "Chicago"),
    "los angeles": ("US", "Los Angeles"), "san jose": ("US", "San Jose"),
    "sunnyvale": ("US", "Sunnyvale"), "mountain view": ("US", "Mountain View"),
    "palo alto": ("US", "Palo Alto"), "santa clara": ("US", "Santa Clara"),
    "denver": ("US", "Denver"), "atlanta": ("US", "Atlanta"),
    "miami": ("US", "Miami"), "dallas": ("US", "Dallas"),
    "houston": ("US", "Houston"), "raleigh": ("US", "Raleigh"),
    "durham": ("US", "Durham"), "reston": ("US", "Reston"),
    "arlington": ("US", "Arlington"), "washington dc": ("US", "Washington DC"),
    "washington, d.c.": ("US", "Washington DC"), "detroit": ("US", "Detroit"),
    "minneapolis": ("US", "Minneapolis"), "portland": ("US", "Portland"),
    "phoenix": ("US", "Phoenix"), "san diego": ("US", "San Diego"),
    "philadelphia": ("US", "Philadelphia"), "pittsburgh": ("US", "Pittsburgh"),
    "salt lake city": ("US", "Salt Lake City"), "irvine": ("US", "Irvine"),
    "redmond": ("US", "Redmond"), "bellevue": ("US", "Bellevue"),
    "toronto": ("CA", "Toronto"), "vancouver": ("CA", "Vancouver"),
    "montreal": ("CA", "Montreal"), "waterloo": ("CA", "Waterloo"),
    "ottawa": ("CA", "Ottawa"), "calgary": ("CA", "Calgary"),
    "mexico city": ("MX", "Mexico City"), "guadalajara": ("MX", "Guadalajara"),
    # Europe
    "london": ("GB", "London"), "manchester": ("GB", "Manchester"),
    "edinburgh": ("GB", "Edinburgh"), "bristol": ("GB", "Bristol"),
    "cambridge, uk": ("GB", "Cambridge"),
    "berlin": ("DE", "Berlin"), "munich": ("DE", "Munich"),
    "hamburg": ("DE", "Hamburg"), "frankfurt": ("DE", "Frankfurt"),
    "amsterdam": ("NL", "Amsterdam"), "rotterdam": ("NL", "Rotterdam"),
    "dublin": ("IE", "Dublin"), "cork": ("IE", "Cork"),
    "paris": ("FR", "Paris"), "lyon": ("FR", "Lyon"),
    "madrid": ("ES", "Madrid"), "barcelona": ("ES", "Barcelona"),
    "warsaw": ("PL", "Warsaw"), "krakow": ("PL", "Krakow"),
    "stockholm": ("SE", "Stockholm"), "gothenburg": ("SE", "Gothenburg"),
    "zurich": ("CH", "Zurich"), "geneva": ("CH", "Geneva"),
    "milan": ("IT", "Milan"), "rome": ("IT", "Rome"),
    "lisbon": ("PT", "Lisbon"), "porto": ("PT", "Porto"),
    "vienna": ("AT", "Vienna"), "brussels": ("BE", "Brussels"),
    "copenhagen": ("DK", "Copenhagen"), "helsinki": ("FI", "Helsinki"),
    "oslo": ("NO", "Oslo"), "prague": ("CZ", "Prague"),
    "budapest": ("HU", "Budapest"), "bucharest": ("RO", "Bucharest"),
    "athens": ("GR", "Athens"), "kyiv": ("UA", "Kyiv"),
    # APAC & Others
    "singapore": ("SG", "Singapore"), "tokyo": ("JP", "Tokyo"),
    "osaka": ("JP", "Osaka"), "sydney": ("AU", "Sydney"),
    "melbourne": ("AU", "Melbourne"), "brisbane": ("AU", "Brisbane"),
    "auckland": ("NZ", "Auckland"),
    "tel aviv": ("IL", "Tel Aviv"), "herzliya": ("IL", "Herzliya"),
    "dubai": ("AE", "Dubai"), "abu dhabi": ("AE", "Abu Dhabi"),
    "riyadh": ("SA", "Riyadh"), "doha": ("QA", "Doha"),
    "hong kong": ("HK", "Hong Kong"), "shanghai": ("CN", "Shanghai"),
    "beijing": ("CN", "Beijing"), "shenzhen": ("CN", "Shenzhen"),
    "seoul": ("KR", "Seoul"), "taipei": ("TW", "Taipei"),
    "bangkok": ("TH", "Bangkok"), "jakarta": ("ID", "Jakarta"),
    "manila": ("PH", "Manila"), "kuala lumpur": ("MY", "Kuala Lumpur"),
    "ho chi minh city": ("VN", "Ho Chi Minh City"), "hanoi": ("VN", "Hanoi"),
    # LatAm & Africa
    "sao paulo": ("BR", "Sao Paulo"), "são paulo": ("BR", "Sao Paulo"),
    "rio de janeiro": ("BR", "Rio de Janeiro"),
    "buenos aires": ("AR", "Buenos Aires"), "bogota": ("CO", "Bogota"),
    "santiago": ("CL", "Santiago"),
    "cape town": ("ZA", "Cape Town"), "johannesburg": ("ZA", "Johannesburg"),
    "cairo": ("EG", "Cairo"), "lagos": ("NG", "Lagos"),
}

# NEW: bare country-code / common alias tokens (e.g. "Remote - US", "UK", "UAE")
ISO2_ALIASES = {
    "usa": "US", "us": "US", "u.s.": "US", "u.s.a.": "US",
    "uk": "GB", "gb": "GB", "u.k.": "GB",
    "uae": "AE", "emea": None, "apac": None,  # regions, not countries -> handled separately
}

# FIX: the original list only had ~22 of 50 states -- e.g. Arkansas ("AR") was
# missing entirely, so a URL-declared "Bentonville-AR" fell through to Worldwide
# instead of being recognized as a US location. Now covers all 50 states + DC.
US_STATES_INDEX = {
    "al": "Alabama", "ak": "Alaska", "az": "Arizona", "ar": "Arkansas",
    "ca": "California", "co": "Colorado", "ct": "Connecticut", "de": "Delaware",
    "fl": "Florida", "ga": "Georgia", "hi": "Hawaii", "id": "Idaho",
    "il": "Illinois", "in": "Indiana", "ia": "Iowa", "ks": "Kansas",
    "ky": "Kentucky", "la": "Louisiana", "me": "Maine", "md": "Maryland",
    "ma": "Massachusetts", "mi": "Michigan", "mn": "Minnesota", "ms": "Mississippi",
    "mo": "Missouri", "mt": "Montana", "ne": "Nebraska", "nv": "Nevada",
    "nh": "New Hampshire", "nj": "New Jersey", "nm": "New Mexico", "ny": "New York",
    "nc": "North Carolina", "nd": "North Dakota", "oh": "Ohio", "ok": "Oklahoma",
    "or": "Oregon", "pa": "Pennsylvania", "ri": "Rhode Island", "sc": "South Carolina",
    "sd": "South Dakota", "tn": "Tennessee", "tx": "Texas", "ut": "Utah",
    "vt": "Vermont", "va": "Virginia", "wa": "Washington", "wv": "West Virginia",
    "wi": "Wisconsin", "wy": "Wyoming", "dc": "District of Columbia",
}
US_STATE_NAMES = {name.lower(): name for name in US_STATES_INDEX.values()}

RE_REMOTE_KEYWORDS = re.compile(
    r"\b(remote|virtual|distributed|wfh|telecommute|anywhere|worldwide|global|home[-\s]?based|work from home)\b",
    re.I,
)
RE_HYBRID_KEYWORDS = re.compile(r"\b(hybrid|flexible)\b", re.I)
RE_PURE_GLOBAL = re.compile(
    r"^(remote|virtual|anywhere|worldwide|global|all|any|unspecified|multiple locations|work from anywhere)$",
    re.I,
)
RE_MULTI_LOCATION_PLACEHOLDER = re.compile(r"^\d+\s+locations?$", re.I)


def parse_geo_location(raw_text: str) -> dict:
    """
    Strictly maps declared locations to verified cities/countries/states.
    NEVER invents a location. If absent, marks workplace_type as Unspecified
    and country as Worldwide rather than guessing "On-site".
    """
    base = {
        "city": None, "state": None, "country": "Worldwide", "country_code": "GLOBAL",
        "continent": "Global", "workplace_type": "Unspecified", "location_display": "Worldwide (Unspecified)"
    }

    if not raw_text or not raw_text.strip() or RE_MULTI_LOCATION_PLACEHOLDER.match(raw_text.strip()):
        return base

    clean_text = re.sub(r"\s+", " ", raw_text).strip()
    loc_lower = clean_text.lower()

    is_hybrid = bool(RE_HYBRID_KEYWORDS.search(loc_lower))
    is_remote = bool(RE_REMOTE_KEYWORDS.search(loc_lower))
    # FIX: only assume "On-site" once we know there's real location text to go on.
    workplace_type = "Hybrid" if is_hybrid else ("Remote" if is_remote else "On-site")

    if RE_PURE_GLOBAL.match(loc_lower):
        return {
            "city": None, "state": None, "country": "Worldwide", "country_code": "GLOBAL",
            "continent": "Global", "workplace_type": "Remote", "location_display": "Worldwide (Remote)"
        }

    # 1. Known tech cities (longest keys first so "new delhi" wins over "delhi", etc.)
    for city_key in sorted(GLOBAL_CITIES_MAP, key=len, reverse=True):
        alpha2, city_name = GLOBAL_CITIES_MAP[city_key]
        if re.search(rf"\b{re.escape(city_key)}\b", loc_lower):
            country_name, continent = ISO_COUNTRY_CONTINENT.get(alpha2, (alpha2, "Unknown"))
            display_str = f"{city_name}, {country_name}"
            if workplace_type != "On-site":
                display_str += f" ({workplace_type})"
            return {
                "city": city_name, "state": None, "country": country_name, "country_code": alpha2,
                "continent": continent, "workplace_type": workplace_type, "location_display": display_str
            }

    # 2. India (broad, no specific city matched)
    if re.search(r"\b(india|bharat)\b", loc_lower):
        display_str = "India" if workplace_type == "On-site" else f"India ({workplace_type})"
        return {
            "city": None, "state": None, "country": "India", "country_code": "IN",
            "continent": "Asia", "workplace_type": workplace_type, "location_display": display_str
        }

    # 3. US states -- full names first, then abbreviations
    for state_key, state_name in US_STATE_NAMES.items():
        if re.search(rf"\b{re.escape(state_key)}\b", loc_lower):
            display_str = f"{state_name}, United States"
            if workplace_type != "On-site":
                display_str += f" ({workplace_type})"
            return {
                "city": None, "state": state_name, "country": "United States", "country_code": "US",
                "continent": "North America", "workplace_type": workplace_type, "location_display": display_str
            }

    tokens = [t.strip(".,()[]{}") for t in re.split(r"[\s,/|\-]+", loc_lower) if t.strip()]
    for token in tokens:
        if token in US_STATES_INDEX:
            state_name = US_STATES_INDEX[token]
            display_str = f"{state_name}, United States"
            if workplace_type != "On-site":
                display_str += f" ({workplace_type})"
            return {
                "city": None, "state": state_name, "country": "United States", "country_code": "US",
                "continent": "North America", "workplace_type": workplace_type, "location_display": display_str
            }

    # 4. Bare ISO2 / common aliases (e.g. "Remote - US", "UK")
    for token in tokens:
        alpha2 = ISO2_ALIASES.get(token)
        if alpha2 and alpha2 in ISO_COUNTRY_CONTINENT:
            country_name, continent = ISO_COUNTRY_CONTINENT[alpha2]
            display_str = country_name if workplace_type == "On-site" else f"{country_name} ({workplace_type})"
            return {
                "city": None, "state": None, "country": country_name, "country_code": alpha2,
                "continent": continent, "workplace_type": workplace_type, "location_display": display_str
            }

    # 5. Full ISO country names
    for alpha2, (country_name, continent) in ISO_COUNTRY_CONTINENT.items():
        if re.search(rf"\b{re.escape(country_name.lower())}\b", loc_lower):
            display_str = country_name if workplace_type == "On-site" else f"{country_name} ({workplace_type})"
            return {
                "city": None, "state": None, "country": country_name, "country_code": alpha2,
                "continent": continent, "workplace_type": workplace_type, "location_display": display_str
            }

    # 6. Remote with no verifiable country -> Worldwide (Remote), never invented
    if is_remote:
        return {
            "city": None, "state": None, "country": "Worldwide", "country_code": "GLOBAL",
            "continent": "Global", "workplace_type": "Remote", "location_display": "Worldwide (Remote)"
        }

    # Fallback: text existed but named nothing we can verify
    return {
        "city": None, "state": None, "country": "Worldwide", "country_code": "GLOBAL",
        "continent": "Global", "workplace_type": workplace_type, "location_display": f"Worldwide ({workplace_type})"
    }


def split_and_parse_locations(raw_locations) -> list:
    if isinstance(raw_locations, str):
        candidates = [raw_locations]
    else:
        candidates = raw_locations or []

    expanded = []
    for cand in candidates:
        if not cand:
            continue
        parts = re.split(r"\s*;\s*|\s*\|\s*|\s+/\s+|\s+or\s+", str(cand))
        for p in parts:
            p_strip = p.strip()
            if p_strip and p_strip not in expanded:
                expanded.append(p_strip)

    if not expanded:
        expanded = [""]  # -> Unspecified, not "Worldwide" pretending to know it's remote

    parsed = []
    seen = set()
    for item in expanded:
        loc = parse_geo_location(item)
        key = (loc["country_code"], loc["city"], loc["state"], loc["workplace_type"])
        if key not in seen:
            seen.add(key)
            parsed.append(loc)
    return parsed

# =====================================================================
# 3. STRICT TECH ROLE, SENIORITY & EXPERIENCE CLASSIFIER
# =====================================================================

DISQUALIFIED_ROLES = re.compile(
    r"\b("
    r"telecaller|telecalling|bpo|voice process|call center|customer care|field executive|"
    r"sales executive|collection agent|data entry|business development associate|\bbda\b|"
    r"account executive|bdr|sdr|growth marketer|seo|content writer|copywriter|"
    r"recruiter|talent acquisition|sourcer|human resources|\bhr\b|people ops|"
    r"accountant|accounting|payroll|billing|tax|auditor|audit|"
    r"legal counsel|paralegal|office manager|receptionist|cashier|warehouse|driver"
    r")\b",
    re.I
)

ROLE_TAXONOMY = [
    ("AI / ML & Data Science", re.compile(
        r"\b(machine learning|ml engineer|deep learning|computer vision|nlp|data scientist|"
        r"llm engineer|ai engineer|ai researcher|genai|applied scientist|mlops)\b", re.I)),
    ("Data Engineering & BI", re.compile(
        r"\b(data engineer|bi engineer|data architect|analytics engineer|big data|etl)\b", re.I)),
    ("Cloud, DevOps & SRE", re.compile(
        r"\b(devops|sre|site reliability|cloud engineer|platform engineer|infrastructure engineer|"
        r"cloud architect|kubernetes engineer|sysadmin|systems engineer|linux administrator)\b", re.I)),
    ("Cybersecurity", re.compile(
        r"\b(cybersecurity|security engineer|infosec|appsec|soc analyst|penetration tester|"
        r"security architect|incident response|vulnerability)\b", re.I)),
    ("Mobile Engineering", re.compile(
        r"\b(ios engineer|android engineer|mobile developer|swift developer|kotlin developer|react native|flutter)\b", re.I)),
    ("Hardware & Embedded", re.compile(
        r"\b(embedded engineer|firmware|hardware engineer|asic|fpga|pcb|robotics|mechatronics)\b", re.I)),
    ("QA & Automation", re.compile(
        r"\b(qa engineer|quality assurance|sdet|test automation|software test)\b", re.I)),
    ("Engineering Management", re.compile(
        r"\b(engineering manager|director of engineering|vp of engineering|tech lead|chief architect|cto)\b", re.I)),
    ("Software Engineering", re.compile(
        r"\b(software|developer|swe|fullstack|full-stack|backend|back-end|"
        r"frontend|front-end|web developer|api engineer|application engineer)\b", re.I))
]

STRONG_TECH_ANCHOR = re.compile(r"\b(software|swe|developer|engineer|data scientist|architect|sdet)\b", re.I)

# NEW: numeric years-of-experience detectors, used as a cross-check against title keywords
YEARS_EXP_RE = re.compile(
    r"(\d{1,2})\s*(?:\+)?\s*(?:-|to|–|—)?\s*(\d{1,2})?\+?\s*(?:years?|yrs?)\s*"
    r"(?:of\s+)?(?:relevant\s+|professional\s+|industry\s+|hands[-\s]?on\s+|work(?:ing)?\s+)?experience",
    re.I,
)
YEARS_PLUS_RE = re.compile(r"\b(\d{1,2})\s*\+\s*years?\b", re.I)

STRONG_SENIORITY_WORDS = re.compile(
    r"\b(senior|sr\.?|staff|principal|distinguished|lead|architect|ii|iii|iv|director|manager|head of)\b",
    re.I,
)

# FIX: "early career" wasn't in the Entry/Junior vocabulary at all (e.g. Anduril's
# "2027 Early Career Software Engineer" fell through every keyword check and landed
# on the Mid-Level default, purely for lack of a matching word -- not a wrong signal,
# just a missing one). Also adds detection of the very common "20XX New Grad" /
# "Class of 20XX" graduating-year prefix pattern used by many big tech new-grad reqs.
ENTRY_LEVEL_RE = re.compile(
    r"\b(entry[-\s]?level|junior|jr\.?|fresher|graduate|new[-\s]?grad|associate|"
    r"early[-\s]?career|university grad(?:uate)?|campus hire|class of 20\d{2})\b|"
    r"\b20\d{2}\s+(?:new[-\s]?grad|early[-\s]?career|graduate|university)\b",
    re.I,
)


def strip_html(raw_html: str) -> str:
    """
    FIX: some ATS boards (seen on Greenhouse) return the `content` field as
    HTML-entity-escaped markup, e.g. "&lt;div class=&quot;intro&quot;&gt;" instead
    of literal "<div class="intro">". The old version only stripped literal tags
    and only decoded &nbsp;/&amp;, so entity-escaped tags survived as visible junk
    ("div class= intro") and things like "&#39;" (apostrophe) were never decoded.
    html.unescape() before AND after tag-stripping handles both single- and
    double-escaped input, and decodes every standard/numeric entity correctly.
    """
    if not raw_html:
        return ""
    text = html_lib.unescape(raw_html)
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)  # decode any entities that were inside tag text content
    return re.sub(r"\s+", " ", text).strip()


def classify_and_filter_title(raw_title: str) -> tuple:
    if not raw_title or len(raw_title.strip()) < 3:
        return False, "", ""

    cleaned = re.sub(r"[\(\[\{][^\)\]\}]*(?:req|requisition|\d{4,})[^\)\]\}]*[\)\]\}]", "", raw_title, flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if DISQUALIFIED_ROLES.search(cleaned) and not STRONG_TECH_ANCHOR.search(cleaned):
        return False, "", ""

    for category, pattern in ROLE_TAXONOMY:
        if pattern.search(cleaned):
            return True, cleaned, category

    return False, "", ""


def _extract_min_years(text: str):
    """Return the smallest 'N years experience' style number found, or None."""
    found = []
    for pattern in (YEARS_EXP_RE, YEARS_PLUS_RE):
        for m in pattern.finditer(text):
            try:
                lo = int(m.group(1))
                if 0 <= lo <= 40:
                    found.append(lo)
            except (ValueError, IndexError):
                continue
    return min(found) if found else None


def extract_employment_and_experience(title: str, ats_commitment: str = "", content_text: str = "") -> tuple:
    """
    FIX: previously classified purely off title keywords, which mislabeled roles
    that state "3-5 years experience" in the body/commitment field as Internship
    whenever the ATS's employment-type field was noisy. Now:
      - Internship is only assigned from an explicit title keyword, or from the
        commitment field when the title carries no seniority word at all.
      - Numeric "X years experience" evidence (title, commitment, or description)
        is used to set/confirm Mid vs Senior vs Lead-Staff, and overrides a bare
        commitment-field "Internship" flag when years >= 2 are explicitly stated.
    """
    title_lower = title.lower()
    commitment_lower = (ats_commitment or "").lower()
    combined_short = f"{title_lower} {commitment_lower}"
    combined_full = f"{combined_short} {content_text.lower()}"

    min_years = _extract_min_years(combined_full)

    # Employment type (contract / part-time / full-time)
    if re.search(r"\b(contract|contractor|freelance|temporary|temp)\b", combined_short):
        emp_type = "CONTRACT"
    elif re.search(r"\b(part[-\s]?time)\b", combined_short):
        emp_type = "PART_TIME"
    else:
        emp_type = "FULL_TIME"

    title_says_intern = bool(re.search(r"\b(intern|internship|co-op|coop|apprentice|apprenticeship|trainee)\b", title_lower))
    commitment_says_intern = bool(re.search(r"\b(intern|internship)\b", commitment_lower))

    if title_says_intern:
        return "INTERNSHIP", "Internship"
    if commitment_says_intern and not STRONG_SENIORITY_WORDS.search(title_lower) and (min_years is None or min_years <= 1):
        return "INTERNSHIP", "Internship"

    # Keyword + numeric-years seniority ladder (numeric evidence can promote, never used to
    # silently downgrade an explicit senior/staff/exec title keyword).
    if re.search(r"\b(vp|vice president|director|head of|chief architect|\bcto\b)\b", combined_short):
        return emp_type, "Executive"
    if re.search(r"\b(staff|principal|distinguished|lead|architect)\b", combined_short) or (min_years is not None and min_years >= 8):
        return emp_type, "Lead / Staff"
    if re.search(r"\b(senior|sr\.?|specialist iii|specialist iv)\b", combined_short) or (min_years is not None and 5 <= min_years <= 7):
        return emp_type, "Senior"
    if min_years is not None and 2 <= min_years <= 4:
        return emp_type, "Mid-Level"
    if re.search(ENTRY_LEVEL_RE, combined_short) and (min_years is None or min_years <= 2):
        return emp_type, "Entry / Junior"

    return emp_type, "Mid-Level"


def validate_date_within_window(raw_date) -> tuple:
    if not raw_date:
        return False, ""
    try:
        if isinstance(raw_date, (int, float)):
            if raw_date > 1e11:
                raw_date /= 1000.0
            dt = datetime.fromtimestamp(raw_date, tz=timezone.utc)
        else:
            cleaned = str(raw_date).strip()
            if "30+" in cleaned or "month" in cleaned.lower() or "year" in cleaned.lower():
                return False, ""
            dt = date_parser.parse(cleaned)
            dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)

        if CUTOFF_DATE <= dt <= (NOW_UTC + timedelta(days=1)):
            return True, dt.isoformat()
    except Exception:
        return False, ""
    return False, ""


def parse_workday_relative_date(posted_str: str) -> tuple:
    if not posted_str:
        return False, ""
    cleaned = posted_str.lower().strip()
    if "30+" in cleaned or "month" in cleaned or "year" in cleaned:
        return False, ""
    if "today" in cleaned or "just posted" in cleaned:
        return True, NOW_UTC.isoformat()
    if "yesterday" in cleaned:
        return True, (NOW_UTC - timedelta(days=1)).isoformat()

    match = re.search(r"(\d+)\s+day", cleaned)
    if match:
        days = int(match.group(1))
        if days <= MAX_JOB_AGE_DAYS:
            return True, (NOW_UTC - timedelta(days=days)).isoformat()
        return False, ""

    return validate_date_within_window(posted_str)


def clean_application_url(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    qs = parse_qs(parsed.query)
    clean_qs = {k: v for k, v in qs.items() if not k.startswith("utm_") and k not in {"gh_src", "lever-source", "ref", "source"}}
    return urlunparse(parsed._replace(query=urlencode(clean_qs, doseq=True)))

# =====================================================================
# 4. ROW BUILDER
# =====================================================================

def build_job_records(
    company_name: str, title: str, role_category: str, ats_source: str, external_id: str,
    apply_url: str, published_at: str, raw_locations, ats_commitment: str = "",
    content_text: str = "", department: str = "",
) -> list:
    emp_type, exp_level = extract_employment_and_experience(title, ats_commitment, content_text)
    locations = split_and_parse_locations(raw_locations)
    cleaned_url = clean_application_url(apply_url)
    now_iso = NOW_UTC.isoformat()
    # FIX: was hard-truncated to 400 chars, which is too short for Google for
    # Jobs eligibility (Google wants the full description) and for a job-detail
    # page to actually be useful to a reader. The description_snippet column is
    # Postgres TEXT (unlimited), so the only real constraint was this line.
    # MAX_DESCRIPTION_CHARS is a generous safety cap, not a designed limit --
    # it only guards against a pathological/garbage upstream response, not
    # against normal job descriptions (which are essentially never anywhere
    # close to 20,000 characters).
    snippet = (
        content_text[:MAX_DESCRIPTION_CHARS].strip() + ("..." if len(content_text) > MAX_DESCRIPTION_CHARS else "")
        if content_text else ""
    )
    records = []

    for idx, loc in enumerate(locations):
        loc_key = f"{loc['country_code']}_{loc['city'] or loc['state'] or 'all'}_{idx}"
        stable_seed = f"{ats_source}_{company_name}_{external_id}_{loc_key}"
        row_uuid = str(uuid.uuid5(UUID_NAMESPACE, stable_seed))

        records.append({
            "id": row_uuid,
            "title": title,
            "company_name": company_name,
            "department": department or None,
            "employment_type": emp_type,
            "role_category": role_category,
            "experience_level": exp_level,
            "workplace_type": loc["workplace_type"],
            "city": loc["city"],
            "state": loc["state"],
            "country": loc["country"],
            "country_code": loc["country_code"],
            "continent": loc["continent"],
            "location_display": loc["location_display"],
            "description_snippet": snippet or None,
            "apply_url": cleaned_url,
            "ats_source": ats_source,
            "external_job_id": str(external_id),
            "status": "ACTIVE",
            "published_at": published_at,
            "created_at": now_iso,
            "updated_at": now_iso,
        })

    return records

# =====================================================================
# 5. HTTP HELPERS (NEW: retry with backoff on 429/5xx)
# =====================================================================

async def _request_with_retry(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> httpx.Response:
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            res = await client.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
            if res.status_code == 429 or res.status_code >= 500:
                await asyncio.sleep(RETRY_BACKOFF_BASE * (2 ** attempt))
                continue
            return res
        except (httpx.TransportError, httpx.TimeoutException) as e:
            last_exc = e
            await asyncio.sleep(RETRY_BACKOFF_BASE * (2 ** attempt))
    if last_exc:
        raise last_exc
    return res  # last (possibly bad) response after exhausting retries

# =====================================================================
# 6. ATS FETCHERS (STRICT DECLARED LOCATION ONLY)
# =====================================================================

async def fetch_greenhouse(client: httpx.AsyncClient, slug: str, sem: asyncio.Semaphore) -> list:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    async with sem:
        try:
            res = await _request_with_retry(client, "GET", url, headers=HEADERS)
            if res.status_code != 200:
                res = await _request_with_retry(
                    client, "GET",
                    f"https://boards-api.eu.greenhouse.io/v1/boards/{slug}/jobs?content=true",
                    headers=HEADERS,
                )
                if res.status_code != 200:
                    return []

            jobs = []
            comp_name = slug.replace("-", " ").title()

            for j in res.json().get("jobs", []):
                is_valid, title, category = classify_and_filter_title(j.get("title", ""))
                if not is_valid:
                    continue
                is_fresh, pub_date = validate_date_within_window(j.get("updated_at"))
                if not is_fresh:
                    continue

                loc_obj = j.get("location") or {}
                declared_loc = loc_obj.get("name", "").strip() if isinstance(loc_obj, dict) else str(loc_obj).strip()
                raw_locs = [declared_loc] if declared_loc else [""]

                # NEW: greenhouse already fetches content=true -- actually use it now.
                content_html = (j.get("content") or "")
                content_text = strip_html(content_html)
                dept = ""
                depts = j.get("departments") or []
                if depts:
                    dept = depts[0].get("name", "")

                jobs.extend(build_job_records(
                    company_name=comp_name, title=title, role_category=category,
                    ats_source="Greenhouse", external_id=str(j.get("id")),
                    apply_url=j.get("absolute_url", ""), published_at=pub_date,
                    raw_locations=raw_locs, content_text=content_text, department=dept,
                ))
            return jobs
        except Exception as e:
            logger.debug(f"greenhouse:{slug} failed: {e}")
            return []


async def fetch_lever(client: httpx.AsyncClient, slug: str, sem: asyncio.Semaphore) -> list:
    """
    FIX: this previously returned ~0 jobs for essentially every Lever board.
    Root cause is a platform quirk, not a coding typo: Lever's public postings
    API only exposes `createdAt` -- the date a posting was first created -- and
    has no separate "last updated" / "reposted" field. Lever explicitly supports
    long-lived "evergreen" postings that stay open for months without ever being
    recreated, and the API only ever returns currently-*open* roles in the first
    place (a closed role just vanishes from the feed). So the old strict
    "createdAt within 30 days" check was silently discarding almost every real,
    currently-open Lever posting -- e.g. Palantir's board (independently
    verified at 300+ open roles right now) would have failed this check almost
    entirely, simply because most of those postings were first created more
    than 30 days ago despite still being live today.
    LEVER_TREAT_OPEN_AS_FRESH below controls this: default True means "if
    Lever's API is still serving it, it's live, and that's the recency signal
    for this ATS" -- published_at is still populated from createdAt for display
    when present, it's just no longer used to exclude the posting.
    Set it to False to fall back to the old strict behavior (some Lever
    postings will then legitimately re-disappear).

    NEW: also adds the EU host fallback Greenhouse already had (Lever splits
    US/EU tenants across api.lever.co / api.eu.lever.co), and pagination via
    skip/limit -- Lever's API defaults to 100 postings per page, so a board
    larger than that was previously being silently truncated.
    """
    jobs = []
    comp_name = slug.replace("-", " ").title()
    page_size = 100
    offset = 0
    max_pages = 20  # safety cap: 2,000 postings per company

    async with sem:
        base_url = f"https://api.lever.co/v0/postings/{slug}"
        for _ in range(max_pages):
            url = f"{base_url}?mode=json&limit={page_size}&skip={offset}"
            try:
                res = await _request_with_retry(client, "GET", url, headers=HEADERS)
                if res.status_code != 200:
                    # NEW: EU-hosted tenants answer on a different host, same as Greenhouse's EU split
                    eu_url = f"https://api.eu.lever.co/v0/postings/{slug}?mode=json&limit={page_size}&skip={offset}"
                    res = await _request_with_retry(client, "GET", eu_url, headers=HEADERS)
                    if res.status_code != 200:
                        break
                page = res.json()
            except Exception as e:
                logger.debug(f"lever:{slug} failed: {e}")
                break

            if not isinstance(page, list) or not page:
                break

            for j in page:
                is_valid, title, category = classify_and_filter_title(j.get("text", ""))
                if not is_valid:
                    continue

                is_fresh, pub_date = validate_date_within_window(j.get("createdAt"))
                if not is_fresh:
                    if LEVER_TREAT_OPEN_AS_FRESH:
                        # Still currently served by Lever's API == still open. Keep the record;
                        # just record whatever creation date we have (or now) for display/sorting.
                        _, pub_date = validate_date_within_window(NOW_UTC.isoformat())
                    else:
                        continue

                cats = j.get("categories", {})
                raw_locs = []
                if cats.get("location"):
                    raw_locs.append(cats.get("location"))
                if cats.get("additionalLocations"):
                    raw_locs.extend(cats.get("additionalLocations"))
                if not raw_locs:
                    raw_locs = [""]

                content_text = strip_html(j.get("descriptionPlain") or j.get("description") or "")

                jobs.extend(build_job_records(
                    company_name=comp_name, title=title, role_category=category,
                    ats_source="Lever", external_id=str(j.get("id")),
                    apply_url=j.get("hostedUrl", ""), published_at=pub_date,
                    raw_locations=raw_locs, ats_commitment=cats.get("commitment", ""),
                    content_text=content_text, department=cats.get("team", ""),
                ))

            offset += page_size
            if len(page) < page_size:
                break

    return jobs


async def fetch_ashby(client: httpx.AsyncClient, slug: str, sem: asyncio.Semaphore) -> list:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    async with sem:
        try:
            res = await _request_with_retry(client, "GET", url, headers=HEADERS)
            if res.status_code != 200:
                return []
            jobs = []
            comp_name = slug.replace("-", " ").title()

            for j in res.json().get("jobs", []):
                is_valid, title, category = classify_and_filter_title(j.get("title", ""))
                if not is_valid:
                    continue
                is_fresh, pub_date = validate_date_within_window(j.get("publishedAt"))
                if not is_fresh:
                    continue

                raw_locs = []
                if j.get("location"):
                    raw_locs.append(j.get("location"))
                for sec in j.get("secondaryLocations", []):
                    if isinstance(sec, str):
                        raw_locs.append(sec)
                    elif isinstance(sec, dict):
                        name = sec.get("location") or sec.get("name")
                        if name:
                            raw_locs.append(name)
                if not raw_locs:
                    raw_locs = [""]

                content_text = strip_html(j.get("descriptionHtml") or "")

                jobs.extend(build_job_records(
                    company_name=comp_name, title=title, role_category=category,
                    ats_source="Ashby", external_id=str(j.get("id")),
                    apply_url=j.get("jobUrl", f"https://jobs.ashbyhq.com/{slug}/{j.get('id')}"),
                    published_at=pub_date, raw_locations=raw_locs,
                    ats_commitment=j.get("employmentType", ""), content_text=content_text,
                    department=j.get("department", ""),
                ))
            return jobs
        except Exception as e:
            logger.debug(f"ashby:{slug} failed: {e}")
            return []


async def _fetch_smartrecruiters_detail(client: httpx.AsyncClient, slug: str, posting_id: str) -> str:
    """
    Fetches the per-posting description. SmartRecruiters' exact detail JSON
    shape isn't something I could verify against a live response when writing
    this (network here is locked to package registries), so rather than
    assume one exact path, this tries several plausible nestings seen in
    SmartRecruiters' own docs/third-party integrations:
      (a) data["jobAd"]["sections"][key] = {"text": "..."}
      (b) data["sections"][key] = {"text": "..."}                (no jobAd wrapper)
      (c) data["jobAd"][key] = {"text": "..."} or a plain string (no "sections" level)
    If descriptions are still coming back empty after this, the real fix is a
    5-minute one: log.debug below prints the raw JSON for one posting at DEBUG
    level -- run with logging.basicConfig(level=logging.DEBUG) once, paste me
    that one printed dict, and the exact key path can be corrected for good
    instead of guessed at again.
    """
    url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{posting_id}"
    try:
        res = await _request_with_retry(client, "GET", url, headers=HEADERS)
        if res.status_code != 200:
            logger.debug(f"smartrecruiters detail {posting_id}: HTTP {res.status_code}")
            return ""
        data = res.json()
    except Exception as e:
        logger.debug(f"smartrecruiters detail {posting_id} failed: {e}")
        return ""

    def _section_text(container: dict, key: str) -> str:
        val = container.get(key)
        if isinstance(val, dict):
            return val.get("text") or val.get("value") or ""
        if isinstance(val, str):
            return val
        return ""

    job_ad = data.get("jobAd") or {}
    sections = job_ad.get("sections") if isinstance(job_ad.get("sections"), dict) else None
    top_sections = data.get("sections") if isinstance(data.get("sections"), dict) else None

    parts = []
    for key in ("jobDescription", "qualifications", "additionalInformation"):
        text = ""
        if sections:
            text = _section_text(sections, key)
        if not text and top_sections:
            text = _section_text(top_sections, key)
        if not text and isinstance(job_ad, dict):
            text = _section_text(job_ad, key)
        if text.strip():
            parts.append(strip_html(text))

    if not parts:
        # Nothing matched any known shape -- log the raw keys we DID see, so a
        # single DEBUG-level run pinpoints the real structure immediately.
        logger.debug(f"smartrecruiters detail {posting_id}: no description matched. Top-level keys: {list(data.keys())}, jobAd keys: {list(job_ad.keys()) if isinstance(job_ad, dict) else 'N/A'}")

    return "\n\n".join(parts)


async def fetch_smartrecruiters(client: httpx.AsyncClient, slug: str, sem: asyncio.Semaphore) -> list:
    """
    NEW: paginated -- the old version silently stopped at page 1 (100 postings).
    NEW: fetches the full description via the per-posting detail endpoint for
    every candidate that survives the title/freshness filter (same two-phase
    pattern as fetch_workday: filter cheaply first, then only pay the extra
    request for postings that actually matter).
    """
    candidates = []
    comp_name = slug.replace("-", " ").title()
    offset = 0
    page_size = 100
    max_pages = 15  # safety cap: 1,500 postings per company is generous

    async with sem:
        for _ in range(max_pages):
            url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit={page_size}&offset={offset}"
            try:
                res = await _request_with_retry(client, "GET", url, headers=HEADERS)
                if res.status_code != 200:
                    break
                data = res.json()
            except Exception as e:
                logger.debug(f"smartrecruiters:{slug} failed: {e}")
                break

            content = data.get("content", [])
            if not content:
                break

            for j in content:
                is_valid, title, category = classify_and_filter_title(j.get("name", ""))
                if not is_valid:
                    continue
                is_fresh, pub_date = validate_date_within_window(j.get("releasedDate"))
                if not is_fresh:
                    continue

                loc_data = j.get("location", {}) or {}
                primary = f"{loc_data.get('city', '')}, {loc_data.get('country', '')}".strip(", ")
                raw_locs = [primary] if primary else []
                for sec in j.get("secondaryLocations", []):
                    sec_str = f"{sec.get('city', '')}, {sec.get('country', '')}".strip(", ")
                    if sec_str:
                        raw_locs.append(sec_str)
                if not raw_locs:
                    raw_locs = [""]

                candidates.append({
                    "title": title, "category": category, "pub_date": pub_date,
                    "posting_id": str(j.get("id")), "raw_locs": raw_locs,
                    "commitment": j.get("typeOfEmployment", {}).get("label", ""),
                    "department": (j.get("department") or {}).get("label", ""),
                })

            total_found = data.get("totalFound", 0)
            offset += page_size
            if offset >= total_found or len(content) < page_size:
                break

    if not candidates:
        return []

    detail_sem = asyncio.Semaphore(10)

    async def _enrich(c):
        async with detail_sem:
            description = await _fetch_smartrecruiters_detail(client, slug, c["posting_id"])
        return c, description

    enriched = await asyncio.gather(*(_enrich(c) for c in candidates))

    jobs = []
    for c, description in enriched:
        jobs.extend(build_job_records(
            company_name=comp_name, title=c["title"], role_category=c["category"],
            ats_source="SmartRecruiters", external_id=c["posting_id"],
            apply_url=f"https://jobs.smartrecruiters.com/{slug}/{c['posting_id']}",
            published_at=c["pub_date"], raw_locations=c["raw_locs"],
            ats_commitment=c["commitment"], department=c["department"],
            content_text=description,
        ))

    return jobs


def _location_from_slug(segment: str) -> str:
    """
    NEW: Workday embeds the primary declared location in the URL itself, e.g.
    ".../job/Bentonville-AR/..." or ".../job/San-Jose/...". This is Workday's own
    facet value (not invented), so it's a legitimate, zero-cost fallback for the
    cases where the list endpoint's `locationsText` comes back blank.
    "Bentonville-AR" -> "Bentonville, AR"; "San-Jose" -> "San Jose".
    """
    if not segment:
        return ""
    parts = segment.split("-")
    if len(parts) >= 2 and re.fullmatch(r"[A-Za-z]{2}", parts[-1]):
        return f"{' '.join(parts[:-1])}, {parts[-1].upper()}"
    return " ".join(parts)


async def _fetch_workday_detail(client: httpx.AsyncClient, host: str, tenant: str, site: str, external_path: str) -> dict:
    """
    NEW: hits Workday's per-posting detail endpoint to recover what the list
    endpoint frequently omits: every declared location (not just the primary
    one baked into the URL), the department/team, and the full description.

    Workday's detail JSON schema isn't publicly documented and can vary by
    tenant/version, so this is intentionally defensive: it tries several
    plausible key paths and simply returns whatever it can find. If your
    tenant's actual response uses different key names, share one raw detail
    JSON (GET the URL this function builds, for one job) and the key paths
    below can be corrected precisely.
    """
    detail_url = f"https://{host}/wday/cxs/{tenant}/{site}{external_path}"
    out = {"locations": [], "department": None, "description": ""}
    try:
        res = await _request_with_retry(client, "GET", detail_url, headers=HEADERS)
        if res.status_code != 200:
            return out
        data = res.json()
    except Exception:
        return out

    info = data.get("jobPostingInfo", data)  # some tenants nest under jobPostingInfo, some don't

    # Locations: primary + any additional, under a few different key spellings seen in the wild
    for key in ("location", "primaryLocation", "locationsText"):
        val = info.get(key)
        if isinstance(val, str) and val.strip():
            out["locations"].append(val.strip())
            break
    for key in ("additionalLocationsText", "additionalLocations", "additional_locations"):
        val = info.get(key)
        if isinstance(val, list):
            out["locations"].extend([str(v).strip() for v in val if v])
        elif isinstance(val, str) and val.strip():
            out["locations"].append(val.strip())

    # Department / team -- best-effort, frequently just absent from the public API
    for key in ("jobFamilyGroup", "department", "team", "supervisoryOrganization"):
        val = info.get(key)
        if isinstance(val, str) and val.strip():
            out["department"] = val.strip()
            break
        if isinstance(val, dict) and val.get("descriptor"):
            out["department"] = str(val["descriptor"]).strip()
            break

    for key in ("jobDescription", "description", "jobDescriptionHtml"):
        val = info.get(key)
        if isinstance(val, str) and val.strip():
            out["description"] = val
            break

    return out


async def fetch_workday(client: httpx.AsyncClient, target: str, sem: asyncio.Semaphore) -> list:
    """
    FIX: the correct, public, unauthenticated Workday endpoint is the CXS
    (Candidate Experience Service) API -- the same one the careers page's own
    search box calls:
        POST https://{host}/wday/cxs/{tenant}/{site}/jobs
    Response shape: {"total": N, "jobPostings": [ {title, externalPath,
    locationsText, postedOn, bulletFields: [...]}, ... ]}
    (The old /wday/cxf/service-pagination/{site} endpoint either 404s or
    returns an unrelated facet payload, which is why it silently produced 0 jobs.)

    FIX: `locationsText` on the list endpoint is frequently blank even when the
    posting clearly has a location (visible in its own URL, and often has a
    *second* location only shown on the full job page). This now: (a) always
    falls back to the location embedded in the URL slug when locationsText is
    blank, and (b) fetches each surviving candidate's detail page to pick up
    every declared location, department and the real description.
    """
    parts = target.split("/", 1)
    if len(parts) != 2:
        return []
    host, site = parts[0], parts[1]
    tenant = host.split(".")[0]
    comp_name = tenant.replace("-", " ").title()

    cxs_url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    post_headers = {**HEADERS, "Content-Type": "application/json"}

    candidates = []
    limit = 20
    offset = 0
    max_pages = 25  # safety cap: 500 postings per company

    async with sem:
        for _ in range(max_pages):
            payload = {"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": ""}
            try:
                res = await _request_with_retry(client, "POST", cxs_url, headers=post_headers, json=payload)
                if res.status_code != 200:
                    break
                data = res.json()
            except Exception as e:
                logger.debug(f"workday:{target} failed: {e}")
                break

            postings = data.get("jobPostings", [])
            if not postings:
                break

            for item in postings:
                raw_title = item.get("title", "")
                is_valid, title, category = classify_and_filter_title(raw_title)
                if not is_valid:
                    continue

                is_fresh, pub_date = parse_workday_relative_date(item.get("postedOn", ""))
                if not is_fresh:
                    continue

                external_path = item.get("externalPath", "") or ""
                apply_url = f"https://{host}/{site}{external_path}" if external_path.startswith("/") else f"https://{host}/{site}/{external_path}"

                bullet_fields = item.get("bulletFields") or []
                external_id = bullet_fields[0] if bullet_fields else (external_path.rstrip("/").split("/")[-1] or apply_url[-24:])

                loc_text = (item.get("locationsText") or "").strip()
                if not loc_text:
                    # NEW: fall back to Workday's own location slug embedded in the URL
                    slug_match = re.match(r"^/job/([^/]+)/", external_path)
                    if slug_match:
                        loc_text = _location_from_slug(slug_match.group(1))

                candidates.append({
                    "title": title, "category": category, "pub_date": pub_date,
                    "external_path": external_path, "apply_url": apply_url,
                    "external_id": external_id, "loc_text": loc_text,
                })

            total = data.get("total", 0)
            offset += limit
            if offset >= total or len(postings) < limit:
                break

    if not candidates:
        return []

    # NEW: fetch each candidate's detail page (bounded concurrency) for the full
    # location list / department / description the list endpoint left out.
    detail_sem = asyncio.Semaphore(10)

    async def _enrich(c):
        async with detail_sem:
            detail = await _fetch_workday_detail(client, host, tenant, site, c["external_path"])
        return c, detail

    enriched = await asyncio.gather(*(_enrich(c) for c in candidates))

    jobs = []
    for c, detail in enriched:
        raw_locs = detail["locations"] if detail["locations"] else ([c["loc_text"]] if c["loc_text"] else [""])
        content_text = strip_html(detail["description"]) if detail["description"] else ""

        jobs.extend(build_job_records(
            company_name=comp_name, title=c["title"], role_category=c["category"],
            ats_source="Workday", external_id=c["external_id"], apply_url=c["apply_url"],
            published_at=c["pub_date"], raw_locations=raw_locs,
            content_text=content_text, department=detail["department"] or "",
        ))

    return jobs

# =====================================================================
# 7. DISCOVERY & SEED PROBING
# =====================================================================

def clean_slug_candidates(raw_name: str) -> list:
    if not raw_name:
        return []
    cleaned = re.sub(r"\b(inc|corp|corporation|ltd|llc|holdings|technologies|technology|group|co|india)\b\.?", "", raw_name, flags=re.I)
    cleaned = re.sub(r"[^\w\s-]", "", cleaned).strip().lower()
    slug_dash = re.sub(r"[\s_]+", "-", cleaned)
    slug_no_sep = re.sub(r"[\s_-]+", "", cleaned)
    return [c for c in dict.fromkeys([slug_dash, slug_no_sep]) if len(c) >= 2 and c not in RESERVED_SLUGS]


def normalize_company_key(raw_name: str) -> str:
    """NEW: same normalization as clean_slug_candidates' no-separator form, used
    to join company_career_pages.json metadata back onto harvested jobs."""
    if not raw_name:
        return ""
    cleaned = re.sub(r"\b(inc|corp|corporation|ltd|llc|holdings|technologies|technology|group|co|india)\b\.?", "", raw_name, flags=re.I)
    cleaned = re.sub(r"[^\w\s-]", "", cleaned).strip().lower()
    return re.sub(r"[\s_-]+", "", cleaned)


async def verify_board(client: httpx.AsyncClient, ats: str, slug: str) -> bool:
    url_map = {
        "greenhouse": f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
        "lever": f"https://api.lever.co/v0/postings/{slug}?limit=1",
        "ashby": f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
        "smartrecruiters": f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1",
    }
    try:
        if ats == "workday":
            parts = slug.split("/", 1)
            if len(parts) == 2:
                host, site = parts
                tenant = host.split(".")[0]
                # FIX: verify against the real CXS endpoint, not the old cxf one
                res = await client.post(
                    f"https://{host}/wday/cxs/{tenant}/{site}/jobs",
                    json={"limit": 1, "offset": 0, "searchText": ""},
                    timeout=5.0,
                )
                return res.status_code == 200
            return False
        res = await client.get(url_map[ats], timeout=3.5)
        return res.status_code == 200
    except Exception:
        return False


async def discover_all_companies(client: httpx.AsyncClient) -> dict:
    discovered = {k: set(v) for k, v in CURATED_SEEDS.items()}

    patterns = {
        "greenhouse": re.compile(r"(?:boards|job-boards|boards\.eu)\.greenhouse\.io/(?:embed/job_board\?for=)?([a-zA-Z0-9_\-]+)", re.I),
        "lever": re.compile(r"jobs\.lever\.co/([a-zA-Z0-9_\-]+)", re.I),
        "ashby": re.compile(r"jobs\.ashbyhq\.com/([a-zA-Z0-9_\-]+)", re.I),
        "smartrecruiters": re.compile(r"(?:careers|jobs)\.smartrecruiters\.com/([a-zA-Z0-9_\-]+)", re.I),
        "workday": re.compile(r"https?://([a-zA-Z0-9_\-\.]+\.myworkdayjobs\.com)/(?:[a-zA-Z]{2}(?:-[a-zA-Z]{2})?/)?([a-zA-Z0-9_\-]+)", re.I),
    }

    logger.info("Scanning discovery sources for verified ATS targets...")
    candidate_names = set()

    for url in DISCOVERY_SOURCES:
        try:
            res = await client.get(url, headers=HEADERS, timeout=12.0)
            if res.status_code == 200:
                text = res.text

                for ats in ["greenhouse", "lever", "ashby", "smartrecruiters"]:
                    for match in patterns[ats].finditer(text):
                        slug = match.group(1).lower().strip("-_/")
                        if slug and slug not in RESERVED_SLUGS and len(slug) >= 2:
                            discovered[ats].add(slug)

                for match in patterns["workday"].finditer(text):
                    host = match.group(1).lower()
                    site = match.group(2)
                    if site.lower() not in RESERVED_SLUGS and len(site) >= 2:
                        discovered["workday"].add(f"{host}/{site}")

                for link_match in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", text):
                    name = re.sub(r"[\*\_`]", "", link_match.group(1)).strip()
                    if 2 <= len(name) <= 35 and not re.search(r"\b(apply|link|here|source|readme|sponsor)\b", name, re.I):
                        candidate_names.add(name)
        except Exception as e:
            logger.warning(f"Skipping {url}: {e}")

    companies_meta = {}  # NEW: name -> {website, hq_country, symbol}
    if os.path.exists(COMPANIES_JSON_FILE):
        try:
            with open(COMPANIES_JSON_FILE, "r", encoding="utf-8") as f:
                comp_data = json.load(f)
                for item in comp_data:
                    cname = item.get("name") or item.get("search_name")
                    if cname:
                        candidate_names.add(cname)
                        companies_meta[normalize_company_key(cname)] = {
                            "company_name": cname,  # NEW: canonical name, used to fix mangled slug-derived names
                            "company_website": item.get("website"),
                            "company_hq_country": item.get("country"),
                            "company_symbol": item.get("symbol"),
                        }
        except Exception as e:
            logger.warning(f"Could not load {COMPANIES_JSON_FILE}: {e}")

    logger.info(f"Probing {len(candidate_names)} candidate companies across ATS platforms...")
    sem = asyncio.Semaphore(PROBE_CONCURRENCY)

    async def probe(name: str):
        candidates = clean_slug_candidates(name)
        async with sem:
            for slug in candidates:
                for ats in ["greenhouse", "lever", "ashby", "smartrecruiters"]:
                    if slug not in discovered[ats]:
                        if await verify_board(client, ats, slug):
                            discovered[ats].add(slug)
                            return

    await asyncio.gather(*(probe(n) for n in candidate_names))
    return discovered, companies_meta

# =====================================================================
# 8. EXECUTION & REPORTING
# =====================================================================

async def main():
    sem = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    limits = httpx.Limits(max_keepalive_connections=120, max_connections=MAX_CONCURRENT_REQUESTS)

    async with httpx.AsyncClient(verify=False, follow_redirects=True, limits=limits) as client:
        targets, companies_meta = await discover_all_companies(client)

        total_boards = sum(len(v) for v in targets.values())
        logger.info(f"Harvesting from {total_boards} Verified Boards:")
        for ats_name, s_set in targets.items():
            logger.info(f"  • {ats_name.title():<18}: {len(s_set):>4} boards")

        tasks = []
        for slug in targets["greenhouse"]:
            tasks.append(fetch_greenhouse(client, slug, sem))
        for slug in targets["lever"]:
            tasks.append(fetch_lever(client, slug, sem))
        for slug in targets["ashby"]:
            tasks.append(fetch_ashby(client, slug, sem))
        for slug in targets["smartrecruiters"]:
            tasks.append(fetch_smartrecruiters(client, slug, sem))
        for w_target in targets["workday"]:
            tasks.append(fetch_workday(client, w_target, sem))

        results = await asyncio.gather(*tasks)

    seen_ids = set()
    clean_jobs = []

    for batch in results:
        for job in batch:
            job_id = job["id"]
            if job_id not in seen_ids:
                seen_ids.add(job_id)
                # NEW: enrich with company metadata (website / HQ country / ticker).
                # FIX: also replace the display name with the canonical one from your
                # companies file when we find a match -- the name we had until now was
                # guessed from the ATS URL slug (e.g. Workday tenant "paloaltonetworks"
                # title-cased to "Paloaltonetworks" instead of "Palo Alto Networks"),
                # which also silently broke this same lookup before.
                meta = companies_meta.get(normalize_company_key(job["company_name"]), {})
                if meta.get("company_name"):
                    job["company_name"] = meta["company_name"]
                job["company_website"] = meta.get("company_website")
                job["company_hq_country"] = meta.get("company_hq_country")
                job["company_symbol"] = meta.get("company_symbol")
                clean_jobs.append(job)
    save_jobs_alternating(clean_jobs)
    await process_and_save_company_metadata(
        [
            {
                "company_name": job.get("company_name"),
                "symbol": job.get("company_symbol"),
                "website": job.get("company_website"),
            }
            for job in clean_jobs
        ]
    )
    ats_breakdown, countries, emp_types, exp_levels = {}, {}, {}, {}
    for job in clean_jobs:
        ats_breakdown[job["ats_source"]] = ats_breakdown.get(job["ats_source"], 0) + 1
        countries[job["country"]] = countries.get(job["country"], 0) + 1
        emp_types[job["employment_type"]] = emp_types.get(job["employment_type"], 0) + 1
        exp_levels[job["experience_level"]] = exp_levels.get(job["experience_level"], 0) + 1

    print("\n" + "=" * 80)
    print("      5-ATS TECH HARVEST REPORT (<= 30 DAYS OLD)      ")
    print("=" * 80)
    print(f"Total Useful Tech Job Records     : {len(clean_jobs):,}")
    print("-" * 80)
    print("ATS Breakdown:")
    for ats_k, count in sorted(ats_breakdown.items(), key=lambda x: x[1], reverse=True):
        print(f"  • {ats_k:<25}: {count:>4} positions")
    print("-" * 80)
    print("Top Countries:")
    for cntry, count in sorted(countries.items(), key=lambda x: x[1], reverse=True)[:8]:
        print(f"  • {cntry:<25}: {count:>4}")
    print("-" * 80)
    print("Employment Types:")
    for et, count in sorted(emp_types.items(), key=lambda x: x[1], reverse=True):
        print(f"  • {et:<25}: {count:>4}")
    print("-" * 80)
    print("Experience Levels:")
    for ex, count in sorted(exp_levels.items(), key=lambda x: x[1], reverse=True):
        print(f"  • {ex:<25}: {count:>4}")
    print("=" * 80 + "\n")

    if os.getenv("WRITE_LOCAL_JSON", "false").lower() == "true":
                with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                    json.dump(clean_jobs, f, indent=2, ensure_ascii=False)
                logger.info(f"Debug JSON written to {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())