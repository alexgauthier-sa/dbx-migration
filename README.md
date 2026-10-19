# Sigma workbook and data-model assessment

This project inventories Sigma workbooks and data models, identifies workbook-to-data-model relationships, and exports element lineage/dependencies as CSV files.

It uses current Sigma v2 endpoints rather than the deprecated workbook schema endpoint:

- `GET /v2/workbooks` and `GET /v2/dataModels`
- `GET /v2/workbooks/{workbookId}/sources`
- `GET /v2/dataModels/{dataModelId}/sources`
- `GET /v2/workbooks/{workbookId}/elements`
- `GET /v2/workbooks/{workbookId}/lineage/elements/{elementId}`

## Setup

Python 3.10 or newer is required. No runtime packages beyond the Python standard library are needed.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
export SIGMA_BASE_URL="https://api.sigmacomputing.com"
export SIGMA_CLIENT_ID="..."
export SIGMA_CLIENT_SECRET="..."
```

Use the API base URL shown under **Administration > Developer access** in Sigma; it varies by hosting region. As an alternative to client credentials, set `SIGMA_ACCESS_TOKEN` to an existing bearer token. Do not commit credentials or `.env` files.

The API credential's owner controls which documents the scripts can see. For a whole-organization assessment, use credentials owned by a Sigma admin and pass `--all-org-documents`.

## Run

Full inventory and element-level lineage:

```bash
sigma-assess --output-dir output
# or: python scripts/assess_sigma.py --output-dir output
```

Faster document/source inventory, without one lineage request per workbook element:

```bash
python scripts/export_inventory.py --output-dir output
```

Useful options:

```text
--workers N             concurrent document requests (default 8)
--no-lineage            omit element and dependency collection
--all-org-documents     admin-only organization-wide discovery
--timeout SECONDS       per-request timeout
--verbose               diagnostic logging
```

The client follows all pagination tokens, refreshes client-credential tokens, retries rate limits and transient server errors with backoff, and continues the assessment when an individual document cannot be read.

## Reports

| CSV | Contents |
| --- | --- |
| `workbook_assessment.csv` | One row per workbook with relationship, element, edge, and source-type counts |
| `workbook_data_model_relations.csv` | Workbook → data model → selected data-model element |
| `dependency_edges.csv` | Directed source/target edges returned by Sigma's element-lineage API |
| `source_assets.csv` | Workbook and data-model sources, including tables, custom SQL, legacy datasets, and data models |
| `workbook_elements.csv` | Element inventory used for lineage collection |
| `workbooks.csv` | Workbook inventory and ownership metadata |
| `data_models.csv` | Data-model inventory and ownership metadata |
| `collection_errors.csv` | Permission, endpoint, and collection failures that need follow-up |

`source_assets.csv` retains the complete source object in `source_json`, so new or tenant-specific Sigma source fields are not lost. HTTP 400 responses from lineage calls are omitted from `collection_errors.csv` because Sigma documents that controls and other non-data elements do not support lineage.

## Interpreting the results

- `workbook_data_model_relations.csv` is the primary workbook-to-data-model mapping.
- Use `dependency_edges.csv` for impact analysis within each workbook. An edge points from `source_node_id` to `target_node_id`.
- Use `source_assets.csv` to identify data-model-to-data-model dependencies and direct warehouse/custom-SQL sources.
- Review `collection_errors.csv` before treating counts as complete. Access is limited to what the API credential owner can view unless the admin-only option is used.

## Test

```bash
python -m unittest discover -s tests
```
