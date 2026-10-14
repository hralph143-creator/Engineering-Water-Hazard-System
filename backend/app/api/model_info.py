import json
import pandas as pd
from fastapi import APIRouter

from ..config import get_config
from ..schemas.common import ok
from ..services.model_service import get_model_service

router = APIRouter(prefix="/model", tags=["model"])


@router.get("/status")
def model_status():
    return ok(get_model_service().status())


@router.get("/evaluation")
def model_evaluation():
    cfg = get_config()
    history = []
    training = {"epochs": None, "best_epoch": None, "best_val_iou": None, "elapsed_minutes": None}
    if cfg.model.training_history.is_file():
        frame = pd.read_csv(cfg.model.training_history)
        history = frame.where(lambda x: x.notna(), None).to_dict("records")
        if not frame.empty:
            best = frame.loc[frame["iou"].idxmax()]
            training.update(epochs=int(frame["epoch"].max()), best_epoch=int(best["epoch"]),
                            best_val_iou=float(best["iou"]))
    if cfg.model.experiment_summary.is_file():
        try:
            summary = json.loads(cfg.model.experiment_summary.read_text(encoding="utf-8"))
            training["elapsed_minutes"] = float(summary["elapsed_seconds"]) / 60
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return ok({"status": get_model_service().status(), "history": history, "training": training})
