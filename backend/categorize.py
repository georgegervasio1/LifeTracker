"""Rule-based categories for imported transactions (CSV / statement imports).

Plaid-synced transactions already come with categories; imports don't, so we assign
them here. Rules are checked in order — first match wins — so put specific rules
(e.g. groceries, card payments) before broad ones (e.g. food & drink).
"""
import re

# Categories that move money between your own accounts. Excluded from spending and income.
TRANSFER_CATEGORIES = {"Transfer", "Credit Card Payment", "Transfer In", "Transfer Out",
                       "Loan Payments", "Venmo Transfer"}

RULES = [
    ("Credit Card Payment", r"CREDIT CRD AUTOPAY|PAYMENT TO CHASE CARD|AUTOMATIC PAYMENT - THANK|PAYMENT THANK YOU|CARD PAYMENT|EPAY"),
    ("Transfer",            r"ONLINE TRANSFER|TRANSFER (FROM|TO)|WIRE (IN|OUT)|BOOK TRANSFER"),
    ("Income",              r"PAYROLL|DIRECT DEP|SALARY|INTEREST PAYMENT|DIVIDEND|IRS TREAS|TAX REF|ACHFILE|TAXRFD"),
    ("Taxes And Government", r"USATAXPYMT|IRS\b|SECRETARY OF STA|VILLAGE ?OF|\bDMV\b|CITY OF"),
    ("Zelle",               r"\bZELLE\b"),
    ("Cash App",            r"CASH APP"),
    ("Cash And ATM",        r"\bATM\b"),
    ("Venmo",               r"\bVENMO\b"),
    ("Fees",                r"\bFEE\b|SERVICE CHARGE|OVERDRAFT"),
    ("Groceries",           r"TRADER JOE|WHOLE FOODS|JEWEL|SAFEWAY|GIANT FOOD|HARRIS TEETER|MARIANO|\bALDI\b|KROGER|GROCERY|INSTACART|WEGMANS|FRESH MARKET|WHOLEFDS|GIANT \d|GROCER|SUPERMARKET|7-ELEVEN"),
    ("Travel",              r"\bUNITED\b|AMERICAN AIR|DELTA AIR|SOUTHWEST|JETBLUE|ALASKA AIR|SPIRIT AIR|HOTEL|MARRIOTT|HILTON|HYATT|AIRBNB|EXPEDIA|TRAVEL GUARD|BOOKING\.COM|VRBO"),
    ("Transportation",      r"METRA|VENTRA|\bUBER\b(?!.*EATS)|\bLYFT\b|DIVVY|WMATA|SMARTRIP|AMTRAK|PARKING|PARKMOBILE|\bSHELL\b|\bBP#|EXXON|CHEVRON|\bMOBIL\b|CITGO|SPEEDWAY|SUNOCO|\bTOLL\b|E-?ZPASS|CAPITAL BIKESHARE|^METRO\b|LIME\*|SOUTH SHORE RR|CAR RENTAL|\bHERTZ\b|\bAVIS\b|ENTERPRISE RENT"),
    ("Health And Fitness",  r"FITNESS|\bGYM\b|EQUINOX|ORANGETHEORY|PELOTON|CLASSPASS|SOULCYCLE|MARATHO|YOGA|CLIMB|WHOOP"),
    ("Personal Care",       r"\bCVS\b|WALGREENS|CLEANERS|SALON|BARBER|\bSPA\b|SEPHORA|\bULTA\b"),
    ("Medical",             r"PHARMACY|HOSPITAL|CLINIC|DENTAL|MEDICAL|DOCTOR|LABCORP|QUEST DIAG"),
    ("Entertainment",       r"TICKPICK|TICKETMASTER|STUBHUB|SEATGEEK|\bAMC\b|CINEMA|THEATRE|THEATER|NETFLIX|SPOTIFY|YOUTUBE|HULU|DISNEY|HBO|MAX\.COM|PEACOCK|APPLE\.COM/BILL|GOLF|GLF\*|BOWL|CONCERT|MUSEUM|TM \*|EVENTTICKET|COURSE\b|FGT\*"),
    ("Bills And Utilities", r"GAS LIGHT|\bWGL\b|PEPCO|DOMINION|COMED|COMCAST|XFINITY|VERIZON|T-MOBILE|AT&T|SPECTRUM|WATER|ELECTRIC|INSURANCE|GEICO|STATE FARM|\bRENT\b"),
    ("Gifts And Donations", r"DONAT|CHARITY|GOFUNDME|FOUNDATION"),
    ("Shopping",            r"TARGET|AMAZON|AMZN|WALMART|COSTCO|BEST BUY|\bNIKE\b|LULULEMON|J\.?CREW|UNIQLO|ETSY|EBAY|SHOP\b|\bSP\b|STORE|MACYS|VINEYARD VINES|DHGATE|NORDSTROM|BLOOMINGDALE|ZARA"),
    ("Food And Drink",      r"TST\*|SQ \*|RESTAURANT|CAFE|COFFEE|PIZZA|SWEETGREEN|DAIRY QUEEN|STARBUCKS|CHIPOTLE|\bBAR\b|\bPUB\b|GRILL|KITCHEN|TAVERN|LOUNGE|BREW|DOORDASH|UBER EATS|GRUBHUB|BAKERY|\bDELI\b|TACO|BURGER|SUSHI|ALE HOUSE|SUPPER CLUB|LIQUOR|WINE|SALOON|FLAT\b|CLUB\b|SALOON|DINER|BAGEL|CREPE|NOODLE|RAMEN|\bTHAI\b|SMOKEHOUSE|BBQ|LEVY@|365 MARKET|STEAK|\bTAP\b|CANTINA|\bWINGS?\b|CAVA\b|MCDONALD|PORTILLO|HOT DOG|BUBBLE TEA|CONEY|BISTRO|CONCESSION|\bINN\b|TAQUER|BURRITO|SANDWICH|SUBWAY|SHAKE SHACK|PANERA"),
]
_COMPILED = [(cat, re.compile(rx, re.I)) for cat, rx in RULES]


def categorize(description, amount=None, is_card=False):
    """Return a category for a transaction. amount follows LifeTracker's convention:
    positive = money out, negative = money in."""
    d = description or ""
    for cat, rx in _COMPILED:
        if rx.search(d):
            return cat
    if amount is not None and amount < 0:
        # Money coming back onto a credit card that isn't a payment is a refund/credit.
        return "Refund" if is_card else "Other Income"
    return "Other"
