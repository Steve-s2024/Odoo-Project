from odoo import _, api, models
from odoo.addons.base_import.models.base_import import ImportValidationError


class BaseImport(models.TransientModel):
    _inherit = "base_import.import"

    _business_duplicate_models = {"sale.order", "stock.quant", "mrp.production"}

    @api.model
    def get_fields_tree(self, model, depth=3):
        fields_tree = super().get_fields_tree(model, depth=depth)
        # mrp.production.name is readonly because normal manufacturing orders
        # receive a sequence automatically.  The ORM still accepts an explicit
        # name during import. An omitted column uses the normal MO sequence.
        # Expose only this one readonly field on the root manufacturing import
        # so both `name` and `制造单号` headers are suggested automatically.
        if (
            self.res_model == "mrp.production"
            and model == "mrp.production"
            and not any(field.get("name") == "name" for field in fields_tree)
        ):
            fields_tree.insert(1, {
                "id": "name",
                "name": "name",
                "string": _("制造单号"),
                "required": False,
                "fields": [],
                "type": "char",
                "model_name": model,
            })
        if self.res_model == model == "mrp.production":
            for field in fields_tree:
                if field.get("name") == "name":
                    field["required"] = False
        return fields_tree

    def execute_import(self, fields, columns, options, dryrun=False):
        self.ensure_one()
        # Inspect the entire remaining file before importing the first batch.
        # The normal importer only handles a window at a time.
        if self.res_model == "mrp.production" and not self.env.context.get("mrp_destinations_prepared"):
            return self._execute_mrp_destination_import(fields, columns, options, dryrun)
        business_batch = self.env["stock.subwarehouse.business.import.result.batch"]
        if self.res_model in self._business_duplicate_models:
            if not dryrun:
                if not options.get("skip"):
                    business_batch.search([
                        ("import_job_id", "=", self.id),
                        ("user_id", "=", self.env.user.id),
                        ("res_model", "=", self.res_model),
                    ]).unlink()
                    business_batch = business_batch.create({
                        "import_job_id": self.id,
                        "res_model": self.res_model,
                    })
                else:
                    business_batch = business_batch.search([
                        ("import_job_id", "=", self.id),
                        ("user_id", "=", self.env.user.id),
                        ("res_model", "=", self.res_model),
                    ], order="id desc", limit=1)
                    if not business_batch:
                        business_batch = business_batch.create({
                            "import_job_id": self.id,
                            "res_model": self.res_model,
                        })
            self = self.with_context(
                business_import_result_batch_id=business_batch.id,
                business_import_source_offset=options.get("skip", 0),
                business_import_has_headers=options.get("has_headers", False),
            )

        if self.res_model == "sale.order":
            self = self.with_context(
                sale_import_source_offset=options.get("skip", 0),
                sale_import_has_headers=options.get("has_headers", False),
            )
            result = super().execute_import(fields, columns, options, dryrun=dryrun)
            return self._complete_business_import_action(result, business_batch, dryrun)

        if self.res_model in {"stock.quant", "mrp.production"}:
            result = super().execute_import(fields, columns, options, dryrun=dryrun)
            return self._complete_business_import_action(result, business_batch, dryrun)

        if self.res_model != "product.template":
            return super().execute_import(fields, columns, options, dryrun=dryrun)

        batch = self.env["stock.subwarehouse.product.import.result.batch"]
        if not dryrun:
            if not options.get("skip"):
                batch.search([
                    ("import_job_id", "=", self.id),
                    ("user_id", "=", self.env.user.id),
                ]).unlink()
                batch = batch.create({"import_job_id": self.id})
            else:
                batch = batch.search([
                    ("import_job_id", "=", self.id),
                    ("user_id", "=", self.env.user.id),
                ], order="id desc", limit=1)
                if not batch:
                    batch = batch.create({"import_job_id": self.id})

        self = self.with_context(
            product_import_result_batch_id=batch.id,
            product_import_source_offset=options.get("skip", 0),
            product_import_has_headers=options.get("has_headers", False),
        )
        result = super().execute_import(fields, columns, options, dryrun=dryrun)
        if batch and not dryrun:
            result["x_product_import_result_action"] = batch.action_open_lines()
            has_more_rows = bool(result.get("nextrow"))
            result["x_product_import_result_action"]["x_open_when_complete"] = not has_more_rows
        return result

    def _execute_mrp_destination_import(self, fields, columns, options, dryrun):
        try:
            data, import_fields = self._convert_import_data(fields, options)
        except ImportValidationError as error:
            return {"ids": [], "messages": [error.__dict__], "nextrow": 0}
        model = self.env["mrp.production"]
        plan = model._inspect_mrp_import_destinations(import_fields, data)
        decision = options.get("mrp_destination_decision")
        approved = set(options.get("mrp_destination_keys") or [])
        if plan["missing"] and (
            decision not in {"create", "skip"}
            or (decision == "create" and any(item["key"] not in approved for item in plan["missing"]))
        ):
            offset = options.get("skip", 0) + (2 if options.get("has_headers") else 1)
            return {
                "ids": [], "messages": [], "nextrow": 0,
                "x_mrp_missing_destinations": [{
                    **{key: item[key] for key in ("key", "name", "company", "parent")},
                    "rows": [offset + index for index in item["source_indexes"]],
                } for item in plan["missing"]],
            }
        # This savepoint includes warehouse creation, which precedes Odoo's
        # own import savepoint. Test imports and failed batches leave neither
        # locations nor result rows behind.
        savepoint = self.env.cr.savepoint()
        try:
            if decision == "create" and plan["missing"]:
                model._create_mrp_import_destinations(plan["missing"])
            result = self.with_context(mrp_destinations_prepared=True).execute_import(
                fields, columns, dict(options), dryrun=dryrun,
            )
            failed = any(message.get("type") in {"error", "danger"} for message in result.get("messages", []))
            savepoint.close(rollback=dryrun or failed)
            if failed:
                result.pop("x_business_import_result_action", None)
            return result
        except Exception:
            savepoint.close(rollback=True)
            raise

    def _complete_business_import_action(self, result, batch, dryrun):
        if batch and not dryrun:
            result["x_business_import_result_action"] = batch.action_open_lines()
            result["x_business_import_result_action"]["x_open_when_complete"] = not bool(
                result.get("nextrow")
            )
        return result
