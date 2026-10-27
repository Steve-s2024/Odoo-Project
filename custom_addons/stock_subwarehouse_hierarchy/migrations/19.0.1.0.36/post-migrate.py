def migrate(cr, version):
    """Restore the Chinese ERP name without guessing or losing shop English."""
    cr.execute("""
        UPDATE product_template
           SET name = jsonb_set(
                   jsonb_set(
                       COALESCE(name, '{}'::jsonb),
                       '{zh_CN}',
                       to_jsonb(COALESCE(name->>'zh_CN', name->>'en_US')),
                       TRUE
                   ),
                   '{en_US}',
                   to_jsonb(COALESCE(name->>'zh_CN', name->>'en_US')),
                   TRUE
               )
         WHERE name IS NOT NULL
           AND name->>'en_US' IS DISTINCT FROM COALESCE(name->>'zh_CN', name->>'en_US')
    """)
