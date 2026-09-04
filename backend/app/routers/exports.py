"""SDTM CSV + Define-XML regulatory exports (CDISC SDTM IG v3.3)."""
from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_study_or_404
from ..models import User
from ..sdtm import (
    AE_COLUMNS,
    DM_COLUMNS,
    build_ae,
    build_define_xml,
    build_dm,
    to_csv,
)
from ..security import RequireRoles

router = APIRouter(prefix="/exports", tags=["Exports (CDISC SDTM)"])
EXPORT_ROLES = RequireRoles("admin", "regulator", "pi", "pv")


@router.get("/studies/{study_id}/sdtm/dm.csv", response_class=PlainTextResponse)
def export_dm(study_id: str, db: Session = Depends(get_db),
              _: User = Depends(EXPORT_ROLES)):
    study = get_study_or_404(db, study_id)
    csv_text = to_csv(DM_COLUMNS, build_dm(db, study))
    return PlainTextResponse(
        content=csv_text,
        headers={"Content-Disposition": f'attachment; filename="{study.protocol_number}-dm.csv"'},
        media_type="text/csv",
    )


@router.get("/studies/{study_id}/sdtm/ae.csv", response_class=PlainTextResponse)
def export_ae(study_id: str, db: Session = Depends(get_db),
              _: User = Depends(EXPORT_ROLES)):
    study = get_study_or_404(db, study_id)
    csv_text = to_csv(AE_COLUMNS, build_ae(db, study))
    return PlainTextResponse(
        content=csv_text,
        headers={"Content-Disposition": f'attachment; filename="{study.protocol_number}-ae.csv"'},
        media_type="text/csv",
    )


@router.get("/studies/{study_id}/sdtm/define.xml")
def export_define(study_id: str, db: Session = Depends(get_db),
                  _: User = Depends(EXPORT_ROLES)):
    study = get_study_or_404(db, study_id)
    xml = build_define_xml(study)
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{study.protocol_number}-define.xml"'},
    )


@router.get("/studies/{study_id}/sdtm/preview")
def preview(study_id: str, db: Session = Depends(get_db),
            _: User = Depends(EXPORT_ROLES)):
    """JSON preview of the SDTM DM + AE datasets (no file download)."""
    study = get_study_or_404(db, study_id)
    dm = build_dm(db, study)
    ae = build_ae(db, study)
    return {
        "study_id": study.id,
        "protocol_number": study.protocol_number,
        "dm": {"columns": DM_COLUMNS, "row_count": len(dm), "rows": dm[:20]},
        "ae": {"columns": AE_COLUMNS, "row_count": len(ae), "rows": ae[:20]},
        "standard": "CDISC SDTM IG v3.3",
    }
