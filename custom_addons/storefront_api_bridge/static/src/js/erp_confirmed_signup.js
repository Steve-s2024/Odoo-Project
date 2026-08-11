/** @odoo-module **/

function enableErpConfirmedSignupFeedback() {
    const form = document.querySelector("form[data-storefront-erp-signup='1']");
    if (!form) {
        return;
    }
    form.addEventListener("submit", () => {
        const button = form.querySelector("button[type='submit']");
        if (!button || button.disabled) {
            return;
        }
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
        const isEnglish = document.documentElement.lang.toLowerCase().startsWith("en");
        button.textContent = isEnglish
            ? "Waiting for ERP confirmation…"
            : "正在等待 ERP 确认…";
    });
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", enableErpConfirmedSignupFeedback, {once: true});
} else {
    enableErpConfirmedSignupFeedback();
}
