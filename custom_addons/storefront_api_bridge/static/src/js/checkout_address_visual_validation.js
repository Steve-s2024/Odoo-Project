import { CustomerAddress } from "@portal/interactions/address";
import { patch } from "@web/core/utils/patch";

const INVALID_CLASS = "x_checkout_address_invalid";

patch(CustomerAddress.prototype, {
    setup() {
        super.setup();
        if (this.addressForm?.dataset.submitUrl !== "/shop/address/submit") {
            return;
        }

        this._visualInvalidField = null;
        this._visualInvalidFrame = null;
        this._boundVisualInvalid = this._onVisualInvalid.bind(this);
        this._boundVisualCorrection = this._onVisualCorrection.bind(this);
        this.addressForm.addEventListener("invalid", this._boundVisualInvalid, true);
        this.addressForm.addEventListener("input", this._boundVisualCorrection);
        this.addressForm.addEventListener("change", this._boundVisualCorrection);
    },

    async saveAddress(event) {
        const result = await super.saveAddress(event);
        if (!this._boundVisualInvalid) {
            return result;
        }

        // Server-side rules may reject values that pass HTML constraint
        // validation. Reuse Odoo's `is-invalid` markers but keep the feedback
        // visual, avoiding an English-only error paragraph on Chinese pages.
        const serverInvalidFields = [...this.addressForm.querySelectorAll(".is-invalid")];
        for (const field of serverInvalidFields) {
            field.classList.add(INVALID_CLASS);
            field.setAttribute("aria-invalid", "true");
        }
        if (serverInvalidFields.length) {
            const firstInvalid = serverInvalidFields[0];
            firstInvalid.scrollIntoView({ behavior: "smooth", block: "center" });
            firstInvalid.focus({ preventScroll: true });
        }
        return result;
    },

    destroy() {
        if (this.addressForm && this._boundVisualInvalid) {
            this.addressForm.removeEventListener("invalid", this._boundVisualInvalid, true);
            this.addressForm.removeEventListener("input", this._boundVisualCorrection);
            this.addressForm.removeEventListener("change", this._boundVisualCorrection);
        }
        if (this._visualInvalidFrame) {
            cancelAnimationFrame(this._visualInvalidFrame);
        }
        super.destroy();
    },

    _onVisualInvalid(event) {
        // Odoo's native address interaction calls reportValidity(). Cancelling
        // the `invalid` event keeps constraint validation intact while
        // suppressing the browser-owned, often incorrectly localized tooltip.
        event.preventDefault();
        const field = event.target;
        if (!(field instanceof HTMLElement)) {
            return;
        }
        field.classList.add(INVALID_CLASS);
        field.setAttribute("aria-invalid", "true");
        this._visualInvalidField ||= field;
        if (!this._visualInvalidFrame) {
            this._visualInvalidFrame = requestAnimationFrame(() => {
                const firstInvalid = this._visualInvalidField;
                this._visualInvalidField = null;
                this._visualInvalidFrame = null;
                firstInvalid?.scrollIntoView({ behavior: "smooth", block: "center" });
                firstInvalid?.focus({ preventScroll: true });
            });
        }
    },

    _onVisualCorrection(event) {
        const field = event.target;
        if (!(field instanceof HTMLInputElement || field instanceof HTMLSelectElement)) {
            return;
        }
        // Remove only the visual marker. Server-owned `.is-invalid` state and
        // messages remain authoritative until Odoo validates the next submit.
        if (field.validity.valid) {
            field.classList.remove(INVALID_CLASS);
            field.removeAttribute("aria-invalid");
        }
    },
});
