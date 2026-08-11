from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


LOCATION_RULESET_VERSION = "india-location-2026-08-12-v2"


@dataclass(frozen=True)
class LocationClassification:
    status: str  # india | foreign | unknown
    country_code: str | None
    normalized_location: str
    evidence: str

    @property
    def is_india(self) -> bool:
        return self.status == "india"


_SPACE_RE = re.compile(r"\s+")
_NONWORD_RE = re.compile(r"[^a-z0-9]+")


def _norm(value: str | None) -> str:
    text = (value or "").lower().replace("&", " and ")
    text = _NONWORD_RE.sub(" ", text)
    return _SPACE_RE.sub(" ", text).strip()


def _contains_phrase(text: str, phrase: str) -> bool:
    p = _norm(phrase)
    if not p:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(p)}(?![a-z0-9])", text) is not None


# Canonical Indian states and union territories. Historical spellings are kept
# because ATS data is often stale or copied from older office records.
_REGION_ALIASES: dict[str, tuple[str, ...]] = {
    "Andhra Pradesh": ("andhra pradesh",),
    "Arunachal Pradesh": ("arunachal pradesh",),
    "Assam": ("assam",),
    "Bihar": ("bihar",),
    "Chhattisgarh": ("chhattisgarh", "chattisgarh"),
    "Goa": ("goa",),
    "Gujarat": ("gujarat",),
    "Haryana": ("haryana",),
    "Himachal Pradesh": ("himachal pradesh",),
    "Jharkhand": ("jharkhand",),
    "Karnataka": ("karnataka",),
    "Kerala": ("kerala",),
    "Madhya Pradesh": ("madhya pradesh",),
    "Maharashtra": ("maharashtra",),
    "Manipur": ("manipur",),
    "Meghalaya": ("meghalaya",),
    "Mizoram": ("mizoram",),
    "Nagaland": ("nagaland",),
    "Odisha": ("odisha", "orissa"),
    "Punjab": ("punjab",),
    "Rajasthan": ("rajasthan",),
    "Sikkim": ("sikkim",),
    "Tamil Nadu": ("tamil nadu", "tamilnadu"),
    "Telangana": ("telangana",),
    "Tripura": ("tripura",),
    "Uttar Pradesh": ("uttar pradesh",),
    "Uttarakhand": ("uttarakhand", "uttaranchal"),
    "West Bengal": ("west bengal",),
    "Andaman and Nicobar Islands": ("andaman and nicobar", "andaman nicobar"),
    "Chandigarh": ("chandigarh",),
    "Dadra and Nagar Haveli and Daman and Diu": (
        "dadra and nagar haveli", "daman and diu", "dadra nagar haveli",
    ),
    "Delhi": ("national capital territory of delhi", "nct delhi", "delhi"),
    "Jammu and Kashmir": ("jammu and kashmir", "jammu kashmir"),
    "Ladakh": ("ladakh",),
    "Lakshadweep": ("lakshadweep",),
    "Puducherry": ("puducherry", "pondicherry"),
}


# The list is intentionally broader than the usual top-10 tech hubs. It covers
# common tier-1/tier-2 hiring cities, legacy spellings, and cities frequently seen
# in Workday/Greenhouse/Eightfold location strings.
_CITY_ALIASES: dict[str, tuple[str, ...]] = {
    "Agra": ("agra",),
    "Ahmedabad": ("ahmedabad",),
    "Aizawl": ("aizawl",),
    "Ajmer": ("ajmer",),
    "Aligarh": ("aligarh",),
    "Alwar": ("alwar",),
    "Amaravati": ("amaravati",),
    "Amritsar": ("amritsar",),
    "Anand": ("anand",),
    "Aurangabad": ("aurangabad", "chhatrapati sambhajinagar"),
    "Bareilly": ("bareilly",),
    "Belagavi": ("belagavi", "belgaum"),
    "Bengaluru": ("bengaluru", "bangalore", "b lore"),
    "Bhopal": ("bhopal",),
    "Bhubaneswar": ("bhubaneswar", "bhubaneshwar"),
    "Bokaro": ("bokaro",),
    "Chandigarh": ("chandigarh",),
    "Chennai": ("chennai", "madras"),
    "Coimbatore": ("coimbatore",),
    "Cuttack": ("cuttack",),
    "Dehradun": ("dehradun",),
    "Delhi": ("new delhi", "delhi ncr", "delhi"),
    "Dhanbad": ("dhanbad",),
    "Dharamshala": ("dharamshala", "dharmshala"),
    "Durgapur": ("durgapur",),
    "Faridabad": ("faridabad",),
    "Gandhinagar": ("gandhinagar",),
    "Ghaziabad": ("ghaziabad",),
    "Greater Noida": ("greater noida",),
    "Gurugram": ("gurugram", "gurgaon"),
    "Guwahati": ("guwahati",),
    "Gwalior": ("gwalior",),
    "Hubballi": ("hubballi", "hubli"),
    "Hyderabad": ("hyderabad",),
    "Indore": ("indore",),
    "Jaipur": ("jaipur",),
    "Jalandhar": ("jalandhar",),
    "Jammu": ("jammu",),
    "Jamshedpur": ("jamshedpur",),
    "Jodhpur": ("jodhpur",),
    "Kanpur": ("kanpur",),
    "Kochi": ("kochi", "cochin"),
    "Kolhapur": ("kolhapur",),
    "Kolkata": ("kolkata", "calcutta"),
    "Kota": ("kota",),
    "Kozhikode": ("kozhikode", "calicut"),
    "Lucknow": ("lucknow",),
    "Ludhiana": ("ludhiana",),
    "Madurai": ("madurai",),
    "Mangaluru": ("mangaluru", "mangalore"),
    "Meerut": ("meerut",),
    "Mohali": ("mohali", "sas nagar", "s a s nagar"),
    "Mumbai": ("mumbai", "bombay"),
    "Mysuru": ("mysuru", "mysore"),
    "Nagpur": ("nagpur",),
    "Nashik": ("nashik", "nasik"),
    "Navi Mumbai": ("navi mumbai", "new bombay"),
    "Nellore": ("nellore",),
    "Noida": ("noida",),
    "Panaji": ("panaji", "panjim"),
    "Patna": ("patna",),
    "Prayagraj": ("prayagraj", "allahabad"),
    "Pune": ("pune", "poona"),
    "Raipur": ("raipur",),
    "Rajkot": ("rajkot",),
    "Ranchi": ("ranchi",),
    "Rohtak": ("rohtak",),
    "Salem": ("salem",),
    "Shillong": ("shillong",),
    "Siliguri": ("siliguri",),
    "Srinagar": ("srinagar",),
    "Surat": ("surat",),
    "Thane": ("thane",),
    "Thiruvananthapuram": ("thiruvananthapuram", "trivandrum"),
    "Tiruchirappalli": ("tiruchirappalli", "trichy"),
    "Tirupati": ("tirupati",),
    "Udaipur": ("udaipur",),
    "Vadodara": ("vadodara", "baroda"),
    "Varanasi": ("varanasi", "banaras", "benares"),
    "Vijayawada": ("vijayawada",),
    "Visakhapatnam": ("visakhapatnam", "vizag", "vishakhapatnam"),
    "Warangal": ("warangal",),
}


# Office/locality names used by employers that sometimes omit the parent city.
_TECH_LOCALITY_ALIASES: dict[str, tuple[str, ...]] = {
    "Bengaluru": (
        "whitefield", "electronic city", "manyata tech park", "manyata embassy business park",
        "bellandur", "marathahalli", "koramangala", "hsr layout", "sarjapur road",
    ),
    "Hyderabad": (
        "gachibowli", "hitec city", "hi tech city", "madhapur", "kondapur",
        "financial district hyderabad", "nanakramguda",
    ),
    "Gurugram": (
        "dlf cyber city", "cyber hub gurgaon", "udyog vihar", "golf course road gurgaon",
        "sohna road gurgaon",
    ),
    "Mumbai": ("powai", "andheri", "goregaon", "bandra kurla complex", "bkc mumbai"),
    "Navi Mumbai": ("airoli", "ghansoli"),
    "Pune": ("hinjawadi", "hinjewadi", "kharadi", "magarpatta", "viman nagar", "yerawada", "yerwada"),
    "Kochi": ("infopark kochi", "smartcity kochi"),
    "Thiruvananthapuram": ("technopark trivandrum", "technopark thiruvananthapuram"),
    "Kolkata": ("new town kolkata", "sector v kolkata", "salt lake kolkata"),
}


# Common airport/internal office codes. These are accepted only as exact uppercase
# tokens in the raw location string, avoiding accidental matches inside prose.
_OFFICE_CODES: dict[str, str] = {
    "BLR": "Bengaluru", "BGLR": "Bengaluru", "HYD": "Hyderabad",
    "DEL": "Delhi", "BOM": "Mumbai", "MUM": "Mumbai", "MAA": "Chennai",
    "CHE": "Chennai", "PNQ": "Pune", "CCU": "Kolkata", "KOL": "Kolkata",
    "AMD": "Ahmedabad", "COK": "Kochi", "TRV": "Thiruvananthapuram",
    "CJB": "Coimbatore", "GGN": "Gurugram", "JAI": "Jaipur", "IDR": "Indore",
    "NAG": "Nagpur", "GOI": "Goa", "IXC": "Chandigarh", "LKO": "Lucknow",
    "BBI": "Bhubaneswar", "GAU": "Guwahati", "VTZ": "Visakhapatnam",
    "VGA": "Vijayawada", "PAT": "Patna", "RPR": "Raipur", "RNC": "Ranchi",
}


_STRUCTURED_STATE_CODES: dict[str, str] = {
    "AP": "Andhra Pradesh", "AR": "Arunachal Pradesh", "AS": "Assam", "BR": "Bihar",
    "CG": "Chhattisgarh", "GA": "Goa", "GJ": "Gujarat", "GUJ": "Gujarat",
    "HR": "Haryana", "HP": "Himachal Pradesh", "JH": "Jharkhand", "KA": "Karnataka",
    "KAR": "Karnataka", "KL": "Kerala", "MP": "Madhya Pradesh", "MH": "Maharashtra",
    "MN": "Manipur", "ML": "Meghalaya", "MZ": "Mizoram", "NL": "Nagaland",
    "OD": "Odisha", "OR": "Odisha", "PB": "Punjab", "RJ": "Rajasthan", "SK": "Sikkim",
    "TN": "Tamil Nadu", "TS": "Telangana", "TG": "Telangana", "TR": "Tripura",
    "UP": "Uttar Pradesh", "WB": "West Bengal", "AN": "Andaman and Nicobar Islands",
    "CH": "Chandigarh", "DL": "Delhi", "JK": "Jammu and Kashmir", "LA": "Ladakh",
    "LD": "Lakshadweep", "PY": "Puducherry",
}


# Explicit foreign-country evidence outranks a bare city/state alias. This matters
# for place names shared across borders, such as Hyderabad/Punjab in Pakistan or
# Salem in the US. India still wins for a true multi-location listing that explicitly
# contains India (e.g. "Bengaluru, India / London, UK").
_FOREIGN_COUNTRY_ALIASES = (
    "united states", "united states of america", "usa", "canada", "united kingdom", "great britain",
    "england", "scotland", "wales", "singapore", "australia", "new zealand", "germany", "france",
    "ireland", "netherlands", "spain", "italy", "portugal", "sweden", "norway", "denmark", "finland",
    "switzerland", "austria", "belgium", "poland", "romania", "czech republic", "czechia", "hungary",
    "ukraine", "israel", "united arab emirates", "uae", "saudi arabia", "qatar", "bahrain", "oman",
    "pakistan", "bangladesh", "nepal", "sri lanka", "bhutan", "maldives", "afghanistan",
    "japan", "south korea", "korea", "china", "hong kong", "taiwan", "malaysia", "indonesia",
    "philippines", "thailand", "vietnam", "brazil", "mexico", "argentina", "chile", "colombia",
    "south africa", "kenya", "nigeria", "egypt",
)


_LOCATION_KEY_HINTS = (
    "location", "country", "city", "state", "province", "region", "office", "address", "workplace", "place",
)
_COUNTRY_KEY_HINTS = ("country", "countrycode", "country_code", "countryid", "country_id")
_STATE_KEY_HINTS = ("state", "province", "region")


def _canonical_alias_match(text: str, groups: dict[str, tuple[str, ...]]) -> str | None:
    matches: list[tuple[int, str]] = []
    for canonical, aliases in groups.items():
        for alias in aliases:
            if _contains_phrase(text, alias):
                matches.append((len(_norm(alias)), canonical))
    if not matches:
        return None
    matches.sort(reverse=True)
    return matches[0][1]


def _office_code_match(raw: str | None) -> str | None:
    value = raw or ""
    for code, canonical in _OFFICE_CODES.items():
        if re.search(rf"(?<![A-Z0-9]){re.escape(code)}(?![A-Z0-9])", value):
            return canonical
    return None


def _has_explicit_india_country(raw: str | None) -> bool:
    text = _norm(raw)
    if not text:
        return False
    if any(_contains_phrase(text, token) for token in ("india", "republic of india", "bharat")):
        return True
    stripped = (raw or "").strip()
    if stripped.upper() in {"IN", "IND"}:
        return True
    return re.fullmatch(
        r"(?i)(?:remote\s*[-,/|:]\s*(?:IN|IND)|(?:IN|IND)\s*[-,/|:]\s*remote)", stripped,
    ) is not None


def _india_text_match(raw: str | None) -> tuple[str | None, str | None]:
    text = _norm(raw)
    if not text:
        return None, None
    if _has_explicit_india_country(raw):
        if "remote" in text:
            return "Remote", "country_name_or_code"
        city = _canonical_alias_match(text, _CITY_ALIASES)
        if city:
            return city, "country+city"
        region = _canonical_alias_match(text, _REGION_ALIASES)
        if region:
            return region, "country+region"
        return "India", "country_name_or_code"

    city = _canonical_alias_match(text, _CITY_ALIASES)
    if city:
        return city, "city"
    region = _canonical_alias_match(text, _REGION_ALIASES)
    if region:
        return region, "region"
    locality = _canonical_alias_match(text, _TECH_LOCALITY_ALIASES)
    if locality:
        return locality, "tech_locality"
    code = _office_code_match(raw)
    if code:
        return code, "office_code"
    return None, None


def _explicit_foreign_text(raw: str | None) -> str | None:
    text = _norm(raw)
    if not text:
        return None
    # Explicit India country evidence means this is a legitimate India-capable
    # multi-location role even if another country is also listed.
    if _has_explicit_india_country(raw):
        return None
    for country in _FOREIGN_COUNTRY_ALIASES:
        if _contains_phrase(text, country):
            return country
    return None


def _location_like_values(raw: Any, *, max_depth: int = 5) -> list[tuple[str, str]]:
    """Extract structured location/country/state values from ATS raw JSON."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    out: list[tuple[str, str]] = []

    def walk(value: Any, path: str, depth: int) -> None:
        if depth > max_depth:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                key_s = str(key)
                key_n = _norm(key_s).replace(" ", "")
                child_path = f"{path}.{key_s}" if path else key_s
                hinted = any(h.replace("_", "") in key_n for h in _LOCATION_KEY_HINTS)
                if hinted and isinstance(child, (str, int, float)):
                    out.append((child_path, str(child)))
                if isinstance(child, (dict, list, tuple)):
                    walk(child, child_path, depth + 1)
        elif isinstance(value, (list, tuple)):
            for idx, child in enumerate(value):
                walk(child, f"{path}[{idx}]", depth + 1)

    walk(raw, "raw", 0)
    return out


def _structured_country_status(raw: Any) -> tuple[str | None, str | None]:
    """Return explicit structured country status before considering city aliases."""
    india_evidence: str | None = None
    foreign_evidence: str | None = None
    for path, value in _location_like_values(raw):
        path_n = _norm(path).replace(" ", "")
        if not any(h.replace("_", "") in path_n for h in _COUNTRY_KEY_HINTS):
            continue
        upper = value.strip().upper()
        text = _norm(value)
        if upper in {"IN", "IND", "INDIA"} or _contains_phrase(text, "india") or _contains_phrase(text, "bharat"):
            india_evidence = f"{path}=india"
            continue
        foreign = _explicit_foreign_text(value)
        if foreign:
            foreign_evidence = f"{path}={foreign}"
            continue
        # In an explicitly country-labelled field, a non-India ISO-like code is
        # enough to rule out India even if the code is not in our country-name list.
        if re.fullmatch(r"[A-Z]{2,3}", upper):
            foreign_evidence = f"{path}=country_code:{upper}"

    # If an ATS genuinely lists several allowed countries and one is India, keep it:
    # the job is available to an India-based candidate. Otherwise foreign wins.
    if india_evidence:
        return "india", india_evidence
    if foreign_evidence:
        return "foreign", foreign_evidence
    return None, None


def _structured_india_non_country(raw: Any) -> tuple[str | None, str | None]:
    for path, value in _location_like_values(raw):
        path_n = _norm(path).replace(" ", "")
        if any(h.replace("_", "") in path_n for h in _COUNTRY_KEY_HINTS):
            continue
        upper = value.strip().upper()
        if any(h in path_n for h in _STATE_KEY_HINTS) and upper in _STRUCTURED_STATE_CODES:
            return _STRUCTURED_STATE_CODES[upper], f"{path}=state_code:{upper}"
        # If this structured value itself says another country, do not harvest an
        # overlapping India city/state token from it.
        if _explicit_foreign_text(value):
            continue
        canonical, reason = _india_text_match(value)
        if canonical:
            return canonical, f"{path}:{reason}"
    return None, None


def _description_location_match(description: str | None) -> tuple[str | None, str | None]:
    if not description:
        return None, None
    lines = [line.strip() for line in description.splitlines() if line.strip()]
    for line in lines[:120]:
        if re.search(r"(?i)\b(?:job\s+|work\s+|workplace\s+|office\s+|primary\s+)?location\s*[:\-]", line):
            value = re.split(r"[:\-]", line, maxsplit=1)[-1].strip()
            if _explicit_foreign_text(value):
                continue
            canonical, reason = _india_text_match(value)
            if canonical:
                return canonical, f"description_location:{reason}"

    text = _norm(description)
    strong_patterns = (
        r"\b(?:position|role|job) (?:is )?based in india\b",
        r"\bindia based (?:position|role|job|team)\b",
        r"\bremote (?:within|in|from) india\b",
        r"\bwork remotely (?:within|from) india\b",
        r"\b(?:candidates|applicants) (?:must be )?(?:located|based|resident) in india\b",
        r"\bopen to (?:candidates|applicants) (?:located|based) in india\b",
        r"\bwork location india\b",
    )
    if any(re.search(pattern, text) for pattern in strong_patterns):
        return "Remote" if "remote" in text else "India", "description_strong_india"
    return None, None


def _normalized(canonical: str, original: str | None) -> str:
    if canonical == "Remote":
        return "Remote, India"
    if canonical == "India":
        return "India"
    # Canonical city/state normalization deliberately collapses spelling variants
    # (Bangalore/Bengaluru, Gurgaon/Gurugram, etc.) for deduplication.
    return f"{canonical}, India"


def classify_india_location(
    location: str | None,
    *,
    description: str | None = None,
    raw: Any = None,
) -> LocationClassification:
    """Classify whether a job is genuinely available in India.

    Precedence is deliberate:
      1. explicit structured ATS country fields,
      2. explicit country names/codes in the displayed location,
      3. explicit foreign country names,
      4. Indian city/state/locality/office-code aliases,
      5. structured non-country location fields,
      6. strong JD location language,
      7. unknown.

    Only positive India evidence returns status='india'. Unknown/ambiguous locations
    are not treated as India, preventing foreign jobs from leaking into candidate
    results while structured ATS fields and broad aliases maximize India recall.
    """
    country_status, country_evidence = _structured_country_status(raw)
    if country_status == "foreign":
        return LocationClassification(
            "foreign", None, (location or "Unknown").strip() or "Unknown", country_evidence or "structured_foreign_country",
        )
    if country_status == "india":
        primary, _ = _india_text_match(location)
        canonical = primary or "India"
        return LocationClassification(
            "india", "IN", _normalized(canonical, location), country_evidence or "structured_india_country",
        )

    # An explicit India country marker in the displayed location is the strongest
    # unstructured signal and also supports true multi-country job listings.
    if _has_explicit_india_country(location):
        primary, primary_reason = _india_text_match(location)
        canonical = primary or "India"
        return LocationClassification("india", "IN", _normalized(canonical, location), f"location:{primary_reason or 'country'}")

    # Explicit foreign country evidence outranks bare city/state aliases. Without
    # this precedence, "Hyderabad, Pakistan" could be mistaken for Hyderabad, India.
    foreign = _explicit_foreign_text(location)
    if foreign:
        return LocationClassification("foreign", None, (location or "Unknown").strip() or "Unknown", f"location:{foreign}")

    primary, primary_reason = _india_text_match(location)
    if primary:
        return LocationClassification("india", "IN", _normalized(primary, location), f"location:{primary_reason}")

    structured, evidence = _structured_india_non_country(raw)
    if structured:
        return LocationClassification("india", "IN", _normalized(structured, location), evidence or "structured_location")

    desc, desc_reason = _description_location_match(description)
    if desc:
        return LocationClassification("india", "IN", _normalized(desc, location), desc_reason or "description")

    if _norm(location) in {"remote", "anywhere", "global", "worldwide", "multiple locations", "various locations"}:
        return LocationClassification("unknown", None, (location or "Unknown").strip() or "Unknown", "remote_or_global_without_country")
    return LocationClassification("unknown", None, (location or "Unknown").strip() or "Unknown", "no_positive_india_evidence")


def is_india_job(location: str | None, *, description: str | None = None, raw: Any = None) -> bool:
    return classify_india_location(location, description=description, raw=raw).is_india


def normalized_india_location(location: str | None, *, description: str | None = None, raw: Any = None) -> str:
    return classify_india_location(location, description=description, raw=raw).normalized_location
