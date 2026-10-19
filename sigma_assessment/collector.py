"""Collect and flatten Sigma inventory and dependency data."""

from __future__ import annotations

import csv
import json
import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .client import SigmaAPIError, SigmaClient

LOG = logging.getLogger(__name__)

WORKBOOK_FIELDS = ["workbook_id", "name", "path", "url", "owner_id", "created_by", "updated_by", "created_at", "updated_at", "latest_version", "is_archived"]
MODEL_FIELDS = ["data_model_id", "name", "path", "url", "owner_id", "created_by", "updated_by", "created_at", "updated_at", "latest_version", "is_archived"]
RELATION_FIELDS = ["workbook_id", "workbook_name", "workbook_path", "data_model_id", "data_model_name", "data_model_path", "data_model_element_id", "version_tag_id"]
SOURCE_FIELDS = ["consumer_kind", "consumer_id", "consumer_name", "source_type", "source_id", "source_name", "connection_id", "source_path", "element_ids", "version_tag_id", "source_json"]
ELEMENT_FIELDS = ["workbook_id", "workbook_name", "element_id", "element_name", "element_type", "visualization_type", "error"]
DEPENDENCY_FIELDS = ["workbook_id", "workbook_name", "requested_element_id", "source_node_id", "source_element_id", "source_name", "source_type", "target_node_id", "target_element_id", "target_name", "target_type", "edge_type"]
ERROR_FIELDS = ["scope", "object_id", "object_name", "endpoint", "http_status", "message"]


def _value(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in item and item[key] is not None:
            return item[key]
    return ""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass
class Assessment:
    workbooks: list[dict[str, Any]] = field(default_factory=list)
    data_models: list[dict[str, Any]] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    elements: list[dict[str, Any]] = field(default_factory=list)
    dependencies: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)


class Collector:
    def __init__(self, client: SigmaClient, workers: int = 8, skip_permission_check: bool = False):
        self.client = client
        self.workers = max(1, workers)
        self.skip_permission_check = skip_permission_check

    def collect(self, include_lineage: bool = True) -> Assessment:
        result = Assessment()
        workbook_raw = list(self.client.paginate("/v2/workbooks", params={"excludeTags": True, "skipPermissionCheck": self.skip_permission_check}))
        model_raw = list(self.client.paginate("/v2/dataModels", params={"excludeTags": True, "skipPermissionCheck": self.skip_permission_check}))
        result.workbooks = [self._workbook_row(x) for x in workbook_raw]
        result.data_models = [self._model_row(x) for x in model_raw]
        model_index = {row["data_model_id"]: row for row in result.data_models}

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(self._collect_workbook, wb, model_index, include_lineage): wb for wb in workbook_raw}
            futures.update({pool.submit(self._collect_model_sources, dm): dm for dm in model_raw})
            for future in as_completed(futures):
                obj = futures[future]
                try:
                    partial = future.result()
                except Exception as exc:  # keep a large assessment running when one document fails
                    kind = "workbook" if "workbookId" in obj else "data_model"
                    object_id = _value(obj, "workbookId", "dataModelId")
                    result.errors.append(self._error(kind, object_id, _value(obj, "name"), "document collection", exc))
                    LOG.warning("Unable to assess %s %s: %s", kind, object_id, exc)
                    continue
                result.relations.extend(partial.relations)
                result.sources.extend(partial.sources)
                result.elements.extend(partial.elements)
                result.dependencies.extend(partial.dependencies)
                result.errors.extend(partial.errors)

        result.relations.sort(key=lambda x: (x["workbook_name"], x["data_model_name"], x["data_model_element_id"]))
        result.sources.sort(key=lambda x: (x["consumer_kind"], x["consumer_name"], x["source_type"], x["source_id"]))
        result.elements.sort(key=lambda x: (x["workbook_name"], x["element_name"], x["element_id"]))
        result.dependencies.sort(key=lambda x: (x["workbook_name"], x["requested_element_id"], x["source_node_id"], x["target_node_id"]))
        return result

    def _collect_workbook(self, workbook: dict[str, Any], model_index: dict[str, dict[str, Any]], include_lineage: bool) -> Assessment:
        out = Assessment()
        wid, wname = workbook["workbookId"], _value(workbook, "name")
        try:
            payload = self.client.request(f"/v2/workbooks/{wid}/sources")
            raw_sources = payload if isinstance(payload, list) else payload.get("entries", [])
            for source in raw_sources:
                out.sources.append(self._source_row("workbook", wid, wname, source))
                if source.get("type") == "data-model" and source.get("dataModelId"):
                    model = model_index.get(source["dataModelId"], {})
                    element_ids = source.get("elementIds") or [""]
                    for element_id in element_ids:
                        out.relations.append({
                            "workbook_id": wid, "workbook_name": wname, "workbook_path": _value(workbook, "path"),
                            "data_model_id": source["dataModelId"], "data_model_name": _value(model, "name"),
                            "data_model_path": _value(model, "path"), "data_model_element_id": element_id,
                            "version_tag_id": _value(source, "versionTagId"),
                        })
        except Exception as exc:
            out.errors.append(self._error("workbook", wid, wname, f"/v2/workbooks/{wid}/sources", exc))

        if not include_lineage:
            return out
        try:
            elements = list(self.client.paginate(f"/v2/workbooks/{wid}/elements"))
        except Exception as exc:
            out.errors.append(self._error("workbook", wid, wname, f"/v2/workbooks/{wid}/elements", exc))
            return out
        for element in elements:
            eid = _value(element, "elementId")
            out.elements.append({
                "workbook_id": wid, "workbook_name": wname, "element_id": eid,
                "element_name": _value(element, "name"), "element_type": _value(element, "type"),
                "visualization_type": _value(element, "vizualizationType", "visualizationType"), "error": _value(element, "error"),
            })
            if not eid:
                continue
            try:
                lineage = self.client.request(f"/v2/workbooks/{wid}/lineage/elements/{eid}")
                nodes = lineage.get("dependencies", {})
                for edge in lineage.get("edges", []):
                    source_node = nodes.get(edge.get("source"), {})
                    target_node = nodes.get(edge.get("target"), {})
                    out.dependencies.append({
                        "workbook_id": wid, "workbook_name": wname, "requested_element_id": eid,
                        "source_node_id": _value(source_node, "nodeId") or _value(edge, "source"),
                        "source_element_id": _value(source_node, "elementId"), "source_name": _value(source_node, "name"), "source_type": _value(source_node, "type"),
                        "target_node_id": _value(target_node, "nodeId") or _value(edge, "target"),
                        "target_element_id": _value(target_node, "elementId"), "target_name": _value(target_node, "name"), "target_type": _value(target_node, "type"),
                        "edge_type": _value(edge, "type"),
                    })
            except SigmaAPIError as exc:
                # Sigma only supports lineage for data elements; UI/control elements commonly return 400.
                if exc.status != 400:
                    out.errors.append(self._error("element", eid, _value(element, "name"), f"/v2/workbooks/{wid}/lineage/elements/{eid}", exc))
            except Exception as exc:
                out.errors.append(self._error("element", eid, _value(element, "name"), f"/v2/workbooks/{wid}/lineage/elements/{eid}", exc))
        return out

    def _collect_model_sources(self, model: dict[str, Any]) -> Assessment:
        out = Assessment()
        mid, name = model["dataModelId"], _value(model, "name")
        path = f"/v2/dataModels/{mid}/sources"
        try:
            for source in self.client.paginate(path, page_param="pageToken", next_field="nextPageToken", limit_param="pageSize", limit=500):
                out.sources.append(self._source_row("data_model", mid, name, source))
        except Exception as exc:
            out.errors.append(self._error("data_model", mid, name, path, exc))
        return out

    @staticmethod
    def _workbook_row(x: dict[str, Any]) -> dict[str, Any]:
        return {"workbook_id": _value(x, "workbookId"), "name": _value(x, "name"), "path": _value(x, "path"), "url": _value(x, "url"), "owner_id": _value(x, "ownerId"), "created_by": _value(x, "createdBy"), "updated_by": _value(x, "updatedBy"), "created_at": _value(x, "createdAt"), "updated_at": _value(x, "updatedAt"), "latest_version": _value(x, "latestVersion"), "is_archived": _value(x, "isArchived")}

    @staticmethod
    def _model_row(x: dict[str, Any]) -> dict[str, Any]:
        return {"data_model_id": _value(x, "dataModelId"), "name": _value(x, "name"), "path": _value(x, "path"), "url": _value(x, "url"), "owner_id": _value(x, "ownerId"), "created_by": _value(x, "createdBy"), "updated_by": _value(x, "updatedBy"), "created_at": _value(x, "createdAt"), "updated_at": _value(x, "updatedAt"), "latest_version": _value(x, "latestVersion"), "is_archived": _value(x, "isArchived")}

    @staticmethod
    def _source_row(kind: str, consumer_id: str, consumer_name: str, source: dict[str, Any]) -> dict[str, Any]:
        source_id = _value(source, "dataModelId", "datasetId", "tableId", "elementId", "id")
        source_path = _value(source, "path", "tablePath", "url")
        return {
            "consumer_kind": kind, "consumer_id": consumer_id, "consumer_name": consumer_name,
            "source_type": _value(source, "type"), "source_id": source_id, "source_name": _value(source, "name"),
            "connection_id": _value(source, "connectionId"), "source_path": source_path,
            "element_ids": _json(source.get("elementIds", [])), "version_tag_id": _value(source, "versionTagId"),
            "source_json": _json(source),
        }

    @staticmethod
    def _error(scope: str, object_id: str, name: str, endpoint: str, exc: Exception) -> dict[str, Any]:
        return {"scope": scope, "object_id": object_id, "object_name": name, "endpoint": endpoint, "http_status": getattr(exc, "status", ""), "message": str(exc)}


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_assessment(result: Assessment, output_dir: Path) -> None:
    write_csv(output_dir / "workbooks.csv", result.workbooks, WORKBOOK_FIELDS)
    write_csv(output_dir / "data_models.csv", result.data_models, MODEL_FIELDS)
    write_csv(output_dir / "workbook_data_model_relations.csv", result.relations, RELATION_FIELDS)
    write_csv(output_dir / "source_assets.csv", result.sources, SOURCE_FIELDS)
    write_csv(output_dir / "workbook_elements.csv", result.elements, ELEMENT_FIELDS)
    write_csv(output_dir / "dependency_edges.csv", result.dependencies, DEPENDENCY_FIELDS)
    write_csv(output_dir / "collection_errors.csv", result.errors, ERROR_FIELDS)

    models_by_workbook = Counter(row["workbook_id"] for row in result.relations)
    elements_by_workbook = Counter(row["workbook_id"] for row in result.elements)
    edges_by_workbook = Counter(row["workbook_id"] for row in result.dependencies)
    source_types: dict[str, Counter[str]] = {}
    for row in result.sources:
        source_types.setdefault(row["consumer_id"], Counter())[row["source_type"] or "unknown"] += 1
    summary = []
    for wb in result.workbooks:
        wid = wb["workbook_id"]
        summary.append({
            "workbook_id": wid, "workbook_name": wb["name"], "workbook_path": wb["path"],
            "data_model_relation_count": models_by_workbook[wid], "element_count": elements_by_workbook[wid],
            "dependency_edge_count": edges_by_workbook[wid], "source_type_counts": _json(source_types.get(wid, {})),
        })
    write_csv(output_dir / "workbook_assessment.csv", summary, ["workbook_id", "workbook_name", "workbook_path", "data_model_relation_count", "element_count", "dependency_edge_count", "source_type_counts"])

