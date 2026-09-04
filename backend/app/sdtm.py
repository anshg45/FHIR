"""CDISC SDTM exports (DM + AE domains) and a Define-XML v2.0 skeleton.

Values come from live PostgreSQL data - nothing is mocked. Variable names and
controlled terminology follow the CDISC SDTM Implementation Guide v3.3.
"""
import csv
import io
from datetime import datetime, timezone
from xml.sax.saxutils import escape

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AdverseEvent, Patient, ResearchStudy

DM_COLUMNS = [
    "STUDYID", "DOMAIN", "USUBJID", "SUBJID", "RFSTDTC", "RFENDTC",
    "SITEID", "AGE", "AGEU", "SEX", "RACE", "COUNTRY", "ARM", "ARMCD", "DMDTC",
]

AE_COLUMNS = [
    "STUDYID", "DOMAIN", "USUBJID", "AESEQ", "AETERM", "AEDECOD", "AEBODSYS",
    "AEPTCD", "AESER", "AESEV", "AEREL", "AEOUT", "AEACN", "AESTDTC", "AEENDTC",
    "AEDTC",
]

SEX_MAP = {"male": "M", "female": "F", "other": "U", "unknown": "U"}
AESER_MAP = {"serious": "Y", "non_serious": "N"}
AESEV_MAP = {"mild": "MILD", "moderate": "MODERATE", "severe": "SEVERE"}
AEREL_MAP = {
    "certain": "RELATED",
    "probable": "PROBABLY RELATED",
    "possible": "POSSIBLY RELATED",
    "unlikely": "UNLIKELY RELATED",
    "not_related": "NOT RELATED",
    "unassessable": "NOT ASSESSABLE",
}
AEOUT_MAP = {
    "recovered": "RECOVERED/RESOLVED",
    "recovering": "RECOVERING/RESOLVING",
    "ongoing": "NOT RECOVERED/NOT RESOLVED",
    "recovered_with_sequelae": "RECOVERED/RESOLVED WITH SEQUELAE",
    "fatal": "FATAL",
    "unknown": "UNKNOWN",
}


def _iso(v) -> str:
    return v.isoformat() if v is not None else ""


def _usubjid(study: ResearchStudy, p: Patient) -> str:
    return f"{study.protocol_number}-{p.screening_number}"


def build_dm(db: Session, study: ResearchStudy) -> list[dict]:
    patients = (
        db.execute(
            select(Patient).where(Patient.study_id == study.id).order_by(Patient.screening_number)
        )
        .scalars()
        .all()
    )
    site_code = study.site.code if study.site else ""
    rows = []
    for p in patients:
        rows.append(
            {
                "STUDYID": study.protocol_number,
                "DOMAIN": "DM",
                "USUBJID": _usubjid(study, p),
                "SUBJID": p.screening_number,
                "RFSTDTC": _iso(p.enrollment_date),
                "RFENDTC": _iso(p.completion_date),
                "SITEID": site_code,
                "AGE": p.age if p.age is not None else "",
                "AGEU": "YEARS",
                "SEX": SEX_MAP.get((p.sex or "").lower(), "U"),
                "RACE": "",
                "COUNTRY": "IND",
                "ARM": p.arm or "",
                "ARMCD": (p.arm or "")[:20].upper().replace(" ", ""),
                "DMDTC": _iso(p.screening_date),
            }
        )
    return rows


def build_ae(db: Session, study: ResearchStudy) -> list[dict]:
    aes = (
        db.execute(
            select(AdverseEvent)
            .where(AdverseEvent.study_id == study.id)
            .order_by(AdverseEvent.ae_number)
        )
        .scalars()
        .all()
    )
    per_subject: dict[str, int] = {}
    rows = []
    for ae in aes:
        p = ae.patient
        usubjid = _usubjid(study, p) if p else ""
        per_subject[usubjid] = per_subject.get(usubjid, 0) + 1
        rows.append(
            {
                "STUDYID": study.protocol_number,
                "DOMAIN": "AE",
                "USUBJID": usubjid,
                "AESEQ": per_subject[usubjid],
                "AETERM": ae.description,
                "AEDECOD": ae.meddra_pt or ae.ae_term or "",
                "AEBODSYS": ae.meddra_soc or "",
                "AEPTCD": ae.meddra_code or "",
                "AESER": AESER_MAP.get(ae.seriousness, "N"),
                "AESEV": AESEV_MAP.get(ae.severity or "", ""),
                "AEREL": AEREL_MAP.get(ae.causality or "", ""),
                "AEOUT": AEOUT_MAP.get(ae.outcome or "", ""),
                "AEACN": (ae.action_taken or "").upper(),
                "AESTDTC": _iso(ae.onset_date),
                "AEENDTC": _iso(ae.resolution_date),
                "AEDTC": _iso(ae.report_date),
            }
        )
    return rows


def to_csv(columns: list[str], rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


# -------------------------------------------------------------- Define-XML
DM_META = [
    ("STUDYID", "text", "Study Identifier", "Req"),
    ("DOMAIN", "text", "Domain Abbreviation", "Req"),
    ("USUBJID", "text", "Unique Subject Identifier", "Req"),
    ("SUBJID", "text", "Subject Identifier for the Study", "Req"),
    ("RFSTDTC", "datetime", "Subject Reference Start Date/Time", "Exp"),
    ("RFENDTC", "datetime", "Subject Reference End Date/Time", "Exp"),
    ("SITEID", "text", "Study Site Identifier", "Req"),
    ("AGE", "integer", "Age", "Exp"),
    ("AGEU", "text", "Age Units", "Exp"),
    ("SEX", "text", "Sex", "Req"),
    ("RACE", "text", "Race", "Perm"),
    ("COUNTRY", "text", "Country", "Req"),
    ("ARM", "text", "Description of Planned Arm", "Req"),
    ("ARMCD", "text", "Planned Arm Code", "Req"),
    ("DMDTC", "datetime", "Date/Time of Collection", "Perm"),
]

AE_META = [
    ("STUDYID", "text", "Study Identifier", "Req"),
    ("DOMAIN", "text", "Domain Abbreviation", "Req"),
    ("USUBJID", "text", "Unique Subject Identifier", "Req"),
    ("AESEQ", "integer", "Sequence Number", "Req"),
    ("AETERM", "text", "Reported Term for the Adverse Event", "Req"),
    ("AEDECOD", "text", "Dictionary-Derived Term", "Req"),
    ("AEBODSYS", "text", "Body System or Organ Class", "Exp"),
    ("AEPTCD", "text", "Preferred Term Code", "Perm"),
    ("AESER", "text", "Serious Event", "Exp"),
    ("AESEV", "text", "Severity/Intensity", "Perm"),
    ("AEREL", "text", "Causality", "Exp"),
    ("AEOUT", "text", "Outcome of Adverse Event", "Perm"),
    ("AEACN", "text", "Action Taken with Study Treatment", "Perm"),
    ("AESTDTC", "datetime", "Start Date/Time of Adverse Event", "Exp"),
    ("AEENDTC", "datetime", "End Date/Time of Adverse Event", "Perm"),
    ("AEDTC", "datetime", "Date/Time of Collection", "Perm"),
]


def _item_refs(prefix, meta):
    out = []
    for i, (name, _d, _l, core) in enumerate(meta):
        mandatory = "Yes" if core == "Req" else "No"
        out.append(
            '        <ItemRef ItemOID="IT.%s.%s" OrderNumber="%d" Mandatory="%s"/>'
            % (prefix, name, i + 1, mandatory)
        )
    return "\n".join(out)


def _item_defs(prefix, meta):
    out = []
    for name, dtype, label, _core in meta:
        out.append(
            '      <ItemDef OID="IT.%s.%s" Name="%s" DataType="%s">\n'
            '        <Description><TranslatedText xml:lang="en">%s</TranslatedText>'
            "</Description>\n"
            "      </ItemDef>" % (prefix, name, name, dtype, escape(label))
        )
    return "\n".join(out)


def build_define_xml(study: ResearchStudy) -> str:
    now = datetime.now(timezone.utc).isoformat()
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<ODM xmlns="http://www.cdisc.org/ns/odm/v1.3"',
        '     xmlns:def="http://www.cdisc.org/ns/def/v2.0"',
        '     xmlns:xlink="http://www.w3.org/1999/xlink"',
        '     ODMVersion="1.3.2" FileType="Snapshot"',
        '     FileOID="AIIA.%s.DEFINE"' % escape(study.protocol_number),
        '     CreationDateTime="%s">' % now,
        '  <Study OID="ST.%s">' % escape(study.protocol_number),
        "    <GlobalVariables>",
        "      <StudyName>%s</StudyName>" % escape(study.protocol_number),
        "      <StudyDescription>%s</StudyDescription>" % escape(study.title or ""),
        "      <ProtocolName>%s</ProtocolName>" % escape(study.protocol_number),
        "    </GlobalVariables>",
        '    <MetaDataVersion OID="MDV.1" Name="SDTM Define-XML"',
        '                     Description="AIIA CTMS generated SDTM metadata"',
        '                     def:DefineVersion="2.0.0" def:StandardName="SDTM-IG"',
        '                     def:StandardVersion="3.3">',
        '      <ItemGroupDef OID="IG.DM" Name="DM" Repeating="No" IsReferenceData="No"',
        '                    Purpose="Tabulation" def:Structure="One record per subject"',
        '                    def:Class="SPECIAL PURPOSE" def:ArchiveLocationID="LF.DM">',
        '        <Description><TranslatedText xml:lang="en">Demographics'
        "</TranslatedText></Description>",
        _item_refs("DM", DM_META),
        "      </ItemGroupDef>",
        '      <ItemGroupDef OID="IG.AE" Name="AE" Repeating="Yes" IsReferenceData="No"',
        '                    Purpose="Tabulation"',
        '                    def:Structure="One record per adverse event per subject"',
        '                    def:Class="EVENTS" def:ArchiveLocationID="LF.AE">',
        '        <Description><TranslatedText xml:lang="en">Adverse Events'
        "</TranslatedText></Description>",
        _item_refs("AE", AE_META),
        "      </ItemGroupDef>",
        _item_defs("DM", DM_META),
        _item_defs("AE", AE_META),
        '      <def:leaf ID="LF.DM" xlink:href="dm.csv">',
        "        <def:title>dm.csv</def:title>",
        "      </def:leaf>",
        '      <def:leaf ID="LF.AE" xlink:href="ae.csv">',
        "        <def:title>ae.csv</def:title>",
        "      </def:leaf>",
        "    </MetaDataVersion>",
        "  </Study>",
        "</ODM>",
        "",
    ]
    return "\n".join(parts)
