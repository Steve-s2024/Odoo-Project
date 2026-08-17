def migrate(cr, version):
    cr.execute(
        """
        UPDATE payment_provider
           SET state = 'disabled', write_date = NOW()
         WHERE code = 'custom'
           AND (
                LOWER(COALESCE(name->>'en_US', '')) = 'cash on delivery'
                OR COALESCE(name->>'zh_CN', '') = '货到付款'
           )
        """
    )
