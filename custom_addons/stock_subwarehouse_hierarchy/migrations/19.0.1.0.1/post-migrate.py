def migrate(cr, version):
    cr.execute("""
        UPDATE product_template
           SET list_price = CASE
                   WHEN upper(default_code) ~ '^[0-9]{6}S1-[A-Z]{2}007-' THEN 2860.0
                   WHEN upper(default_code) ~ '^[0-9]{6}S1-[A-Z]{2}010-' THEN 3880.0
                   ELSE list_price
               END,
               x_website_usd_price = CASE
                   WHEN upper(default_code) ~ '^[0-9]{6}S1-[A-Z]{2}007-' THEN 470.0
                   WHEN upper(default_code) ~ '^[0-9]{6}S1-[A-Z]{2}010-' THEN 550.0
                   ELSE x_website_usd_price
               END
         WHERE upper(default_code) ~ '^[0-9]{6}S1-[A-Z]{2}(007|010)-'
    """)
