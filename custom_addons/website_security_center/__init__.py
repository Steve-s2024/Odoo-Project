from . import models


def post_init_hook(env):
    env["website.security.policy"].sudo()._ensure_defaults()
    env["website.security.health.check"].sudo()._ensure_defaults()
