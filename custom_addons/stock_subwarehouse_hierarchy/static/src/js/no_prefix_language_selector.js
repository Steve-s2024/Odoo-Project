document.addEventListener(
    "click",
    function (event) {
        const languageLink = event.target.closest(".js_change_lang[data-url_code]");
        if (!languageLink) {
            return;
        }
        event.preventDefault();
        event.stopImmediatePropagation();

        const langCode = encodeURIComponent(languageLink.dataset.url_code);
        const targetUrl = (
            window.location.pathname
            + window.location.search.replace(/[&?]edit_translations[^&?]+/, "")
            + window.location.hash
        );
        window.location.href = `/website/shop_lang/${langCode}?r=${encodeURIComponent(targetUrl)}`;
    },
    true
);
