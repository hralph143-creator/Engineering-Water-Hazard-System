import hashlib
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from ..config import get_config


class Base(DeclarativeBase):
    pass


cfg = get_config()
engine = create_engine(
    f"sqlite:///{cfg.paths.database.as_posix()}",
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def enable_foreign_keys(dbapi_connection, _connection_record):
    dbapi_connection.execute("PRAGMA foreign_keys=ON")


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from . import models  # noqa: F401
    Base.metadata.create_all(engine)
    migrate_schema()


def migrate_schema() -> None:
    """对现有 SQLite 数据库执行幂等原位迁移，并保留历史业务数据。"""
    with engine.begin() as connection:
        inspector = inspect(connection)
        dataset_columns = {item["name"] for item in inspector.get_columns("datasets")}
        task_columns = {item["name"] for item in inspector.get_columns("inference_tasks")}
        if "file_hash" not in dataset_columns:
            connection.execute(text("ALTER TABLE datasets ADD COLUMN file_hash VARCHAR(64)"))
        if "capture_time" not in dataset_columns:
            connection.execute(text("ALTER TABLE datasets ADD COLUMN capture_time DATETIME"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_datasets_capture_time ON datasets(capture_time)"))
        additions = {
            "created_at": "DATETIME",
            "batch_id": "VARCHAR(36)",
            "retry_of_task_id": "VARCHAR(36)",
        }
        for name, sql_type in additions.items():
            if name not in task_columns:
                connection.execute(text(f"ALTER TABLE inference_tasks ADD COLUMN {name} {sql_type}"))
        # 结果概率统计字段（幂等，原位升级，保留历史数据）
        result_columns = {item["name"] for item in inspector.get_columns("inference_results")}
        result_additions = {
            "probability_min": "FLOAT",
            "probability_max": "FLOAT",
            "probability_mean": "FLOAT",
            "probability_median": "FLOAT",
            "high_confidence_flood_pixel_count": "INTEGER",
            "high_confidence_flood_ratio": "FLOAT",
        }
        for name, sql_type in result_additions.items():
            if name not in result_columns:
                connection.execute(
                    text(f"ALTER TABLE inference_results ADD COLUMN {name} {sql_type}")
                )
        connection.execute(text(
            "UPDATE inference_tasks SET created_at = COALESCE(started_at, CURRENT_TIMESTAMP) "
            "WHERE created_at IS NULL"
        ))
        connection.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_datasets_file_hash "
            "ON datasets(file_hash) WHERE file_hash IS NOT NULL"
        ))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_inference_tasks_batch_id ON inference_tasks(batch_id)"
        ))
        # change_analyses 由 metadata.create_all 创建；这里显式补齐查询索引，
        # 使从早期预览版本升级时也保持幂等。
        if inspector.has_table("change_analyses"):
            for sql in (
                "CREATE INDEX IF NOT EXISTS ix_change_analyses_created_at ON change_analyses(created_at)",
                "CREATE INDEX IF NOT EXISTS ix_change_analyses_status ON change_analyses(status)",
                "CREATE INDEX IF NOT EXISTS ix_change_analyses_task_t1_id ON change_analyses(task_t1_id)",
                "CREATE INDEX IF NOT EXISTS ix_change_analyses_task_t2_id ON change_analyses(task_t2_id)",
                "CREATE INDEX IF NOT EXISTS ix_change_analyses_result_t1_id ON change_analyses(result_t1_id)",
                "CREATE INDEX IF NOT EXISTS ix_change_analyses_result_t2_id ON change_analyses(result_t2_id)",
            ):
                connection.execute(text(sql))
        # 第四阶段可空项目关联：旧记录无需补齐即可继续读取。
        for table_name in ("datasets", "inference_tasks", "change_analyses", "risk_assessments"):
            if inspector.has_table(table_name):
                columns = {item["name"] for item in inspector.get_columns(table_name)}
                if "project_id" not in columns:
                    connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN project_id VARCHAR(36)"))
                connection.execute(text(
                    f"CREATE INDEX IF NOT EXISTS ix_{table_name}_project_id ON {table_name}(project_id)"
                ))
        if inspector.has_table("risk_assessments"):
            for sql in (
                "CREATE INDEX IF NOT EXISTS ix_risk_assessments_created_at ON risk_assessments(created_at)",
                "CREATE INDEX IF NOT EXISTS ix_risk_assessments_status ON risk_assessments(status)",
                "CREATE INDEX IF NOT EXISTS ix_risk_assessments_source_result_id ON risk_assessments(source_result_id)",
                "CREATE INDEX IF NOT EXISTS ix_risk_assessments_source_change_analysis_id ON risk_assessments(source_change_analysis_id)",
            ):
                connection.execute(text(sql))

        rows = connection.execute(text("SELECT id, path FROM datasets WHERE file_hash IS NULL")).all()
        known = set(connection.execute(text(
            "SELECT file_hash FROM datasets WHERE file_hash IS NOT NULL"
        )).scalars())
        for dataset_id, raw_path in rows:
            path = Path(raw_path)
            if not path.is_file():
                continue
            digest = hashlib.sha256()
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            value = digest.hexdigest()
            if value in known:
                continue
            connection.execute(
                text("UPDATE datasets SET file_hash=:value WHERE id=:dataset_id"),
                {"value": value, "dataset_id": dataset_id},
            )
            known.add(value)
