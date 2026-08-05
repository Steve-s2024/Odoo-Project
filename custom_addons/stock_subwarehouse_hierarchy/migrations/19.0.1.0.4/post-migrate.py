def migrate(cr, version):
    cr.execute(
        """
        UPDATE ir_ui_view AS view
           SET active = FALSE,
               key = view.key || '.legacy.' || view.id::text
         WHERE view.key = 'stock_subwarehouse_hierarchy.product_page_shop_group_siblings'
           AND NOT EXISTS (
                SELECT 1
                  FROM ir_model_data AS xmlid
                 WHERE xmlid.model = 'ir.ui.view'
                   AND xmlid.res_id = view.id
                   AND xmlid.module = 'stock_subwarehouse_hierarchy'
                   AND xmlid.name = 'product_page_shop_group_siblings'
           )
        """
    )
