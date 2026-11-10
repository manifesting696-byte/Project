"""Semantic catalog reads and administrator table enrollment."""

from fastapi import APIRouter, Depends

from enterprise_agent.server import deps
from enterprise_agent.server.auth import current_user, require
from enterprise_agent.server.schemas import CatalogRefresh, TableEnrollment

router = APIRouter()


@router.get("/catalog")
def catalog(user=Depends(current_user)):
    registry = deps.application().registry
    return {**registry.catalog, "published_metrics": registry.published("metrics")}


@router.get("/tables")
def tables(user=Depends(current_user)):
    registry = deps.application().registry
    return {
        "snapshot_id": registry.snapshot_id,
        "catalog_version": registry.catalog_version,
        "total_rows": sum(entry["rows"] for entry in registry.tables.values()),
        "tables": [
            {
                "name": name,
                "rows": entry["rows"],
                "columns": [
                    {"name": col, **definition}
                    for col, definition in entry["columns"].items()
                ],
            }
            for name, entry in registry.tables.items()
        ],
    }


@router.get("/catalog/tables/available")
def available_tables(user=Depends(require("admin"))):
    return deps.application().available_tables()


@router.post("/catalog/tables")
def enroll_table(body: TableEnrollment, user=Depends(require("admin"))):
    options = body.model_dump(exclude={"relations"})
    options["relations"] = (
        [relation.model_dump(by_alias=True) for relation in body.relations]
        if body.relations is not None
        else None
    )
    return deps.application().change_tables("enroll", **options)


@router.delete("/catalog/tables/{table_name}")
def remove_table(
    table_name: str, expected_version: str | None = None, user=Depends(require("admin"))
):
    return deps.application().change_tables(
        "remove", name=table_name, expected_version=expected_version
    )


@router.post("/catalog/tables/refresh")
def refresh_tables(body: CatalogRefresh, user=Depends(require("admin"))):
    return deps.application().change_tables(
        "refresh", expected_version=body.expected_version
    )
