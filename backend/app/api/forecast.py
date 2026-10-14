from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db.database import get_db
from ..schemas.common import ok
from ..services.forecast_service import build_forecast

router = APIRouter(prefix="/forecast", tags=["forecast"])


@router.get("")
def get_forecast(project_id: str | None = None, db: Session = Depends(get_db)):
    return ok(build_forecast(db, project_id))
