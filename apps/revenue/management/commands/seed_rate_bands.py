"""
Seeds real gazette-derived rate bands, transcribed from
docs/reference/KAC Gazette.xlsx (via the cleaned-up per-bye-law split in
docs/reference/KAC Gazette - Split by Bye-Law/). Only schedules whose
structure and numbers are unambiguous are transcribed here:

- Control of Advertisement (30010034): 24 RANGE bands, one per sign/advert
  type. Source: split doc 02.
- Liquor Licensing (30010051): 9 TIERED bands (Large/Medium/Small), one per
  licensed establishment type. Source: split doc 09.
- Communication Mast License (30010056): one unlabeled TIERED band
  (Large/Medium/Small) — the gazette gives one flat triple for the whole
  item, no sub-classification. Source: split doc 02 (Category D cross-ref).
- Building & Construction Materials Dealers (30010035): 16 RANGE bands.
  Source: split doc 04.
- Trade License, Private Lockup Shops and Allied Matters (30010043): 84
  RANGE bands (69 "yearly shops/kiosks" types + 15 "yearly trade licence
  business premises" types — both schedules price the same item). Source:
  split doc 06.
- Contractors (30010048): 4 bands — Construction and Supply are TIERED by
  the contractor's own project-value bracket (4 tiers each); Services and
  Consultancy are FLAT. Source: split doc 08. (Doc 08 also lists Tender
  Fees, a separate concept under a different harmonised code — not seeded
  here; two of its four figures are corrupted in the source ("1424000",
  inconsistent with the other two clean values), not safe to hardcode.)
- Wrong Parking, Corporate Parking Permit/License (30010052): 3 RANGE bands
  — just the "Corporate Parking Permit Fee/Per Annum" table. Source: split
  doc 10. (The "Wrong Parking" figures next to it are penalties, not a
  chargeable fee — correctly excluded.)
- Community and Development Levy (30010062): 112 bands, mostly TIERED by
  Rural/Semi Urban/Urban (a few 2-tier where the source has no Rural
  figure), one FLAT ("Individual (Adult)"). Source: split doc 11. Where the
  same establishment name recurs across the source's Category A/B/C with
  different prices (e.g. "Construction Companies"), the category is
  appended to the label to disambiguate — every label must be unique per
  item. Three data points were dropped as unrecoverable transcription
  errors rather than guessed at: "Construction Companies" (National)'s
  Rural figure ("200.000.00" — ambiguous multi-dot number), "Supermarket"'s
  Urban figure ("100,000,00" — malformed thousands-grouping), and
  "Corporate Offices"'s Rural figure (a literal encoding-corrupted
  character in the source). This file's own Category D (Communication
  Mast) duplicate is skipped entirely — its numbers are corrupted in this
  copy ("Large :143,500,000.00" etc.) and a clean version is already seeded
  from split doc 02/09 above.

Deliberately NOT transcribed, per the split's own README and this command's
earlier pass: Tenement Rate Collection (30010049) — no bye-law explicitly
named this in the source; Regulated Premises (30010054) and Foodstuff
Regulation (30010053) — the source gives Regulated Premises three
overlapping, partly self-contradictory schedules (e.g. "Canteen" priced two
incompatible ways). Real data, too ambiguous to responsibly hardcode — enter
via the admin rate-band editor once confirmed against the council's actual
current bye-law text.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.revenue.models import CouncilRevenueItem
from apps.revenue.services import replace_rate_bands
from apps.tenancy.context import set_council_context
from apps.tenancy.models import Council

# (label, min_amount, max_amount)
CONTROL_OF_ADVERTISEMENT_BANDS = [
    ("School Sign Board", 20800, 40800),
    ("Neon Sign", 20200, 40200),
    ("Metal Fixed", 10400, 20400),
    ("Wooden Fixed", 10800, 20800),
    ("Metal Standing (Two Faces)", 10800, 20800),
    ("Metal Standing (Dual Faces)", 10400, 20400),
    ("Wooden Standing", 10000, 20000),
    ("Wooden Standing (Two Faces)", 20000, 40000),
    ("Electrical Fixed", 15000, 25000),
    ("Plastic Fixed", 15000, 25000),
    ("Electrical Standing", 20000, 40000),
    ("Electrical Standing (Two Faces)", 38000, 68000),
    ("Plastic Standing", 20000, 40000),
    ("Plastic Standing (Two Faces)", 38000, 38000),
    ("Special Sign Board", 150000, 300000),
    ("Carving", 8000, 16000),
    ("Banners", 10000, 20000),
    ("Posters", 5000, 10000),
    ("Tin Plates", 20000, 40000),
    ("Advert on Cloths (Prior to Colour)", 30000, 60000),
    ("Major Highway / Town Bill Board", 350000, 500000),
    ("Lamp Plate Advert", 60000, 100000),
    ("Multinational Companies", 700000, 1000000),
    ("Financial Institution", 200000, 500000),
]

# (label, large, medium, small)
LIQUOR_LICENSING_BANDS = [
    ("Wholesale Liquor", 200000, 100000, 50000),
    ("Depot (Beer)", 500000, 250000, 150000),
    ("Departmental / Super Store Liquor", 200000, 100000, 50000),
    ("Supermarket / Shop", 50000, 20000, 15000),
    ("Restaurant Liquor", 20000, 10000, 5000),
    ("Hotels", 500000, 200000, 70000),
    ("Beer Parlor", 20000, 10000, 5000),
    ("Native Liquor", 1500, 500, 100),
    ("Club Liquor", 150000, 100000, 50000),
]

COMMUNICATION_MAST_TIERS = [("Large", 2000000), ("Medium", 1500000), ("Small", 1000000)]

# (label, min_amount, max_amount)
BUILDING_MATERIALS_BANDS = [
    ("Paint Depot", 50000, 100000),
    ("Cement", 10000, 20000),
    ("Cement (Warehouse)", 50000, 70000),
    ("Iron", 50000, 70000),
    ("Rod/Pipe", 70000, 100000),
    ("Paint Shop", 10000, 20000),
    ("POP Cement", 20000, 50000),
    ("Aluminum Profile", 100000, 250000),
    ("Roofing Sheet", 50000, 100000),
    ("Plumbing Material", 30000, 50000),
    ("Tiles", 50000, 150000),
    ("Iron Gate/Doors", 70000, 200000),
    ("Electrical Material (Cable, Transformer, Poles & Pipe)", 150000, 500000),
    ("Ceiling Material", 40000, 70000),
    ("Gravel Site", 150000, 250000),
    ("Sand Seller", 50000, 150000),
]

# (label, min_amount, max_amount) — First Schedule (yearly shops/kiosks) +
# Second Schedule (yearly trade licence, business premises), same item.
TRADE_LICENSE_BANDS = [
    ("Clinic/Private Hospitals", 10000, 45000),
    ("Patent Medicine Dealers", 5000, 15000),
    ("Pharmacy", 15000, 80000),
    ("Blacksmith", 2000, 10000),
    ("Goldsmith", 2000, 10000),
    ("POS", 10000, 25000),
    ("Printing Press", 10000, 100000),
    ("Dentist Shop", 10000, 50000),
    ("Herbal Shop", 5000, 20000),
    ("Optical Shop", 5000, 20000),
    ("Mechanic Workshop", 5000, 25000),
    ("Vulcanizing Shop", 1000, 2000),
    ("Watch Repairing Shop", 2000, 5000),
    ("Carwash", 5000, 20000),
    ("Grinding Machine", 5000, 15000),
    ("Welding Shop", 5000, 20000),
    ("Electrical Appliances Shop", 5000, 20000),
    ("Electronic Workshop", 5000, 20000),
    ("Pool/Bet Shop", 10000, 30000),
    ("Panel Beater Workshop", 5000, 20000),
    ("Spare Parts (Motor)", 15000, 70000),
    ("Spare Parts (Try-cycle)", 5000, 20000),
    ("Spare Parts (Motor Cycle)", 5000, 10000),
    ("Spare Parts (Bicycle)", 2000, 5000),
    ("Airline Ticketing Office", 50000, 150000),
    ("Media Houses (Print)", 200000, 300000),
    ("Departmental Store", 150000, 500000),
    ("Provision Store", 2000, 10000),
    ("Super Store", 50000, 100000),
    ("Super Market", 20000, 70000),
    ("Telephone Accessories Shop", 2000, 5000),
    ("Car Stand", 50000, 100000),
    ("Beauty Shop", 5000, 10000),
    ("Cosmetic Shop", 5000, 20000),
    ("Plastic Product Shop", 5000, 20000),
    ("Bookshop and Stationaries", 5000, 30000),
    ("Electrical Shop", 10000, 30000),
    ("Electronic Shop", 15000, 35000),
    ("Gas Refilling Shop", 5000, 10000),
    ("Coffee Shop", 5000, 10000),
    ("Bicycle Shop", 10000, 20000),
    ("Tricycle Shop", 20000, 50000),
    ("Motor Cycle Shop", 15000, 30000),
    ("Kiosk", 2000, 5000),
    ("Block Moulding Stand", 10000, 50000),
    ("Furniture/Carpentry Shop", 5000, 10000),
    ("Hair Dressing Salon", 3000, 10000),
    ("Barbing Salon Shop", 3000, 10000),
    ("Cinema Houses", 20000, 50000),
    ("Viewing Centres", 5000, 10000),
    ("Rentals", 5000, 10000),
    ("Photographic Studio", 2000, 5000),
    ("Business Centre/Cyber Cafe", 5000, 10000),
    ("Curtain Material Shop", 10000, 30000),
    ("Boutique Shop", 5000, 15000),
    ("Agro-Chemical Shop", 10000, 30000),
    ("Industrial Chemical Shop", 15000, 30000),
    ("Tools/Equipment Shop", 50000, 150000),
    ("Tailoring/Fashion Design Shop", 5000, 15000),
    ("Shoe Maker Shop", 5000, 20000),
    ("Spray/Painter Workshop", 5000, 20000),
    ("Mobile Phone Shop", 20000, 50000),
    ("Corporate Offices", 10000, 20000),
    ("Foam Shop", 10000, 20000),
    ("Foam Depot", 50000, 150000),
    ("Arts and Crafts Shop", 3000, 5000),
    ("Kerosene Shop", 3000, 5000),
    ("Soap and Detergent Depot", 20000, 50000),
    ("Tobacco Distribution Shop", 20000, 70000),
    ("Commercial Banks", 250000, 300000),
    ("Micro Finance Banks", 50000, 100000),
    ("Insurance Company", 50000, 100000),
    ("Bureau de Change", 100000, 200000),
    ("Mortgage Banks", 100000, 200000),
    ("Construction Company (Multi-National)", 500000, 1000000),
    ("Construction Company (Local)", 200000, 509000.13),
    ("Petrol Station", 100000, 150000),
    ("Quarries", 500000, 750000),
    ("Power Distribution Companies (DISCO's)", 1000000, 1500000),
    ("Telecommunication Company", 200000, 500000),
    ("Furniture Showroom", 200000, 500000),
    ("Warehouse (Exception of Premises and Building Materials Regulated)", 150000, 500000),
    ("Electric/Electronic Equipment Installation Company", 150000, 500000),
    ("Manufacturing Company", 150000, 450000),
]

# (label, [(bracket_label, fee), ...]) for TIERED bands; (label, flat_amount) for FLAT
CONTRACTORS_TIERED = [
    ("Construction", [
        ("₦1,000,000 – ₦5,000,000", 20000),
        ("₦5,100,000 – ₦50,000,000", 35000),
        ("₦50,100,000 – ₦99,000,000", 150000),
        ("₦100,000,000 and Above", 300000),
    ]),
    ("Supply", [
        ("₦100,000 – ₦1,000,000", 20000),
        ("₦1,100,000 – ₦10,000,000", 30000),
        ("₦11,000,000 – ₦50,000,000", 100000),
        ("₦51,000,000 and Above", 250000),
    ]),
]
CONTRACTORS_FLAT = [
    ("Services", 24000),
    ("Consultancy", 120000),
]

# (label, min_amount, max_amount) — Corporate Parking Permit Fee/Per Annum only.
WRONG_PARKING_CORPORATE_BANDS = [
    ("Lorries/Tippers", 250000, 500000),
    ("Car/Buses/Vans/Pick-up", 500000, 1000000),
    ("Dyna Delivery Van/J5", 150000, 250000),
]

# (label, [(tier_label, amount), ...]) for TIERED; a single-tier entry is
# seeded as FLAT instead (Individual (Adult), below).
COMMUNITY_LEVY_TIERED = [
    ("Construction Companies (Multinational)", [("Semi Urban", 1500000), ("Urban", 3000000)]),
    ("Oil and Gas Companies (Multinational)", [("Semi Urban", 2500000), ("Urban", 5000000)]),
    ("Manufacturing Companies (Multinational)", [("Semi Urban", 750000000), ("Urban", 1500000)]),
    ("Consultancy Firms (Multinational)", [("Semi Urban", 750000000), ("Urban", 1500000)]),
    ("Marine (Multinational)", [("Semi Urban", 1750000), ("Urban", 3500000)]),
    ("Quarries (Multinational)", [("Semi Urban", 1750000), ("Urban", 2000000)]),
    ("Warehouse/Depot/Showroom, Departmental Store (Multinational)", [("Semi Urban", 750000000), ("Urban", 1500000)]),
    ("Workshop/Yard (Multinational)", [("Semi Urban", 1000000), ("Urban", 2000000)]),
    ("Trailer/Heavy Duty/Equipment/Industrial Park (Multinational)", [("Semi Urban", 1500005), ("Urban", 3000000)]),
    ("Courier Company/Delivery Service Agent (Multinational)", [("Semi Urban", 250000), ("Urban", 500000)]),
    ("Commercial Banks (National)", [("Rural", 200000), ("Semi Urban", 500000), ("Urban", 1000000)]),
    ("Construction Companies (National)", [("Semi Urban", 500000), ("Urban", 1000000)]),
    ("Oil and Gas Companies (National)", [("Semi Urban", 1000000), ("Urban", 2000000)]),
    ("Mortgage Banks (National)", [("Rural", 150000), ("Semi Urban", 250000), ("Urban", 500000)]),
    ("Manufacturing Companies (National)", [("Semi Urban", 250000), ("Urban", 500000)]),
    ("Microfinance Banks (National)", [("Rural", 50000), ("Semi Urban", 125000), ("Urban", 250000)]),
    ("Power Distribution Companies (DISCO's) (National)", [("Semi Urban", 1250000), ("Urban", 2500000)]),
    ("Quarries (National)", [("Semi Urban", 1000000), ("Urban", 2000000)]),
    ("Media Houses (Electronic) (National)", [("Semi Urban", 750000), ("Urban", 1500000)]),
    ("Insurance Companies (National)", [("Semi Urban", 75000), ("Urban", 150000)]),
    ("Bureau De Change (National)", [("Semi Urban", 100000), ("Urban", 200000)]),
    ("Petrol Station (National)", [("Rural", 30000), ("Semi Urban", 100000), ("Urban", 200000)]),
    ("Petrol Station (Mega) (National)", [("Semi Urban", 200000), ("Urban", 400000)]),
    ("Furniture Warehouses/Showrooms/Departmental Stores (National)", [("Rural", 20000), ("Semi Urban", 250000), ("Urban", 500000)]),
    ("Electrical/Electronic Equipment/Installation Companies (National)", [("Rural", 25000), ("Semi Urban", 250000), ("Urban", 500000)]),
    ("Estate/Facility Development Companies/Organization (National)", [("Semi Urban", 1000000), ("Urban", 2000000)]),
    ("Private Schools (National)", [("Rural", 20000), ("Semi Urban", 100000), ("Urban", 200000)]),
    ("Soap/Detergent & Other Allied Products (National)", [("Semi Urban", 250000), ("Urban", 500000)]),
    ("Farms/Facilities Not Applicable to Local or Peasant Farmers (National)", [("Semi Urban", 250000), ("Urban", 500000)]),
    ("Warehouse/Depot/Showroom (National)", [("Semi Urban", 250000), ("Urban", 500000)]),
    ("Workshop/Yard (National)", [("Rural", 25000), ("Semi Urban", 100000), ("Urban", 200000)]),
    ("Trailer/Heavy Duty/Equipment/Industrial Park (National)", [("Semi Urban", 200000), ("Urban", 400000)]),
    ("Courier Company/Delivery Service Agent (National)", [("Semi Urban", 100000), ("Urban", 200000)]),
    ("Guest Houses/Motels (Local)", [("Rural", 30000), ("Semi Urban", 25000), ("Urban", 50000)]),
    ("Hotels (5 Star) (Local)", [("Semi Urban", 750000), ("Urban", 1500000)]),
    ("Hotels (4 Star) (Local)", [("Semi Urban", 500000), ("Urban", 1000000)]),
    ("Hotels (3 Star) (Local)", [("Semi Urban", 350000), ("Urban", 700000)]),
    ("Hotels (2 Star) (Local)", [("Semi Urban", 250000), ("Urban", 500000)]),
    ("Hotels (Ordinary) (Local)", [("Rural", 50000), ("Semi Urban", 200000), ("Urban", 400000)]),
    ("Private Clinic/Hospital (Local)", [("Rural", 30000), ("Semi Urban", 100000), ("Urban", 200000)]),
    ("Eatery/Restaurant (Local)", [("Rural", 5000), ("Semi Urban", 150000), ("Urban", 300000)]),
    ("Shop/Offices (Local)", [("Rural", 10000), ("Semi Urban", 50000), ("Urban", 100000)]),
    ("Bakery/Confectioneries (Local)", [("Rural", 30000), ("Semi Urban", 100000), ("Urban", 200000)]),
    ("Bottle/Pure Water Company/Factory (Local)", [("Rural", 100000), ("Semi Urban", 150000), ("Urban", 300000)]),
    ("Pure Water Company/Factory (Local)", [("Rural", 20000), ("Semi Urban", 50000), ("Urban", 100000)]),
    ("Duplex", [("Rural", 10000), ("Semi Urban", 30000), ("Urban", 60000)]),
    ("Flat", [("Rural", 5000), ("Semi Urban", 15000), ("Urban", 35000)]),
    ("Bungalow", [("Rural", 5000), ("Semi Urban", 10000), ("Urban", 20000)]),
    ("Self-Contain Apartment", [("Rural", 2000), ("Semi Urban", 3000), ("Urban", 10000)]),
    ("Mansion", [("Rural", 20000), ("Semi Urban", 150000), ("Urban", 300000)]),
    ("One and Two Bedroom", [("Rural", 2000), ("Semi Urban", 3000), ("Urban", 6000)]),
    ("Patent Medicine Dealer", [("Rural", 5000), ("Semi Urban", 10000), ("Urban", 20000)]),
    ("Pharmacy", [("Rural", 10000), ("Semi Urban", 50000), ("Urban", 100000)]),
    ("Printing Press", [("Rural", 25000), ("Semi Urban", 50000), ("Urban", 100000)]),
    ("Dentist Shop", [("Rural", 10000), ("Semi Urban", 25000), ("Urban", 50000)]),
    ("Optical Shop", [("Rural", 10000), ("Semi Urban", 10000), ("Urban", 20000)]),
    ("Mechanic Workshop", [("Rural", 5000), ("Semi Urban", 10000), ("Urban", 20000)]),
    ("Watch Repairing Shop", [("Rural", 2000), ("Semi Urban", 2500), ("Urban", 5000)]),
    ("Carwash", [("Rural", 5000), ("Semi Urban", 10000), ("Urban", 20000)]),
    ("Welding Shop", [("Rural", 5000), ("Semi Urban", 10000), ("Urban", 20000)]),
    ("Electrical Appliances Shop", [("Rural", 70000), ("Semi Urban", 10000), ("Urban", 20000)]),
    ("Electronic Workshop", [("Rural", 7000), ("Semi Urban", 15000), ("Urban", 30000)]),
    ("Pool/Bet Shop", [("Rural", 10000), ("Semi Urban", 25000), ("Urban", 50000)]),
    ("Panel Beater Workshop", [("Rural", 5000), ("Semi Urban", 15000), ("Urban", 30000)]),
    ("Spare Parts (Motor)", [("Rural", 15000), ("Semi Urban", 35000), ("Urban", 70000)]),
    ("Spare Parts (Try-cycle)", [("Rural", 10000), ("Semi Urban", 15000), ("Urban", 30000)]),
    ("Spare Parts (Motor Cycle)", [("Rural", 10000), ("Semi Urban", 15000), ("Urban", 30000)]),
    ("Airline Ticketing Office", [("Semi Urban", 100000), ("Urban", 200000)]),
    ("Media Houses (Print)", [("Rural", 150000), ("Semi Urban", 200000), ("Urban", 400000)]),
    ("Departmental Store", [("Rural", 100000), ("Semi Urban", 250000), ("Urban", 500000)]),
    ("Provision Store", [("Rural", 5000), ("Semi Urban", 15000), ("Urban", 30000)]),
    ("Super Store", [("Rural", 10000), ("Semi Urban", 15000), ("Urban", 30000)]),
    ("Supermarket", [("Rural", 10000), ("Semi Urban", 50000)]),
    ("Telephone Accessories Shop", [("Rural", 10000), ("Semi Urban", 15000), ("Urban", 30000)]),
    ("Car Stand", [("Semi Urban", 100000), ("Urban", 200000)]),
    ("Beauty Shop", [("Rural", 5000), ("Semi Urban", 15000), ("Urban", 30000)]),
    ("Cosmetics Shop", [("Rural", 5000), ("Semi Urban", 15000), ("Urban", 30000)]),
    ("Plastic Product Shop", [("Rural", 5000), ("Semi Urban", 10000), ("Urban", 20000)]),
    ("Bookshop and Stationeries", [("Rural", 5000), ("Semi Urban", 15000), ("Urban", 30000)]),
    ("Electrical Shop", [("Rural", 5000), ("Semi Urban", 15000), ("Urban", 30000)]),
    ("Electronic Shop", [("Rural", 5000), ("Semi Urban", 15000), ("Urban", 30000)]),
    ("Gas Refilling Shop", [("Rural", 5000), ("Semi Urban", 25000), ("Urban", 50000)]),
    ("Coffee Shop", [("Semi Urban", 5000), ("Urban", 10000)]),
    ("Tricycle Shop (Already Assembled)", [("Rural", 10000), ("Semi Urban", 25000), ("Urban", 50000)]),
    ("Motor Cycle Shop (Already Assembled)", [("Rural", 10000), ("Semi Urban", 15000), ("Urban", 30000)]),
    ("Block/Interlock Industry", [("Rural", 5000), ("Semi Urban", 150000), ("Urban", 300000)]),
    ("Furniture/Carpentry Shop", [("Rural", 5000), ("Semi Urban", 15000), ("Urban", 30000)]),
    ("Hair Dressing Salon", [("Rural", 2000), ("Semi Urban", 5000), ("Urban", 10000)]),
    ("Barbing Salon Shop", [("Rural", 2000), ("Semi Urban", 5000), ("Urban", 10000)]),
    ("Cinema Houses", [("Rural", 10000), ("Semi Urban", 25000), ("Urban", 50000)]),
    ("Viewing Centres", [("Semi Urban", 10000), ("Urban", 30000)]),
    ("Rentals", [("Rural", 10000), ("Semi Urban", 15000), ("Urban", 30000)]),
    ("Photographic Studio", [("Rural", 2000), ("Semi Urban", 5000), ("Urban", 10000)]),
    ("Business Centre/Cyber Cafe", [("Rural", 5000), ("Semi Urban", 10000), ("Urban", 20000)]),
    ("Curtain Material Shop", [("Rural", 5000), ("Semi Urban", 15000), ("Urban", 30000)]),
    ("Boutique Shop", [("Rural", 5000), ("Semi Urban", 15000), ("Urban", 30000)]),
    ("Agro-Chemical Shop", [("Rural", 10000), ("Semi Urban", 15000), ("Urban", 30000)]),
    ("Industrial Chemical Shop", [("Rural", 10000), ("Semi Urban", 15000), ("Urban", 30000)]),
    ("Tools/Equipment Shop", [("Rural", 10000), ("Semi Urban", 75000), ("Urban", 150000)]),
    ("Tailoring/Fashion Design Shop", [("Rural", 5000), ("Semi Urban", 25000), ("Urban", 50000)]),
    ("Shoe Maker Shop", [("Rural", 5000), ("Semi Urban", 10000), ("Urban", 20000)]),
    ("Spray/Painter Workshop", [("Rural", 10000), ("Semi Urban", 15000), ("Urban", 30000)]),
    ("Mobile Phone Shop", [("Rural", 10000), ("Semi Urban", 25000), ("Urban", 50000)]),
    ("Corporate Offices", [("Semi Urban", 15000), ("Urban", 30000)]),
    ("Foam Shop", [("Rural", 10000), ("Semi Urban", 25000), ("Urban", 50000)]),
    ("Cement Shop or Store (Not Depot or Warehouse)", [("Rural", 10000), ("Semi Urban", 30000), ("Urban", 60000)]),
    ("Arts and Crafts Shop", [("Rural", 2000), ("Semi Urban", 5000), ("Urban", 10000)]),
    ("Kerosene Shop", [("Rural", 2000), ("Semi Urban", 5000), ("Urban", 10000)]),
    ("Soap and Detergent Shop (Not Depot or Warehouse)", [("Rural", 10000), ("Semi Urban", 25000), ("Urban", 50000)]),
    ("Tobacco Distribution Shop (Not Depot or Warehouse)", [("Rural", 50000), ("Semi Urban", 175000), ("Urban", 350000)]),
    ("Photo Lab", [("Rural", 10000), ("Semi Urban", 50000), ("Urban", 100000)]),
]
COMMUNITY_LEVY_FLAT = [
    ("Individual (Adult)", 1000),
]


class Command(BaseCommand):
    help = "Seed real gazette-derived rate bands for the unambiguous KAC Gazette schedules."

    def add_arguments(self, parser):
        parser.add_argument("--council-code", default="KAC")

    @transaction.atomic
    def handle(self, *args, **options):
        # SET LOCAL (what set_council_context issues) is transaction-scoped —
        # must run inside one atomic block for the whole command, same as
        # seed_kuje.py's own @transaction.atomic handle().
        council = Council.objects.get(council_code=options["council_code"])
        set_council_context(council.id)

        self._seed(council, "30010034", "Control of Advertisement", [
            self._range(label, mn, mx) for label, mn, mx in CONTROL_OF_ADVERTISEMENT_BANDS
        ])
        self._seed(council, "30010051", "Liquor Licensing", [
            self._tiered(label, [("Large", large), ("Medium", medium), ("Small", small)])
            for label, large, medium, small in LIQUOR_LICENSING_BANDS
        ])
        self._seed(council, "30010056", "Communication Mast License", [
            self._tiered("", COMMUNICATION_MAST_TIERS)
        ])
        self._seed(council, "30010035", "Building & Construction Materials Dealers", [
            self._range(label, mn, mx) for label, mn, mx in BUILDING_MATERIALS_BANDS
        ])
        self._seed(council, "30010043", "Trade License, Private Lockup Shops and Allied Matters", [
            self._range(label, mn, mx) for label, mn, mx in TRADE_LICENSE_BANDS
        ])
        self._seed(council, "30010048", "Contractors", [
            self._tiered(label, tiers) for label, tiers in CONTRACTORS_TIERED
        ] + [
            self._flat(label, amount) for label, amount in CONTRACTORS_FLAT
        ])
        self._seed(council, "30010052", "Wrong Parking, Corporate Parking Permit/License", [
            self._range(label, mn, mx) for label, mn, mx in WRONG_PARKING_CORPORATE_BANDS
        ])
        self._seed(council, "30010062", "Community and Development Levy", [
            self._tiered(label, tiers) for label, tiers in COMMUNITY_LEVY_TIERED
        ] + [
            self._flat(label, amount) for label, amount in COMMUNITY_LEVY_FLAT
        ])

    @staticmethod
    def _range(label, min_amount, max_amount):
        return {"label": label, "rate_mode": "RANGE", "min_amount": min_amount, "max_amount": max_amount}

    @staticmethod
    def _tiered(label, tiers):
        return {"label": label, "rate_mode": "TIERED", "tiers": [{"label": t, "amount": a} for t, a in tiers]}

    @staticmethod
    def _flat(label, amount):
        return {"label": label, "rate_mode": "FLAT", "flat_amount": amount}

    def _get_item(self, council, code, name):
        try:
            return CouncilRevenueItem.objects.get(council=council, harmonised_code=code)
        except CouncilRevenueItem.DoesNotExist:
            self.stdout.write(self.style.WARNING(f"{code} {name} not activated for {council.council_code} — skipping"))
            return None

    def _seed(self, council, code, name, bands):
        item = self._get_item(council, code, name)
        if item is None:
            return
        if item.active_bands.exists():
            self.stdout.write(self.style.WARNING(f"{code} {name} already has rate bands — skipping (already seeded)"))
            return
        replace_rate_bands(council_revenue_item=item, actor=None, bands=bands)
        self.stdout.write(self.style.SUCCESS(f"{code} {name}: seeded {len(bands)} band(s)"))
