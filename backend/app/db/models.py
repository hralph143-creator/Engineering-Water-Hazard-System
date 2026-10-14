from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow():
    return datetime.now(timezone.utc)


class DatasetRecord(Base):
    __tablename__ = "datasets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(Text, unique=True)
    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)
    source_type: Mapped[str] = mapped_column(String(64), default="Sentinel-1 SAR")
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    bands: Mapped[int] = mapped_column(Integer)
    dtype: Mapped[str] = mapped_column(String(64), default="")
    crs: Mapped[str | None] = mapped_column(String(128), nullable=True)
    transform: Mapped[str] = mapped_column(Text, default="")
    bounds: Mapped[str] = mapped_column(Text, default="")
    nodata: Mapped[str | None] = mapped_column(String(64), nullable=True)
    vv_stats: Mapped[str] = mapped_column(Text, default="{}")
    vh_stats: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    capture_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="valid")
    project_id: Mapped[str | None] = mapped_column(ForeignKey("construction_projects.id", ondelete="SET NULL"), nullable=True, index=True)

    tasks: Mapped[list["InferenceTask"]] = relationship(back_populates="dataset")


class InferenceTask(Base):
    __tablename__ = "inference_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    threshold: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    checkpoint: Mapped[str] = mapped_column(Text)
    model_name: Mapped[str] = mapped_column(String(128), default="U-Net")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    batch_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    retry_of_task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("construction_projects.id", ondelete="SET NULL"), nullable=True, index=True)

    dataset: Mapped[DatasetRecord] = relationship(back_populates="tasks")
    result: Mapped["InferenceResult | None"] = relationship(
        back_populates="task", cascade="all, delete-orphan", uselist=False
    )


class InferenceResult(Base):
    __tablename__ = "inference_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("inference_tasks.id", ondelete="CASCADE"), unique=True)
    probability_path: Mapped[str] = mapped_column(Text)
    mask_path: Mapped[str] = mapped_column(Text)
    comparison_path: Mapped[str] = mapped_column(Text)
    flood_pixel_count: Mapped[int] = mapped_column(Integer)
    valid_pixel_count: Mapped[int] = mapped_column(Integer)
    flood_ratio: Mapped[float] = mapped_column(Float)
    mean_confidence: Mapped[float] = mapped_column(Float)
    risk_level: Mapped[str] = mapped_column(String(32))
    inference_seconds: Mapped[float] = mapped_column(Float)
    # 概率统计（仅对有效像素计算；旧结果可能为 NULL）
    probability_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    probability_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    probability_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    probability_median: Mapped[float | None] = mapped_column(Float, nullable=True)
    high_confidence_flood_pixel_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    high_confidence_flood_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    task: Mapped[InferenceTask] = relationship(back_populates="result")


class ChangeAnalysis(Base):
    """一次可追溯、可复用的 T1→T2 积水变化分析。"""

    __tablename__ = "change_analyses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    task_t1_id: Mapped[str] = mapped_column(
        ForeignKey("inference_tasks.id", ondelete="RESTRICT"), index=True
    )
    task_t2_id: Mapped[str] = mapped_column(
        ForeignKey("inference_tasks.id", ondelete="RESTRICT"), index=True
    )
    result_t1_id: Mapped[str] = mapped_column(
        ForeignKey("inference_results.id", ondelete="RESTRICT"), index=True
    )
    result_t2_id: Mapped[str] = mapped_column(
        ForeignKey("inference_results.id", ondelete="RESTRICT"), index=True
    )
    dataset_t1_id: Mapped[str] = mapped_column(ForeignKey("datasets.id", ondelete="RESTRICT"))
    dataset_t2_id: Mapped[str] = mapped_column(ForeignKey("datasets.id", ondelete="RESTRICT"))

    # 保留创建时快照，源任务后续变化也不会改变历史报告语义。
    dataset_t1_name: Mapped[str] = mapped_column(String(255))
    dataset_t2_name: Mapped[str] = mapped_column(String(255))
    finished_t1_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_t2_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    model_t1_name: Mapped[str] = mapped_column(String(128))
    model_t2_name: Mapped[str] = mapped_column(String(128))
    threshold_t1: Mapped[float] = mapped_column(Float)
    threshold_t2: Mapped[float] = mapped_column(Float)

    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    spatial_consistent: Mapped[bool | None] = mapped_column(nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    algorithm_version: Mapped[str] = mapped_column(String(64), default="pixel-change-v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    new_water_pixels: Mapped[int | None] = mapped_column(Integer, nullable=True)
    persistent_water_pixels: Mapped[int | None] = mapped_column(Integer, nullable=True)
    receded_water_pixels: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stable_non_water_pixels: Mapped[int | None] = mapped_column(Integer, nullable=True)
    valid_pixels: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_water_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    persistent_water_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    receded_water_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    stable_non_water_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    t1_water_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    t2_water_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_change_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    change_png_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    change_geotiff_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("construction_projects.id", ondelete="SET NULL"), nullable=True, index=True)


class RiskAssessment(Base):
    """基于真实识别与变化成果生成的可解释基础水害辅助研判。"""

    __tablename__ = "risk_assessments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    assessment_name: Mapped[str] = mapped_column(String(255))
    source_type: Mapped[str] = mapped_column(String(32), default="change_analysis")
    source_result_id: Mapped[str | None] = mapped_column(
        ForeignKey("inference_results.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    source_change_analysis_id: Mapped[str | None] = mapped_column(
        ForeignKey("change_analyses.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    source_t1_name: Mapped[str] = mapped_column(String(255))
    source_t2_name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    attention_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rule_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_water_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    new_water_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    persistent_water_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    receded_water_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_change_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_snapshot: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_reason_codes: Mapped[str] = mapped_column(Text, default="[]")
    risk_reason_text: Mapped[str] = mapped_column(Text, default="[]")
    inspection_advice: Mapped[str] = mapped_column(Text, default="[]")
    operation_note: Mapped[str] = mapped_column(Text)
    high_attention_pixels: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inspection_pixels: Mapped[int | None] = mapped_column(Integer, nullable=True)
    general_pixels: Mapped[int | None] = mapped_column(Integer, nullable=True)
    nodata_pixels: Mapped[int | None] = mapped_column(Integer, nullable=True)
    risk_png_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_geotiff_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_json_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("construction_projects.id", ondelete="SET NULL"), nullable=True, index=True)


class ConstructionProject(Base):
    __tablename__ = "construction_projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_name: Mapped[str] = mapped_column(String(255))
    project_code: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    project_type: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text, default="")
    location_text: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class WorkArea(Base):
    __tablename__ = "work_areas"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("construction_projects.id", ondelete="RESTRICT"), index=True)
    area_name: Mapped[str] = mapped_column(String(255))
    area_code: Mapped[str] = mapped_column(String(128))
    area_type: Mapped[str] = mapped_column(String(64), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    geometry_type: Mapped[str] = mapped_column(String(32))
    geometry_geojson: Mapped[str] = mapped_column(Text)
    crs: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("project_id", "area_code", name="uq_work_area_project_code"),)


class WorkAreaImpact(Base):
    __tablename__ = "work_area_impacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    risk_assessment_id: Mapped[str] = mapped_column(ForeignKey("risk_assessments.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("construction_projects.id", ondelete="RESTRICT"), index=True)
    work_area_id: Mapped[str] = mapped_column(ForeignKey("work_areas.id", ondelete="RESTRICT"), index=True)
    analysis_status: Mapped[str] = mapped_column(String(32), default="completed")
    total_valid_pixels: Mapped[int] = mapped_column(Integer, default=0)
    nodata_pixels: Mapped[int] = mapped_column(Integer, default=0)
    general_pixels: Mapped[int] = mapped_column(Integer, default=0)
    inspection_pixels: Mapped[int] = mapped_column(Integer, default=0)
    high_attention_pixels: Mapped[int] = mapped_column(Integer, default=0)
    general_ratio: Mapped[float] = mapped_column(Float, default=0)
    inspection_ratio: Mapped[float] = mapped_column(Float, default=0)
    high_attention_ratio: Mapped[float] = mapped_column(Float, default=0)
    affected_pixels: Mapped[int] = mapped_column(Integer, default=0)
    affected_ratio: Mapped[float] = mapped_column(Float, default=0)
    dominant_class: Mapped[str] = mapped_column(String(32), default="general")
    impact_level: Mapped[str] = mapped_column(String(32))
    advice: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    algorithm_version: Mapped[str] = mapped_column(String(64), default="work-area-impact-v1")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    __table_args__ = (UniqueConstraint("risk_assessment_id", "work_area_id", name="uq_risk_work_area_impact"),)


class ConstructionEquipment(Base):
    __tablename__ = "construction_equipment"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("construction_projects.id", ondelete="RESTRICT"), index=True)
    equipment_code: Mapped[str] = mapped_column(String(128))
    equipment_name: Mapped[str] = mapped_column(String(255))
    equipment_type: Mapped[str] = mapped_column(String(64), index=True)
    manufacturer: Mapped[str] = mapped_column(String(255), default="")
    model: Mapped[str] = mapped_column(String(255), default="")
    business_status: Mapped[str] = mapped_column(String(32), default="idle", index=True)
    lifecycle_status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("project_id", "equipment_code", name="uq_equipment_project_code"),)


class EquipmentPosition(Base):
    __tablename__ = "equipment_positions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    equipment_id: Mapped[str] = mapped_column(ForeignKey("construction_equipment.id", ondelete="RESTRICT"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("construction_projects.id", ondelete="RESTRICT"), index=True)
    x: Mapped[float] = mapped_column(Float)
    y: Mapped[float] = mapped_column(Float)
    crs: Mapped[str] = mapped_column(String(128))
    position_source: Mapped[str] = mapped_column(String(32))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    accuracy_note: Mapped[str] = mapped_column(String(512), default="")
    source_note: Mapped[str] = mapped_column(Text, default="")


class EquipmentRiskLink(Base):
    __tablename__ = "equipment_risk_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    equipment_id: Mapped[str] = mapped_column(ForeignKey("construction_equipment.id", ondelete="RESTRICT"), index=True)
    position_id: Mapped[str] = mapped_column(ForeignKey("equipment_positions.id", ondelete="RESTRICT"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("construction_projects.id", ondelete="RESTRICT"), index=True)
    risk_assessment_id: Mapped[str] = mapped_column(ForeignKey("risk_assessments.id", ondelete="CASCADE"), index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    risk_analysis_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    time_difference_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    position_age_hours: Mapped[float] = mapped_column(Float)
    position_status: Mapped[str] = mapped_column(String(32))
    risk_class: Mapped[str] = mapped_column(String(64))
    risk_zone_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    risk_label: Mapped[str] = mapped_column(String(64))
    work_area_ids: Mapped[str] = mapped_column(Text, default="[]")
    primary_work_area_id: Mapped[str | None] = mapped_column(ForeignKey("work_areas.id", ondelete="SET NULL"), nullable=True, index=True)
    work_area_impact_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason_text: Mapped[str] = mapped_column(Text)
    advice_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    rule_version: Mapped[str] = mapped_column(String(64), default="equipment-water-link-v1")
    __table_args__ = (UniqueConstraint("risk_assessment_id", "equipment_id", name="uq_risk_equipment_link"),)


class TerrainDataset(Base):
    __tablename__ = "terrain_datasets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("construction_projects.id", ondelete="SET NULL"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(Text, unique=True)
    file_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    data_type: Mapped[str] = mapped_column(String(32), default="dem")
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    band_count: Mapped[int] = mapped_column(Integer)
    dtype: Mapped[str] = mapped_column(String(64))
    crs: Mapped[str] = mapped_column(String(128))
    transform: Mapped[str] = mapped_column(Text)
    bounds: Mapped[str] = mapped_column(Text)
    nodata: Mapped[str | None] = mapped_column(String(64), nullable=True)
    min_elevation: Mapped[float] = mapped_column(Float)
    max_elevation: Mapped[float] = mapped_column(Float)
    mean_elevation: Mapped[float] = mapped_column(Float)
    elevation_std: Mapped[float] = mapped_column(Float)
    valid_pixel_count: Mapped[int] = mapped_column(Integer)
    nodata_ratio: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), default="valid", index=True)
    source_name: Mapped[str] = mapped_column(String(255), default="")
    capture_date: Mapped[str] = mapped_column(String(64), default="")
    resolution_note: Mapped[str] = mapped_column(String(255), default="")
    vertical_datum_note: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    elevation_preview_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    slope_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    slope_preview_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    low_mask_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    low_mask_preview_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_json_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    terrain_rule_version: Mapped[str] = mapped_column(String(64), default="terrain-analysis-v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class WorkAreaTerrainProfile(Base):
    __tablename__ = "work_area_terrain_profiles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("construction_projects.id", ondelete="RESTRICT"), index=True)
    work_area_id: Mapped[str] = mapped_column(ForeignKey("work_areas.id", ondelete="RESTRICT"), index=True)
    dem_id: Mapped[str] = mapped_column(ForeignKey("terrain_datasets.id", ondelete="RESTRICT"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="completed")
    min_elevation: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_elevation: Mapped[float | None] = mapped_column(Float, nullable=True)
    mean_elevation: Mapped[float | None] = mapped_column(Float, nullable=True)
    elevation_range: Mapped[float | None] = mapped_column(Float, nullable=True)
    mean_slope: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_slope: Mapped[float | None] = mapped_column(Float, nullable=True)
    low_elevation_pixels: Mapped[int] = mapped_column(Integer, default=0)
    valid_pixels: Mapped[int] = mapped_column(Integer, default=0)
    low_elevation_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    terrain_description: Mapped[str] = mapped_column(Text, default="")
    terrain_rule_version: Mapped[str] = mapped_column(String(64), default="terrain-analysis-v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    __table_args__ = (UniqueConstraint("work_area_id", "dem_id", name="uq_area_dem_profile"),)


class TerrainFusionAnalysis(Base):
    __tablename__ = "terrain_fusion_analyses"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    risk_assessment_id: Mapped[str] = mapped_column(ForeignKey("risk_assessments.id", ondelete="CASCADE"), index=True)
    dem_id: Mapped[str] = mapped_column(ForeignKey("terrain_datasets.id", ondelete="RESTRICT"), index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("construction_projects.id", ondelete="SET NULL"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="completed")
    high_attention_mean_elevation: Mapped[float | None] = mapped_column(Float, nullable=True)
    high_attention_mean_slope: Mapped[float | None] = mapped_column(Float, nullable=True)
    high_attention_low_elevation_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    inspection_mean_elevation: Mapped[float | None] = mapped_column(Float, nullable=True)
    inspection_mean_slope: Mapped[float | None] = mapped_column(Float, nullable=True)
    inspection_low_elevation_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    general_mean_elevation: Mapped[float | None] = mapped_column(Float, nullable=True)
    general_mean_slope: Mapped[float | None] = mapped_column(Float, nullable=True)
    general_low_elevation_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    algorithm_version: Mapped[str] = mapped_column(String(64), default="terrain-analysis-v1")
    summary_json_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    __table_args__ = (UniqueConstraint("risk_assessment_id", "dem_id", name="uq_risk_dem_fusion"),)


class EquipmentTerrainSnapshot(Base):
    __tablename__ = "equipment_terrain_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    equipment_id: Mapped[str] = mapped_column(ForeignKey("construction_equipment.id", ondelete="RESTRICT"), index=True)
    position_id: Mapped[str] = mapped_column(ForeignKey("equipment_positions.id", ondelete="RESTRICT"), index=True)
    dem_id: Mapped[str] = mapped_column(ForeignKey("terrain_datasets.id", ondelete="RESTRICT"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("construction_projects.id", ondelete="RESTRICT"), index=True)
    status: Mapped[str] = mapped_column(String(32))
    elevation: Mapped[float | None] = mapped_column(Float, nullable=True)
    slope: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_low_elevation: Mapped[bool | None] = mapped_column(nullable=True)
    terrain_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    algorithm_version: Mapped[str] = mapped_column(String(64), default="terrain-analysis-v1")
    __table_args__ = (UniqueConstraint("position_id", "dem_id", name="uq_position_dem_terrain"),)


class RainfallStation(Base):
    __tablename__ = "rainfall_stations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("construction_projects.id", ondelete="RESTRICT"), index=True)
    station_code: Mapped[str] = mapped_column(String(128))
    station_name: Mapped[str] = mapped_column(String(255))
    station_type: Mapped[str] = mapped_column(String(32), default="manual_station")
    x: Mapped[float | None] = mapped_column(Float, nullable=True)
    y: Mapped[float | None] = mapped_column(Float, nullable=True)
    crs: Mapped[str | None] = mapped_column(String(128), nullable=True)
    location_description: Mapped[str] = mapped_column(String(255), default="")
    source_name: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("project_id", "station_code", name="uq_rain_station_project_code"),)


class RainfallObservation(Base):
    __tablename__ = "rainfall_observations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    station_id: Mapped[str] = mapped_column(ForeignKey("rainfall_stations.id", ondelete="RESTRICT"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("construction_projects.id", ondelete="RESTRICT"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    interval_minutes: Mapped[int] = mapped_column(Integer)
    rainfall_mm: Mapped[float] = mapped_column(Float)
    rainfall_rate_mm_h: Mapped[float] = mapped_column(Float)
    source_type: Mapped[str] = mapped_column(String(32), default="manual")
    quality_status: Mapped[str] = mapped_column(String(32), default="valid")
    source_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("station_id", "observed_at", "interval_minutes", name="uq_rain_observation_business"),)


class RainfallEvent(Base):
    __tablename__ = "rainfall_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("construction_projects.id", ondelete="RESTRICT"), index=True)
    generation_id: Mapped[str] = mapped_column(String(36), index=True)
    event_name: Mapped[str] = mapped_column(String(255))
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    duration_minutes: Mapped[int] = mapped_column(Integer)
    total_rainfall_mm: Mapped[float] = mapped_column(Float)
    peak_interval_rainfall_mm: Mapped[float] = mapped_column(Float)
    peak_interval_minutes: Mapped[int] = mapped_column(Integer)
    peak_rainfall_rate_mm_h: Mapped[float] = mapped_column(Float)
    peak_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    observation_count: Mapped[int] = mapped_column(Integer)
    station_count: Mapped[int] = mapped_column(Integer)
    event_status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    rule_version: Mapped[str] = mapped_column(String(64), default="rainfall-event-v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ChangeRainfallContext(Base):
    __tablename__ = "change_rainfall_contexts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    change_analysis_id: Mapped[str] = mapped_column(ForeignKey("change_analyses.id", ondelete="RESTRICT"), unique=True, index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("construction_projects.id", ondelete="RESTRICT"), index=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    pre_window_hours: Mapped[float] = mapped_column(Float)
    total_rainfall_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    rainfall_event_count: Mapped[int] = mapped_column(Integer, default=0)
    observation_count: Mapped[int] = mapped_column(Integer, default=0)
    station_count: Mapped[int] = mapped_column(Integer, default=0)
    peak_rainfall_rate_mm_h: Mapped[float | None] = mapped_column(Float, nullable=True)
    peak_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    has_rainfall_data: Mapped[bool] = mapped_column(default=False)
    coverage_status: Mapped[str] = mapped_column(String(32))
    time_reference: Mapped[str] = mapped_column(String(64))
    rainfall_event_ids: Mapped[str] = mapped_column(Text, default="[]")
    rule_version: Mapped[str] = mapped_column(String(64), default="rainfall-event-v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    summary_json_path: Mapped[str | None] = mapped_column(Text, nullable=True)


class RiskRainfallContext(Base):
    __tablename__ = "risk_rainfall_contexts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    risk_assessment_id: Mapped[str] = mapped_column(ForeignKey("risk_assessments.id", ondelete="RESTRICT"), unique=True, index=True)
    change_rainfall_context_id: Mapped[str] = mapped_column(ForeignKey("change_rainfall_contexts.id", ondelete="RESTRICT"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("construction_projects.id", ondelete="RESTRICT"), index=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    total_rainfall_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    rainfall_event_count: Mapped[int] = mapped_column(Integer, default=0)
    observation_count: Mapped[int] = mapped_column(Integer, default=0)
    station_count: Mapped[int] = mapped_column(Integer, default=0)
    peak_rainfall_rate_mm_h: Mapped[float | None] = mapped_column(Float, nullable=True)
    peak_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latest_event_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latest_event_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    has_rainfall_data: Mapped[bool] = mapped_column(default=False)
    coverage_status: Mapped[str] = mapped_column(String(32))
    time_reference: Mapped[str] = mapped_column(String(64))
    rainfall_event_ids: Mapped[str] = mapped_column(Text, default="[]")
    rule_version: Mapped[str] = mapped_column(String(64), default="rainfall-event-v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UavDataset(Base):
    __tablename__ = "uav_datasets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("construction_projects.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(Text, unique=True)
    file_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    capture_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    flight_name: Mapped[str] = mapped_column(String(255), default="")
    flight_batch: Mapped[str] = mapped_column(String(255), default="")
    source_name: Mapped[str] = mapped_column(String(255), default="")
    source_note: Mapped[str] = mapped_column(Text, default="")
    device_note: Mapped[str] = mapped_column(String(255), default="")
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    band_count: Mapped[int] = mapped_column(Integer)
    dtype: Mapped[str] = mapped_column(String(64))
    crs: Mapped[str] = mapped_column(String(128))
    transform: Mapped[str] = mapped_column(Text)
    bounds: Mapped[str] = mapped_column(Text)
    nodata: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pixel_size_x: Mapped[float] = mapped_column(Float)
    pixel_size_y: Mapped[float] = mapped_column(Float)
    valid_pixel_count: Mapped[int] = mapped_column(Integer)
    nodata_ratio: Mapped[float] = mapped_column(Float)
    resolution_note: Mapped[str] = mapped_column(String(255), default="")
    preview_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="valid", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class UavWorkAreaCoverage(Base):
    __tablename__ = "uav_work_area_coverages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    uav_dataset_id: Mapped[str] = mapped_column(ForeignKey("uav_datasets.id", ondelete="RESTRICT"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("construction_projects.id", ondelete="RESTRICT"), index=True)
    work_area_id: Mapped[str] = mapped_column(ForeignKey("work_areas.id", ondelete="RESTRICT"), index=True)
    coverage_status: Mapped[str] = mapped_column(String(32))
    covered_pixels: Mapped[int] = mapped_column(Integer, default=0)
    valid_area_pixels: Mapped[int] = mapped_column(Integer, default=0)
    coverage_ratio: Mapped[float] = mapped_column(Float, default=0)
    crop_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis_version: Mapped[str] = mapped_column(String(64), default="uav-spatial-v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    __table_args__ = (UniqueConstraint("uav_dataset_id", "work_area_id", name="uq_uav_work_area_coverage"),)


class UavRiskOverlay(Base):
    __tablename__ = "uav_risk_overlays"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    uav_dataset_id: Mapped[str] = mapped_column(ForeignKey("uav_datasets.id", ondelete="RESTRICT"), index=True)
    risk_assessment_id: Mapped[str] = mapped_column(ForeignKey("risk_assessments.id", ondelete="RESTRICT"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("construction_projects.id", ondelete="RESTRICT"), index=True)
    high_attention_covered_pixels: Mapped[int] = mapped_column(Integer, default=0)
    inspection_covered_pixels: Mapped[int] = mapped_column(Integer, default=0)
    general_covered_pixels: Mapped[int] = mapped_column(Integer, default=0)
    high_attention_coverage_ratio: Mapped[float] = mapped_column(Float, default=0)
    inspection_coverage_ratio: Mapped[float] = mapped_column(Float, default=0)
    general_coverage_ratio: Mapped[float] = mapped_column(Float, default=0)
    high_attention_crop_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    inspection_crop_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_json_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    time_difference_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_reference_status: Mapped[str] = mapped_column(String(32), default="TIME_REFERENCE_LIMITED")
    analysis_version: Mapped[str] = mapped_column(String(64), default="uav-spatial-v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("uav_dataset_id", "risk_assessment_id", name="uq_uav_risk_overlay"),)


class UavVerificationRecord(Base):
    __tablename__ = "uav_verification_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("construction_projects.id", ondelete="RESTRICT"), index=True)
    uav_dataset_id: Mapped[str] = mapped_column(ForeignKey("uav_datasets.id", ondelete="RESTRICT"), index=True)
    risk_assessment_id: Mapped[str] = mapped_column(ForeignKey("risk_assessments.id", ondelete="RESTRICT"), index=True)
    work_area_id: Mapped[str | None] = mapped_column(ForeignKey("work_areas.id", ondelete="RESTRICT"), nullable=True, index=True)
    verification_status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    observation_type: Mapped[str] = mapped_column(String(64), default="OTHER")
    finding_text: Mapped[str] = mapped_column(Text, default="")
    recommendation_text: Mapped[str] = mapped_column(Text, default="")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    image_crop_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    structure_version: Mapped[str] = mapped_column(String(64), default="uav-verification-v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class OperationAssistanceRecord(Base):
    """基于正式风险成果生成或由用户创建的水害作业辅助事项。"""

    __tablename__ = "operation_assistance_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("construction_projects.id", ondelete="RESTRICT"), nullable=True, index=True)
    risk_assessment_id: Mapped[str | None] = mapped_column(ForeignKey("risk_assessments.id", ondelete="RESTRICT"), nullable=True, index=True)
    work_area_id: Mapped[str | None] = mapped_column(ForeignKey("work_areas.id", ondelete="RESTRICT"), nullable=True, index=True)
    equipment_id: Mapped[str | None] = mapped_column(ForeignKey("construction_equipment.id", ondelete="RESTRICT"), nullable=True, index=True)
    assistance_type: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(255))
    priority_level: Mapped[str] = mapped_column(String(32), default="NORMAL", index=True)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    source_type: Mapped[str] = mapped_column(String(32), default="rule", index=True)
    source_summary: Mapped[str] = mapped_column(Text, default="")
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    reason_codes: Mapped[str] = mapped_column(Text, default="[]")
    recommendation_text: Mapped[str] = mapped_column(Text, default="")
    inspection_checklist: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by_note: Mapped[str] = mapped_column(Text, default="")
    rule_version: Mapped[str] = mapped_column(String(64), default="operation-assistance-v1")


class AssistanceVerification(Base):
    """作业辅助事项的现场人工核查记录。"""

    __tablename__ = "assistance_verifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    assistance_record_id: Mapped[str] = mapped_column(ForeignKey("operation_assistance_records.id", ondelete="RESTRICT"), index=True)
    verification_result: Mapped[str] = mapped_column(String(64), index=True)
    finding_text: Mapped[str] = mapped_column(Text, default="")
    action_note: Mapped[str] = mapped_column(Text, default="")
    checklist_json: Mapped[str] = mapped_column(Text, default="[]")
    attachment_note: Mapped[str] = mapped_column(Text, default="")
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
