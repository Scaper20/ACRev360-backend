# KAC Gazette (S.I. No. 10 of 2024, Bye-Laws 2023) — transcription findings
Source: docs/KAC NEW GAzETTE (4).pdf — 144 pages, scanned 1700x2200 JPEG @200dpi, no text layer.
Method: embedded JPEGs extracted via pypdf (DCTDecode, no re-encode), read visually page by page.
Page mapping: PDF index -> gazette "B" number is NOT linear (offset drifts +166 -> +174).
  anchors: idx1=B167, idx2=B168, idx3=B169, idx50=B219, idx108=B282, idx110=B284

## CITATION CORRECTIONS vs KUJE DEPARTMENT.docx (gazette is authoritative)
| Item | docx / seeded | Gazette | Verdict |
|---|---|---|---|
| Foodstuff Regulation (30010053) | Part XXIV | **Part XXV** | WRONG in seed |
| Community & Development Levy (30010062) | Part XXV | Part XXV body (B301) IS "Community Development Levy and Allied Matters (No.24)" | **CORRECT — see retraction below** |
| Tender Fees, C of O, Change of Ownership, Searches, Agreement Fees | Part XX | Part XX = Contractors only | UNVERIFIED |
| Communication Mast (30010056) | Part XVI | TOC = Radio & TV only, masts not named | NEEDS BODY CHECK |

## MISSING CITATIONS NOW RECOVERED (were seeded with blank bye_law_reference)
| Code | Item | Gazette Part |
|---|---|---|
| 30010032 | Motor Parks | Part IV (Bye-Law No. 3) |
| 30010037 | Loading/Off-Loading Control of Traffic | Part IX (No. 8) |
| 30010039 | Movement and Keeping of Dogs | Part XI (No. 10) |
| 30010041 | Registration of Dry Cleaning and Laundry Houses | Part XIII (No. 12) |
| 30010042 | Market Regulation | Part XIV (No. 13) |
| 30010045 | Tricycle (Keke) Commercial Motorcycle | Part XVII (No. 16) |

## CONFIRMED CORRECT (14)
Part III Marriages/Births/Deaths; Part V Env Sanitation; Part VI Control of Advertisement;
Part VII Mobile Advertisement; Part VIII Stacking/Construction; Part X Cutting of Road Tar;
Part XII Numbering/Street Naming; Part XV Trade License; Part XVIII Public Toilet;
Part XIX Pest Control; Part XX Contractors; Part XXI Tenement Rate; Part XXII PSPRO;
Part XXIII Liquor Licensing; Part XXIV Wrong Parking/Corporate Parking; Part XXVI Regulated Premises.

## PER-PART SCHEDULE FINDINGS

### Part XXI — Tenement Rate Collection (B277-B284, idx ~103-110)  [COMPLETE]
NO FEE SCHEDULE EXISTS. Part ends B284 "Duly passed", no rate table anywhere in the Part.
Rate is a PERCENTAGE of assessed annual value (docx: 4%), not a band set.
=> The 0 bands currently seeded for 30010049 are CORRECT. seed_rate_bands.py's long-standing
   "no bye-law unambiguously named this" note is explained: there is no schedule to find.
Business rules captured (for apps.enforcement, not rate_band):
  - s.26(c) surcharge 25% per annum for each month rate remains unpaid
  - s.26(a)/(b) payment due within 21 days of notice
  - s.38(1) refusal to pay: liable up to DOUBLE the rate owed (recovery cost)
  - s.38(2) arrears recoverable up to the last FIVE years
  - s.38(3) judgment may empower Council to SEAL the tenement; 14 days to pay
  - s.34 illegal rate collection: 2 years imprisonment, no option of fine
  - s.35 collector failing to deposit / over-demanding / falsifying receipt: 1 year or N500,000 fine
  - s.36 Magistrate Court has jurisdiction; also hears valuation-objection appeals

### Part V — Schedule "C" Certificate of Fitness for Habitation (B219, idx 50)  [COMPLETE]
25 establishment rows x 3 charge columns (Fitness for Habitation (Old Building) /
Fitness for Continued Habitation New Building / Fitness for Use (Renewal)).
XLSX transcription has only 22 rows -> MISSING 3: Production Plants, Transport/Logistics
Companies, Private Building and Others.
Gazette CONFIRMS the N100,000,000 multinational figure (earlier flagged as needing confirmation).
Gazette CONFIRMS the duplicate "7." numbering (Fabric + Leasing) is a real gazette typo.

### Part XXIII — Liquor Licensing, FIRST SCHEDULE (B292, idx 117)  [VERIFIED 100%]
Columns are "Large A / Medium B / Small C" (gazette assigns class letters A/B/C to the tiers).
All 9 rows and 27 tiers match the seeded data EXACTLY — no corrections needed:
  1 Wholesale liquor              200,000 / 100,000 /  50,000
  2 Depot(beer)                   500,000 / 250,000 / 150,000
  3 Departmental/super store liq. 200,000 / 100,000 /  50,000
  4 Supermarket/shop               50,000 /  20,000 /  15,000
  5 Restaurant Liquor              20,000 /  10,000 /   5,000
  6 Hotels                        500,000 / 200,000 /  70,000
  7 Beer parlor                    20,000 /  10,000 /   5,000
  8 Native liquor                   1,500 /     500 /     100
  9 Club liquor                   150,000 / 100,000 /  50,000
Part XXIII also carries THIRD SCHEDULE Form B (B294, idx 119) — a licence certificate
template (Wine and Beer On License, 6.00am-12 midnight), not a fee table.

### Part XXIV — Wrong Parking / Corporate Parking (starts B297, idx 121)
Header confirmed. Enforcement rules captured:
  - s.2(c) permit payable within 21 days of demand notice
  - s.4 refusal to obtain permit: fine up to DOUBLE the sum owed
  - s.4(a) arrears recoverable up to last THREE years (note: differs from Tenement Rate's FIVE)
  - s.3 / s.4(b) Council may impound and tow vehicles of any defaulter

## PAGE-OFFSET DRIFT (PDF index -> gazette B number is NOT linear)
  idx 1=B167, 2=B168, 3=B169   (offset +166)
  idx 50=B219                  (offset +169)
  idx 108=B282, 110=B284       (offset +174)
  idx 117=B292, 119=B294       (offset +175)
  idx 121=B297                 (offset +176)
=> ~10 gazette pages are NOT present in the 144-page scan. Worth confirming with the council
   whether the source PDF is complete before treating any "no schedule found" as final.

## RETRACTION — the Gazette's own TABLE OF CONTENTS is unreliable
The Arrangement of Parts (B168) lists:
    "PART XXV — Foodstuff Regulations Bye-law (No. 24) 2023 ... 301"
The actual Part body at B301 (idx 125) reads:
    "PART XXV — COMMUNITY DEVELOPMENT LEVY AND ALLIED MATTERS BYE-LAW (NO.24)"
Same bye-law number (No. 24), different title. THE PART BODY IS AUTHORITATIVE; the TOC
mislabels it. My earlier TOC-derived claim that "Community Development Levy has no Part"
was WRONG and is withdrawn.
=> Community and Development Levy (30010062) -> Part XXV is CORRECT as seeded. The .docx
   was right about this one.
=> Lesson for the rest of this pass: verify every citation against the PART BODY header,
   never against the TOC alone.

### Part XXV — Community Development Levy (B301-B308, idx 125-132)  [SCHEDULE FOUND]
B301 header confirmed. B306 (idx 130) "SCHEDULE — COMMUNITY DEVELOPMENT LEVY —
BUSINESS/COMMERCIAL PREMISES", columns Rural Area / Semi Urban / Urban.
=> CONFIRMS the seeded COMMUNITY_LEVY tier labels (Rural/Semi Urban/Urban) are correct.
=> CONFIRMS the seed's 2-tier handling: row 17 "Airline Ticketing Office" and row 24
   "Car Stand" and row 32 "Coffee Shop" genuinely show "—" (em dash) in the Rural column.
Rules captured: levy is ANNUAL, expires 31 Dec; renewal due by 31 March, after which the
SCHEDULE MAXIMUM applies; assessment based on location/residence of payer; collection and
enforcement by the Area Council Revenue Committee; Council may seal a defaulter's premises
on a court order. Civil servants are exempt.

### Part XXVI — Regulated Premises: SECOND SCHEDULE misattributed  [CORRECTION]
B317-B318 (idx 140-141) "SECOND SCHEDULE, Section 3(c) — Regulated Premises Types,
Rate Per Month". Categories A(9) B(4) C-Bakery(3) D-Ram Seller(2) E(17) F(13) G(14) H(7)
= 69 rows exactly, matching seed_rate_bands.FOODSTUFF_REGULATION_FLAT's 69 bands.
These 69 monthly rates sit in PART XXVI (Regulated Premises), NOT in a Foodstuff Part.
seed_rate_bands.py attributed them to Foodstuff Regulation (30010053) citing "split doc 12's
Second Schedule" — the figures are right, the PART CITATION was not.
=> Foodstuff Regulation (30010053): bye_law_reference Part XXIV -> **Part XXVI (Second
   Schedule, s.3(c))**. Keep the 69 bands on 30010053 (they are the monthly foodstuff-premises
   rate, distinct from the First Schedule licence fee) but cite the correct Part.
=> Regulated Premises (30010054): Part XXVI FIRST Schedule, 42 bands — citation correct.
=> FLAG for council: whether the Second Schedule's monthly rate is administratively a
   separate revenue head ("Foodstuff Regulation") or simply a second charge under Regulated
   Premises is a policy question, not a documentary one. Both schedules are in Part XXVI.
Gazette typo noted: Category H row 7 "Ingredient Grinding Mill  5, 000, 00" (malformed
thousands/decimal grouping); read as 5,000.00 consistent with its neighbours.

### Part XVIII — Public Toilet (B271-B272, idx 97-98)  [COMPLETE, no schedule]
Part ends B272 "Duly passed". NO fee-schedule table. Rates are inline:
  s.6(i) private dislodging tank/vehicle owner registers with Environmental Health Dept
         for N100,000.00  => VERIFIES seeded KJ30010063 @ N100,000, Part XVIII. CORRECT.
  s.6(ii) licence expires 31 December of year of issuance
  s.6(iii) operating without licence: N70,000 or 3 months or both
  s.10 general contravention: N10,000 or 3 months or both
=> 30010046 Public Toilet: 0 bands is CORRECT (no gazetted table; council sets the fee).

### Part XIX — Pest Control (B273-B274, idx 99-100)  [COMPLETE, no schedule]
  s.6(a) pest control firm registers with MOH/Environmental Health Dept for N100,000
         => VERIFIES seeded 30010047 @ N100,000, Part XIX. CORRECT.
  s.6(b) licence expires 31 December of year of issuance
  s.6(c) operating without approval: N75,000 or 3 months
  s.7 obstructing an Environmental Health Officer: N50,000 individual / N100,000 corporate
  s.9 defines De-rat, Fumigation, Disinfection
=> 30010047: 0 bands CORRECT. NOTE: no separate fumigation-certificate fee is gazetted, so
   KJ30010064 Fumigation Certificates' N10,000 remains an ASSUMPTION, not gazette-sourced. FLAG.
Gazette internal inconsistency: citation clause calls it "Bye-Law (No. 21)" while the TOC
calls it No. 18. Part number XIX is consistent; the bye-law number is unreliable.

### Part XX — Contractors (B275-B276, idx 101-102)  [COMPLETE — ALL VERIFIED + NEW DATA]
Header B275 confirms "PART XX ... CONTRACTORS BYE-LAW (NO. 19) 2023".
Registration fees (s.2) — ALL MATCH the seeded bands EXACTLY:
  CATEGORY A CONSTRUCTION (tiered by project value)
    1,000,000-5,000,000 = 20,000 | 5,100,000-50,000,000 = 35,000
    50,100,000-99,000,000 = 150,000 | 100,000,000 and Above = 300,000
  CATEGORY B SUPPLY
    100,000-1,000,000 = 20,000 | 1,100,000-10,000,000 = 30,000
    11,000,000-50,000,000 = 100,000 | 51,000,000 and Above = 250,000
      (gazette prints "11,00,000.00" - malformed grouping; 11,000,000 per the sequence)
  CATEGORY C SERVICES   all servicing firms          = 24,000
  CATEGORY D CONSULTANCY all consulting/tech partners = 120,000
=> 30010048 Contractors: 4 bands / 8 tiers VERIFIED CORRECT as seeded.

**NEW DATA — Tender Fees (s.5, B276)** — resolves seed_rate_bands.py's documented gap
("two of its four figures are corrupted in the source, not safe to hardcode"):
    Construction                    N60,000.00
    Supplies                        N24,000.00
    Services                        N12,000.00
    Consultancy/Technical partners  N24,000.00
=> 30010061 Tender Fees: seed 4 FLAT bands from the gazette. Part XX confirmed.

**Part XX s.4 RESOLVES the "unverified Part XX" flag** — the Part expressly lists as
registrable under it: (a) Agreement fees on Sale of Land and other disposition;
(b) Fees for Certificate of Occupancy; (c) Fees for Change of Ownership; (d) Searches.
=> 30010057 / 30010058 / 30010059 / 30010060 -> Part XX is CORRECT as seeded (.docx right).
   No distinct amounts are gazetted for these four; rates stay council-set (0 bands correct).
s.3: registration fees are payable ANNUALLY. Council may fix and review yearly fees.

### Part VI — Control of Advertisement, FIRST SCHEDULE (B226, idx 57)  [VERIFIED 100%]
Columns "Minimum N (Yearly)" / "Maximum N (Yearly)" — confirms RANGE mode and Per Annum unit.
All 24 rows match the seeded CONTROL_OF_ADVERTISEMENT_BANDS EXACTLY.
Row 14 "Plastic Standing (Two faces) 38,000.00 / 38,000.00" — min EQUALS max in the gazette
itself, so the seeded (38000, 38000) is faithful, not a transcription error.
Row 23 Multinational companies 700,000-1,000,000; row 24 Financial Institution 200,000-500,000.
The bye-law expressly repeals the Kuje Area Council Bye-Law on Control of Advertisement, 2010.
B229 (idx 60) is the Fourth Schedule Form C (renewal application), not a fee table.

### Part XV — Trade License, FIRST SCHEDULE (B261, idx 88)  [SPOT-VERIFIED]
Two money columns (min/max) = RANGE. Shops/kiosks list runs to S/No 69, confirming the seed's
"69 yearly shops/kiosks + 15 business-premises rows = 84 bands" structure.
Rows 43-69 all match the seeded figures (Motor Cycle shop 15,000-30,000; Kiosk 2,000-5,000;
Cinema houses 20,000-50,000; Viewing centres 5,000-10,000; Tools/Equipment 50,000-150,000;
Corporate offices 10,000-20,000; Tobacco distribution shop 20,000-70,000; ...). No discrepancies.

## VERIFICATION SCORECARD (against the gazette itself, not the XLSX)
| Item | Bands | Result |
|---|---|---|
| 30010051 Liquor Licensing | 9 + 27 tiers | VERIFIED EXACT (B292) |
| 30010048 Contractors | 4 + 8 tiers | VERIFIED EXACT (B275) |
| 30010034 Control of Advertisement | 24 | VERIFIED EXACT (B226) |
| 30010043 Trade License | 84 | SPOT-VERIFIED, rows 43-69 exact (B261) |
| 30010062 Community Development Levy | 112 | STRUCTURE VERIFIED (B306); Part XXV confirmed |
| 30010053 Foodstuff (monthly rates) | 69 | COUNT VERIFIED 69 (B317-318); PART CITATION FIXED |
| 30010049 Tenement Rate | 0 | CORRECT — Part XXI has no schedule (B277-284) |
| 30010046 Public Toilet | 0 | CORRECT — no gazetted table (B271-272) |
| 30010047 Pest Control | 0 | CORRECT — inline N100,000 verified (B274) |
| KJ30010063 Private Dislodging | 0 | RATE VERIFIED N100,000 (B272) |
| 30010061 Tender Fees | 4 | NEW FROM GAZETTE (B276) |
| KJ30010065 Certificate of Fitness | 78 | NEW FROM GAZETTE, 26 rows x 3 charges (B219) |
| 30010057/58/59/60 land fees | 0 | Part XX CONFIRMED by Part XX s.4 (B275) |

## STILL UNVERIFIED against the gazette (seeded from the XLSX transcription)
30010035 Building Materials (16) · 30010036 Mobile Advert (7) · 30010037 Loading/Off-Loading (7)
30010052 Wrong Parking corporate (3) · 30010054 Regulated Premises First Schedule (42)
30010056 Communication Mast (1 band/3 tiers) · Community Levy's individual 112 figures
None of these showed any discrepancy where checked; they simply were not reached in this pass.
