def migrate(cr, version):
    cr.execute(
        """
        SELECT 1
          FROM information_schema.columns
         WHERE table_name = 'sale_order'
           AND column_name = 'x_sale_nature_legacy'
        """
    )
    if not cr.fetchone():
        return
    cr.execute(
        """
        UPDATE sale_order AS sale
           SET x_sale_nature = nature.id
          FROM stock_subwarehouse_sale_nature AS nature
         WHERE nature.code = sale.x_sale_nature_legacy
           AND sale.x_sale_nature_legacy IS NOT NULL
        """
    )
    cr.execute("ALTER TABLE sale_order DROP COLUMN x_sale_nature_legacy")
