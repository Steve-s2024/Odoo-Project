from . import controllers
from . import models
from . import wizards


def post_init_hook(env):
    dashboard = env.ref(
        "spreadsheet_dashboard.ir_actions_dashboard_action",
        raise_if_not_found=False,
    )
    discuss = env.ref("mail.action_discuss", raise_if_not_found=False)
    if not dashboard:
        return

    users = env["res.users"].search([("share", "=", False)])
    discuss_id = discuss.id if discuss else False
    users_to_update = users.filtered(
        lambda user: not user.action_id or user.action_id.id == discuss_id
    )
    users_to_update.sudo().write({"action_id": dashboard.id})
