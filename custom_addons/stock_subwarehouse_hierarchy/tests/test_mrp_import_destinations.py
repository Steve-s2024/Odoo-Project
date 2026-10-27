import csv
from io import StringIO

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "mrp_destination_import")
class TestMrpImportDestinations(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, shop_api_skip_event=True, tracking_disable=True))
        cls.warehouse = cls.env["stock.warehouse"].create({"name": "MRP Import Test", "code": "MIT"})
        cls.product = cls.env["product.product"].create({"name": "MRP import product", "is_storable": True})
        cls.context = {
            "default_picking_type_id": cls.warehouse.manu_type_id.id,
            "default_location_dest_id": cls.warehouse.lot_stock_id.id,
        }
        cls.import_fields = ["product_id/.id", "product_qty", "product_uom_id/.id", "location_dest_id"]

    def rows(self, *destinations):
        return [[str(self.product.id), "1", str(self.product.uom_id.id), destination] for destination in destinations]

    def importer(self, rows, fields=None):
        fields = fields or self.import_fields
        text = StringIO()
        writer = csv.writer(text)
        writer.writerow(fields)
        writer.writerows(rows)
        return self.env["base_import.import"].with_context(**self.context).create({
            "res_model": "mrp.production", "file": text.getvalue().encode(),
            "file_name": "manufacturing.csv", "file_type": "text/csv",
        })

    def options(self, **kwargs):
        return {
            "advanced": True, "has_headers": True, "skip": 0, "limit": 100,
            "encoding": "utf-8", "separator": ",", "quoting": '"',
            "date_format": "", "datetime_format": "",
            "float_thousand_separator": ",", "float_decimal_separator": ".",
            "name_create_enabled_fields": {}, **kwargs,
        }

    def run_import(self, importer, *, fields=None, dryrun=False, **options):
        fields = fields or self.import_fields
        return importer.execute_import(fields, fields, self.options(**options), dryrun=dryrun)

    def approve(self, preview):
        return {"mrp_destination_decision": "create", "mrp_destination_keys": [
            item["key"] for item in preview["x_mrp_missing_destinations"]
        ]}

    def test_omitted_name_uses_sequence_and_result_names(self):
        importer = self.importer(self.rows(self.warehouse.lot_stock_id.complete_name, self.warehouse.lot_stock_id.complete_name))
        result = self.run_import(importer)
        self.assertFalse(result["messages"], result)
        orders = self.env["mrp.production"].browse(result["ids"])
        self.assertEqual(len(orders), 2)
        self.assertEqual(len(set(orders.mapped("name"))), 2)
        self.assertTrue(all(name and name != "New" for name in orders.mapped("name")))
        lines = self.env["stock.subwarehouse.business.import.result.line"].search([
            ("batch_id.import_job_id", "=", importer.id),
        ])
        self.assertEqual(set(lines.mapped("identifier")), set(orders.mapped("name")))
        self.assertEqual(set(lines.mapped("source_row")), {2, 3})

    def test_preview_scans_all_batches_without_writes(self):
        importer = self.importer(self.rows("New A", "New B", "New A", "New C"))
        counts = [self.env[m].search_count([]) for m in ("stock.location", "mrp.production")]
        result = self.run_import(importer, limit=1)
        self.assertEqual(len(result["x_mrp_missing_destinations"]), 3)
        self.assertEqual(result["x_mrp_missing_destinations"][0]["rows"], [2, 4])
        self.assertEqual(counts, [self.env[m].search_count([]) for m in ("stock.location", "mrp.production")])
        self.assertFalse(self.env["stock.subwarehouse.business.import.result.batch"].search([
            ("import_job_id", "=", importer.id),
        ]))

    def test_create_all_destinations_once_even_beyond_current_batch(self):
        importer = self.importer(self.rows("New A", "New B", "New A"))
        approved = self.approve(self.run_import(importer, limit=1))
        first = self.run_import(importer, limit=1, **approved)
        self.assertFalse(first["messages"], first)
        self.assertEqual(first["nextrow"], 1)
        locations = self.env["stock.location"].search([
            ("location_id", "=", self.warehouse.lot_stock_id.id), ("name", "in", ["New A", "New B"]),
        ])
        self.assertEqual(len(locations), 2)
        second = self.run_import(importer, skip=1, limit=1, **approved)
        third = self.run_import(importer, skip=2, limit=1, **approved)
        self.assertFalse(second["messages"], second)
        self.assertFalse(third["messages"], third)
        orders = self.env["mrp.production"].browse(first["ids"] + second["ids"] + third["ids"])
        self.assertEqual(len(orders), 3)
        self.assertEqual(orders[0].location_dest_id, orders[2].location_dest_id)

    def test_skip_missing_rows_preserves_valid_rows_and_row_numbers(self):
        importer = self.importer(self.rows("Missing A", self.warehouse.lot_stock_id.complete_name, "Missing B"))
        result = self.run_import(importer, mrp_destination_decision="skip")
        self.assertFalse(result["messages"], result)
        self.assertEqual(len(result["ids"]), 1)
        self.assertEqual({r["source_row"] for r in result["x_business_import_failures"]}, {2, 4})
        lines = self.env["stock.subwarehouse.business.import.result.line"].search([
            ("batch_id.import_job_id", "=", importer.id),
        ])
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines.filtered(lambda line: line.status == "success").source_row, 3)

    def test_dryrun_create_rolls_back_locations_orders_and_results(self):
        importer = self.importer(self.rows("Dryrun A", "Dryrun B"))
        approved = self.approve(self.run_import(importer))
        counts = [self.env[m].search_count([]) for m in ("stock.location", "mrp.production", "stock.subwarehouse.business.import.result.batch")]
        result = self.run_import(importer, dryrun=True, **approved)
        self.assertFalse(result["messages"], result)
        self.assertEqual(len(result["ids"]), 2)
        self.assertEqual(counts, [self.env[m].search_count([]) for m in ("stock.location", "mrp.production", "stock.subwarehouse.business.import.result.batch")])
        actual = self.run_import(importer, **approved)
        self.assertFalse(actual["messages"], actual)
        self.assertEqual(len(actual["ids"]), 2)

    def test_full_paths_reuse_ancestors_and_shared_new_nodes(self):
        prefix = self.warehouse.lot_stock_id.complete_name
        importer = self.importer(self.rows(prefix + "/区域甲/货架一", prefix + "/区域甲/货架二"))
        result = self.run_import(importer, **self.approve(self.run_import(importer)))
        self.assertFalse(result["messages"], result)
        destinations = self.env["mrp.production"].browse(result["ids"]).location_dest_id
        self.assertEqual(len(destinations), 2)
        self.assertEqual(len(destinations.location_id), 1)
        self.assertEqual(destinations.location_id.location_id, self.warehouse.lot_stock_id)

    def test_unchosen_new_names_require_new_confirmation(self):
        importer = self.importer(self.rows("Must Confirm"))
        result = self.run_import(importer, mrp_destination_decision="create", mrp_destination_keys=["unrelated"])
        self.assertIn("x_mrp_missing_destinations", result)
        self.assertFalse(result["ids"])

    def test_all_rejected_rows_produce_result_view(self):
        importer = self.importer(self.rows("Missing A", "Missing B"))
        result = self.run_import(importer, mrp_destination_decision="skip")
        self.assertFalse(result["messages"])
        self.assertEqual(result["ids"], [])
        self.assertEqual(len(result["x_business_import_failures"]), 2)
        self.assertTrue(result["x_business_import_result_action"]["x_open_when_complete"])

    def test_missing_database_id_is_skipped_never_created(self):
        fields = [*self.import_fields[:-1], "location_dest_id/.id"]
        importer = self.importer(self.rows("999999999", str(self.warehouse.lot_stock_id.id)), fields)
        result = self.run_import(importer, fields=fields)
        self.assertFalse(result["messages"], result)
        self.assertEqual(len(result["ids"]), 1)
        self.assertEqual(len(result["x_business_import_failures"]), 1)

    def test_inactive_ambiguous_and_non_internal_destinations_are_skipped(self):
        self.env["stock.location"].create([
            {"name": "Duplicate", "location_id": self.warehouse.lot_stock_id.id, "usage": "internal"},
            {"name": "Duplicate", "location_id": self.warehouse.lot_stock_id.id, "usage": "internal"},
            {"name": "Archived", "location_id": self.warehouse.lot_stock_id.id, "usage": "internal", "active": False},
        ])
        importer = self.importer(self.rows("Duplicate", "Archived", self.warehouse.view_location_id.complete_name, self.warehouse.lot_stock_id.complete_name))
        result = self.run_import(importer, mrp_destination_decision="create")
        self.assertFalse(result["messages"], result)
        self.assertEqual(len(result["ids"]), 1)
        self.assertEqual(len(result["x_business_import_failures"]), 3)

    def test_failed_import_rolls_back_new_locations(self):
        rows = self.rows("Never Persist")
        rows[0][0] = "999999999"
        importer = self.importer(rows)
        count = self.env["stock.location"].search_count([])
        result = self.run_import(importer, **self.approve(self.run_import(importer)))
        self.assertTrue(result["messages"], result)
        self.assertEqual(self.env["stock.location"].search_count([]), count)
        self.assertNotIn("x_business_import_result_action", result)

    def test_creation_respects_importer_access(self):
        user = self.env["res.users"].create({
            "name": "MRP Restricted Importer", "login": "mrp-restricted-importer",
            "group_ids": [Command.set([self.env.ref("base.group_user").id])],
        })
        plan = self.env["mrp.production"].with_context(**self.context)._inspect_mrp_import_destinations(self.import_fields, self.rows("Restricted"))
        with self.assertRaises(AccessError):
            self.env["mrp.production"].with_user(user)._create_mrp_import_destinations(plan["missing"])

    def test_existing_and_missing_external_location_ids(self):
        self.env["ir.model.data"].create({
            "module": "__import__", "name": "mrp_test_destination", "model": "stock.location",
            "res_id": self.warehouse.lot_stock_id.id,
        })
        fields = [*self.import_fields[:-1], "location_dest_id/id"]
        importer = self.importer(self.rows("mrp_test_destination", "unknown.missing_destination"), fields)
        result = self.run_import(importer, fields=fields)
        self.assertFalse(result["messages"], result)
        self.assertEqual(len(result["ids"]), 1)
        self.assertEqual(len(result["x_business_import_failures"]), 1)

    def test_company_and_operation_external_ids_keep_location_in_correct_company(self):
        company = self.env["res.company"].create({"name": "MRP Second Company"})
        warehouse = self.env["stock.warehouse"].search([("company_id", "=", company.id)], limit=1)
        self.env["ir.model.data"].create([
            {"module": "__import__", "name": "mrp_second_company", "model": "res.company", "res_id": company.id},
            {"module": "__import__", "name": "mrp_second_operation", "model": "stock.picking.type", "res_id": warehouse.manu_type_id.id},
        ])
        fields = [*self.import_fields, "company_id/id", "picking_type_id/id"]
        rows = [[*self.rows("Company Location")[0], "mrp_second_company", "mrp_second_operation"]]
        model = self.env["mrp.production"].with_context(allowed_company_ids=[self.env.company.id, company.id])
        plan = model._inspect_mrp_import_destinations(fields, rows)
        self.assertEqual(len(plan["missing"]), 1)
        self.assertEqual(plan["missing"][0]["company_id"], company.id)
        self.assertEqual(plan["missing"][0]["parent_id"], warehouse.lot_stock_id.id)
        model._create_mrp_import_destinations(plan["missing"])
        resolved = model._inspect_mrp_import_destinations(fields, rows)
        self.assertFalse(resolved["missing"])
        self.assertEqual(self.env["stock.location"].browse(resolved["resolved"][0]).company_id, company)

    def test_creation_reuses_location_created_after_preview(self):
        model = self.env["mrp.production"].with_context(**self.context)
        plan = model._inspect_mrp_import_destinations(self.import_fields, self.rows("Concurrent Name"))
        model._create_mrp_import_destinations(plan["missing"])
        count = self.env["stock.location"].search_count([])
        model._create_mrp_import_destinations(plan["missing"])
        self.assertEqual(self.env["stock.location"].search_count([]), count)

    def test_named_group_kept_together_at_batch_boundary(self):
        fields = ["name", *self.import_fields]
        rows = [["GROUP-MO-1", *self.rows(self.warehouse.lot_stock_id.complete_name)[0]],
                ["", str(self.product.id), "2", str(self.product.uom_id.id), ""],
                ["GROUP-MO-2", *self.rows(self.warehouse.lot_stock_id.complete_name)[0]]]
        importer = self.importer(rows, fields)
        first = self.run_import(importer, fields=fields, limit=1)
        self.assertFalse(first["messages"], first)
        self.assertEqual(first["nextrow"], 2)
        self.assertEqual(len(first["ids"]), 1)
        second = self.run_import(importer, fields=fields, skip=2, limit=1)
        self.assertFalse(second["messages"], second)
        self.assertEqual(len(second["ids"]), 1)
