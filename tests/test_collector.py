import csv
import tempfile
import unittest
from pathlib import Path

from sigma_assessment.collector import Collector, write_assessment


class FakeClient:
    def paginate(self, path, **kwargs):
        data = {
            "/v2/workbooks": [{"workbookId": "w1", "name": "Revenue", "path": "/Finance"}],
            "/v2/dataModels": [{"dataModelId": "dm1", "name": "Orders", "path": "/Models"}],
            "/v2/workbooks/w1/elements": [{"elementId": "e1", "name": "Sales", "type": "table"}],
            "/v2/dataModels/dm1/sources": [{"type": "table", "connectionId": "c1", "path": "DB.SCHEMA.ORDERS"}],
        }
        yield from data[path]

    def request(self, path, params=None):
        if path == "/v2/workbooks/w1/sources":
            return [{"type": "data-model", "dataModelId": "dm1", "elementIds": ["dme1"]}]
        if path == "/v2/workbooks/w1/lineage/elements/e1":
            return {
                "dependencies": {
                    "n1": {"nodeId": "n1", "name": "Orders", "type": "table"},
                    "n2": {"nodeId": "n2", "elementId": "e1", "name": "Sales", "type": "sheet"},
                },
                "edges": [{"source": "n1", "target": "n2", "type": "source"}],
            }
        raise AssertionError(path)


class CollectorTests(unittest.TestCase):
    def test_collects_relation_sources_and_edges(self):
        result = Collector(FakeClient(), workers=2).collect()
        self.assertEqual(result.relations[0]["data_model_name"], "Orders")
        self.assertEqual(result.relations[0]["data_model_element_id"], "dme1")
        self.assertEqual(result.dependencies[0]["source_name"], "Orders")
        self.assertEqual({row["consumer_kind"] for row in result.sources}, {"workbook", "data_model"})

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            write_assessment(result, output)
            with (output / "workbook_assessment.csv").open(newline="") as handle:
                summary = list(csv.DictReader(handle))
        self.assertEqual(summary[0]["data_model_relation_count"], "1")
        self.assertEqual(summary[0]["dependency_edge_count"], "1")

    def test_no_lineage_skips_elements(self):
        result = Collector(FakeClient(), workers=1).collect(include_lineage=False)
        self.assertFalse(result.elements)
        self.assertFalse(result.dependencies)
        self.assertEqual(len(result.relations), 1)


if __name__ == "__main__":
    unittest.main()
