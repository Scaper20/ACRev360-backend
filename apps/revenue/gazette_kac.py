"""
Rate schedules transcribed directly from the Kuje Area Council Gazette
(S.I. No. 10 of 2024 — Bye-Laws 2023), `docs/KAC NEW GAzETTE (4).pdf`.

Everything here was read off the gazette's own pages, not from
`docs/reference/KAC Gazette.xlsx`. That matters: the XLSX is a secondary
transcription and is demonstrably incomplete — its Schedule "C" has 22 rows
where the gazette has 25.

The PDF is a 144-page scan with no text layer, so it cannot be parsed. Each
page is a single embedded 200 DPI JPEG (DCTDecode); the images were extracted
losslessly with pypdf and read page by page. Every figure below carries its
gazette page ("B nnn") so it can be checked against the source.

Discipline, same as `seed_rate_bands`: transcribe what is legible, never guess
a digit, and record the gazette's own typos rather than silently tidying them.

Page-number caution: the PDF index → gazette "B" number offset drifts from +166
to +177 across the document, so roughly ten gazette pages are absent from this
scan. A schedule that appears to be missing may simply be on a page that was
not scanned — see `docs/backups/` notes and the findings log before concluding
that a bye-law has no schedule.
"""

# ---------------------------------------------------------------------------
# Part XX — Contractors Bye-Law (No. 19) 2023, s.5 (gazette B276)
#
# Fills the gap seed_rate_bands.py documented and refused to guess at: "Doc 08
# also lists Tender Fees ... two of its four figures are corrupted in the
# source ('1424000', inconsistent with the other two clean values), not safe to
# hardcode." All four are clean and unambiguous in the gazette itself.
#
# (label, amount)
# ---------------------------------------------------------------------------
TENDER_FEES_FLAT = [
    ("Construction", 60000),
    ("Supplies", 24000),
    ("Services", 12000),
    ("Consultancy/Technical Partners", 24000),
]

# ---------------------------------------------------------------------------
# Part V — Schedule "C" (gazette B219)
# "Fee payable for issuance of Certificate of Fitness for Habitation (CFH) or
#  Certificate of Fitness of Continued Habitation"
#
# Three charge columns per establishment, so each row becomes three FLAT bands
# rather than one — they are three distinct charges, not a range.
#
# The XLSX transcription stopped at row 22; rows 23-25 (Production Plants,
# Transport/Logistics, Private Building and Others) exist only in the gazette.
#
# Two gazette quirks preserved deliberately:
#   - the S/N column numbers two different rows "7" (Fabric and Leasing
#     Companies). Both are kept; they are distinct establishments.
#   - several spellings are the gazette's own (MANUFACTORING, HOSPTIALS,
#     PHAMACEAUTICAL, PETROLEUM DEPORT). Left as printed, title-cased.
#
# The N100,000,000 multinational figure was flagged in an earlier pass as
# needing council confirmation before being entered anywhere. The gazette page
# confirms it verbatim, so it is no longer an unverified number — though its
# sheer scale is still worth the council's eye.
#
# (establishment, habitation_old, continued_habitation_new, fitness_for_use_renewal)
# ---------------------------------------------------------------------------
CERTIFICATE_OF_FITNESS_SCHEDULE_C = [
    ("Multi-National Companies", 100000000, 40000000, 60000000),
    ("Oil & Gas Companies (Local)", 60000000, 30000000, 45000000),
    ("Oil Service Companies", 20000000, 30000000, 30000000),
    ("Petroleum Deport", 20000000, 15000000, 15000000),
    ("Financial Institution", 5000000, 2500000, 3000000),
    ("Construction Companies", 10000000, 7000000, 7500000),
    ("Fabric Companies", 500000, 1000000, 200000),
    ("Leasing Companies", 500000, 1000000, 100000),
    ("Technical Equipment & Machinery Companies", 500000, 1000000, 200000),
    ("Manufactoring Companies", 1000000, 2500000, 750000),
    ("Quarry Companies", 3000000, 7000000, 1000000),
    ("Hospitality Industries", 100000, 250000, 50000),
    ("Hosptials", 100000, 250000, 50000),
    ("Schools", 50000, 150000, 20000),
    ("Petrol/Filling Stations", 250000, 500000, 100000),
    ("Phamaceautical Companies", 75000, 150000, 50000),
    ("Farms", 500000, 750000, 700000),
    ("Ware Houses", 200000, 100000, 50000),
    ("Pipe Yards", 150000, 200000, 100000),
    ("Electrical/Power Holding Co.", 2000000, 5000000, 1000000),
    ("Plazas", 250000, 500000, 100000),
    ("Workshops", 25000, 75000, 15000),
    ("Public Building", 3000000, 5000000, 200000),
    ("Production Plants", 2000000, 5000000, 1000000),
    ("Transport/Logistics Companies", 1000000, 3000000, 500000),
    ("Private Building and Others", 100000, 200000, 50000),
]

CFH_CHARGE_LABELS = [
    "Fitness for Habitation (Old Building)",
    "Fitness for Continued Habitation (New Building)",
    "Fitness for Use (Renewal)",
]


def certificate_of_fitness_bands():
    """Schedule "C" flattened to 78 FLAT band specs — 26 establishments × the
    three charge types, each labelled "{Establishment} — {Charge}" so every
    label is unique per item as `replace_rate_bands` requires."""
    specs = []
    for establishment, *amounts in CERTIFICATE_OF_FITNESS_SCHEDULE_C:
        for charge_label, amount in zip(CFH_CHARGE_LABELS, amounts):
            specs.append((f"{establishment} — {charge_label}", amount))
    return specs


# ---------------------------------------------------------------------------
# Bye-law citations read off each Part's own BODY HEADER, not the gazette's
# table of contents.
#
# This distinction is not pedantry: the Arrangement of Parts at B168 lists
# "PART XXV — Foodstuff Regulations Bye-law (No. 24)", but the Part itself at
# B301 is headed "PART XXV — COMMUNITY DEVELOPMENT LEVY AND ALLIED MATTERS
# BYE-LAW (NO.24)". Same bye-law number, different title. The body governs.
# (The gazette is internally inconsistent about bye-law *numbers* too — Pest
# Control's citation clause calls itself No. 21 while the TOC calls it No. 18 —
# so only the Part numeral is relied on here.)
# ---------------------------------------------------------------------------
PART_TITLES = {
    "Part I": "Preliminary",
    "Part II": "Enabling Law, Application, Functions and Establishment of Area Council Departments (No. 1)",
    "Part III": "Registration of Marriages, Births and Deaths (No. 2)",
    "Part IV": "Motor Parks — Commercial Vehicles Picking-Up Passengers (No. 3)",
    "Part V": "Environmental Sanitation, Premises Inspection and Prohibition of Indiscriminate Dumping (No. 4)",
    "Part VI": "Control of Advertisement (No. 5)",
    "Part VII": "Regulation of Mobile Advertisement (No. 6)",
    "Part VIII": "Stacking of Building Materials/Construction Permit (No. 7)",
    "Part IX": "Loading/Off-Loading Parking and Control of Traffic (No. 8)",
    "Part X": "Cutting of Road Tar (No. 9)",
    "Part XI": "Movement and Keeping of Dogs (No. 10)",
    "Part XII": "Numbering/Street Naming Regulation (No. 11)",
    "Part XIII": "Registration of Dry Cleaning and Laundry Houses (No. 12)",
    "Part XIV": "Market Regulation (No. 13)",
    "Part XV": "Trade License, Private Lockup Shop and Allied Matters (No. 14)",
    "Part XVI": "Radio and Television Licence (No. 15)",
    "Part XVII": "Tricycle (Keke) Commercial Motorcycle Regulation and Control (No. 16)",
    "Part XVIII": "Public Toilet (No. 17)",
    "Part XIX": "Pest Control (No. 18)",
    "Part XX": "Contractors (No. 19)",
    "Part XXI": "Tenement Rate Collection (No. 20)",
    "Part XXII": "Private Sector Participation Refuse Operation — PSPRO (No. 21)",
    "Part XXIII": "Liquor Licensing (No. 22)",
    "Part XXIV": "Wrong Parking, Corporate Parking Permit/License (No. 23)",
    "Part XXV": "Community Development Levy and Allied Matters (No. 24)",
    "Part XXVI": "Regulated Premises — Hotels, Guest Inn, Restaurants, Bake Houses, Dairies and Places of Sale of Food (No. 25)",
}
