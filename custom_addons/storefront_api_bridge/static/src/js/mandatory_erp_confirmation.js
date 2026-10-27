/** @odoo-module **/

const FORM_CONFIRMING_PATHS = ["/shop/checkout", "/shop/payment"];
const LINK_CONFIRMING_PATHS = ["/shop/checkout", "/shop/payment"];
const CONFIRMATION_TIMEOUT_MS = 30000;

function normalizedPath(value) {
    try {
        return new URL(value, window.location.origin).pathname.replace(/^\/en(?=\/)/, "");
    } catch {
        return "";
    }
}

function matchesPath(value, paths) {
    const path = normalizedPath(value);
    return paths.some((candidate) => (
        candidate.endsWith("/") ? path.startsWith(candidate) : path === candidate
    ));
}

function requiresFormConfirmation(form) {
    const path = normalizedPath(form.action);
    return matchesPath(form.action, FORM_CONFIRMING_PATHS)
        || (form.method.toLowerCase() === "post" && path.startsWith("/shop/erp-payment/start/"));
}

function requiresLinkConfirmation(link) {
    // Ordinary account/application/history/logout links are navigation only.
    // In particular, a /web/login?redirect=... link must never look like an
    // authoritative login submission or hold the page behind an overlay.
    const rawHref = (link.getAttribute("href") || "").trim();
    const isPageControl = rawHref.startsWith("#")
        || link.matches(".dropdown-toggle, [data-bs-toggle='dropdown']")
        || link.getAttribute("role") === "button";
    return !isPageControl
        && !link.closest(".dropdown-menu")
        && matchesPath(link.href, LINK_CONFIRMING_PATHS);
}

function clearConfirmationOverlay() {
    document.querySelectorAll(".x_erp_confirmation_overlay").forEach((overlay) => {
        window.clearTimeout(Number(overlay.dataset.cleanupTimer || 0));
        overlay.remove();
    });
    document.querySelectorAll("[data-erp-confirmation-disabled='1']").forEach((button) => {
        button.disabled = false;
        delete button.dataset.erpConfirmationDisabled;
    });
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
            <span>${isEnglish ? "Please be patient…" : "请耐心等候…"}</span>
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
    // If navigation or a response is interrupted, never strand the customer
    // behind a permanent overlay. Authoritative operations still fail closed
    // on the server; this timer only restores usable navigation.
    overlay.dataset.cleanupTimer = String(window.setTimeout(
        clearConfirmationOverlay, CONFIRMATION_TIMEOUT_MS,
    ));
}

document.addEventListener("submit", (event) => {
    const form = event.target;
    if (form instanceof HTMLFormElement && requiresFormConfirmation(form)) {
        showConfirmationOverlay(form);
    }
}, true);

document.addEventListener("click", (event) => {
    const link = event.target.closest("a[href]");
    if (link && requiresLinkConfirmation(link)) {
        showConfirmationOverlay(link);
    }
}, true);

// Browsers can restore the payment-selection page from the back/forward cache
// together with the pre-navigation overlay.  Remove that stale UI state so a
// customer can always leave or resume a pending payment.
window.addEventListener("pageshow", () => {
    clearConfirmationOverlay();
});
