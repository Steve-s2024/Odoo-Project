from odoo import _, api, models


class BaseImport(models.TransientModel):
    _inherit = "base_import.import"

    _business_duplicate_models = {"sale.order", "stock.quant", "mrp.production"}

    @api.model
    def get_fields_tree(self, model, depth=3):
        fields_tree = super().get_fields_tree(model, depth=depth)
        # mrp.production.name is readonly because normal manufacturing orders
        # receive a sequence automatically.  The ORM still accepts an explicit
        # name during import, and our duplicate guard requires that identifier.
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
                "required": True,
                "fields": [],
                "type": "char",
                "model_name": model,
            })
        return fields_tree

    def execute_import(self, fields, columns, options, dryrun=False):
        self.ensure_one()
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

    def _complete_business_import_action(self, result, batch, dryrun):
        if batch and not dryrun:
            result["x_business_import_result_action"] = batch.action_open_lines()
            result["x_business_import_result_action"]["x_open_when_complete"] = not bool(
                result.get("nextrow")
            )
        return result
