"""Recorded, known-good model answers for the four sample debriefs (Sample mode and tests).

These mirror what a correct model call returns. Tests also feed deliberately wrong answers
through the guards to prove bad output is caught.
"""
from db import golden

_T = {d["Debrief ID"]: d["Transcript (what the broker says)"] for d in golden()["debriefs"]}


def term(field, status, number=None, text=None, date=None, evidence=""):
    return {"field": field, "status": status, "number": number, "text": text, "date": date, "evidence": evidence}


def act(desc, owner, otype, due, due_text, ev):
    return {"description": desc, "owner_name": owner, "owner_type": otype, "due_date": due, "due_text": due_text, "evidence": ev}


FIXTURES = {
    "B1": {"transcript": _T["B1"], "result": {
        "deal_id": "D1", "deal_evidence": "Northpoint Logistics Center, 2650 Northpoint Boulevard in Haslet",
        "meeting_type": "Buyer tour and terms", "counterparty_name": "Priya Raman", "counterparty_company": "Halvorsen Industrial Partners",
        "terms": [
            term("price", "agreed", 29700000, evidence="They're in at twenty nine point seven million"),
            term("stated_cap_rate", "stated", 0.055, evidence="five and a half percent cap on in-place NOI"),
            term("deposit", "agreed", 750000, evidence="seven hundred fifty thousand"),
            term("dd_days", "agreed", 45, evidence="forty five day due diligence period"),
            term("dd_start_date", "agreed", date="2026-11-06", evidence="Priya wants the PSA executed by this Friday, November sixth, and diligence starts that day"),
            term("closing_days_after_dd", "agreed", 30, evidence="Closing is thirty days after diligence ends"),
            term("financing", "agreed", text="All cash, no financing contingency", evidence="All cash, no financing contingency"),
            term("psa_draft_date", "agreed", date="2026-11-04", evidence="Owen Pratt should turn the PSA draft by Wednesday the fourth"),
        ],
        "superseded": [{"field": "deposit", "number": 500000, "reason": "correction", "evidence": "Earnest money is five hundred, no, sorry"}],
        "actions": [
            act("Open escrow and send wiring instructions to the buyer's team", "Marco Delgado", "external", None, "Not stated",
                "Marco Delgado at Lakeview Title needs to open escrow and send wiring instructions to Priya's team"),
            act("Turn the PSA draft", "Owen Pratt", "external", "2026-11-04", "by Wednesday the fourth", "Owen Pratt should turn the PSA draft by Wednesday the fourth"),
            act("Send Priya the rent roll and the T-12", "Jenna", "internal", "2026-11-02", "today", "Jenna, please send Priya the rent roll and the T-12 today"),
            act("Update the BOV comps with this trade", "Jenna", "internal", None, "Not stated", "update the BOV comps with this trade"),
            act("Get Priya the Lone Star Home Goods renewal history", None, "unassigned", None, "Not stated", "so let's get her the renewal history"),
        ],
        "emails_needed": [
            {"to_name": "Marco Delgado", "purpose": "Open escrow", "audience": "vendor",
             "points": ["Please open escrow for the sale to Halvorsen Industrial Partners", "Please send wiring instructions to Priya Raman's team", "PSA execution is targeted for Nov 6, 2026"]},
            {"to_name": "Owen Pratt", "purpose": "PSA draft by Nov 4", "audience": "vendor",
             "points": ["Please turn the PSA draft by Wednesday, Nov 4", "Terms are below"]},
            {"to_name": "Priya Raman", "purpose": "Thank you and next steps", "audience": "counterparty",
             "points": ["Thank you for touring the property today", "Jenna Cho will send the rent roll and T-12 today", "We are targeting PSA execution by Friday, Nov 6", "We will send the Lone Star Home Goods renewal history"]},
        ],
        "new_contacts": [],
    }},
    "B2": {"transcript": _T["B2"], "result": {
        "deal_id": "D2", "deal_evidence": "Cedar Row Office, 1800 Cedar Row Drive, Dallas",
        "meeting_type": "LOI meeting", "counterparty_name": "Henry Lam", "counterparty_company": "Stonebridge Capital",
        "terms": [
            term("price", "agreed", 18250000, evidence="eighteen and a quarter million"),
            term("stated_cap_rate", "not_mentioned"),
            term("deposit", "agreed", 400000, evidence="Four hundred thousand dollars earnest money"),
            term("dd_days", "agreed", 30, evidence="thirty day due diligence"),
            term("closing_date", "not_mentioned"),
            term("financing", "agreed", text="About 60% LTV at 6.40% fixed for 5 years, Riverbend Commercial Bank (Dana Whitfield)",
                 evidence="about sixty percent loan to value at six point four percent fixed for five years"),
            term("psa_draft_date", "agreed", date="2026-11-11", evidence="target is Wednesday the eleventh"),
        ],
        "superseded": [{"field": "price", "number": 18500000, "reason": "correction", "evidence": "We signed the LOI at eighteen and a half, sorry"}],
        "actions": [
            act("Confirm Summit Ridge Engineering's renewal status with the property manager", "Jenna", "internal", "2026-11-06", "by Friday",
                "Jenna, please confirm their renewal status with the property manager by Friday"),
            act("Draft the PSA", "Owen Pratt", "external", "2026-11-11", "Wednesday the eleventh", "Owen Pratt will draft the PSA, target is Wednesday the eleventh"),
            act("Set up Henry's second tour", "Ravi", "internal", None, "next week", "Ravi, set up Henry's second tour for next week"),
            act("Send Henry a recap of the LOI terms", "I", "internal", "2026-11-03", "today", "I'll send Henry a recap of the LOI terms today"),
        ],
        "emails_needed": [
            {"to_name": "Henry Lam", "purpose": "LOI recap", "audience": "counterparty",
             "points": ["Thank you for signing the LOI today", "Ravi Patel will set up your second tour next week", "We are confirming Summit Ridge Engineering's renewal status"]},
            {"to_name": "Owen Pratt", "purpose": "PSA draft by Nov 11", "audience": "vendor",
             "points": ["Please draft the PSA for Cedar Row Office, target Wednesday, Nov 11", "LOI terms are below"]},
        ],
        "new_contacts": [],
    }},
    "B3": {"transcript": _T["B3"], "result": {
        "deal_id": "D3", "deal_evidence": "Trinity Commons Apartments, 4100 Trinity Commons Parkway, Fort Worth",
        "meeting_type": "Listing pitch", "counterparty_name": "Graham Whitley", "counterparty_company": "Whitley Family Holdings",
        "terms": [
            term("price", "not_mentioned", text="No deal terms yet", evidence="No deal terms yet, this is a pitch"),
            term("seller_price_expectation", "expectation", text="$41M to $42M", evidence="Graham thinks it's worth forty one to forty two million"),
            term("internal_estimate", "internal_confidential", 40500000, evidence="Our early read on the T-12 is closer to forty and a half"),
            term("deposit", "not_mentioned"), term("dd_days", "not_mentioned"),
            term("closing_date", "not_mentioned"), term("financing", "not_mentioned"),
        ],
        "superseded": [],
        "actions": [
            act("Finish the BOV", "Jenna", "internal", "2026-11-13", "by Friday the thirteenth", "Jenna, finish the BOV by Friday the thirteenth"),
            act("Schedule a unit walk", "Ravi", "internal", "2026-11-12", "for the twelfth", "Ravi, schedule a unit walk for the twelfth"),
            act("Send Graham the engagement proposal", "I", "internal", "2026-11-09", "by Monday the ninth", "I'll send Graham our engagement proposal by Monday the ninth"),
            act("Send the last three months of rent rolls", "Daniel", "external", None, "Not stated",
                "His nephew Daniel handles the books and will send the last three months of rent rolls"),
        ],
        "emails_needed": [
            {"to_name": "Graham Whitley", "purpose": "Thank you and next steps", "audience": "client",
             "points": ["Thank you for meeting with us about Trinity Commons", "We will send our engagement proposal by Monday, Nov 9",
                        "Ravi Patel will schedule a unit walk for Thursday, Nov 12", "Please ask Daniel to send the last three months of rent rolls"]},
        ],
        "new_contacts": [{"name": "Daniel", "relationship": "Graham Whitley's nephew; handles the books", "evidence": "His nephew Daniel handles the books"}],
    }},
    "B4": {"transcript": _T["B4"], "result": {
        "deal_id": "D1", "deal_evidence": "Quick update on Northpoint Logistics",
        "meeting_type": "Retrade call", "counterparty_name": "Priya Raman", "counterparty_company": "Halvorsen Industrial Partners",
        "terms": [
            term("price", "agreed", 29300000, evidence="we landed at twenty nine point three million"),
            term("dd_extension_days", "agreed", 10, evidence="with a ten day extension"),
            term("deposit", "agreed", 750000, evidence="Deposit stays at seven fifty"),
        ],
        "superseded": [
            {"field": "price", "number": 29220000, "reason": "rejected_proposal", "evidence": "They asked for a price cut to twenty nine point two two million"},
            {"field": "dd_extension_days", "number": 15, "reason": "rejected_proposal", "evidence": "fifteen more days of diligence"},
        ],
        "actions": [
            act("Draft the first amendment to the PSA", "Owen", "external", "2026-11-23", "by Monday the twenty third", "Owen needs to draft the first amendment to the PSA by Monday the twenty third"),
            act("Rerun the cap rate on the new price and update the BOV file", "Jenna", "internal", None, "Not stated", "Jenna, rerun the cap rate on the new price and update the BOV file"),
            act("Let Marco know the dates moved", "I", "internal", None, "Not stated", "I'll let Marco know the dates moved"),
        ],
        "emails_needed": [
            {"to_name": "Owen Pratt", "purpose": "First amendment by Nov 23", "audience": "vendor",
             "points": ["Please draft the first amendment to the PSA by Monday, Nov 23", "Updated terms are below"]},
            {"to_name": "Marco Delgado", "purpose": "Dates moved", "audience": "vendor",
             "points": ["The due diligence period was extended by 10 days", "Updated dates are below"]},
            {"to_name": "Priya Raman", "purpose": "Confirming amended terms", "audience": "counterparty",
             "points": ["Confirming the amended price and a 10-day diligence extension", "Owen Pratt will circulate the first amendment"]},
        ],
        "new_contacts": [],
    }},
}
