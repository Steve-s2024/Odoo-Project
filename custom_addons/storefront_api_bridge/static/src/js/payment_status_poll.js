const statusPage = document.querySelector("[data-storefront-payment-status]");

const countdowns = document.querySelectorAll("[data-storefront-payment-expiry]");
for (const countdown of countdowns) {
    const expiry = Number(countdown.dataset.storefrontPaymentExpiry || 0);
    const value = countdown.querySelector("[data-payment-countdown-value]");
    if (!expiry || !value) {
        continue;
    }
    const updateCountdown = () => {
        const seconds = Math.max(0, Math.ceil((expiry - Date.now()) / 1000));
        const minutes = Math.floor(seconds / 60);
        const remainder = String(seconds % 60).padStart(2, "0");
        value.textContent = `${minutes}:${remainder}`;
        if (!seconds) {
            countdown.classList.add("is-expired");
            value.textContent = countdown.dataset.expiredLabel || "Expired";
            const container = countdown.closest("[data-storefront-payment-expiry]") || countdown;
            const status = container.querySelector(".x_purchase_status");
            if (status) {
                status.textContent = countdown.dataset.expiredLabel || "Expired";
            }
            if (statusPage) {
                window.setTimeout(() => window.location.assign(
                    statusPage.dataset.expiredUrl || "/purchase-history"
                ), 500);
            }
            return;
        }
        window.setTimeout(updateCountdown, 1000);
    };
    updateCountdown();
}

if (
    statusPage?.dataset.paymentPending === "true"
    && statusPage.dataset.paymentSimulation !== "true"
) {
    const paymentId = statusPage.dataset.paymentId || "unknown";
    const storageKey = `storefront-payment-poll:${paymentId}`;
    const expiresAt = Number(statusPage.dataset.paymentExpiresAt || 0);

    const poll = async () => {
        if (!expiresAt || Date.now() >= expiresAt) {
            sessionStorage.removeItem(storageKey);
            window.location.assign(
                statusPage.dataset.expiredUrl || "/purchase-history"
            );
            return;
        }
        try {
            const response = await fetch(
                `/shop/payment/status/poll?payment_id=${encodeURIComponent(paymentId)}`,
                {
                    method: "GET",
                    credentials: "same-origin",
                    headers: {Accept: "application/json"},
                },
            );
            if (response.ok) {
                const result = await response.json();
                if (result.state === "done" && result.redirect) {
                    sessionStorage.removeItem(storageKey);
                    window.location.assign(result.redirect);
                    return;
                }
                if (result.state === "expired") {
                    sessionStorage.removeItem(storageKey);
                    window.location.assign(
                        result.redirect || statusPage.dataset.expiredUrl || "/purchase-history"
                    );
                    return;
                }
            }
        } catch {
            // ERP or the network is unavailable. Keep the order pending and
            // retry without inferring success from Shop-side state.
        }
        window.setTimeout(poll, 5000);
    };
    window.setTimeout(poll, 5000);
} else if (statusPage?.dataset.receiptPending === "true") {
    const paymentId = statusPage.dataset.paymentId || "unknown";
    const storageKey = `storefront-receipt-poll:${paymentId}`;
    const now = Date.now();
    const startedAt = Number(sessionStorage.getItem(storageKey)) || now;
    sessionStorage.setItem(storageKey, String(startedAt));

    // Odoo post-processes a successful transaction asynchronously. Keep the
    // success page visible while waiting, and stop after a bounded interval.
    if (now - startedAt < 2 * 60 * 1000) {
        window.setTimeout(() => window.location.reload(), 2000);
    } else {
        sessionStorage.removeItem(storageKey);
    }
} else if (statusPage?.dataset.paymentId) {
    sessionStorage.removeItem(
        `storefront-payment-poll:${statusPage.dataset.paymentId}`
    );
    sessionStorage.removeItem(
        `storefront-receipt-poll:${statusPage.dataset.paymentId}`
    );
}
