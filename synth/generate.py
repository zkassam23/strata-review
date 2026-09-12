"""Generate a synthetic strata package for Strata Plan BCS3392, 2135 Springer Avenue, Burnaby.

Planted signals (see PLANTED at the bottom for the expected outcome):
  * roof: leaks 2024-25, condition report Sep 2025, two quotes Nov 2025, third quote and
    deferral to the 2026 AGM in Apr 2026. No levy passed. -> special_levies red
  * contingency reserve $298,412 vs depreciation report recommendation $1,420,000 -> red
  * 2019 envelope assessment recommends remediation within five years; minutes never record
    it being tendered or done -> building_envelope red
  * water damage deductible raised to $100,000 in Dec 2025 -> insurance amber
  * rental restriction bylaw s.41 still on the books -> bylaws amber
  * depreciation report dated June 2022 -> currency amber
  * Form B: no arrears, no levies, no lawsuits, parking stall 87 LCP, locker 12 CP -> notes
Roughly a third of the minutes pages are image-only scans; one is deliberately degraded so
the low-OCR appendix has something to show.

Usage: python -m synth.generate [out_dir]   (default synth/package)
"""
from __future__ import annotations

import io
import random
import sys
from datetime import date
from pathlib import Path

from PIL import Image, ImageFilter
from pypdf import PdfReader, PdfWriter
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib import colors

PLAN = "BCS3392"
ADDRESS = "2135 Springer Avenue, Burnaby, BC"
NAME = "The Springer"
UNIT = "1204"
LOT = 118
ENTITLEMENT = 182
TOTAL_ENTITLEMENT = 10000

styles = getSampleStyleSheet()
BODY = ParagraphStyle("body", parent=styles["Normal"], fontName="Helvetica", fontSize=10.5, leading=14.5, spaceAfter=7, alignment=TA_LEFT)
H1 = ParagraphStyle("h1", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=15, leading=19, spaceAfter=10)
H2 = ParagraphStyle("h2", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=11.5, leading=15, spaceBefore=8, spaceAfter=4)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=9, leading=12)


def fmt_date(d: date) -> str:
    return f"{d.day} {d.strftime('%B %Y')}"


def build_pdf(path: Path, flow, header: str, footer_label: str = ""):
    def on_page(c: canvas.Canvas, doc):
        c.saveState()
        c.setFont("Helvetica", 8.5)
        c.drawString(0.9 * inch, 10.55 * inch, header)
        c.line(0.9 * inch, 10.48 * inch, 7.6 * inch, 10.48 * inch)
        c.drawString(0.9 * inch, 0.55 * inch, f"{footer_label}Page {doc.page}")
        c.restoreState()

    doc = SimpleDocTemplate(str(path), pagesize=letter, leftMargin=0.9 * inch, rightMargin=0.9 * inch,
                            topMargin=1.05 * inch, bottomMargin=0.9 * inch, title=header)
    doc.build(flow, onFirstPage=on_page, onLaterPages=on_page)


def P(text, style=BODY):
    return Paragraph(text, style)


# ---------------------------------------------------------------------------------------
# Council minutes
# ---------------------------------------------------------------------------------------
FILLER = [
    ("Landscaping", "The landscaping contractor has completed the fall clean-up of the courtyard planters. Council asked the property manager to obtain a quote for replacing the dead cedars along the Springer Avenue frontage. The lawn irrigation timer has been adjusted for the season."),
    ("Elevators", "The elevator maintenance contractor reported that both cars passed their annual inspection. A door operator on car two was replaced under the maintenance contract at no additional cost. Council asked that the entrapment log be circulated with the next report."),
    ("Fobs and access", "Twelve replacement fobs were issued during the month. Council reminded owners that fobs are $45 each and must be requested through the property manager. The parkade gate sensor was recalibrated after an owner reported it closing on a vehicle."),
    ("Garbage and recycling", "The waste hauler has advised of a 4% increase effective next month. Council asked the property manager to survey two alternative haulers before the next meeting. Owners are reminded that cardboard must be flattened."),
    ("Mechanical", "The boiler service was completed. One circulation pump is showing bearing wear and will be replaced from the operating budget at an estimated cost of $3,800. Domestic hot water temperatures were verified at the recirculation return."),
    ("Correspondence", "Council received a letter from the owner of a unit on the ninth floor regarding noise from a neighbouring suite. The property manager has written to the owner of the neighbouring suite reminding them of the bylaws on noise. No further action at this time."),
    ("Bylaw enforcement", "Two warning letters were issued for items stored on balconies contrary to the bylaws. One fine of $50 was levied for a repeat parking infraction in a visitor stall. The owner has paid the fine."),
    ("Amenity room", "The amenity room booking fee remains $75 with a $300 damage deposit. The room was booked four times during the month. Council approved replacement of the dishwasher in the amenity kitchen at a cost of $1,140 from the operating fund."),
    ("Lobby and common areas", "The lobby carpet cleaning was completed. A cracked tile near the mail room will be replaced. Council approved a quote of $960 for repainting the second floor corridor where scuffing had become noticeable."),
    ("Parkade", "The annual parkade pressure wash is scheduled for the spring. Two light fixtures in the P2 level were replaced with LED units. Council discussed line painting and asked for a quote."),
    ("Property manager's report", "The property manager reviewed the monthly financial package. Operating expenses are tracking to budget with the exception of repairs and maintenance, which is running slightly ahead owing to plumbing call-outs. There are three owners in arrears totalling less than one month of fees between them; the standard reminder letters have been sent."),
]

PLANTED_ITEMS = {
    (2024, 10): [("Roof leak, unit 1801", "The owner of unit 1801 reported water staining on the ceiling of the living room after heavy rain on 12 October. The roofing contractor attended, found a failed seam in the membrane near the mechanical penthouse and completed a temporary patch. The invoice of $2,400 was paid from the operating fund. Council noted that this is the second membrane repair this year and asked the property manager to keep a log of roof call-outs.")],
    (2025, 2): [("Roof call-outs", "The property manager tabled the roof call-out log. There have been four membrane repairs since March 2024 totalling $7,900. Council asked the roofing contractor for an opinion on remaining service life at the next inspection.")],
    (2025, 6): [("Roof condition", "Following a further leak affecting units 1801 and 1802 in May, council resolved to commission a roof condition assessment from a consulting engineer. Motion by K. Sandhu, seconded by M. Trent, to engage RDH Building Science to assess the roof membrane at a cost not to exceed $6,500. CARRIED unanimously.")],
    (2025, 9): [("Roof condition assessment", "The roof condition assessment dated 22 August 2025 was received. The consultant's opinion is that the two-ply SBS membrane installed in 2009 is at the end of its serviceable life, with widespread seam failure and blistering across the main roof and penthouse roof. The consultant recommends full replacement within 12 to 18 months rather than continued patching. Council resolved to obtain three quotes for full membrane replacement and to report to the owners at the AGM.")],
    (2025, 11): [("Roof replacement quotes", "Two quotes for full roof membrane replacement have been received: Pacific Roofing at $780,000 and Westcoast Membrane Systems at $940,000, both plus GST. A third quote from Summit Roofing is expected in the new year. The property manager confirmed that the contingency reserve fund, at approximately $290,000, cannot fund the work and that a special levy would be required for the balance. Council discussed whether to bring a levy resolution to a special general meeting or to the 2026 AGM. No decision was made pending the third quote. Owners are advised that a special levy is likely to be proposed in 2026.")],
    (2025, 12): [("Insurance renewal", "The strata insurance policy renewed on 1 December 2025 with BFL Canada. The annual premium increased to $128,400 from $109,000. The insurer has increased the water damage deductible to $100,000 per occurrence from $50,000. The property manager will circulate a notice to owners recommending they review their own policies for deductible assessment coverage. Council noted that under bylaw 34 an owner may be responsible for the deductible where a claim originates in their strata lot.")],
    (2026, 2): [("Rental bylaw inquiry", "An owner wrote asking whether the rental restriction in bylaw 41 is still enforceable following the provincial changes in November 2022. Council noted that the consolidated bylaws still contain section 41 limiting rentals to 20 strata lots. The property manager will ask the strata's lawyer for an opinion and council will consider whether to bring a bylaw amendment to the AGM.")],
    (2026, 4): [("Roof replacement, decision deferred", "The third quote, from Summit Roofing at $850,000 plus GST, was received and reviewed together with the earlier quotes of $780,000 and $940,000. All three are for full replacement of the main and penthouse roof membranes with a two-ply SBS system and a 20-year manufacturer's warranty. Council discussed funding. The contingency reserve fund stands at $298,412 as at 31 December 2025 and cannot fund the work. After discussion, council resolved to defer the decision to the annual general meeting in October 2026, at which a special levy resolution will be put to the owners. Motion by M. Trent, seconded by R. Ilic, to defer. CARRIED, one opposed. Council asked the property manager to prepare a levy schedule by unit entitlement for the AGM notice package.")],
    (2026, 7): [("AGM preparation", "Council reviewed the draft AGM notice. The agenda will include the operating budget, the proposed special levy resolution for roof replacement, and a resolution to amend bylaw 41. The property manager reminded council that the depreciation report, last updated in June 2022, is due for renewal and that a budget line should be included for the update.")],
}

COUNCIL = ["K. Sandhu (President)", "M. Trent (Vice President)", "R. Ilic (Treasurer)", "A. Okafor", "J. Lindqvist"]


def meeting_dates() -> list[date]:
    out = []
    y, m = 2024, 9
    rng = random.Random(7)
    while (y, m) <= (2026, 8):
        out.append(date(y, m, rng.choice([8, 9, 12, 14, 15, 16])))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def council_meeting_flow(d: date, idx: int, dates: list[date]):
    rng = random.Random(d.toordinal())
    flow = [P(f"STRATA PLAN {PLAN}, {NAME}", H1),
            P(f"MINUTES OF THE STRATA COUNCIL MEETING", H2),
            P(f"Held on {fmt_date(d)} at 7:00 pm in the amenity room, {ADDRESS}."),
            P("Council members present: " + ", ".join(COUNCIL[:rng.randint(4, 5)]) + ". Also present: D. Fraser, Property Manager, Pacific Quorum Properties."),
            P("1. Call to order", H2), P("The meeting was called to order at 7:02 pm. A quorum of council was confirmed."),
            P("2. Approval of previous minutes", H2)]
    if idx > 0:
        flow.append(P(f"The minutes of the council meeting held on {fmt_date(dates[idx - 1])} were approved as circulated."))
    else:
        flow.append(P("The minutes of the previous council meeting were approved as circulated."))
    flow.append(P("3. Financial report", H2))
    crf = 232000 + int((d.toordinal() - date(2024, 9, 1).toordinal()) / 730 * 66000) + rng.randint(-1500, 1500)
    flow.append(P(f"The treasurer reported that the contingency reserve fund balance was ${crf:,.0f} at the end of the previous month. Operating fund balance was ${rng.randint(38000, 61000):,.0f}. Accounts receivable stood at ${rng.randint(900, 4100):,.0f}."))
    n = 4
    items = list(PLANTED_ITEMS.get((d.year, d.month), []))
    pool = FILLER[:]
    rng.shuffle(pool)
    items += pool[: rng.randint(5, 7)]
    for heading, text in items:
        flow.append(P(f"{n}. {heading}", H2))
        flow.append(P(text))
        n += 1
    flow.append(P(f"{n}. Next meeting and adjournment", H2))
    nxt = dates[idx + 1] if idx + 1 < len(dates) else None
    flow.append(P((f"The next council meeting will be held on {fmt_date(nxt)}. " if nxt else "") + f"There being no further business the meeting adjourned at {rng.choice(['8:35', '8:50', '9:05', '9:20'])} pm."))
    flow.append(P("These minutes are subject to approval at the next meeting of the strata council.", SMALL))
    return flow


def make_council_minutes(tmp: Path, out: Path) -> tuple[Path, set[int], set[int]]:
    """Returns path, set of page indexes (0-based) to scan, set to degrade."""
    dates = meeting_dates()
    writer = PdfWriter()
    scan_pages: set[int] = set()
    degrade: set[int] = set()
    offset = 0
    for i, d in enumerate(dates):
        p = tmp / f"cm_{i:02d}.pdf"
        header = f"Strata Plan {PLAN}  ·  {NAME}  ·  Council Meeting Minutes  ·  {fmt_date(d)}"
        build_pdf(p, council_meeting_flow(d, i, dates), header)
        r = PdfReader(str(p))
        n = len(r.pages)
        for pg in r.pages:
            writer.add_page(pg)
        if i % 3 == 1:                      # every third meeting is a scan
            scan_pages.update(range(offset, offset + n))
        if d.year == 2025 and d.month == 1:  # one deliberately bad scan
            scan_pages.update(range(offset, offset + n))
            degrade.add(offset + 1)
        offset += n
    path = out / f"{PLAN} - council minutes 2024-2026.pdf"
    with open(path, "wb") as f:
        writer.write(f)
    return path, scan_pages, degrade


# ---------------------------------------------------------------------------------------
# Other documents
# ---------------------------------------------------------------------------------------
def make_agm(out: Path) -> Path:
    d = date(2025, 10, 21)
    flow = [P(f"STRATA PLAN {PLAN}, {NAME}", H1), P("MINUTES OF THE ANNUAL GENERAL MEETING", H2),
            P(f"Held on {fmt_date(d)} at 7:00 pm in the amenity room, {ADDRESS}."),
            P("Registration confirmed 61 strata lots represented in person and 23 by proxy, for a total of 84 of 118 eligible votes. A quorum was declared present. D. Fraser of Pacific Quorum Properties chaired the meeting at the request of council."),
            P("1. Call to order and proof of notice", H2), P("The meeting was called to order at 7:06 pm. The notice of meeting dated 26 September 2025 was confirmed to have been delivered in accordance with the Strata Property Act."),
            P("2. Approval of the 2024 AGM minutes", H2), P("Moved, seconded and CARRIED that the minutes of the annual general meeting held 22 October 2024 be approved as circulated."),
            P("3. Council report", H2),
            P("The president reported on the year. The main item is the roof. A roof condition assessment received in September concluded that the membrane is at the end of its life and council is obtaining quotes for full replacement. Preliminary pricing suggests the cost will be in the range of $800,000 to $950,000. The contingency reserve fund cannot fund work of this size and owners should expect a special levy resolution to be brought to a general meeting in 2026 once council has three quotes. The president also reported that the depreciation report dated June 2022 will be due for its three-year update and council will budget for it in the coming year."),
            P("4. Financial statements", H2), P("The treasurer presented the audited financial statements for the fiscal year ended 31 December 2024. The contingency reserve fund balance at year end was $237,600. Moved, seconded and CARRIED that the financial statements be received."),
            P("5. Budget for fiscal 2026", H2),
            P("The proposed operating budget of $1,146,000 was presented, an increase of 6.8% over the prior year driven by insurance and utilities. The budget includes a contribution to the contingency reserve fund of $61,000, unchanged from the prior year. An owner asked why the contribution was not increased given the depreciation report recommendation of a materially higher reserve balance; the treasurer replied that council had chosen to hold fees and address the roof by levy. Moved by the owner of SL 44, seconded by the owner of SL 97, that the budget be approved as presented. CARRIED by majority vote, 71 in favour, 13 opposed."),
            P("6. Depreciation report", H2), P("An owner asked whether the depreciation report would be updated before the roof decision. Council replied that the report from June 2022 already identifies the roof for replacement in 2027 at an estimated $820,000 in 2022 dollars, and that the update will be commissioned in 2026."),
            P("7. Election of council", H2), P("The following owners were elected by acclamation to council for the coming year: K. Sandhu, M. Trent, R. Ilic, A. Okafor, J. Lindqvist."),
            P("8. Insurance", H2), P("The property manager advised that the insurance renewal in December is expected to bring a further premium increase and that the insurer has signalled a higher water damage deductible. Details will be circulated after renewal."),
            P("9. Termination", H2), P("There being no further business the meeting terminated at 8:41 pm.")]
    path = out / f"{PLAN} - AGM minutes 2025.pdf"
    build_pdf(path, flow, f"Strata Plan {PLAN}  ·  {NAME}  ·  Annual General Meeting Minutes  ·  {fmt_date(d)}")
    return path


def make_depreciation(out: Path) -> Path:
    flow = [P("DEPRECIATION REPORT", H1), P(f"Strata Plan {PLAN}, {NAME}", H2), P(ADDRESS),
            P("Prepared for The Owners, Strata Plan BCS3392 by Halsall Reserve Consultants Ltd."),
            P("Report date: 15 June 2022. Financial information current to 31 December 2021."),
            P("This depreciation report has been prepared in accordance with section 94 of the Strata Property Act and the Strata Property Regulation. It includes a physical component inventory, an assessment of remaining useful life, and three funding models for the contingency reserve fund."),
            PageBreak(),
            P("1. Executive summary", H2),
            P("The building is a 22-storey concrete high-rise completed in 2009 with 118 residential strata lots, a two-level underground parkade, and a total unit entitlement of 10,000. The building is generally in fair to good condition for its age. The principal near-term expenditure is the roof membrane, which is approaching the end of its useful life, followed by the parkade membrane, domestic water piping and elevator modernisation within the 30-year study period."),
            P("The contingency reserve fund balance at 31 December 2021 was $210,400. The annual contribution is $61,000. Under all three funding models this contribution is insufficient to meet the projected expenditures, and the report recommends that the strata corporation move toward a target balance of $1,420,000 by fiscal 2027 to fund the roof and parkade membrane work without special levies. Achieving that balance would require annual contributions of approximately $240,000 from 2023. Where the strata elects not to increase contributions, the shortfall will need to be funded by special levy at the time of each project."),
            PageBreak(),
            P("2. Building description", H2),
            P("Year of construction: 2009. Construction: cast-in-place reinforced concrete, with window wall and curtain wall glazing systems, metal panel cladding at the podium, and a two-ply SBS modified bitumen roofing membrane on the main roof and penthouse roof. Strata lots: 118 residential. Parking: 141 stalls across two underground levels, the majority designated limited common property. Storage: 96 lockers on P1, common property allocated by council."),
            P("3. Methodology", H2),
            P("A visual site review was conducted on 3 and 4 May 2022. Component quantities were taken from the architectural drawings and verified on site. Remaining useful life estimates reflect observed condition. Cost estimates are in 2022 dollars and are escalated in the funding models at 3% per year."),
            PageBreak(),
            P("4. Component inventory and condition (selected)", H2)]
    rows = [["Component", "Installed", "Typical life", "Remaining life", "Est. cost (2022)"],
            ["Roof membrane, main and penthouse (SBS)", "2009", "20 yrs", "5 yrs (2027)", "$820,000"],
            ["Parkade traffic membrane", "2009", "15 yrs", "3 yrs (2025)", "$410,000"],
            ["Building envelope sealants", "2009", "12 yrs", "0 yrs (overdue)", "$380,000"],
            ["Domestic water piping", "2009", "30 yrs", "17 yrs", "$1,150,000"],
            ["Elevators, modernisation (2)", "2009", "25 yrs", "12 yrs", "$640,000"],
            ["Boilers and DHW", "2009", "20 yrs", "7 yrs", "$190,000"],
            ["Corridor finishes", "2016", "12 yrs", "6 yrs", "$260,000"]]
    t = Table(rows, colWidths=[2.6 * inch, 0.8 * inch, 0.9 * inch, 1.2 * inch, 1.2 * inch])
    t.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 9), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
                           ("GRID", (0, 0), (-1, -1), 0.4, colors.grey)]))
    flow += [t, Spacer(1, 10),
             P("Roof membrane. The two-ply SBS membrane shows blistering, seam separation and ponding at the main roof drains. The mechanical penthouse roof has been patched repeatedly. Full replacement is recommended in 2027 at an estimated $820,000 in 2022 dollars; escalated to 2027 this is approximately $950,000."),
             P("Building envelope sealants. Perimeter sealants at window wall and cladding joints are beyond their service life and were identified for remediation in the 2019 building envelope condition assessment by Morrison Hershfield. We understand that work has not yet been carried out. The estimate above is carried from that assessment."),
             PageBreak(),
             P("5. Contingency reserve fund analysis", H2),
             P("Opening balance (31 December 2021): $210,400. Current annual contribution: $61,000. Projected expenditures in the first five years of the study period total $1,610,000, dominated by the roof membrane, parkade membrane and envelope sealant work."),
             P("Recommended contingency reserve fund balance: $1,420,000 by fiscal 2027. This is the balance required to fund the 2025 to 2027 projects from reserves. Model 1 (current contributions) reaches $530,000 by 2027 and relies on special levies of approximately $1,100,000 in aggregate. Model 2 (recommended) increases contributions to $240,000 per year from 2023 and avoids levies for the first ten years. Model 3 phases the increase over three years and requires a single levy of about $480,000 in 2027."),
             P("Unit entitlement. Special levies and contributions are apportioned by unit entitlement. The total unit entitlement for the strata plan is 10,000."),
             PageBreak(),
             P("6. Funding model tables", H2), P("Model 1, current contributions: year-end balances 2022 $271,000; 2023 $333,000; 2024 $396,000; 2025 $460,000 (before parkade); 2026 $525,000; 2027 $590,000 (before roof)."),
             P("Model 2, recommended: year-end balances 2022 $450,000; 2023 $690,000; 2024 $930,000; 2025 $1,170,000; 2026 $1,420,000."),
             P("Model 3, phased: contributions $120,000 in 2023, $180,000 in 2024, $240,000 from 2025."),
             PageBreak(),
             P("7. Limitations", H2), P("This report is based on a visual review and the documents provided by the strata corporation. It is not a building envelope condition assessment and does not include destructive testing. Cost estimates are opinions of probable cost and should be confirmed by tender. The report should be updated at least every three years as required by the regulation in force at the time of writing.")]
    path = out / f"{PLAN} - depreciation report 2022.pdf"
    build_pdf(path, flow, f"Depreciation Report  ·  Strata Plan {PLAN}  ·  Halsall Reserve Consultants  ·  15 June 2022")
    return path


def make_form_b(out: Path) -> Path:
    d = date(2026, 8, 28)
    flow = [P("Strata Property Act", SMALL), P("FORM B", H1), P("INFORMATION CERTIFICATE", H2), P("(Section 59)"),
            P(f"The Owners, Strata Plan {PLAN} certify that the information contained in this certificate with respect to Strata Lot {LOT}, Suite {UNIT}, {ADDRESS} is correct as of the date of this certificate, {fmt_date(d)}."),
            P("(a) Monthly strata fees payable by the owner of the strata lot described above: $612.40", ),
            P("(b) Any amount owing to the strata corporation by the owner of the strata lot described above (other than an amount paid into court, or to the strata corporation in trust under section 114): $0.00. There are no arrears on this strata lot."),
            P("(c) Any agreements under which the owner of the strata lot takes responsibility for expenses relating to alterations to the strata lot, the common property or the common assets: None."),
            P("(d) Any amount that the owner of the strata lot described above is obligated to pay in the future for a special levy that has already been approved and the date by which the payment is to be made: None. No special levy has been approved."),
            P("(e) Any amount by which the expenses of the strata corporation for the current fiscal year are expected to exceed the expenses budgeted for the fiscal year: $0.00."),
            P("(f) The amount in the contingency reserve fund minus any expenditures which have already been approved but not yet taken from the fund: $298,412.00 as at 31 December 2025."),
            P("(g) Any amendments to the bylaws that are not yet filed in the land title office: None."),
            P("(h) Any resolution passed by a 3/4 vote or unanimous vote that is required to be filed in the land title office but that has not yet been filed: None."),
            P("(i) Any notice that has been given for a resolution that has not been voted on, if the resolution requires a 3/4 vote or unanimous vote or deals with an amendment to the bylaws: Council has advised owners that a special levy resolution for roof replacement is expected to be put to the annual general meeting in October 2026. No notice of resolution has been issued as at the date of this certificate."),
            P("(j) Any court proceeding or arbitration in which the strata corporation is a party and any tribunal claim in which the strata corporation is a party: None. Any judgments against the strata corporation: None."),
            P("(k) Any work order or notice of work orders from a public or local authority: None."),
            P("(l) The number of strata lots in the strata plan that are rented: 19 of 118."),
            P("(m) Parking stall(s) and storage locker(s) allocated to the strata lot: Parking stall 87 on level P2 is designated limited common property for the exclusive use of strata lot 118 on the strata plan. Storage locker 12 on level P1 is common property allocated to strata lot 118 by council under bylaw 27; the allocation may be changed by council on notice."),
            P("Unit entitlement of the strata lot: 182."),
            PageBreak(),
            P("Attachments", H2),
            P("The following documents are attached: rules of the strata corporation; the current budget; the owner developer's rental disclosure statement under section 139; the most recent depreciation report obtained by the strata corporation (June 2022); the most recent financial statements; the strata insurance summary."),
            P("Signed by the strata council of The Owners, Strata Plan BCS3392, by its property manager, Pacific Quorum Properties Ltd."),
            P(f"Date: {fmt_date(d)}")]
    path = out / f"{PLAN} - Form B.pdf"
    build_pdf(path, flow, f"Form B Information Certificate  ·  Strata Plan {PLAN}  ·  Strata Lot {LOT}")
    return path


def make_financials(out: Path) -> Path:
    flow = [P(f"THE OWNERS, STRATA PLAN {PLAN}", H1), P("FINANCIAL STATEMENTS", H2), P("For the fiscal year ended 31 December 2025"),
            P("Prepared by Pacific Quorum Properties Ltd. Reviewed, not audited."),
            P("Contents: statement of financial position, statement of operations (operating fund), statement of contingency reserve fund, notes."),
            PageBreak(),
            P("STATEMENT OF FINANCIAL POSITION as at 31 December 2025", H2)]
    rows = [["", "2025", "2024"], ["ASSETS", "", ""], ["Cash, operating", "$71,204", "$58,910"], ["Cash and term deposits, contingency reserve", "$298,412", "$237,600"],
            ["Accounts receivable, owners", "$3,240", "$5,115"], ["Prepaid insurance", "$117,700", "$99,900"], ["Total assets", "$490,556", "$401,525"],
            ["LIABILITIES", "", ""], ["Accounts payable", "$41,880", "$36,210"], ["Prepaid strata fees", "$9,160", "$8,720"],
            ["FUND BALANCES", "", ""], ["Operating fund", "$141,104", "$118,995"], ["Contingency reserve fund", "$298,412", "$237,600"], ["Total liabilities and fund balances", "$490,556", "$401,525"]]
    t = Table(rows, colWidths=[3.6 * inch, 1.3 * inch, 1.3 * inch])
    t.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 9.5), ("GRID", (0, 0), (-1, -1), 0.3, colors.grey)]))
    flow += [t, PageBreak(), P("STATEMENT OF OPERATIONS, OPERATING FUND, year ended 31 December 2025", H2)]
    rows = [["", "Budget 2025", "Actual 2025"], ["Strata fees", "$1,073,000", "$1,073,000"], ["Insurance", "$109,000", "$113,850"], ["Utilities", "$188,000", "$196,420"],
            ["Repairs and maintenance", "$142,000", "$171,335"], ["Management and admin", "$96,000", "$96,000"], ["Contracts (elevator, mechanical, cleaning)", "$214,000", "$209,870"],
            ["Contribution to contingency reserve fund", "$61,000", "$61,000"], ["Surplus (deficit)", "$0", "$22,109"]]
    t2 = Table(rows, colWidths=[3.6 * inch, 1.3 * inch, 1.3 * inch])
    t2.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 9.5), ("GRID", (0, 0), (-1, -1), 0.3, colors.grey)]))
    flow += [t2, PageBreak(), P("STATEMENT OF CONTINGENCY RESERVE FUND, year ended 31 December 2025", H2),
             P("Opening balance, 1 January 2025: $237,600. Contribution from operating fund: $61,000. Interest earned: $7,712. Expenditures: roof condition assessment $6,500 and parkade drain repair $1,400. Closing balance, 31 December 2025: $298,412."),
             P("The contingency reserve fund balance represents approximately 28% of one year's operating expenses. The depreciation report dated June 2022 recommended a balance of $1,420,000 by 2027. No special levy fund exists; no special levies were approved during the year."),
             P("NOTES", H2),
             P("1. Accounts receivable of $3,240 relate to three strata lots. Strata lot 118 has no balance owing. 2. Total unit entitlement of the strata plan is 10,000. 3. The strata corporation is not party to any legal proceedings.")]
    path = out / f"{PLAN} - financials FY2025.pdf"
    build_pdf(path, flow, f"Financial Statements  ·  The Owners, Strata Plan {PLAN}  ·  Year ended 31 December 2025")
    return path


def make_insurance(out: Path) -> Path:
    flow = [P("STRATA INSURANCE SUMMARY", H1), P(f"Named insured: The Owners, Strata Plan {PLAN}, {ADDRESS}", H2),
            P("Broker: BFL Canada Insurance Services Inc. Insurer: lead insurer Aviva Insurance Company of Canada with participating subscribers. Policy period: 1 December 2025 to 1 December 2026. This summary is provided for information and does not replace the policy wording."),
            P("Coverage: property, all risks, replacement cost, total insured value $48,200,000. Commercial general liability $10,000,000. Directors and officers $2,000,000. Equipment breakdown included. Earthquake included. Flood included."),
            PageBreak(),
            P("DEDUCTIBLES", H2)]
    rows = [["Peril", "Deductible"], ["Water damage (per occurrence)", "$100,000"], ["Sewer backup", "$25,000"], ["Earthquake", "10% of total insured value"],
            ["Flood", "$25,000"], ["All other perils", "$10,000"], ["Equipment breakdown", "$5,000"]]
    t = Table(rows, colWidths=[3.5 * inch, 2.4 * inch])
    t.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 10), ("GRID", (0, 0), (-1, -1), 0.3, colors.grey)]))
    flow += [t, Spacer(1, 12),
             P("Note on water damage deductible: the water damage deductible increased from $50,000 to $100,000 at this renewal following claims history. Under the strata corporation's bylaws an owner may be responsible for the deductible where the loss originates in their strata lot. Owners are recommended to carry deductible assessment coverage on their own policy of at least $100,000."),
             PageBreak(),
             P("PREMIUM", H2), P("Annual premium: $128,400 including taxes. Prior year: $109,000."),
             P("Claims history (five years): 2023 water escape from unit 1502, paid $84,000 net of deductible; 2024 water escape from unit 906, paid $61,000 net of deductible.")]
    path = out / f"{PLAN} - insurance summary.pdf"
    build_pdf(path, flow, f"Insurance Summary  ·  Strata Plan {PLAN}  ·  Policy period 1 Dec 2025 to 1 Dec 2026")
    return path


def make_envelope(out: Path) -> Path:
    flow = [P("BUILDING ENVELOPE CONDITION ASSESSMENT", H1), P(f"Strata Plan {PLAN}, {NAME}, {ADDRESS}", H2),
            P("Prepared by Morrison Hershfield Limited for The Owners, Strata Plan BCS3392. Report date: 10 May 2019. Project no. 1902244."),
            P("1. Introduction", H2), P("We were retained to carry out a visual and exploratory condition assessment of the building envelope following reports of water ingress at suites on the north elevation in the winter of 2018-19. The assessment included a visual review from grade, from the roof and from swing stage at four drops, and exploratory openings at three locations."),
            PageBreak(),
            P("2. Observations", H2),
            P("Window wall system. The window wall assemblies are generally performing. Perimeter sealant joints between the window wall frames and adjacent cladding show widespread adhesive failure on the north and west elevations, with sealant debonded from the substrate at an estimated 40% of joint length reviewed. Sealant on the south and east elevations is in fair condition."),
            P("Metal panel cladding, podium. Sealant at panel joints is cracked and debonded at several locations. Moisture was detected behind the panels at one exploratory opening on the west elevation."),
            P("Water ingress. The reported water ingress at suites 402, 702 and 1102 (north elevation) is consistent with sealant failure at the window wall perimeter above each location. Staining was observed on interior finishes at each suite."),
            PageBreak(),
            P("3. Conclusions", H2),
            P("The building envelope is at the stage where perimeter sealants have reached the end of their service life on the exposed elevations. Continued deterioration will increase the frequency of water ingress into suites and risks concealed damage to the cladding backup."),
            P("4. Recommendations", H2),
            P("We recommend a full sealant replacement program on the north and west elevations, and targeted replacement on the south and east elevations, together with repairs to the podium cladding joints. This work should be carried out within five years of this report, that is by 2024, to limit consequential damage. Our opinion of probable cost for the recommended remediation is $340,000 to $420,000 including access, contingency and consulting fees, excluding GST."),
            P("We further recommend that the strata corporation carry out an interim targeted sealant repair at suites 402, 702 and 1102 within the next twelve months, and that the recommended program be incorporated into the depreciation report."),
            PageBreak(),
            P("5. Limitations", H2), P("This assessment is based on visual observation and a limited number of exploratory openings. Concealed conditions may differ. This report is for the use of the strata corporation and should not be relied on by third parties without our written consent.")]
    path = out / f"{PLAN} - envelope assessment 2019.pdf"
    build_pdf(path, flow, f"Building Envelope Condition Assessment  ·  Strata Plan {PLAN}  ·  Morrison Hershfield  ·  10 May 2019")
    return path


def make_bylaws(out: Path) -> Path:
    flow = [P(f"THE OWNERS, STRATA PLAN {PLAN}", H1), P("CONSOLIDATED BYLAWS", H2),
            P("Consolidated for convenience of reference to include all amendments filed at the Land Title Office to 31 March 2024. In case of discrepancy the filed bylaws prevail. These bylaws replace the Standard Bylaws under the Strata Property Act except where stated."),
            P("Division 1, Duties of owners, tenants, occupants and visitors", H2),
            P("1. An owner must pay strata fees on or before the first day of the month to which the strata fees relate."),
            P("3. An owner, tenant, occupant or visitor must not use a strata lot, the common property or common assets in a way that causes a nuisance or hazard to another person, causes unreasonable noise, or is contrary to a purpose for which the strata lot or common property is intended."),
            PageBreak(),
            P("Division 2, Pets and occupancy", H2),
            P("15. An owner, tenant or occupant must not keep any pets on a strata lot other than one or more of the following: a reasonable number of fish or other small aquarium animals; up to two caged birds; up to two dogs or two cats, or one of each, each not exceeding 20 kg at maturity. Dogs must be leashed on common property."),
            P("16. There is no restriction on the age of occupants of a strata lot."),
            P("Division 3, Insurance", H2),
            P("34. An owner is responsible for and must indemnify the strata corporation for the deductible portion of any insurance claim under the strata corporation's policy where the loss or damage originated in the owner's strata lot, whether or not the owner was negligent, to the extent permitted by the Strata Property Act. The strata corporation may recover the deductible as a charge against the strata lot."),
            PageBreak(),
            P("Division 4, Rentals and short-term accommodation", H2),
            P("41. Rental restriction. The number of residential strata lots that may be rented at any one time is limited to 20. An owner wishing to rent a strata lot must apply to council and, where the limit has been reached, will be placed on a waiting list in order of application. This bylaw does not apply to a family member as defined in the Strata Property Regulation. [Note: this bylaw has not been amended since the Strata Property Act was amended on 24 November 2022. Council is obtaining legal advice on its effect.]"),
            P("42. Short-term accommodation. An owner, tenant or occupant must not use a strata lot, or permit a strata lot to be used, for short-term accommodation, being accommodation for a period of less than 30 consecutive days, including through any online platform. A fine of $1,000 per day may be levied for a contravention of this bylaw."),
            P("Division 5, Parking and storage", H2),
            P("27. Storage lockers are common property and are allocated to strata lots by council. Council may reallocate a locker on 30 days' notice. Parking stalls designated as limited common property on the strata plan are for the exclusive use of the strata lot to which they are designated. Visitor stalls may not be used by residents."),
            PageBreak(),
            P("Division 6, Fines and enforcement", H2),
            P("30. The strata corporation may fine an owner or tenant a maximum of $200 for each contravention of a bylaw and $50 for each contravention of a rule, except as otherwise provided in these bylaws. Continuing contraventions may be fined every seven days.")]
    path = out / f"{PLAN} - bylaws consolidated.pdf"
    build_pdf(path, flow, f"Consolidated Bylaws  ·  The Owners, Strata Plan {PLAN}  ·  to 31 March 2024")
    return path


# ---------------------------------------------------------------------------------------
# Scan simulation
# ---------------------------------------------------------------------------------------
def _page_to_scan_image(pdf_path: Path, index: int, degrade: bool, rng: random.Random) -> Image.Image:
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(pdf_path))
    dpi = 85 if degrade else 150
    img = pdf[index].render(scale=dpi / 72).to_pil().convert("L")
    img = img.rotate(rng.uniform(-0.6, 0.6), resample=Image.BICUBIC, fillcolor=255)
    px = img.load()
    w, h = img.size
    noise = 90 if degrade else 18
    for _ in range(int(w * h * (0.02 if degrade else 0.004))):
        x, y = rng.randrange(w), rng.randrange(h)
        px[x, y] = max(0, min(255, px[x, y] - rng.randint(0, noise)))
    if degrade:
        img = img.filter(ImageFilter.GaussianBlur(1.0)).point(lambda v: 255 if v > 165 else int(v * 0.8))
    else:
        img = img.filter(ImageFilter.GaussianBlur(0.35))
    return img


def scanify(pdf_path: Path, scan_pages: set[int], degrade: set[int]) -> None:
    """Replace the given 0-based pages with image-only pages, in place."""
    rng = random.Random(42)
    reader = PdfReader(str(pdf_path))
    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        if i not in scan_pages:
            writer.add_page(page)
            continue
        img = _page_to_scan_image(pdf_path, i, i in degrade, rng)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=45 if i in degrade else 62)
        buf.seek(0)
        pbuf = io.BytesIO()
        c = canvas.Canvas(pbuf, pagesize=letter)
        from reportlab.lib.utils import ImageReader
        c.drawImage(ImageReader(buf), 0, 0, width=letter[0], height=letter[1])
        c.showPage()
        c.save()
        pbuf.seek(0)
        writer.add_page(PdfReader(pbuf).pages[0])
    with open(pdf_path, "wb") as f:
        writer.write(f)


def generate(out_dir: Path) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.pdf"):
        old.unlink()
    tmp = out_dir / "_tmp"
    tmp.mkdir(exist_ok=True)
    cm, scan_pages, degrade = make_council_minutes(tmp, out_dir)
    scanify(cm, scan_pages, degrade)
    files = [cm, make_agm(out_dir), make_depreciation(out_dir), make_form_b(out_dir), make_financials(out_dir),
             make_insurance(out_dir), make_envelope(out_dir), make_bylaws(out_dir)]
    for f in tmp.glob("*"):
        f.unlink()
    tmp.rmdir()
    # one merged PDF of everything, for the split-by-content path
    merged = PdfWriter()
    for f in files:
        for pg in PdfReader(str(f)).pages:
            merged.add_page(pg)
    merged_path = out_dir.parent / "package-merged.pdf"
    with open(merged_path, "wb") as fh:
        merged.write(fh)
    n_cm = len(PdfReader(str(cm)).pages)
    return {"files": [str(f) for f in files], "merged": str(merged_path), "council_pages": n_cm,
            "scanned_pages": sorted(p + 1 for p in scan_pages), "degraded_pages": sorted(p + 1 for p in degrade)}


PLANTED = {
    "expected_red": ["special_levies", "contingency_reserve", "building_envelope"],
    "expected_amber": ["insurance_deductibles", "bylaws", "depreciation_report_currency"],
    "unit_entitlement": ENTITLEMENT, "total_entitlement": TOTAL_ENTITLEMENT,
    "roof_quotes": [780000, 850000, 940000], "crf_balance": 298412, "crf_recommended": 1420000,
    "water_deductible": 100000,
}

if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "package"
    info = generate(out)
    print(f"wrote {len(info['files'])} PDFs to {out}")
    print(f"council minutes: {info['council_pages']} pages, scanned {len(info['scanned_pages'])}, degraded {info['degraded_pages']}")
    print(f"merged: {info['merged']}")
