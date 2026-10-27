from collections import defaultdict

from odoo import api, models
from odoo.exceptions import UserError


def destination_key(value):
    return "/".join(part.strip().casefold() for part in str(value or "").split("/"))


class MrpImportDestination(models.Model):
    _inherit = "mrp.production"

    @api.model
    def _inspect_mrp_import_destinations(self, import_fields, rows):
        """Resolve the whole file in bulk, without creating any warehouse data."""
        plan = {"missing": [], "invalid": {}, "resolved": {}, "field_index": None}
        supported = {"location_dest_id", "location_dest_id/.id", "location_dest_id/id"}
        indexes = [i for i, field in enumerate(import_fields) if field in supported]
        if not indexes:
            return plan
        if len(indexes) != 1:
            raise UserError("成品库位只能映射一列，请保留名称、数据库 ID 或外部 ID 中的一种。")
        index = plan["field_index"] = indexes[0]
        field = import_fields[index]
        Location = self.env["stock.location"]
        locations = Location.with_context(active_test=False).search([
            ("company_id", "in", [False, *self.env.companies.ids]),
        ])
        by_id = {location.id: location for location in locations}
        by_path, by_name = defaultdict(list), defaultdict(list)
        for location in locations:
            by_path[destination_key(location.complete_name)].append(location)
            by_name[destination_key(location.name)].append(location)

        # External IDs are identifiers, not names from which a location can be
        # invented. Resolve all of them in one metadata query.
        external = {}
        if field.endswith("/id"):
            values = {str(row[index] or "").strip() for row in rows}
            xmlids = {v if "." in v else "__import__." + v for v in values if v}
            metadata = self.env["ir.model.data"].sudo().search([
                ("model", "=", "stock.location"),
                ("module", "in", list({v.split(".", 1)[0] for v in xmlids})),
                ("name", "in", list({v.split(".", 1)[1] for v in xmlids})),
            ])
            external = {f"{item.module}.{item.name}": item.res_id for item in metadata}

        defaults = self.default_get(["location_dest_id", "picking_type_id"])
        company_fields = [i for i, f in enumerate(import_fields) if f in {"company_id", "company_id/.id", "company_id/id"}]
        picking_fields = [i for i, f in enumerate(import_fields) if f in {"picking_type_id", "picking_type_id/.id", "picking_type_id/id"}]
        picking_types = self.env["stock.picking.type"].search([
            ("code", "=", "mrp_operation"), ("company_id", "in", self.env.companies.ids),
        ])
        references = {}
        if "company_id/id" in import_fields or "picking_type_id/id" in import_fields:
            references = {(item.model, f"{item.module}.{item.name}"): item.res_id
                          for item in self.env["ir.model.data"].sudo().search([
                              ("model", "in", ["res.company", "stock.picking.type"]),
                          ])}

        def matches_reference(record, value, field_name):
            if field_name.endswith("/.id"):
                return str(record.id) == value
            if field_name.endswith("/id"):
                xmlid = value if "." in value else "__import__." + value
                return references.get((record._name, xmlid)) == record.id
            return value.casefold() in {record.name.casefold(), record.display_name.casefold()}
        missing = {}
        name_index = import_fields.index("name") if "name" in import_fields else None
        parent_company = self.env.company
        for source_index, row in enumerate(rows):
            # A blank-name continuation row inherits its MO's company.
            is_parent = name_index is None or bool(str(row[name_index] or "").strip()) or source_index == 0
            company = self.env.company if is_parent else parent_company
            if company_fields and str(row[company_fields[0]] or "").strip():
                value = str(row[company_fields[0]]).strip()
                matches = self.env.companies.filtered(
                    lambda item: matches_reference(item, value, import_fields[company_fields[0]])
                )
                if len(matches) != 1:
                    plan["invalid"][source_index] = "公司不存在或没有该公司的访问权限。"
                    continue
                company = matches
            if is_parent:
                parent_company = company
            raw = str(row[index] or "").strip()
            if not raw:
                continue
            def suitable(records):
                return [r for r in records if not r.company_id or r.company_id == company]

            parent = by_id.get(self.env.context.get("subwarehouse_manufacturing_location_id"))
            if not parent:
                parent = by_id.get(self.env.context.get("default_location_dest_id"))
            selected_type = None
            if picking_fields and str(row[picking_fields[0]] or "").strip():
                value = str(row[picking_fields[0]]).strip()
                matches = picking_types.filtered(
                    lambda item: item.company_id == company and matches_reference(item, value, import_fields[picking_fields[0]])
                )
                if len(matches) == 1:
                    selected_type = matches
                else:
                    plan["invalid"][source_index] = "制造作业类型不存在、重复或不属于所选公司。"
                    continue
            else:
                selected_type = picking_types.filtered(
                    lambda item: item.id == defaults.get("picking_type_id") and item.company_id == company
                ) or picking_types.filtered(lambda item: item.company_id == company)[:1]
            if not parent or (parent.company_id and parent.company_id != company):
                parent = selected_type.default_location_dest_id if selected_type else None
            if not parent and company == self.env.company:
                parent = by_id.get(defaults.get("location_dest_id"))

            if field == "location_dest_id/.id":
                candidates = suitable([by_id[int(raw)]] if raw.isdecimal() and int(raw) in by_id else [])
            elif field == "location_dest_id/id":
                record_id = external.get(raw if "." in raw else "__import__." + raw)
                candidates = suitable([by_id[record_id]] if record_id in by_id else [])
            else:
                candidates = suitable(by_path[destination_key(raw)])
                if not candidates and parent:
                    candidates = suitable(by_path[destination_key(parent.complete_name + "/" + raw)])
                if not candidates and "/" not in raw:
                    candidates = suitable(by_name[destination_key(raw)])
            if len(candidates) > 1:
                plan["invalid"][source_index] = f"成品库位“{raw}”匹配多个库位，请填写完整路径或数据库 ID。"
                continue
            if candidates:
                location = candidates[0]
                if not location.active or location.usage != "internal":
                    plan["invalid"][source_index] = f"成品库位“{raw}”已停用或不是内部库位。"
                else:
                    plan["resolved"][source_index] = location.id
                continue
            if field != "location_dest_id":
                plan["invalid"][source_index] = f"成品库位标识“{raw}”不存在或不可访问；不能按 ID 创建库位。"
                continue
            parts = [part.strip() for part in raw.split("/")]
            if any(not part or part in {".", ".."} for part in parts):
                plan["invalid"][source_index] = f"成品库位路径“{raw}”无效。"
                continue
            # Reuse the longest known ancestor for explicit full paths.
            for length in range(len(parts) - 1, 0, -1):
                ancestors = suitable(by_path[destination_key("/".join(parts[:length]))])
                if len(ancestors) == 1:
                    parent = ancestors[0]
                    parts = parts[length:]
                    break
                if len(ancestors) > 1:
                    parent = None
                    break
            if not parent or not parent.active or parent.usage not in {"internal", "view"}:
                plan["invalid"][source_index] = f"无法确定“{raw}”的上级仓库，请先选择制造仓库或填写完整库位路径。"
                continue
            key = f"{company.id}:{parent.id}:{destination_key('/'.join(parts))}"
            item = missing.setdefault(key, {
                "key": key, "name": raw, "company_id": company.id,
                "company": company.display_name, "parent_id": parent.id,
                "parent": parent.complete_name, "parts": parts, "source_indexes": [],
            })
            item["source_indexes"].append(source_index)
            plan["invalid"][source_index] = f"成品库位“{raw}”不存在，已按选择跳过。"
        plan["missing"] = list(missing.values())
        return plan

    @api.model
    def _create_mrp_import_destinations(self, entries):
        """Create every confirmed path, respecting the importer's permissions."""
        Location = self.env["stock.location"]
        Location.check_access("create")
        # Serialize competing manufacturing imports without escalating ACLs.
        for company_id in sorted({item["company_id"] for item in entries}):
            self.env.cr.execute("SELECT pg_advisory_xact_lock(hashtext(%s), %s)", [
                "stock_subwarehouse_hierarchy.mrp_destination_import", company_id,
            ])
        locations = Location.with_context(active_test=False).search([
            ("company_id", "in", [False, *self.env.companies.ids]),
        ])
        children = defaultdict(list)
        for location in locations:
            children[(location.location_id.id, destination_key(location.name))].append(location)
        resolved = {}
        for item in entries:
            parent = Location.browse(item["parent_id"])
            parent.check_access("read")
            if not parent.active or (parent.company_id and parent.company_id.id != item["company_id"]):
                raise UserError("新库位的上级仓库已停用或公司已改变，请重新检查导入文件。")
            if item.get("parent") and parent.complete_name != item["parent"]:
                raise UserError("新库位的上级仓库名称或路径已改变，请重新检查导入文件。")
            for part in item["parts"]:
                matches = [r for r in children[(parent.id, destination_key(part))]
                           if not r.company_id or r.company_id.id == item["company_id"]]
                if len(matches) > 1:
                    raise UserError(f"库位“{part}”存在重复，请先修正仓库结构。")
                if matches:
                    parent = matches[0]
                    if not parent.active or parent.usage not in {"internal", "view"}:
                        raise UserError(f"库位“{part}”已停用或不能作为内部库位。")
                else:
                    parent = Location.create({
                        "name": part, "location_id": parent.id, "usage": "internal",
                        "company_id": item["company_id"], "x_is_subwarehouse": True,
                    })
                    children[(parent.location_id.id, destination_key(part))].append(parent)
            if parent.usage != "internal":
                raise UserError("成品库位必须是内部库位，请重新检查导入文件。")
            resolved[item["key"]] = parent.id
        return resolved
