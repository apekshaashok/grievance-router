"""Complaint domain classification (F3) used by the offline rules provider and as a cross-check.

A real LLM does this from the prompt; this keyword scorer exists so the whole system runs
with no API key, so the stub service used for load testing can drive the agent loop, and
as the defined fallback when the model provider is unavailable.
"""
import re
from typing import Dict, List

SEBI_DOMAINS = ("broker", "mutual_fund", "registrar", "listed_company", "depository")

TERMS: Dict[str, List[str]] = {
    "broker": ["stock broker", "stockbroker", "broker", "trading account", "unauthorised trade", "unauthorized trade",
               "unauthorised trades", "unauthorized trades", "square off", "square-off", "intraday", "contract note",
               "margin", "payout", "trading app", "trading platform", "f&o", "brokerage"],
    "mutual_fund": ["mutual fund", "amc", "asset management company", "nav", "sip", "redemption", "redeem", "units",
                    "folio", "fund house", "elss", "scheme"],
    "registrar": ["registrar", "share transfer", "transfer agent", "rta", "ipo refund", "refund of application money"],
    "listed_company": ["listed company", "unlisted company", "delisted company", "dividend", "bonus shares", "bonus share", "rights issue", "buyback",
                       "annual report", "agm", "company has not"],
    "depository": ["depository", "depository participant", "demat account", "demat", "transmission", "pledge",
                   "nsdl", "cdsl", "dp has", "frozen", "freeze", "froze"],
    "bank": ["bank", "fixed deposit", "recurring deposit", "savings account", "current account", "credit card",
             "debit card", "loan", "emi", "upi", "neft", "imps", "net banking", "netbanking", "atm", "cheque",
             "nbfc", "interest credited", "bank's app", "bank app", "branch"],
}

OUT_OF_CORPUS: Dict[str, List[str]] = {
    "insurance": ["insurance", "insurer", "irdai", "ulip", "surrender value", "policy premium", "claim settlement"],
    "crypto assets": ["crypto", "bitcoin", "ethereum", "token", "crypto exchange"],
    "pension": ["nps", "pension fund", "provident fund", "epf"],
    "commodity or chit funds": ["chit fund", "ponzi", "commodity", "gold scheme"],
}

DEFICIENCY = ["not received", "not credited", "delay", "delayed", "unauthor", "mis-sold", "missold", "refus",
              "not allotted", "failed", "fraud", "not issued", "lower than", "frozen", "froze", "freeze",
              "wrong", "without notice", "without my", "did not", "has not", "have not", "not paid", "stuck"]
MARKET_LOSS = ["market fell", "market crash", "price fell", "price dropped", "share price", "stock price fell",
               "returns are lower", "negative returns", "lost money because the market", "loss due to market",
               "nav fell", "nav dropped", "market risk", "market went down", "market declined"]


def _hits(text: str, terms: List[str]) -> List[str]:
    out = []
    for t in terms:
        if re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", text):
            out.append(t)
    return out


def classify(text: str, entity_name: str | None = None) -> dict:
    low = (text + " " + (entity_name or "")).lower()
    hits = {d: _hits(low, t) for d, t in TERMS.items()}
    ooc = {k: _hits(low, t) for k, t in OUT_OF_CORPUS.items()}
    ooc = {k: v for k, v in ooc.items() if v}
    sebi_scores = {d: len(hits[d]) for d in SEBI_DOMAINS}
    best_sebi, best_sebi_n = max(sebi_scores.items(), key=lambda kv: kv[1])
    bank_n = len(hits["bank"])
    market_loss = bool(_hits(low, MARKET_LOSS)) and not _hits(low, DEFICIENCY)

    result = {"domain": "unknown", "regulator": None, "split": False, "out_of_corpus": None,
              "market_loss": market_loss, "unlisted": bool(_hits(low, ["unlisted", "delisted"])), "hits": {k: v for k, v in hits.items() if v}, "product_terms": []}
    if ooc:
        label = max(ooc.items(), key=lambda kv: len(kv[1]))[0]
        if max(len(v) for v in ooc.values()) >= max(bank_n, best_sebi_n):
            result.update(domain="out_of_corpus", out_of_corpus=label, product_terms=ooc[label])
            return result
    if bank_n >= 2 and best_sebi_n >= 2:  # evidence for both a bank and a SEBI-regulated entity
        result.update(domain=best_sebi, split=True, product_terms=hits[best_sebi] + hits["bank"])
        return result
    if bank_n > best_sebi_n:
        result.update(domain="out_of_jurisdiction", regulator="RBI", product_terms=hits["bank"])
    elif best_sebi_n >= 1:
        result.update(domain=best_sebi, regulator="SEBI", product_terms=hits[best_sebi])
    return result
