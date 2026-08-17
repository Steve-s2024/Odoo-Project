def migrate(cr, version):
    cr.execute(
        """
        SELECT data_type
          FROM information_schema.columns
         WHERE table_name = 'sale_order'
           AND column_name = 'x_sale_nature'
        """
    )
    row = cr.fetchone()
    if row and row[0] in ("character varying", "text"):
        cr.execute(
            "ALTER TABLE sale_order RENAME COLUMN x_sale_nature TO x_sale_nature_legacy"
        )
