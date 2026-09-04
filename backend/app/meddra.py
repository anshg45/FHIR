"""SYNTHETIC MedDRA-style dictionary.

IMPORTANT / ASSUMPTION: real MedDRA and WHODrug require paid subscription
licences and cannot be redistributed. This module is a clearly-labelled
SYNTHETIC stub with MedDRA-shaped fields (PT code, Preferred Term, SOC) so the
coding workflow, API contracts and exports are fully exercised. In production
the lookup function is the single swap point for a licensed dictionary.
"""
from difflib import SequenceMatcher

DICTIONARY_NAME = "AIIA-SYNTHETIC-MedDRA-STUB v1.0 (NOT licensed MedDRA)"

# code, preferred term, system organ class
TERMS: list[dict] = [
    {"code": "S10000001", "pt": "Nausea", "soc": "Gastrointestinal disorders"},
    {"code": "S10000002", "pt": "Vomiting", "soc": "Gastrointestinal disorders"},
    {"code": "S10000003", "pt": "Diarrhoea", "soc": "Gastrointestinal disorders"},
    {"code": "S10000004", "pt": "Abdominal pain", "soc": "Gastrointestinal disorders"},
    {"code": "S10000005", "pt": "Constipation", "soc": "Gastrointestinal disorders"},
    {"code": "S10000006", "pt": "Gastritis", "soc": "Gastrointestinal disorders"},
    {"code": "S10000010", "pt": "Headache", "soc": "Nervous system disorders"},
    {"code": "S10000011", "pt": "Dizziness", "soc": "Nervous system disorders"},
    {"code": "S10000012", "pt": "Somnolence", "soc": "Nervous system disorders"},
    {"code": "S10000013", "pt": "Paraesthesia", "soc": "Nervous system disorders"},
    {"code": "S10000020", "pt": "Rash", "soc": "Skin and subcutaneous tissue disorders"},
    {"code": "S10000021", "pt": "Pruritus", "soc": "Skin and subcutaneous tissue disorders"},
    {"code": "S10000022", "pt": "Urticaria", "soc": "Skin and subcutaneous tissue disorders"},
    {"code": "S10000030", "pt": "Fatigue", "soc": "General disorders and administration site conditions"},
    {"code": "S10000031", "pt": "Pyrexia", "soc": "General disorders and administration site conditions"},
    {"code": "S10000032", "pt": "Asthenia", "soc": "General disorders and administration site conditions"},
    {"code": "S10000040", "pt": "Hypertension", "soc": "Vascular disorders"},
    {"code": "S10000041", "pt": "Hypotension", "soc": "Vascular disorders"},
    {"code": "S10000050", "pt": "Alanine aminotransferase increased", "soc": "Investigations"},
    {"code": "S10000051", "pt": "Blood creatinine increased", "soc": "Investigations"},
    {"code": "S10000052", "pt": "Haemoglobin decreased", "soc": "Investigations"},
    {"code": "S10000060", "pt": "Arthralgia", "soc": "Musculoskeletal and connective tissue disorders"},
    {"code": "S10000061", "pt": "Myalgia", "soc": "Musculoskeletal and connective tissue disorders"},
    {"code": "S10000062", "pt": "Back pain", "soc": "Musculoskeletal and connective tissue disorders"},
    {"code": "S10000070", "pt": "Cough", "soc": "Respiratory, thoracic and mediastinal disorders"},
    {"code": "S10000071", "pt": "Dyspnoea", "soc": "Respiratory, thoracic and mediastinal disorders"},
    {"code": "S10000072", "pt": "Bronchospasm", "soc": "Respiratory, thoracic and mediastinal disorders"},
    {"code": "S10000080", "pt": "Anxiety", "soc": "Psychiatric disorders"},
    {"code": "S10000081", "pt": "Insomnia", "soc": "Psychiatric disorders"},
    {"code": "S10000090", "pt": "Anaphylactic reaction", "soc": "Immune system disorders"},
    {"code": "S10000091", "pt": "Hypersensitivity", "soc": "Immune system disorders"},
    {"code": "S10000100", "pt": "Hepatic function abnormal", "soc": "Hepatobiliary disorders"},
    {"code": "S10000101", "pt": "Jaundice", "soc": "Hepatobiliary disorders"},
    {"code": "S10000110", "pt": "Acute kidney injury", "soc": "Renal and urinary disorders"},
    {"code": "S10000120", "pt": "Decreased appetite", "soc": "Metabolism and nutrition disorders"},
    {"code": "S10000121", "pt": "Dehydration", "soc": "Metabolism and nutrition disorders"},
    {"code": "S10000130", "pt": "Death", "soc": "General disorders and administration site conditions"},
]

SYNONYMS = {
    "loose motions": "Diarrhoea",
    "loose stools": "Diarrhoea",
    "stomach pain": "Abdominal pain",
    "stomach ache": "Abdominal pain",
    "vomitting": "Vomiting",
    "giddiness": "Dizziness",
    "itching": "Pruritus",
    "skin rash": "Rash",
    "fever": "Pyrexia",
    "weakness": "Asthenia",
    "tiredness": "Fatigue",
    "joint pain": "Arthralgia",
    "muscle pain": "Myalgia",
    "breathlessness": "Dyspnoea",
    "sleeplessness": "Insomnia",
    "high bp": "Hypertension",
    "low bp": "Hypotension",
    "raised sgpt": "Alanine aminotransferase increased",
    "yellowing of eyes": "Jaundice",
}

_BY_PT = {t["pt"].lower(): t for t in TERMS}
_BY_CODE = {t["code"]: t for t in TERMS}


def search(term: str, limit: int = 8) -> list[dict]:
    q = (term or "").strip().lower()
    if not q:
        return []
    scored = []
    for t in TERMS:
        pt = t["pt"].lower()
        score = 1.0 if q == pt else (0.9 if q in pt or pt in q else SequenceMatcher(None, q, pt).ratio())
        scored.append((score, t))
    for syn, pt in SYNONYMS.items():
        if q in syn or syn in q:
            t = _BY_PT.get(pt.lower())
            if t:
                scored.append((0.95, t))
    scored.sort(key=lambda x: -x[0])
    seen, out = set(), []
    for score, t in scored:
        if t["code"] in seen:
            continue
        seen.add(t["code"])
        out.append({**t, "match_score": round(score, 3), "dictionary": DICTIONARY_NAME})
        if len(out) >= limit:
            break
    return out


def autocode(term: str) -> dict | None:
    """Best-effort automatic coding of a verbatim AE term."""
    hits = search(term, limit=1)
    if not hits or hits[0]["match_score"] < 0.55:
        return None
    return hits[0]


def by_code(code: str) -> dict | None:
    t = _BY_CODE.get(code)
    return {**t, "dictionary": DICTIONARY_NAME} if t else None


def soc_list() -> list[str]:
    return sorted({t["soc"] for t in TERMS})
