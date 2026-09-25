"""Deterministic merchant normalisation + categorisation.

Order of precedence for a transaction's category:
  1. user-defined rules (category_rules table, highest priority first)
  2. built-in merchant rules below
  3. optional LLM fallback for merchants nothing else recognises (import only)
  4. 'Uncategorized'
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from .config import settings

log = logging.getLogger(__name__)

CATEGORIES = [
    "Income", "Housing", "Utilities", "Groceries", "Dining", "Transport", "Shopping",
    "Entertainment", "Subscriptions", "Health", "Insurance", "Travel", "Education",
    "Personal Care", "Fees", "Gifts & Donations", "Transfers", "Uncategorized",
]

# Categories that are fixed obligations rather than discretionary spending.
ESSENTIAL_CATEGORIES = {"Housing", "Utilities", "Insurance", "Groceries", "Health", "Transport"}
# Recurring charges in these categories are "bills", everything else recurring is a "subscription".
BILL_CATEGORIES = {"Housing", "Utilities", "Insurance"}


@dataclass(frozen=True)
class MerchantRule:
    pattern: re.Pattern
    merchant: str
    category: str


def _r(pattern: str, merchant: str, category: str) -> MerchantRule:
    return MerchantRule(re.compile(pattern, re.IGNORECASE), merchant, category)


BUILTIN_RULES: list[MerchantRule] = [
    # income & transfers
    _r(r"PAYROLL|DIRECT DEP|SALARY", "Employer Payroll", "Income"),
    _r(r"UPWORK|FIVERR|STRIPE TRANSFER", "Freelance Income", "Income"),
    _r(r"INTEREST PAYMENT|INTEREST EARNED", "Bank Interest", "Income"),
    _r(r"TRANSFER TO SAVINGS|ONLINE TRANSFER|ZELLE TO SELF", "Internal Transfer", "Transfers"),
    _r(r"CREDIT CARD PAYMENT|AUTOPAY PAYMENT|CARD PMT", "Credit Card Payment", "Transfers"),
    # housing & utilities
    _r(r"\bRENT\b|APARTMENTS|PROPERTY MGMT", "Rent", "Housing"),
    _r(r"CON ?ED|PG&E|ELECTRIC|DUKE ENERGY", "Electric Utility", "Utilities"),
    _r(r"COMCAST|XFINITY|SPECTRUM|VERIZON FIOS", "Internet Provider", "Utilities"),
    _r(r"T-MOBILE|TMOBILE|AT&T|VERIZON WIRELESS", "Mobile Carrier", "Utilities"),
    _r(r"WATER DEPT|CITY WATER", "Water Utility", "Utilities"),
    _r(r"GEICO|STATE FARM|PROGRESSIVE|LEMONADE", "Insurance", "Insurance"),
    # streaming & software subscriptions
    _r(r"NETFLIX", "Netflix", "Subscriptions"),
    _r(r"SPOTIFY", "Spotify", "Subscriptions"),
    _r(r"DISNEY ?PLUS|DISNEYPLUS", "Disney+", "Subscriptions"),
    _r(r"HULU", "Hulu", "Subscriptions"),
    _r(r"HBO ?MAX|MAX\.COM", "Max", "Subscriptions"),
    _r(r"APPLE\.COM/BILL|ICLOUD", "Apple iCloud", "Subscriptions"),
    _r(r"ADOBE", "Adobe Creative Cloud", "Subscriptions"),
    _r(r"OPENAI|CHATGPT", "ChatGPT Plus", "Subscriptions"),
    _r(r"AUDIBLE", "Audible", "Subscriptions"),
    _r(r"HEADSPACE", "Headspace", "Subscriptions"),
    _r(r"NYTIMES|NY TIMES", "New York Times", "Subscriptions"),
    _r(r"AMAZON PRIME|PRIME MEMBERSHIP", "Amazon Prime", "Subscriptions"),
    _r(r"DROPBOX", "Dropbox", "Subscriptions"),
    _r(r"PLANET FITNESS|EQUINOX|24 HOUR FITNESS", "Gym Membership", "Health"),
    # groceries
    _r(r"WHOLE ?FOODS|WFM", "Whole Foods", "Groceries"),
    _r(r"TRADER JOE", "Trader Joe's", "Groceries"),
    _r(r"KROGER", "Kroger", "Groceries"),
    _r(r"SAFEWAY", "Safeway", "Groceries"),
    _r(r"COSTCO", "Costco", "Groceries"),
    # dining
    _r(r"STARBUCKS", "Starbucks", "Dining"),
    _r(r"BLUE BOTTLE", "Blue Bottle Coffee", "Dining"),
    _r(r"CHIPOTLE", "Chipotle", "Dining"),
    _r(r"SWEETGREEN", "Sweetgreen", "Dining"),
    _r(r"DOORDASH", "DoorDash", "Dining"),
    _r(r"UBER ?EATS", "Uber Eats", "Dining"),
    _r(r"GRUBHUB", "Grubhub", "Dining"),
    _r(r"RESTAURANT|BISTRO|PIZZA|SUSHI|TAQUERIA|CAFE", "Restaurant", "Dining"),
    # transport
    _r(r"UBER(?! ?EATS)", "Uber", "Transport"),
    _r(r"LYFT", "Lyft", "Transport"),
    _r(r"SHELL|CHEVRON|EXXON|BP GAS", "Gas Station", "Transport"),
    _r(r"\bMTA\b|METRO CARD|CLIPPER|TRANSIT", "Public Transit", "Transport"),
    _r(r"PARKING|PARKWHIZ", "Parking", "Transport"),
    # shopping
    _r(r"AMZN|AMAZON(?! PRIME)", "Amazon", "Shopping"),
    _r(r"TARGET", "Target", "Shopping"),
    _r(r"BEST ?BUY", "Best Buy", "Shopping"),
    _r(r"IKEA", "IKEA", "Shopping"),
    _r(r"UNIQLO|ZARA|H&M|NORDSTROM", "Clothing Store", "Shopping"),
    # health & personal care
    _r(r"CVS|WALGREENS", "Pharmacy", "Health"),
    _r(r"DENTAL|DENTIST|MEDICAL|CLINIC", "Medical Provider", "Health"),
    _r(r"SALON|BARBER|SEPHORA", "Personal Care", "Personal Care"),
    # entertainment
    _r(r"AMC THEATRES|REGAL|CINEMA", "Movie Theater", "Entertainment"),
    _r(r"TICKETMASTER|STUBHUB", "Event Tickets", "Entertainment"),
    _r(r"STEAM|PLAYSTATION|XBOX|NINTENDO", "Gaming", "Entertainment"),
    # travel
    _r(r"DELTA|UNITED AIR|AMERICAN AIR|SOUTHWEST", "Airline", "Travel"),
    _r(r"MARRIOTT|HILTON|HYATT|AIRBNB", "Lodging", "Travel"),
    # education, gifts, fees
    _r(r"COURSERA|UDEMY|TUITION", "Online Learning", "Education"),
    _r(r"RED CROSS|UNICEF|DONATION|GOFUNDME", "Charity", "Gifts & Donations"),
    _r(r"FOREIGN TRANSACTION FEE|OVERDRAFT|LATE FEE|ATM FEE|SERVICE FEE", "Bank Fee", "Fees"),
]

# India pack. Indian statements are dominated by UPI strings and a different merchant set, so these
# run alongside the rules above rather than replacing them — a user who spends in both places is
# matched correctly either way, and the names below don't collide with the US patterns.
INDIA_RULES: list[MerchantRule] = [
    # income & transfers
    _r(r"SALARY CREDIT|SAL CR|NEFT.*SALARY|MONTHLY SALARY", "Employer Payroll", "Income"),
    _r(r"\bNACH\b|\bECS\b DR", "Auto-debit", "Transfers"),
    _r(r"\bCRED\b(?! ?CARD)|CREDPAY", "CRED", "Transfers"),
    _r(r"IMPS.*SELF|NEFT.*SELF|SELF TRANSFER", "Internal Transfer", "Transfers"),
    # housing & utilities
    _r(r"\bAIRTEL\b|\bJIO\b|RELIANCE JIO|\bVI\b POSTPAID|VODAFONE IDEA", "Telecom", "Utilities"),
    _r(r"TATA POWER|ADANI ELECTRICITY|BESCOM|MSEDCL|TNEB|BSES", "Electricity Board", "Utilities"),
    _r(r"INDANE|HP GAS|BHARATGAS|MAHANAGAR GAS|GAIL GAS", "Cooking Gas", "Utilities"),
    _r(r"ACT FIBERNET|HATHWAY|EXCITEL|JIOFIBER|AIRTEL XSTREAM", "Broadband", "Utilities"),
    _r(r"SOCIETY MAINTENANCE|FLAT RENT|HOUSE RENT|NOBROKER PAY", "Rent", "Housing"),
    _r(r"\bLIC\b|HDFC LIFE|ICICI PRULIFE|STAR HEALTH|NIVA BUPA|BAJAJ ALLIANZ", "Insurance", "Insurance"),
    # subscriptions
    _r(r"HOTSTAR|DISNEY\+ ?HOTSTAR", "Disney+ Hotstar", "Subscriptions"),
    _r(r"SONYLIV|SONY LIV", "SonyLIV", "Subscriptions"),
    _r(r"ZEE5", "ZEE5", "Subscriptions"),
    _r(r"JIOCINEMA|JIOSAAVN", "JioCinema", "Subscriptions"),
    _r(r"GAANA|WYNK", "Music Streaming", "Subscriptions"),
    _r(r"GOOGLE ONE", "Google One", "Subscriptions"),
    _r(r"GOOGLE WORKSPACE|GSUITE", "Google Workspace", "Subscriptions"),
    # groceries & quick commerce
    _r(r"BIGBASKET|BB DAILY", "BigBasket", "Groceries"),
    _r(r"BLINKIT|GROFERS", "Blinkit", "Groceries"),
    _r(r"ZEPTO", "Zepto", "Groceries"),
    _r(r"INSTAMART|SWIGGY INSTAMART", "Swiggy Instamart", "Groceries"),
    _r(r"DMART|D ?MART|AVENUE SUPERMART", "DMart", "Groceries"),
    _r(r"RELIANCE FRESH|SMART BAZAAR|MORE MEGASTORE|SPENCER", "Supermarket", "Groceries"),
    # dining
    _r(r"SWIGGY(?! INSTAMART)", "Swiggy", "Dining"),
    _r(r"ZOMATO|EATERNAL", "Zomato", "Dining"),
    _r(r"\bCCD\b|CAFE COFFEE DAY|THIRD WAVE|BLUE TOKAI|CHAAYOS|\bCHAI POINT\b", "Coffee Shop", "Dining"),
    _r(r"DOMINO|PIZZA HUT|\bKFC\b|MCDONALD|BURGER KING|BIKANERVALA|HALDIRAM", "Fast Food", "Dining"),
    # transport
    _r(r"\bOLA\b|OLACABS", "Ola", "Transport"),
    _r(r"RAPIDO", "Rapido", "Transport"),
    _r(r"\bIRCTC\b|INDIAN RAILWAY", "IRCTC", "Travel"),
    _r(r"INDIAN OIL|\bIOCL\b|BHARAT PETROL|\bBPCL\b|\bHPCL\b|NAYARA", "Fuel", "Transport"),
    _r(r"\bFASTAG\b|NETC FASTAG|PAYTM FASTAG", "FASTag Toll", "Transport"),
    _r(r"\bDMRC\b|METRO RAIL|\bBMTC\b|\bBEST\b BUS|CHALO APP", "Public Transit", "Transport"),
    # shopping
    _r(r"FLIPKART", "Flipkart", "Shopping"),
    _r(r"MYNTRA", "Myntra", "Shopping"),
    _r(r"AJIO", "AJIO", "Shopping"),
    _r(r"NYKAA", "Nykaa", "Personal Care"),
    _r(r"MEESHO", "Meesho", "Shopping"),
    _r(r"CROMA|RELIANCE DIGITAL|VIJAY SALES", "Electronics Store", "Shopping"),
    _r(r"DECATHLON|LIFESTYLE STORES|PANTALOONS|WESTSIDE", "Clothing Store", "Shopping"),
    # health, entertainment, education, investing
    _r(r"APOLLO PHARMACY|PHARMEASY|NETMEDS|1 ?MG|WELLNESS FOREVER", "Pharmacy", "Health"),
    _r(r"PRACTO|APOLLO HOSPITAL|FORTIS|MANIPAL HOSPITAL|MAX HEALTHCARE", "Medical Provider", "Health"),
    _r(r"CULT ?FIT|CULTFIT|GOLD'?S GYM", "Gym Membership", "Health"),
    _r(r"BOOKMYSHOW|\bPVR\b|INOX|CINEPOLIS", "Movies & Events", "Entertainment"),
    _r(r"BYJU|UNACADEMY|VEDANTU|PHYSICSWALLAH|UPGRAD", "Online Learning", "Education"),
    _r(r"ZERODHA|GROWW|UPSTOX|ANGEL ONE|KUVERA|COIN DCX", "Investment Transfer", "Transfers"),
    _r(r"\bPPF\b|\bNPS\b|SUKANYA SAMRIDDHI|\bSIP\b DEBIT", "Investment Transfer", "Transfers"),
    # fees
    _r(r"GST ON|\bIGST\b|\bCGST\b|SGST|CONVENIENCE FEE|SURCHARGE|AMB CHARGES|MIN BAL", "Bank Fee", "Fees"),
]

# India rules are checked FIRST. They name specific companies ("BESCOM", "DMART"), while several US
# rules match generic words ("ELECTRIC", "TRANSIT", "RESTAURANT") that would otherwise swallow them —
# "BESCOM ELECTRICITY BILL" should read as the Bengaluru electricity board, not a generic utility.
# The two packs are otherwise disjoint, which `test_us_rules_still_win_for_us_descriptors` pins down.
BUILTIN_RULES[:0] = INDIA_RULES

# Indian statements carry the payee inside a UPI / IMPS / NEFT string rather than as a clean name:
#   UPI/DR/412345678901/SWIGGY/YESB/swiggy@ybl/Payment  ->  SWIGGY
#   UPI-ZOMATO LTD-ZOMATO@HDFCBANK-HDFC0000001-1234-PAYMENT  ->  ZOMATO LTD
# The payee is the first field that is neither a pure number, a bank/IFSC code nor a mode marker, and a
# VPA (name@handle) is used only as a fallback since handles like `ybl` name the PSP, not the merchant.
_UPI_NOISE = {"UPI", "DR", "CR", "IMPS", "NEFT", "RTGS", "P2M", "P2A", "PAYMENT", "PAY", "COLLECT",
              "REFUND", "NA", "UPIOUT", "UPIIN", "MOB", "INET", "ATM", "POS", "ACH", "NACH"}
# Four-letter PSP / bank short codes that sit between the payee and the VPA. Listed explicitly rather
# than matched as "any four capitals" so a genuine short merchant name is never mistaken for a bank.
_UPI_NOISE |= {"YESB", "ICIC", "HDFC", "SBIN", "AXIS", "UTIB", "PUNB", "BARB", "IDIB", "KKBK", "INDB",
               "IOBA", "CNRB", "UBIN", "MAHB", "BKID", "IDFB", "RATN", "FDRL", "AUBL", "DBSS", "SCBL",
               "CITI", "HSBC", "PYTM", "APAY", "AXIB", "JIOP", "SLIC", "TIME", "OKAX", "OKHD"}
_UPI_RE = re.compile(r"^(?:UPI|IMPS|NEFT|RTGS)[-/]", re.IGNORECASE)
_IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$", re.IGNORECASE)


def parse_upi(description: str) -> str | None:
    """Merchant name inside a UPI/IMPS/NEFT descriptor, or None if this isn't one."""
    if not _UPI_RE.match(description.strip()):
        return None
    fields = [f.strip() for f in re.split(r"[-/|]", description.strip()) if f.strip()]
    vpa = None
    for field in fields:
        upper = field.upper()
        if upper in _UPI_NOISE or _IFSC_RE.match(field) or re.fullmatch(r"[\d\s]+", field):
            continue
        if "@" in field:
            vpa = vpa or field.split("@")[0]
            continue
        return field
    return vpa

_PREFIXES = re.compile(r"^(SQ \*|TST\* ?|PAYPAL \*|POS |DEBIT CARD PURCHASE |ACH )", re.IGNORECASE)
_NOISE = re.compile(r"[#*]?\d{3,}.*$|\s+[A-Z]{2}$|\s{2,}")


def clean_descriptor(description: str) -> str:
    """Best-effort merchant name for descriptors no rule recognises."""
    text = parse_upi(description) or description.strip()
    text = _PREFIXES.sub("", text)
    text = _NOISE.sub("", text).strip(" *-.")
    return text.title() if text else description.strip().title()


def match_builtin(description: str) -> tuple[str, str] | None:
    for rule in BUILTIN_RULES:
        if rule.pattern.search(description):
            return rule.merchant, rule.category
    return None


def categorize(description: str, user_rules: list[dict] | None = None) -> tuple[str, str, str]:
    """Return (merchant, category, source). Pure function — no I/O."""
    builtin = match_builtin(description)
    merchant = builtin[0] if builtin else clean_descriptor(description)
    for rule in sorted(user_rules or [], key=lambda r: -r["priority"]):
        pat = rule["pattern"].lower()
        if pat in description.lower() or pat in merchant.lower():
            return merchant, rule["category"], "user"
    if builtin:
        return merchant, builtin[1], "rule"
    return merchant, "Uncategorized", "default"


def llm_categorize(merchants: list[str]) -> dict[str, str]:
    """Ask gpt-4o-mini to classify merchants nothing else recognised.

    The LLM only picks a label from the fixed CATEGORIES list; it never touches amounts.
    Returns {} when no API key is configured or the call fails.
    """
    if not merchants or not settings.llm_enabled:
        return {}
    try:
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(model=settings.openai_model, temperature=0, api_key=settings.openai_api_key)
        allowed = [c for c in CATEGORIES if c not in ("Income", "Transfers")]
        prompt = (
            "Classify each merchant into exactly one category from this list: "
            f"{json.dumps(allowed)}.\nReply with a JSON object mapping merchant -> category, nothing else.\n"
            f"Merchants: {json.dumps(merchants)}"
        )
        raw = llm.invoke(prompt).content
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
        parsed = json.loads(raw)
        return {m: c for m, c in parsed.items() if c in allowed}
    except Exception as exc:  # network / parsing failures must never break an import
        log.warning("LLM categorisation failed: %s", exc)
        return {}
