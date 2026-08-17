/** @odoo-module **/

const CONFIRMING_PATHS = [
    "/web/login",
    "/shop/checkout",
    "/shop/payment",
    "/shop/erp-payment/start/",
];

function normalizedPath(value) {
    try {
        return new URL(value, window.location.origin).pathname.replace(/^\/en(?=\/)/, "");
    } catch {
        return "";
    }
}

function requiresErpConfirmation(value) {
    const path = normalizedPath(value);
    return CONFIRMING_PATHS.some((candidate) => (
        candidate.endsWith("/") ? path.startsWith(candidate) : path === candidate
    ));
}

function showConfirmationOverlay(source) {
    if (document.querySelector(".x_erp_confirmation_overlay")) {
        return;
    }
    const isEnglish = document.documentElement.lang.toLowerCase().startsWith("en")
        || window.location.pathname.startsWith("/en/");
    const overlay = document.createElement("div");
    overlay.className = "x_erp_confirmation_overlay";
    overlay.setAttribute("role", "status");
    overlay.setAttribute("aria-live", "polite");
    overlay.innerHTML = `
        <div class="x_erp_confirmation_card">
            <span class="x_erp_confirmation_spinner" aria-hidden="true"></span>
            <span>${isEnglish ? "Confirming with ERP…" : "正在等待 ERP 确认…"}</span>
        </div>`;
    document.body.appendChild(overlay);
    requestAnimationFrame(() => overlay.classList.add("is-visible"));
    if (source instanceof HTMLFormElement) {
        requestAnimationFrame(() => {
            source.querySelectorAll("button[type='submit'], input[type='submit']")
                .forEach((button) => {
                    button.disabled = true;
                    button.dataset.erpConfirmationDisabled = "1";
                });
        });
    }
}

document.addEventListener("submit", (event) => {
    const form = event.target;
    if (form instanceof HTMLFormElement && requiresErpConfirmation(form.action)) {
        showConfirmationOverlay(form);
    }
}, true);

document.addEventListener("click", (event) => {
    const link = event.target.closest("a[href]");
    if (link && requiresErpConfirmation(link.href)) {
        showConfirmationOverlay(link);
    }
}, true);

// Browsers can restore the payment-selection page from the back/forward cache
// together with the pre-navigation overlay.  Remove that stale UI state so a
// customer can always leave or resume a pending payment.
window.addEventListener("pageshow", () => {
    document.querySelectorAll(".x_erp_confirmation_overlay").forEach((overlay) => {
        overlay.remove();
    });
    document.querySelectorAll("[data-erp-confirmation-disabled='1']").forEach((button) => {
        button.disabled = false;
        delete button.dataset.erpConfirmationDisabled;
    });
});
