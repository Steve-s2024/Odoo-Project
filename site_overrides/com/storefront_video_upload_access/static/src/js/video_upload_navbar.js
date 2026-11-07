import { NavBar } from "@web/webclient/navbar/navbar";
import { patch } from "@web/core/utils/patch";

const WEBSITE_APP_XMLID = "website.menu_website_configuration";
const VIDEO_UPLOAD_MENU_XMLID =
    "stock_subwarehouse_hierarchy.menu_website_video_upload";

/**
 * Website removes top-level leaf actions from its navbar because most of its
 * own actions are contextual entries nested below Site/Reporting.  The video
 * uploader is intentionally a direct, one-click action, so restore only this
 * leaf after Website has applied its normal filtering.
 */
patch(NavBar.prototype, {
    get currentAppSections() {
        const filteredSections = super.currentAppSections;
        if (
            this.currentApp?.xmlid !== WEBSITE_APP_XMLID ||
            filteredSections.some((section) => section.xmlid === VIDEO_UPLOAD_MENU_XMLID)
        ) {
            return filteredSections;
        }

        const sourceSections =
            this.menuService.getMenuAsTree(this.currentApp.id).childrenTree || [];
        const videoUploadSection = sourceSections.find(
            (section) => section.xmlid === VIDEO_UPLOAD_MENU_XMLID
        );
        if (!videoUploadSection) {
            return filteredSections;
        }

        // Preserve Website's filtered/cloned menu objects and their original
        // ordering, while adding back only the upload action.
        const sectionsById = new Map(
            filteredSections.map((section) => [String(section.id), section])
        );
        sectionsById.set(String(videoUploadSection.id), videoUploadSection);
        return sourceSections
            .filter((section) => sectionsById.has(String(section.id)))
            .map((section) => sectionsById.get(String(section.id)));
    },

    // Keep compatibility with the setter in Website's own NavBar patch.
    set currentAppSections(_) {},
});
