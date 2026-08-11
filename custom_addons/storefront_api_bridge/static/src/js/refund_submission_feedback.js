/** @odoo-module **/

const initializeRefundSubmissionFeedback = () => {
    for (const flash of document.querySelectorAll("[data-refund-flash]")) {
        window.setTimeout(() => flash.classList.add("is-leaving"), 2500);
        window.setTimeout(() => flash.remove(), 3000);
    }

    for (const form of document.querySelectorAll("[data-refund-submit-form]")) {
        form.addEventListener("submit", () => {
            if (form.classList.contains("is-submitting")) {
                return;
            }
            form.classList.add("is-submitting");
            form.setAttribute("aria-busy", "true");
            const submitButton = form.querySelector("button[type='submit']");
            if (submitButton) {
                submitButton.disabled = true;
            }
        });
    }
};

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializeRefundSubmissionFeedback, {once: true});
} else {
    initializeRefundSubmissionFeedback();
}
