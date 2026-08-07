/** @odoo-module **/

import { Interaction } from "@web/public/interaction";
import { registry } from "@web/core/registry";

export class CartStockShortageRow extends Interaction {
    static selector = ".x_stock_shortage_row";
    dynamicContent = {
        ".js_quantity": { "t-on-input": this.fadeWarning },
        ".css_quantity > a": { "t-on-click": this.fadeWarning },
    };

    fadeWarning() {
        this.el.classList.add("x_stock_shortage_resolving");
    }
}

registry
    .category("public.interactions")
    .add("stock_subwarehouse_hierarchy.cart_stock_shortage_row", CartStockShortageRow);

export class CartStockCheckoutGuard extends Interaction {
    static selector = ".o_website_sale_checkout_container";
    dynamicContent = {
        "a[name='website_sale_main_button']": {
            "t-on-click.withTarget": this.blockShortageCheckout,
        },
    };

    blockShortageCheckout(ev) {
        if (!this.el.querySelector(".x_stock_shortage_row")) {
            return;
        }
        ev.preventDefault();
        ev.stopPropagation();
        const warning = this.el.querySelector(".x_cart_stock_warning");
        if (!warning) {
            return;
        }
        warning.classList.remove("d-none", "x_cart_stock_warning_visible");
        void warning.offsetWidth;
        warning.classList.add("x_cart_stock_warning_visible");
    }
}

registry
    .category("public.interactions")
    .add("stock_subwarehouse_hierarchy.cart_stock_checkout_guard", CartStockCheckoutGuard);
