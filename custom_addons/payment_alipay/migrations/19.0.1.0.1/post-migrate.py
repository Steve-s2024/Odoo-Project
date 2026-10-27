def migrate(cr, version):
    cr.execute("""
        SELECT provider_data.res_id, method_data.res_id
          FROM ir_model_data provider_data
          JOIN ir_model_data method_data
            ON method_data.module = 'payment'
           AND method_data.name = 'payment_method_alipay'
           AND method_data.model = 'payment.method'
         WHERE provider_data.module = 'payment_alipay'
           AND provider_data.name = 'payment_provider_alipay'
           AND provider_data.model = 'payment.provider'
    """)
    provider_method = cr.fetchone()
    if provider_method:
        cr.execute("""
            INSERT INTO payment_method_payment_provider_rel
                        (payment_provider_id, payment_method_id)
                 VALUES (%s, %s)
            ON CONFLICT DO NOTHING
        """, provider_method)

    cr.execute("""
        SELECT res_id
          FROM ir_model_data
         WHERE module = 'payment_alipay'
           AND name = 'payment_method_alipay'
           AND model = 'payment.method'
    """)
    row = cr.fetchone()
    if not row:
        return
    duplicate_id = row[0]
    cr.execute("""
        DELETE FROM payment_method_payment_provider_rel
         WHERE payment_method_id = %s
    """, [duplicate_id])
    cr.execute("""
        DELETE FROM ir_model_data
         WHERE module = 'payment_alipay'
           AND name = 'payment_method_alipay'
           AND model = 'payment.method'
    """)
    cr.execute("DELETE FROM payment_method WHERE id = %s", [duplicate_id])
