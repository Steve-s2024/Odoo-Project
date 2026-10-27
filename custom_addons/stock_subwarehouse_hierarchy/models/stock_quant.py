from odoo import _, api, fields, models
from odoo.exceptions import UserError


class StockQuant(models.Model):
    _inherit = "stock.quant"

    @api.model
    def load(self, import_fields, import_data):
        filtered = self._filter_unique_quant_import_rows(import_fields, import_data)
        result = (
            super().load(filtered["fields"], filtered["data"])
            if filtered["data"]
            else {"ids": [], "messages": [], "nextrow": 0}
        )
        result["x_business_import_failures"] = filtered["failures"]
        if filtered["applied"]:
            result["nextrow"] = (
                filtered["source_window_size"]
                if filtered["has_more_source_rows"]
                else 0
            )
            self._record_quant_import_results(result, filtered)
        return result

    @api.model
    def _filter_unique_quant_import_rows(self, import_fields, import_data):
        result = {
            "fields": import_fields,
            "data": import_data,
            "kept_source_indexes": list(range(len(import_data))),
            "identifiers": [],
            "failures": [],
            "applied": False,
            "source_window_size": len(import_data),
            "has_more_source_rows": False,
        }
        if not self.env.context.get("import_file"):
            return result

        import_limit = self.env.context.get("_import_limit")
        window_size = min(len(import_data), import_limit) if import_limit else len(import_data)
        source_data = import_data[:window_size]
        result.update({
            "data": source_data,
            "kept_source_indexes": list(range(window_size)),
            "applied": True,
            "source_window_size": window_size,
            "has_more_source_rows": window_size < len(import_data),
        })
        field_indexes = {name: index for index, name in enumerate(import_fields)}
        product_field = next((name for name in (
            "product_id/default_code", "product_id/.id", "product_id/id", "product_id",
        ) if name in field_indexes), None)
        location_field = next((name for name in (
            "location_id/complete_name", "location_id/.id", "location_id/id", "location_id",
        ) if name in field_indexes), None)
        source_offset = self.env.context.get("business_import_source_offset", 0)
        header_offset = 2 if self.env.context.get("business_import_has_headers") else 1
        if not product_field or not location_field:
            missing = "产品" if not product_field else "库位"
            result["data"] = []
            result["kept_source_indexes"] = []
            result["failures"] = [{
                "source_row": source_offset + index + header_offset,
                "identifier": "",
                "reason": f"必须映射库存记录的{missing}字段。",
            } for index, _row in enumerate(source_data)]
            return result

        optional_fields = {
            key: next((name for name in candidates if name in field_indexes), None)
            for key, candidates in {
                "lot": ("lot_id/name", "lot_id/.id", "lot_id/id", "lot_id"),
                "package": ("package_id/name", "package_id/.id", "package_id/id", "package_id"),
                "owner": ("owner_id/name", "owner_id/.id", "owner_id/id", "owner_id"),
            }.items()
        }

        def normalized(row, field_name):
            return str(row[field_indexes[field_name]] or "").strip().casefold() if field_name else ""

        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            ["stock_subwarehouse_hierarchy.stock_quant_identity_import"],
        )
        self.flush_model(["product_id", "location_id", "lot_id", "package_id", "owner_id"])
        self.env.cr.execute("""
            SELECT lower(btrim(coalesce(pp.default_code, pt.name->>'en_US', ''))),
                   lower(btrim(coalesce(sl.complete_name, sl.name, ''))),
                   lower(btrim(coalesce(lot.name, ''))),
                   lower(btrim(coalesce(pkg.name, ''))),
                   lower(btrim(coalesce(owner.name, ''))),
                   q.product_id::text, q.location_id::text,
                   coalesce(q.lot_id, 0)::text,
                   coalesce(q.package_id, 0)::text,
                   coalesce(q.owner_id, 0)::text
              FROM stock_quant q
              JOIN product_product pp ON pp.id = q.product_id
              JOIN product_template pt ON pt.id = pp.product_tmpl_id
              JOIN stock_location sl ON sl.id = q.location_id
         LEFT JOIN stock_lot lot ON lot.id = q.lot_id
         LEFT JOIN stock_package pkg ON pkg.id = q.package_id
         LEFT JOIN res_partner owner ON owner.id = q.owner_id
        """)
        existing_rows = self.env.cr.fetchall()

        def existing_value(db_row, field_name, text_index, id_index):
            if field_name and field_name.endswith("/.id"):
                return db_row[id_index]
            return db_row[text_index]

        existing = {
            (
                existing_value(row, product_field, 0, 5),
                existing_value(row, location_field, 1, 6),
                existing_value(row, optional_fields["lot"], 2, 7) if optional_fields["lot"] else "",
                existing_value(row, optional_fields["package"], 3, 8) if optional_fields["package"] else "",
                existing_value(row, optional_fields["owner"], 4, 9) if optional_fields["owner"] else "",
            )
            for row in existing_rows
            if (optional_fields["lot"] or row[7] == "0")
            and (optional_fields["package"] or row[8] == "0")
            and (optional_fields["owner"] or row[9] == "0")
        }
        accepted = set()
        kept_rows = []
        kept_indexes = []
        identifiers = []
        failures = []
        for source_index, row in enumerate(source_data):
            product_value = normalized(row, product_field)
            location_value = normalized(row, location_field)
            key = (
                product_value,
                location_value,
                normalized(row, optional_fields["lot"]),
                normalized(row, optional_fields["package"]),
                normalized(row, optional_fields["owner"]),
            )
            display_identifier = " / ".join(filter(None, [
                str(row[field_indexes[product_field]] or "").strip(),
                str(row[field_indexes[location_field]] or "").strip(),
                *(str(row[field_indexes[field]] or "").strip() for field in optional_fields.values() if field),
            ]))
            if not product_value or not location_value:
                reason = "库存记录的产品和库位不能为空。"
            elif key in existing:
                reason = "相同产品、库位、批次、包装和所有者的库存记录已存在于 ERP。"
            elif key in accepted:
                reason = "相同库存记录在本次导入文件中重复。"
            else:
                accepted.add(key)
                kept_rows.append(row)
                kept_indexes.append(source_index)
                identifiers.append(display_identifier)
                continue
            failures.append({
                "source_row": source_offset + source_index + header_offset,
                "identifier": display_identifier,
                "reason": reason,
            })
        result.update({
            "data": kept_rows,
            "kept_source_indexes": kept_indexes,
            "identifiers": identifiers,
            "failures": failures,
        })
        return result

    @api.model
    def _record_quant_import_results(self, result, filtered):
        batch_id = self.env.context.get("business_import_result_batch_id")
        if not batch_id:
            return
        source_offset = self.env.context.get("business_import_source_offset", 0)
        header_offset = 2 if self.env.context.get("business_import_has_headers") else 1
        values = []
        for source_index, identifier, record_id in zip(
            filtered["kept_source_indexes"], filtered["identifiers"], result.get("ids") or [],
        ):
            values.append({
                "batch_id": batch_id,
                "source_row": source_offset + source_index + header_offset,
                "status": "success",
                "identifier": identifier,
                "record_ref": f"stock.quant,{record_id}",
                "reason": "导入成功。",
            })
        values.extend({
            "batch_id": batch_id,
            "source_row": failure["source_row"],
            "status": "failed",
            "identifier": failure["identifier"],
            "reason": failure["reason"],
        } for failure in filtered["failures"])
        if values:
            self.env["stock.subwarehouse.business.import.result.line"].create(values)

    x_material_type = fields.Selection(
        related="product_tmpl_id.x_material_type",
        string="物料类型",
        store=True,
        index=True,
    )

    def action_transfer_selected_out_of_descendant_inventory(self):
        warehouse = self.env["stock.warehouse"].browse(
            self.env.context.get("descendant_inventory_warehouse_id")
        ).exists()
        root_location = self.env["stock.location"].browse(
            self.env.context.get("descendant_inventory_root_location_id")
        ).exists()
        return self.env[
            "stock.subwarehouse.internal.transfer.wizard"
        ].action_open_for_quants(self, root_location, warehouse)

    def _get_descendant_inventory_internal_transfer_type(self, warehouse, quants):
        if warehouse and warehouse.int_type_id:
            return warehouse.int_type_id
        picking_type = self.env["stock.picking.type"].search([
            ("code", "=", "internal"),
            "|",
            ("company_id", "=", quants[:1].company_id.id),
            ("company_id", "=", False),
        ], limit=1)
        if not picking_type:
            raise UserError(_("尚未配置内部调拨作业类型。"))
        return picking_type
