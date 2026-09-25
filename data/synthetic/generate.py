"""Build held-out-style client folders under data/synthetic/.

These cases reuse the themes in data/client_0* (conflicts, joint accounts,
nulls, disposals, multi-platform proceeds, noise) with different people,
figures, platforms, and file shapes. Nothing here is wired into the pipeline.

Run from the repo root:

    uv run python data/synthetic/generate.py
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from docx import Document

ROOT = Path(__file__).resolve().parent

TEMPLATE_SPEC = """# Report specification: Investment Advice Report

What each section of the report must contain. The same specification applies to every client.

## Introduction
- One short paragraph. State which of the client's accounts the report covers (ISA, GIA, pension,
  or a combination).
- Keep the regulatory line about FCA authorisation as written.

## Background & Objectives
- A short, HIGH-LEVEL summary of the client's objectives and circumstances.
- Then a table of the accounts covered: columns Account | Owner | Type | Value.
- Keep this section high level. Specific transaction amounts (top-ups, sale proceeds, the size of
  any new money, tax figures) belong in Recommendations and Tax Implications, NOT here.

## Recommendations
- What the client should do and why, including the amounts involved.
- e.g. mention any platform charges relevant to the recommendation.

## Tax Implications
- Include this section ONLY if the recommendation involves selling or disposing of investments that
  may create a taxable capital gain. If nothing is being sold, omit the section entirely.
- State that the disposal may create a capital gains tax liability, assessed against the annual
  exempt amount.

## Fees & Charges
- Set out the ongoing fees and charges that apply: the platform charge and the ongoing advice
  charge.

## Conclusion
- Must include this risk warning, word for word:
  "The value of investments can fall as well as rise and you may get back less than you invest.
  Past performance is not a guide to future returns."
- Then a closing line inviting the client to proceed.
"""


def _doc(paragraphs: list[str], table: list[tuple[str, str]] | None = None) -> Document:
    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    if table:
        grid = doc.add_table(rows=1 + len(table), cols=2)
        grid.rows[0].cells[0].text = "Field"
        grid.rows[0].cells[1].text = "Detail"
        for index, (key, value) in enumerate(table, start=1):
            grid.rows[index].cells[0].text = key
            grid.rows[index].cells[1].text = value
    return doc


def _write_docx(path: Path, paragraphs: list[str], table: list[tuple[str, str]] | None = None) -> None:
    _doc(paragraphs, table).save(path)


def _noise(platform: str, fund: str, aggregate: str) -> list[str]:
    return [
        f"{platform}: Quarterly Investment Update",
        "Sent to all advised clients. This note is general market commentary and does not "
        "relate to any individual client's accounts or recommendations.",
        "Equity markets were mixed over the quarter. Inflation eased in most major economies "
        "and policy rates were left unchanged.",
        f"Our in-house {fund}, an illustrative model allocation, returned 3.1 per cent over "
        f"the quarter and stood at around {aggregate} in aggregate at quarter-end. This is a "
        "platform figure and is not a holding in any individual client's portfolio.",
        "Stay invested in line with an agreed risk profile. Capital is at risk.",
    ]


def _notes(extra: str) -> str:
    return f"""# Internal notes: data sources

For whoever configures the report: what each source is, and how they relate.
These notes are a starting point, not evidence. Do not treat figures written here as facts.

## The sources

- The custody snapshot is the system of record for which accounts exist and who holds them.
  It carries a snapshot date, and each account carries its own valuation date.
- The meeting note is the adviser's file note. It can disagree with the snapshot because
  it was written on a different day.
- The report request is the instruction for this report: scope, product, and whether
  anything is being sold.

## Account data

Accounts are listed per holder. A jointly-held account may be recorded under each holder.
A field may be blank. An account may be closed, dormant, or pending closure.

## Figures a person finalises

Capital gains tax on a disposal, and the platform and adviser fee rates, are filled in by
a person. The draft must not invent them.

## This case

{extra}
"""


def _account(
    account_id: str,
    platform: str,
    type_: str,
    owner: str,
    value: float | None,
    valuation_date: str | None,
    status: str = "open",
    currency: str = "GBP",
) -> dict:
    return {
        "account_id": account_id,
        "platform": platform,
        "type": type_,
        "owner": owner,
        "status": status,
        "value": value,
        "currency": currency,
        "valuation_date": valuation_date,
    }


def _db(snapshot: str, holders: dict) -> dict:
    return {"snapshot_date": snapshot, "holders": holders}


def _holder(name: str, accounts: list[dict]) -> dict:
    return {"name": name, "accounts": accounts}


def write_case(slug: str, files: dict[str, object]) -> None:
    folder = ROOT / slug
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    for name, payload in files.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, dict) and name.endswith(".json"):
            path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        elif isinstance(payload, str):
            path.write_text(payload, encoding="utf-8")
        elif isinstance(payload, tuple):
            paragraphs, table = payload
            _write_docx(path, paragraphs, table)
        else:
            raise TypeError(name)


def build() -> list[str]:
    cases: list[str] = []

    # 1. Clean ISA top-up. Same theme as client_01, different person and figures.
    write_case(
        "syn_01_isa_topup",
        {
            "client_data_db.json": _db(
                "2026-06-30",
                {
                    "client": _holder(
                        "Priya Nair",
                        [
                            _account("NX-ISA-01", "Northline", "Stocks & Shares ISA", "Priya Nair", 41800, "2026-06-30"),
                            _account("NX-CASH-01", "Northline", "Cash Account", "Priya Nair", 18650, "2026-06-30"),
                        ],
                    )
                },
            ),
            "report_request.docx": (
                ["Report Requirement Summary"],
                [
                    ("Adviser", "Helen Okonkwo"),
                    ("Accounts covered", "Northline Stocks & Shares ISA"),
                    ("Investment amount", "GBP 12,500"),
                    ("Source of funds", "Cash held on deposit in the Northline cash account"),
                    ("Selling existing investments?", "No"),
                    ("Product recommended", "Top up of existing Stocks & Shares ISA"),
                    ("Held in single or joint name?", "Single"),
                    ("Agreed risk profile", "3 (cautious to moderate)"),
                    ("Initial charge", "0%"),
                ],
            ),
            "meeting_notes.docx": (
                [
                    "Annual review with Priya Nair, held 8 July 2026.",
                    "Priya is still working part time and confirmed her circumstances and objectives are unchanged. She remains comfortable with the cautious-to-moderate approach agreed last year.",
                    "She has cash on deposit in her Northline cash account and wants to use part of this year's ISA allowance.",
                    "We agreed she would move £12,500 from the cash account into the Stocks & Shares ISA. Nothing is being sold. There is no disposal.",
                    "She has no income requirement from the portfolio. A future gift to her niece was mentioned and is not for this report.",
                    "Next steps: prepare the advice report covering the ISA top-up.",
                ],
                None,
            ),
            "platform_market_update.docx": (_noise("Northline", "Ashford Cautious Portfolio", "£188,000"), None),
            "fde_notes.md": _notes("Straightforward ISA top-up from cash. No disposal. Do not copy any other client's figures."),
            "template_spec.md": TEMPLATE_SPEC,
        },
    )
    cases.append("syn_01_isa_topup")

    # 2. Dated conflict: meeting is later and higher than the snapshot.
    write_case(
        "syn_02_dated_conflict",
        {
            "client_data_db.json": _db(
                "2026-05-31",
                {
                    "client": _holder(
                        "Owen Blake",
                        [
                            _account("RB-ISA-O", "Redbridge", "Stocks & Shares ISA", "Owen Blake", 54000, "2026-05-31"),
                            _account("RB-GIA-J", "Redbridge", "General Investment Account", "Joint", 67200, "2026-04-12"),
                        ],
                    ),
                    "partner": _holder(
                        "Leah Blake",
                        [
                            _account("RB-ISA-L", "Redbridge", "Stocks & Shares ISA", "Leah Blake", 49300, "2026-05-31"),
                            _account("RB-GIA-J", "Redbridge", "General Investment Account", "Joint", 67200, "2026-04-12"),
                        ],
                    ),
                },
            ),
            "report_request.docx": (
                ["Report Requirement Summary"],
                [
                    ("Adviser", "Helen Okonkwo"),
                    ("Accounts covered", "Redbridge ISAs (Owen and Leah) and the joint GIA"),
                    ("Investment amount", "Full value of the joint GIA"),
                    ("Source of funds", "Joint General Investment Account"),
                    ("Selling existing investments?", "Yes"),
                    ("Product recommended", "Top up of both Stocks & Shares ISAs"),
                    ("Held in single or joint name?", "Joint clients, individual ISAs"),
                    ("Agreed risk profile", "5 (balanced)"),
                    ("Initial charge", "0%"),
                ],
            ),
            "meeting_notes.docx": (
                [
                    "Review meeting with Owen and Leah Blake, held 18 June 2026.",
                    "Both are retired. Objectives are unchanged. A planned cruise has no bearing on the advice.",
                    "I pulled the joint General Investment Account up live. It was showing £71,400, higher than the April valuation on the snapshot.",
                    "We agreed to disinvest the joint GIA in full and split the proceeds equally between the two Stocks & Shares ISAs.",
                    "Selling the GIA is a disposal and may create a capital gains position. I will not quote a tax figure in the meeting.",
                    "Next steps: report covering both ISAs and the joint GIA.",
                ],
                None,
            ),
            "platform_market_update.docx": (_noise("Redbridge", "Kite Balanced Model", "£640,000"), None),
            "fde_notes.md": _notes(
                "The joint GIA appears under both holders with the same April figure. "
                "The June meeting saw a higher live value. Prefer the later dated figure and keep the earlier one visible. Do not average."
            ),
            "template_spec.md": TEMPLATE_SPEC,
        },
    )
    cases.append("syn_02_dated_conflict")

    # 3. Meeting states a balance but gives no date. Snapshot is dated.
    write_case(
        "syn_03_undated_meeting_figure",
        {
            "client_data_db.json": _db(
                "2026-07-15",
                {
                    "client": _holder(
                        "Samir Qureshi",
                        [
                            _account("VL-GIA-01", "Vellum", "General Investment Account", "Samir Qureshi", 91000, "2026-07-15"),
                        ],
                    )
                },
            ),
            "report_request.docx": (
                ["Report Requirement Summary"],
                [
                    ("Adviser", "Helen Okonkwo"),
                    ("Accounts covered", "Vellum GIA"),
                    ("Investment amount", "GBP 15,000"),
                    ("Source of funds", "Vellum General Investment Account"),
                    ("Selling existing investments?", "Yes"),
                    ("Product recommended", "Partial withdrawal from the GIA"),
                    ("Held in single or joint name?", "Single"),
                    ("Agreed risk profile", "4 (moderate)"),
                    ("Initial charge", "0%"),
                ],
            ),
            "meeting_notes.docx": (
                [
                    "Call with Samir Qureshi. He did not have his diary and I did not record a meeting date.",
                    "He said the GIA was about £96,000 when he last looked, but he could not say which day that was.",
                    "We agreed to sell £15,000 of the GIA to fund a house repair. That sale is a disposal.",
                    "Objectives otherwise unchanged. No income requirement.",
                ],
                None,
            ),
            "fde_notes.md": _notes(
                "The meeting balance is approximate and undated. An undated disagreement with a dated snapshot must not be silently resolved or averaged."
            ),
            "template_spec.md": TEMPLATE_SPEC,
        },
    )
    cases.append("syn_03_undated_meeting_figure")

    # 4. Null cash value, plus a closed account that must stay out.
    write_case(
        "syn_04_null_and_closed",
        {
            "client_data_db.json": _db(
                "2026-08-01",
                {
                    "client": _holder(
                        "Greta Holm",
                        [
                            _account("PF-ISA-G", "Pinfold", "Stocks & Shares ISA", "Greta Holm", 33000, "2026-08-01"),
                            _account("PF-CASH-G", "Pinfold", "Cash Account", "Greta Holm", None, None),
                            _account("PF-OLD-G", "Pinfold", "Cash Account", "Greta Holm", 0, "2025-09-01", status="closed"),
                        ],
                    )
                },
            ),
            "report_request.docx": (
                ["Report Requirement Summary"],
                [
                    ("Adviser", "Helen Okonkwo"),
                    ("Accounts covered", "Pinfold ISA and cash"),
                    ("Investment amount", "GBP 4,000"),
                    ("Source of funds", "Pinfold cash account, balance to be confirmed"),
                    ("Selling existing investments?", "No"),
                    ("Product recommended", "ISA top-up once the cash balance is confirmed"),
                    ("Held in single or joint name?", "Single"),
                    ("Agreed risk profile", "2 (cautious)"),
                    ("Initial charge", "0%"),
                ],
            ),
            "meeting_notes.docx": (
                [
                    "Meeting with Greta Holm on 12 August 2026.",
                    "She wants to move £4,000 into her ISA if the cash account can support it. She could not remember the balance.",
                    "She mentioned an old Pinfold cash account that was closed last year. It should be disregarded.",
                    "No investments are being sold.",
                ],
                None,
            ),
            "fde_notes.md": _notes("Blank cash value is a review item. The closed account is not in the report."),
            "template_spec.md": TEMPLATE_SPEC,
        },
    )
    cases.append("syn_04_null_and_closed")

    # 5. Status values that are not the word "closed".
    write_case(
        "syn_05_status_vocabulary",
        {
            "client_data_db.json": _db(
                "2026-08-20",
                {
                    "client": _holder(
                        "Idris Cole",
                        [
                            _account("HW-ISA-I", "Harwood", "Stocks & Shares ISA", "Idris Cole", 27500, "2026-08-20"),
                            _account("HW-GIA-I", "Harwood", "General Investment Account", "Idris Cole", 12000, "2026-08-20", status="dormant"),
                            _account("HW-CASH-I", "Harwood", "Cash Account", "Idris Cole", 800, "2026-01-02", status="pending closure"),
                            _account("HW-SIPP-I", "Harwood", "SIPP", "Idris Cole", 140000, "2026-08-20", status="frozen"),
                        ],
                    )
                },
            ),
            "report_request.docx": (
                ["Report Requirement Summary"],
                [
                    ("Adviser", "Helen Okonkwo"),
                    ("Accounts covered", "Harwood ISA only"),
                    ("Investment amount", "GBP 2,000"),
                    ("Source of funds", "Regular savings"),
                    ("Selling existing investments?", "No"),
                    ("Product recommended", "ISA top-up"),
                    ("Held in single or joint name?", "Single"),
                    ("Agreed risk profile", "4 (moderate)"),
                    ("Initial charge", "0%"),
                ],
            ),
            "meeting_notes.docx": (
                [
                    "Meeting with Idris Cole on 2 September 2026.",
                    "Only the ISA is in scope. The GIA is dormant, the cash account is pending closure, and the SIPP is frozen after a transfer delay.",
                    "We agreed a £2,000 ISA top-up. Nothing is being sold.",
                ],
                None,
            ),
            "fde_notes.md": _notes(
                "Status is not only open or closed. Dormant, frozen, and pending closure should not be treated as ordinary open accounts just because the string is not 'closed'."
            ),
            "template_spec.md": TEMPLATE_SPEC,
        },
    )
    cases.append("syn_05_status_vocabulary")

    # 6. Request is prose, not a pipe table. Same facts, different shape.
    write_case(
        "syn_06_prose_request",
        {
            "client_data_db.json": _db(
                "2026-03-31",
                {
                    "client": _holder(
                        "Nina Petrova",
                        [
                            _account("CL-ISA-N", "Calder", "Stocks & Shares ISA", "Nina Petrova", 22000, "2026-03-31"),
                        ],
                    )
                },
            ),
            "report_request.docx": (
                [
                    "Please prepare an investment advice report for Nina Petrova.",
                    "Cover her Calder Stocks & Shares ISA only.",
                    "Recommend a top-up of £8,000 from cash she holds at her bank, not on the platform.",
                    "Nothing is being sold. The account is in her sole name.",
                    "Agreed risk profile is 3, cautious to moderate. There is no initial charge.",
                    "The product is a top-up of the existing Stocks & Shares ISA.",
                ],
                None,
            ),
            "meeting_notes.docx": (
                [
                    "Meeting with Nina Petrova on 4 April 2026.",
                    "She will fund an £8,000 ISA top-up from cash at her bank. The ISA itself was last valued at £22,000 and we did not see a different figure.",
                    "No disposal. Objectives unchanged: long-term growth, no income required.",
                ],
                None,
            ),
            "fde_notes.md": _notes("The instruction is prose. A parser that only reads 'Key | Value' lines will miss the scope and the selling flag."),
            "template_spec.md": TEMPLATE_SPEC,
        },
    )
    cases.append("syn_06_prose_request")

    # 7. Pipe table, but the labels are not the four training headers.
    write_case(
        "syn_07_renamed_request_labels",
        {
            "client_data_db.json": _db(
                "2026-02-28",
                {
                    "client": _holder(
                        "Tomás Almeida",
                        [
                            _account("SR-ISA-T", "Sable", "Stocks & Shares ISA", "Tomás Almeida", 61000, "2026-02-28"),
                            _account("SR-GIA-T", "Sable", "General Investment Account", "Tomás Almeida", 44000, "2026-02-28"),
                        ],
                    )
                },
            ),
            "report_request.docx": (
                ["Advice instruction"],
                [
                    ("Adviser", "Helen Okonkwo"),
                    ("Wrappers in scope", "Sable ISA"),
                    ("Amount to invest", "GBP 9,000"),
                    ("Where the money comes from", "Sable GIA, partial sale"),
                    ("Disposal required", "Yes"),
                    ("What we are recommending", "ISA subscription from a partial GIA sale"),
                    ("Ownership", "Sole"),
                    ("Risk band", "6 (adventurous)"),
                    ("Set-up fee", "1%"),
                ],
            ),
            "meeting_notes.docx": (
                [
                    "Meeting with Tomás Almeida on 11 March 2026.",
                    "Sell £9,000 from the GIA and subscribe that amount to the ISA. The sale is a disposal.",
                    "Risk band 6. He asked about a 1% set-up fee, which still needs a person to confirm.",
                ],
                None,
            ),
            "fde_notes.md": _notes(
                "Labels differ from the example clients: wrappers in scope, disposal required, set-up fee, risk band. "
                "A label allowlist will keep some fields and drop others."
            ),
            "template_spec.md": TEMPLATE_SPEC,
        },
    )
    cases.append("syn_07_renamed_request_labels")

    # 8. Custody file is a flat account list, not holders → accounts.
    write_case(
        "syn_08_flat_account_list",
        {
            "custody_export.json": {
                "as_of": "2026-09-01",
                "household": "Adeyemi",
                "accounts": [
                    _account("QT-ISA-A", "Quay", "Stocks & Shares ISA", "Amina Adeyemi", 50500, "2026-09-01"),
                    _account("QT-ISA-K", "Quay", "Stocks & Shares ISA", "Kofi Adeyemi", 47200, "2026-09-01"),
                    _account("QT-GIA-J", "Quay", "General Investment Account", "Joint", 88000, "2026-06-01"),
                ],
            },
            "report_request.docx": (
                ["Report Requirement Summary"],
                [
                    ("Adviser", "Helen Okonkwo"),
                    ("Accounts covered", "Quay ISAs and the joint GIA"),
                    ("Investment amount", "Full value of the joint GIA"),
                    ("Source of funds", "Joint GIA"),
                    ("Selling existing investments?", "Yes"),
                    ("Product recommended", "ISA top-ups"),
                    ("Held in single or joint name?", "Joint clients, individual ISAs"),
                    ("Agreed risk profile", "4 (moderate)"),
                    ("Initial charge", "0%"),
                ],
            ),
            "meeting_notes.docx": (
                [
                    "Meeting with Amina and Kofi Adeyemi on 9 September 2026.",
                    "Disinvest the joint GIA, last seen live at £91,000, and split the proceeds across the two ISAs.",
                    "The June custody figure was £88,000. Use the later meeting figure and show the earlier one.",
                    "The sale is a disposal.",
                ],
                None,
            ),
            "fde_notes.md": _notes("Custody export has accounts at the top level and as_of instead of snapshot_date. There is no holders object."),
            "template_spec.md": TEMPLATE_SPEC,
        },
    )
    cases.append("syn_08_flat_account_list")

    # 9. Same account id stored twice with different values. First holder wins in the current parser.
    write_case(
        "syn_09_duplicate_id_disagrees",
        {
            "client_data_db.json": _db(
                "2026-05-01",
                {
                    "client": _holder(
                        "Ruth Ellison",
                        [
                            _account("LN-GIA-J", "Linden", "General Investment Account", "Joint", 150000, "2026-05-01"),
                        ],
                    ),
                    "partner": _holder(
                        "Mark Ellison",
                        [
                            _account("LN-GIA-J", "Linden", "General Investment Account", "Joint", 150250, "2026-05-02"),
                            _account("LN-ISA-M", "Linden", "Stocks & Shares ISA", "Mark Ellison", 30000, "2026-05-01"),
                        ],
                    ),
                },
            ),
            "report_request.docx": (
                ["Report Requirement Summary"],
                [
                    ("Adviser", "Helen Okonkwo"),
                    ("Accounts covered", "Linden joint GIA and Mark's ISA"),
                    ("Investment amount", "GBP 20,000"),
                    ("Source of funds", "Joint GIA"),
                    ("Selling existing investments?", "Yes"),
                    ("Product recommended", "Partial GIA sale into the ISA"),
                    ("Held in single or joint name?", "Joint GIA, single ISA"),
                    ("Agreed risk profile", "4 (moderate)"),
                    ("Initial charge", "0%"),
                ],
            ),
            "meeting_notes.docx": (
                [
                    "Meeting with Ruth and Mark Ellison on 20 May 2026.",
                    "The joint GIA is recorded twice on the custody file and the two rows do not match: £150,000 dated 1 May and £150,250 dated 2 May.",
                    "We agreed to sell £20,000 from the joint GIA into Mark's ISA. That is a disposal.",
                    "Do not drop the second row just because the account id was already seen.",
                ],
                None,
            ),
            "fde_notes.md": _notes("Duplicate account id with a different value and a later valuation date. First-seen must not hide the second row."),
            "template_spec.md": TEMPLATE_SPEC,
        },
    )
    cases.append("syn_09_duplicate_id_disagrees")

    # 10. Two meeting files. Current loader keeps one role and overwrites.
    write_case(
        "syn_10_two_meetings",
        {
            "client_data_db.json": _db(
                "2026-01-31",
                {
                    "client": _holder(
                        "Elena Voss",
                        [_account("BR-ISA-E", "Bramley", "Stocks & Shares ISA", "Elena Voss", 40000, "2026-01-31")],
                    )
                },
            ),
            "report_request.docx": (
                ["Report Requirement Summary"],
                [
                    ("Adviser", "Helen Okonkwo"),
                    ("Accounts covered", "Bramley ISA"),
                    ("Investment amount", "GBP 6,000"),
                    ("Source of funds", "Bonus"),
                    ("Selling existing investments?", "No"),
                    ("Product recommended", "ISA top-up"),
                    ("Held in single or joint name?", "Single"),
                    ("Agreed risk profile", "3 (cautious to moderate)"),
                    ("Initial charge", "0%"),
                ],
            ),
            "meeting_notes_january.docx": (
                [
                    "Meeting with Elena Voss on 2 February 2026.",
                    "We discussed selling the whole ISA. That idea was rejected. Do not treat this note as the instruction.",
                ],
                None,
            ),
            "meeting_notes_april.docx": (
                [
                    "Meeting with Elena Voss on 16 April 2026. This supersedes the February note.",
                    "We agreed a £6,000 top-up from her bonus. Nothing is being sold.",
                    "Circumstances unchanged. She is still employed.",
                ],
                None,
            ),
            "fde_notes.md": _notes("Two meeting files. The April note supersedes February. A single meeting slot will drop one of them."),
            "template_spec.md": TEMPLATE_SPEC,
        },
    )
    cases.append("syn_10_two_meetings")

    # 11. Noise that quotes a client-looking pound figure. Must not become a balance.
    write_case(
        "syn_11_noise_with_figures",
        {
            "client_data_db.json": _db(
                "2026-04-30",
                {
                    "client": _holder(
                        "Chris Daley",
                        [_account("OR-ISA-C", "Orchard", "Stocks & Shares ISA", "Chris Daley", 19000, "2026-04-30")],
                    )
                },
            ),
            "report_request.docx": (
                ["Report Requirement Summary"],
                [
                    ("Adviser", "Helen Okonkwo"),
                    ("Accounts covered", "Orchard ISA"),
                    ("Investment amount", "GBP 3,000"),
                    ("Source of funds", "Monthly savings"),
                    ("Selling existing investments?", "No"),
                    ("Product recommended", "ISA top-up"),
                    ("Held in single or joint name?", "Single"),
                    ("Agreed risk profile", "4 (moderate)"),
                    ("Initial charge", "0%"),
                ],
            ),
            "meeting_notes.docx": (
                [
                    "Meeting with Chris Daley on 6 May 2026.",
                    "Top up the ISA by £3,000. No sale. The ISA value on the snapshot is the one to use.",
                ],
                None,
            ),
            "platform_market_update.docx": (
                [
                    "Orchard Platform: Quarterly Investment Update",
                    "This note is general market commentary and does not relate to any individual client.",
                    "Clients sometimes ask about a model portfolio that stood at £250,000. That figure is not Chris Daley's holding.",
                    "Our in-house Orchard Growth sleeve returned 4.2 per cent. Capital is at risk.",
                ],
                None,
            ),
            "portfolio_pack.docx": (
                [
                    "Orchard model portfolio pack. Not personal advice.",
                    "The growth model holds about £250,000 across the platform. Do not attribute that to this client.",
                ],
                None,
            ),
            "fde_notes.md": _notes("Market pack quotes £250,000. That number must not appear as this client's balance or recommendation."),
            "template_spec.md": TEMPLATE_SPEC,
        },
    )
    cases.append("syn_11_noise_with_figures")

    # 12. Scope text says ISA, but 'joint' and account ids also contain tokens.
    write_case(
        "syn_12_scope_tokens",
        {
            "client_data_db.json": _db(
                "2026-06-01",
                {
                    "client": _holder(
                        "Hannah Reid",
                        [
                            _account("ISA-ONLY-H", "Fen", "Stocks & Shares ISA", "Hannah Reid", 28000, "2026-06-01"),
                            _account("CASH-ISA-H", "Fen", "Cash ISA", "Hannah Reid", 9000, "2026-06-01"),
                            _account("JISA-CHILD", "Fen", "Junior ISA", "Child", 4500, "2026-06-01"),
                            _account("GIA-JOINT", "Fen", "General Investment Account", "Joint", 70000, "2026-06-01"),
                            _account("PENSION-H", "Fen", "Personal Pension", "Hannah Reid", 210000, "2026-06-01"),
                        ],
                    )
                },
            ),
            "report_request.docx": (
                ["Report Requirement Summary"],
                [
                    ("Adviser", "Helen Okonkwo"),
                    ("Accounts covered", "Hannah's Stocks & Shares ISA only"),
                    ("Investment amount", "GBP 5,000"),
                    ("Source of funds", "Savings"),
                    ("Selling existing investments?", "No"),
                    ("Product recommended", "Stocks & Shares ISA top-up"),
                    ("Held in single or joint name?", "Single"),
                    ("Agreed risk profile", "4 (moderate)"),
                    ("Initial charge", "0%"),
                ],
            ),
            "meeting_notes.docx": (
                [
                    "Meeting with Hannah Reid on 10 June 2026.",
                    "This report is only about her Stocks & Shares ISA, account ISA-ONLY-H. Top up £5,000. No sale.",
                    "Leave out the Cash ISA, the Junior ISA, the joint GIA, and the personal pension.",
                ],
                None,
            ),
            "fde_notes.md": _notes(
                "The word ISA appears in three account types and in an account id. "
                "A token match on 'isa' will pull the Cash ISA and the Junior ISA into a report that covers one account."
            ),
            "template_spec.md": TEMPLATE_SPEC,
        },
    )
    cases.append("syn_12_scope_tokens")

    # 13. Scope is by account id, which carries no type word.
    write_case(
        "syn_13_scope_by_id",
        {
            "client_data_db.json": _db(
                "2026-06-15",
                {
                    "client": _holder(
                        "Pauline Cho",
                        [
                            _account("ACC-1001", "Westmill", "Stocks & Shares ISA", "Pauline Cho", 15000, "2026-06-15"),
                            _account("ACC-1002", "Westmill", "General Investment Account", "Pauline Cho", 64000, "2026-06-15"),
                            _account("ACC-1003", "Westmill", "SIPP", "Pauline Cho", 300000, "2026-06-15"),
                        ],
                    )
                },
            ),
            "report_request.docx": (
                ["Report Requirement Summary"],
                [
                    ("Adviser", "Helen Okonkwo"),
                    ("Accounts covered", "ACC-1001"),
                    ("Investment amount", "GBP 1,000"),
                    ("Source of funds", "Savings"),
                    ("Selling existing investments?", "No"),
                    ("Product recommended", "Top up ACC-1001"),
                    ("Held in single or joint name?", "Single"),
                    ("Agreed risk profile", "3 (cautious to moderate)"),
                    ("Initial charge", "0%"),
                ],
            ),
            "meeting_notes.docx": (
                [
                    "Meeting with Pauline Cho on 22 June 2026.",
                    "Cover account ACC-1001 only. Do not include ACC-1002 or ACC-1003.",
                    "Top up £1,000. No sale.",
                ],
                None,
            ),
            "fde_notes.md": _notes("Scope is an account id. Type-token matching finds no tokens and currently includes every open account."),
            "template_spec.md": TEMPLATE_SPEC,
        },
    )
    cases.append("syn_13_scope_by_id")

    # 14. Several money kinds that must not be collapsed into one balance.
    write_case(
        "syn_14_money_kinds",
        {
            "client_data_db.json": _db(
                "2026-04-30",
                {
                    "client": _holder(
                        "Victor Lang",
                        [
                            _account("MD-ISA-V", "Meadow", "Stocks & Shares ISA", "Victor Lang", 36000, "2026-04-30"),
                            _account("MD-CASH-V", "Meadow", "Cash Account", "Victor Lang", 74000, "2026-04-30"),
                            _account("MD-GIA-V", "Meadow", "General Investment Account", "Victor Lang", 50000, "2026-04-30"),
                        ],
                    )
                },
            ),
            "report_request.docx": (
                ["Report Requirement Summary"],
                [
                    ("Adviser", "Helen Okonkwo"),
                    ("Accounts covered", "Meadow ISA, cash, and GIA"),
                    ("Investment amount", "GBP 18,000"),
                    ("Source of funds", "See meeting note: completion money, after a loan repayment, earnout excluded"),
                    ("Selling existing investments?", "Yes"),
                    ("Product recommended", "ISA top-up and a partial GIA sale"),
                    ("Held in single or joint name?", "Single"),
                    ("Agreed risk profile", "5 (balanced)"),
                    ("Initial charge", "0.25%"),
                ],
            ),
            "meeting_notes.docx": (
                [
                    "Planning meeting with Victor Lang on 7 May 2026.",
                    "He sold a workshop. Completion money of £80,000 was received on 2 May 2026 and is sitting with his solicitor. That is received proceeds, not an account balance.",
                    "£22,000 of that completion money is already committed to repay a loan. That repayment is not available to invest.",
                    "A further £30,000 is contingent on a retention and has not been received. Do not invest it.",
                    "Available to invest now is £18,000, which we agreed to move into the ISA. Moving £18,000 is a transfer, not a new balance on the cash account. The cash account remains £74,000.",
                    "Separately, sell £7,500 of the GIA. That sale is a disposal. The GIA balance stays £50,000 until the sale settles.",
                    "Do not treat £80,000, £22,000, £30,000, £18,000, or £7,500 as a replacement for any account value.",
                ],
                None,
            ),
            "fde_notes.md": _notes(
                "Five different money meanings: account balances, received proceeds, a loan repayment, contingent proceeds, and a transfer. "
                "The request amount matches only the £18,000 transfer."
            ),
            "template_spec.md": TEMPLATE_SPEC,
        },
    )
    cases.append("syn_14_money_kinds")

    # 15. Selling flag is not yes/no.
    write_case(
        "syn_15_selling_phrasing",
        {
            "client_data_db.json": _db(
                "2026-07-01",
                {
                    "client": _holder(
                        "Yara Mensah",
                        [
                            _account("DK-GIA-Y", "Drake", "General Investment Account", "Yara Mensah", 42000, "2026-07-01"),
                            _account("DK-ISA-Y", "Drake", "Stocks & Shares ISA", "Yara Mensah", 18000, "2026-07-01"),
                        ],
                    )
                },
            ),
            "report_request.docx": (
                ["Report Requirement Summary"],
                [
                    ("Adviser", "Helen Okonkwo"),
                    ("Accounts covered", "Drake GIA and ISA"),
                    ("Investment amount", "GBP 6,500"),
                    ("Source of funds", "Partial encashment of the GIA"),
                    ("Selling existing investments?", "Only a slice of the GIA; the ISA is untouched"),
                    ("Product recommended", "ISA top-up funded by a GIA sale"),
                    ("Held in single or joint name?", "Single"),
                    ("Agreed risk profile", "4 (moderate)"),
                    ("Initial charge", "0%"),
                ],
            ),
            "meeting_notes.docx": (
                [
                    "Meeting with Yara Mensah on 19 July 2026.",
                    "Sell £6,500 of the GIA and subscribe it to the ISA. The ISA is not being sold.",
                    "This is a disposal of part of the GIA, so tax implications belong in the report.",
                ],
                None,
            ),
            "fde_notes.md": _notes(
                "The selling cell does not start with yes or no. The meeting is clear that a disposal is happening. "
                "Dropping the tax section because the cell failed a yes/no coerce would be wrong."
            ),
            "template_spec.md": TEMPLATE_SPEC,
        },
    )
    cases.append("syn_15_selling_phrasing")

    # 16. Dates are not ISO.
    write_case(
        "syn_16_non_iso_dates",
        {
            "client_data_db.json": {
                "snapshot_date": "30/06/2026",
                "holders": {
                    "client": _holder(
                        "Ben Okoye",
                        [
                            _account("HL-GIA-B", "Harrow", "General Investment Account", "Ben Okoye", 25500, "30/06/2026"),
                        ],
                    )
                },
            },
            "report_request.docx": (
                ["Report Requirement Summary"],
                [
                    ("Adviser", "Helen Okonkwo"),
                    ("Accounts covered", "Harrow GIA"),
                    ("Investment amount", "GBP 2,500"),
                    ("Source of funds", "GIA"),
                    ("Selling existing investments?", "Yes"),
                    ("Product recommended", "Partial sale"),
                    ("Held in single or joint name?", "Single"),
                    ("Agreed risk profile", "4 (moderate)"),
                    ("Initial charge", "0%"),
                ],
            ),
            "meeting_notes.docx": (
                [
                    "Meeting with Ben Okoye on 14 July 2026.",
                    "The custody file dates the GIA as 30/06/2026 at £25,500.",
                    "On 14 July 2026 the live screen showed £26,100.",
                    "Sell £2,500. That is a disposal. Prefer the later figure if both dates can be read.",
                ],
                None,
            ),
            "fde_notes.md": _notes("Valuation dates are DD/MM/YYYY. A parser that only accepts ISO will treat them as undated."),
            "template_spec.md": TEMPLATE_SPEC,
        },
    )
    cases.append("syn_16_non_iso_dates")

    # 17. Currency is not sterling. Renderer currently prefixes £.
    write_case(
        "syn_17_usd_currency",
        {
            "client_data_db.json": _db(
                "2026-05-15",
                {
                    "client": _holder(
                        "Claire Dupont",
                        [
                            _account(
                                "OS-BOND-C",
                                "Ostara",
                                "Offshore Investment Bond",
                                "Claire Dupont",
                                125000,
                                "2026-05-15",
                                currency="USD",
                            ),
                        ],
                    )
                },
            ),
            "report_request.docx": (
                ["Report Requirement Summary"],
                [
                    ("Adviser", "Helen Okonkwo"),
                    ("Accounts covered", "Ostara offshore bond"),
                    ("Investment amount", "USD 10,000"),
                    ("Source of funds", "US dollar cash"),
                    ("Selling existing investments?", "No"),
                    ("Product recommended", "Additional premium to the offshore bond"),
                    ("Held in single or joint name?", "Single"),
                    ("Agreed risk profile", "3 (cautious to moderate)"),
                    ("Initial charge", "0%"),
                ],
            ),
            "meeting_notes.docx": (
                [
                    "Meeting with Claire Dupont on 28 May 2026.",
                    "The bond is denominated in US dollars and was valued at USD 125,000. Do not present that as pounds.",
                    "Add a further USD 10,000 premium. Nothing is being sold.",
                ],
                None,
            ),
            "fde_notes.md": _notes("Values are USD. Formatting them as sterling would misstate the case."),
            "template_spec.md": TEMPLATE_SPEC,
        },
    )
    cases.append("syn_17_usd_currency")

    # 18. Sensitive life event, different from the training inheritance case.
    write_case(
        "syn_18_sensitive_circumstances",
        {
            "client_data_db.json": _db(
                "2026-08-10",
                {
                    "client": _holder(
                        "Margaret Ellis",
                        [
                            _account("WN-ISA-M", "Wendon", "Stocks & Shares ISA", "Margaret Ellis", 47000, "2026-08-10"),
                            _account("WN-CASH-M", "Wendon", "Cash Account", "Margaret Ellis", 95000, "2026-08-10"),
                        ],
                    )
                },
            ),
            "report_request.docx": (
                ["Report Requirement Summary"],
                [
                    ("Adviser", "Helen Okonkwo"),
                    ("Accounts covered", "Wendon ISA"),
                    ("Investment amount", "GBP 20,000"),
                    ("Source of funds", "Inheritance received after the death of her father"),
                    ("Selling existing investments?", "No"),
                    ("Product recommended", "ISA top-up"),
                    ("Held in single or joint name?", "Single"),
                    ("Agreed risk profile", "3 (cautious to moderate)"),
                    ("Initial charge", "0%"),
                ],
            ),
            "meeting_notes.docx": (
                [
                    "Meeting with Margaret Ellis on 21 August 2026.",
                    "Her father died in June. She has received £95,000 from the estate, now in the Wendon cash account. Refer to this carefully.",
                    "She would like £20,000 moved into the ISA. Nothing is being sold.",
                    "A holiday plan and a possible gift to a cousin are not part of this advice.",
                ],
                None,
            ),
            "fde_notes.md": _notes(
                "Inheritance after a bereavement. The background should mention the origin with care and must not invent a tax figure. "
                "This is a different household from any example client."
            ),
            "template_spec.md": TEMPLATE_SPEC,
        },
    )
    cases.append("syn_18_sensitive_circumstances")

    # 19. Multi-platform, but a practice sale rather than a trading company, and different wrappers.
    write_case(
        "syn_19_multi_platform",
        {
            "client_data_db.json": _db(
                "2026-03-31",
                {
                    "client": _holder(
                        "Arthur Singh",
                        [
                            _account("FV-ISA-A", "Fairview", "Stocks & Shares ISA", "Arthur Singh", 62000, "2026-03-31"),
                            _account("FV-GIA-J", "Fairview", "General Investment Account", "Joint", 110000, "2026-01-20"),
                            _account("KN-SIPP-A", "Kinloch", "SIPP", "Arthur Singh", 240000, "2026-03-31"),
                            _account("KN-JISA-A", "Kinloch", "Junior ISA", "Child", 8000, "2026-03-31"),
                        ],
                    ),
                    "partner": _holder(
                        "Noor Singh",
                        [
                            _account("FV-ISA-N", "Fairview", "Stocks & Shares ISA", "Noor Singh", 58000, "2026-03-31"),
                            _account("FV-GIA-J", "Fairview", "General Investment Account", "Joint", 110000, "2026-01-20"),
                            _account("LX-BOND-J", "Loxley", "Onshore Investment Bond", "Joint", 75000, "2026-03-31"),
                        ],
                    ),
                },
            ),
            "report_request.docx": (
                ["Report Requirement Summary"],
                [
                    ("Adviser", "Helen Okonkwo"),
                    ("Accounts covered", "Fairview ISAs, the Fairview joint GIA, both Kinloch pensions wrappers that are Arthur's SIPP, the Loxley onshore bond, and a new joint account"),
                    ("Investment amount", "Practice sale proceeds, see the meeting note"),
                    ("Source of funds", "Sale of a dental practice"),
                    ("Selling existing investments?", "Yes"),
                    ("Product recommended", "ISA and SIPP top-ups, bond left as-is, new joint account"),
                    ("Held in single or joint name?", "A mix of single and joint"),
                    ("Agreed risk profile", "4 (moderate)"),
                    ("Initial charge", "0.5%"),
                ],
            ),
            "meeting_notes.docx": (
                [
                    "Planning meeting with Arthur and Noor Singh on 2 April 2026.",
                    "Arthur completed the sale of his dental practice in March. Completion proceeds of £420,000 were received on 18 March 2026.",
                    "£90,000 of that is committed to a director's loan repayment and is not available.",
                    "£60,000 is a retention, contingent on a two-year clawback, and has not been received.",
                    "Available now is £270,000.",
                    "The Fairview joint GIA was £110,000 on 20 January 2026. Live on 2 April 2026 it showed £118,500.",
                    "We agreed to use both ISA allowances, contribute to Arthur's SIPP, leave the Loxley onshore bond unchanged, and place the rest in a new joint investment account.",
                    "The Junior ISA is the child's and is out of scope.",
                    "Sell £10,000 of the joint GIA as a rebalance. That is a disposal.",
                    "Fee rates were not confirmed in the meeting.",
                ],
                None,
            ),
            "platform_market_update.docx": (_noise("Fairview", "Fairview Balanced Sleeve", "£900,000"), None),
            "portfolio_pack.docx": (
                ["Kinloch model pack. Illustrative only. Aggregate assets £1.2 million. Not this client's money."],
                None,
            ),
            "fde_notes.md": _notes(
                "Three platforms, a practice sale in three money kinds, a stale joint GIA, a child's account out of scope, "
                "and a new joint account that does not exist on the snapshot yet."
            ),
            "template_spec.md": TEMPLATE_SPEC,
        },
    )
    cases.append("syn_19_multi_platform")

    # 20. A relevant file sits in a subfolder. The loader only reads the top of the folder.
    write_case(
        "syn_20_nested_file",
        {
            "client_data_db.json": _db(
                "2026-09-01",
                {
                    "client": _holder(
                        "Lucia Ferrer",
                        [_account("SB-ISA-L", "Southbank", "Stocks & Shares ISA", "Lucia Ferrer", 31000, "2026-09-01")],
                    )
                },
            ),
            "report_request.docx": (
                ["Report Requirement Summary"],
                [
                    ("Adviser", "Helen Okonkwo"),
                    ("Accounts covered", "Southbank ISA"),
                    ("Investment amount", "See the scanned instruction in the inbox folder"),
                    ("Source of funds", "See inbox"),
                    ("Selling existing investments?", "No"),
                    ("Product recommended", "ISA top-up"),
                    ("Held in single or joint name?", "Single"),
                    ("Agreed risk profile", "3 (cautious to moderate)"),
                    ("Initial charge", "0%"),
                ],
            ),
            "meeting_notes.docx": (
                [
                    "Meeting with Lucia Ferrer on 4 September 2026.",
                    "The amount we agreed is written in the inbox note, not here, because the figure arrived after the meeting.",
                    "No sale. Circumstances unchanged.",
                ],
                None,
            ),
            "inbox/follow_up.md": (
                "Follow-up from Lucia Ferrer, 5 September 2026.\n"
                "Please top up the Southbank ISA by £4,400 from her current account.\n"
                "Nothing is being sold.\n"
            ),
            "fde_notes.md": _notes("The agreed amount is in inbox/follow_up.md. A scan that ignores subfolders will miss it."),
            "template_spec.md": TEMPLATE_SPEC,
        },
    )
    cases.append("syn_20_nested_file")

    # 21. Misleading figure planted in the colleague note. Must not be evidence.
    write_case(
        "syn_21_notes_are_not_evidence",
        {
            "client_data_db.json": _db(
                "2026-04-01",
                {
                    "client": _holder(
                        "Jonah Berg",
                        [_account("TP-ISA-J", "Talbot", "Stocks & Shares ISA", "Jonah Berg", 27500, "2026-04-01")],
                    )
                },
            ),
            "report_request.docx": (
                ["Report Requirement Summary"],
                [
                    ("Adviser", "Helen Okonkwo"),
                    ("Accounts covered", "Talbot ISA"),
                    ("Investment amount", "GBP 1,500"),
                    ("Source of funds", "Savings"),
                    ("Selling existing investments?", "No"),
                    ("Product recommended", "ISA top-up"),
                    ("Held in single or joint name?", "Single"),
                    ("Agreed risk profile", "4 (moderate)"),
                    ("Initial charge", "0%"),
                ],
            ),
            "meeting_notes.docx": (
                [
                    "Meeting with Jonah Berg on 9 April 2026.",
                    "Top up the ISA by £1,500. The ISA is £27,500. No sale.",
                ],
                None,
            ),
            "fde_notes.md": _notes(
                "IGNORE THIS FIGURE IF YOU ARE EXTRACTING FACTS: a colleague's scratchpad says the ISA is £99,999. "
                "That number is not on the snapshot or in the meeting. It must not reach the report."
            ),
            "template_spec.md": TEMPLATE_SPEC,
        },
    )
    cases.append("syn_21_notes_are_not_evidence")

    # 22. New account requested, and the phrase is easy to miss or over-trigger.
    write_case(
        "syn_22_new_account",
        {
            "client_data_db.json": _db(
                "2026-07-31",
                {
                    "client": _holder(
                        "Sofia Marin",
                        [
                            _account("GV-ISA-S", "Grove", "Stocks & Shares ISA", "Sofia Marin", 24000, "2026-07-31"),
                        ],
                    ),
                    "partner": _holder(
                        "Eric Marin",
                        [
                            _account("GV-ISA-E", "Grove", "Stocks & Shares ISA", "Eric Marin", 26000, "2026-07-31"),
                        ],
                    ),
                },
            ),
            "report_request.docx": (
                ["Report Requirement Summary"],
                [
                    ("Adviser", "Helen Okonkwo"),
                    ("Accounts covered", "Grove ISAs and a new jointly held general investment account"),
                    ("Investment amount", "GBP 40,000"),
                    ("Source of funds", "Maturing fixed-term deposit, not an existing investment"),
                    ("Selling existing investments?", "No"),
                    ("Product recommended", "New joint GIA"),
                    ("Held in single or joint name?", "New account is joint"),
                    ("Agreed risk profile", "4 (moderate)"),
                    ("Initial charge", "0%"),
                ],
            ),
            "meeting_notes.docx": (
                [
                    "Meeting with Sofia and Eric Marin on 8 August 2026.",
                    "A fixed-term deposit of £40,000 matures next week. It is cash, not a disposal of investments.",
                    "Open a new jointly held general investment account for that £40,000. Do not top up the ISAs.",
                    "The new account is not on the custody snapshot.",
                ],
                None,
            ),
            "fde_notes.md": _notes("A new joint account is recommended and does not exist in the snapshot. No existing investment is sold."),
            "template_spec.md": TEMPLATE_SPEC,
        },
    )
    cases.append("syn_22_new_account")

    # 23. Zero and a negative cash figure.
    write_case(
        "syn_23_zero_and_negative",
        {
            "client_data_db.json": _db(
                "2026-08-31",
                {
                    "client": _holder(
                        "Felix Braun",
                        [
                            _account("RM-ISA-F", "Ramsay", "Stocks & Shares ISA", "Felix Braun", 0, "2026-08-31"),
                            _account("RM-CASH-F", "Ramsay", "Cash Account", "Felix Braun", -120, "2026-08-31"),
                        ],
                    )
                },
            ),
            "report_request.docx": (
                ["Report Requirement Summary"],
                [
                    ("Adviser", "Helen Okonkwo"),
                    ("Accounts covered", "Ramsay ISA and cash"),
                    ("Investment amount", "GBP 500"),
                    ("Source of funds", "External cash, not the overdrawn cash account"),
                    ("Selling existing investments?", "No"),
                    ("Product recommended", "ISA subscription"),
                    ("Held in single or joint name?", "Single"),
                    ("Agreed risk profile", "2 (cautious)"),
                    ("Initial charge", "0%"),
                ],
            ),
            "meeting_notes.docx": (
                [
                    "Meeting with Felix Braun on 3 September 2026.",
                    "The ISA is genuinely empty, value £0. The cash account is overdrawn by £120. Do not treat zero as missing, and do not fund the ISA from the overdrawn account.",
                    "Subscribe £500 from money he will transfer in from his bank. No sale.",
                ],
                None,
            ),
            "fde_notes.md": _notes("Zero is a real value. Negative is a real value. Neither is a blank."),
            "template_spec.md": TEMPLATE_SPEC,
        },
    )
    cases.append("syn_23_zero_and_negative")

    # 24. Image statement the pipeline cannot read, plus a gif the classifier marks as noise.
    write_case(
        "syn_24_unreadable_statement",
        {
            "client_data_db.json": _db(
                "2026-02-01",
                {
                    "client": _holder(
                        "Ines Moreau",
                        [_account("CY-GIA-I", "Cypress", "General Investment Account", "Ines Moreau", 33000, "2026-02-01")],
                    )
                },
            ),
            "report_request.docx": (
                ["Report Requirement Summary"],
                [
                    ("Adviser", "Helen Okonkwo"),
                    ("Accounts covered", "Cypress GIA"),
                    ("Investment amount", "GBP 1,000"),
                    ("Source of funds", "GIA"),
                    ("Selling existing investments?", "Yes"),
                    ("Product recommended", "Small withdrawal"),
                    ("Held in single or joint name?", "Single"),
                    ("Agreed risk profile", "4 (moderate)"),
                    ("Initial charge", "0%"),
                ],
            ),
            "meeting_notes.docx": (
                [
                    "Meeting with Ines Moreau on 11 February 2026.",
                    "She brought a photographed statement. I could not read it. Use the custody snapshot of £33,000 unless a person checks the image.",
                    "Withdraw £1,000. That is a disposal.",
                ],
                None,
            ),
            "fde_notes.md": _notes("statement.png and logo.gif are not text. Do not invent a balance from them."),
            "template_spec.md": TEMPLATE_SPEC,
        },
    )
    (ROOT / "syn_24_unreadable_statement" / "statement.png").write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    )
    (ROOT / "syn_24_unreadable_statement" / "logo.gif").write_bytes(b"GIF89a\x01\x00\x01\x00")
    cases.append("syn_24_unreadable_statement")

    return cases


def main() -> None:
    slugs = build()
    index = {
        "purpose": (
            "Synthetic held-out-style clients. Same report themes as data/client_0*, "
            "different people, figures, platforms, and file shapes. Not consumed by the pipeline."
        ),
        "cases": slugs,
    }
    (ROOT / "index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(slugs)} cases under {ROOT}")


if __name__ == "__main__":
    main()
