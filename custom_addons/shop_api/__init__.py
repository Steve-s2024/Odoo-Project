from . import controllers
from . import models


def post_init_hook(env):
    env["shop.api.configuration"]._ensure_default_configuration()
    env["shop.api.scope"]._ensure_builtin_scopes()
    env["shop.api.endpoint"]._ensure_builtin_endpoints()
    env["shop.api.event.type"]._ensure_builtin_event_types()

    for model_name in env["shop.api.uuid.mixin"]._shop_api_uuid_models():
        records = env[model_name].with_context(active_test=False).search([
            ("shop_api_uuid", "=", False),
        ])
        records._shop_api_ensure_uuid()
