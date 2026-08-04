def migrate(cr, version):
    cr.execute(
        """
        UPDATE ir_ui_view
           SET active = TRUE
         WHERE key = 'website_sale.product_comment'
           AND active IS NOT TRUE
        """
    )
