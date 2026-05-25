"""迁移/回滚v2路由"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from shisi.application.migration_service import MigrationService

from .schemas import MigrationResponse, MigrationStatusResponse, RollbackResponse

router = APIRouter(prefix="/v2/migration", tags=["v2-migration"])


def get_migration_service() -> MigrationService:
    return MigrationService()


@router.post("/execute", response_model=MigrationResponse)
async def execute_migration(service: MigrationService = Depends(get_migration_service)):  # noqa: B008
    result = service.execute()
    return MigrationResponse(
        total_migrated=result.total_migrated,
        total_failed=result.total_failed,
        errors=result.errors,
        active_character=result.active_character,
    )


@router.post("/rollback", response_model=RollbackResponse)
async def execute_rollback(service: MigrationService = Depends(get_migration_service)):  # noqa: B008
    result = service.rollback()
    return RollbackResponse(success=result.success, message=result.message)


@router.get("/status", response_model=MigrationStatusResponse)
async def get_migration_status(service: MigrationService = Depends(get_migration_service)):  # noqa: B008
    status = service.status()
    return MigrationStatusResponse(
        v2_table_exists=status.v2_table_exists,
        legacy_table_exists=status.legacy_table_exists,
        v2_record_count=status.v2_record_count,
        legacy_record_count=status.legacy_record_count,
    )
