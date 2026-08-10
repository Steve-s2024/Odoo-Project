def migrate(cr, version):
    cr.execute("UPDATE payment_method SET active = TRUE WHERE code = 'alipay'")
