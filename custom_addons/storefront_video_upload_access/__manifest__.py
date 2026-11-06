{
    "name": "SUN Storefront Video Upload Access",
    "summary": "Expose website video upload safely to storefront designers",
    "version": "19.0.1.0.1",
    "category": "Website/Website",
    "author": "SUN",
    "license": "LGPL-3",
    "depends": ["stock_subwarehouse_hierarchy", "website"],
    "data": [
        "views/video_upload_menu.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "storefront_video_upload_access/static/src/js/video_upload_navbar.js",
        ],
    },
    "installable": True,
    "application": False,
}
