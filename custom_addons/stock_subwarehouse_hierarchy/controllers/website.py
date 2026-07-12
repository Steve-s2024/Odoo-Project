from odoo import http
from odoo.http import request, route


class StockSubwarehouseWebsite(http.Controller):
    @route("/stores", type="http", auth="public", website=True, sitemap=True)
    def stores(self, **kwargs):
        stores = [
            {
                "name": "思安奇北京旗舰店",
                "english_name": "SUN Beijing Flagship Store",
                "city": "北京",
                "english_city": "Beijing",
                "address": "朝阳区雪具大道 88 号 A 座 1 层",
                "english_address": "1F, Tower A, 88 Snow Gear Avenue, Chaoyang District",
                "hours": "周一至周日 10:00-21:00",
                "english_hours": "Mon-Sun 10:00-21:00",
                "phone": "010-6000-1888",
                "services": "新品试穿、雪鞋热塑、租赁归还、售后保养",
                "english_services": "Fitting, boot molding, rental returns, after-sales service",
            },
            {
                "name": "思安奇张家口崇礼店",
                "english_name": "SUN Chongli Distributor",
                "city": "张家口",
                "english_city": "Zhangjiakou",
                "address": "崇礼区雪场路 19 号游客中心旁",
                "english_address": "Beside the visitor center, 19 Ski Resort Road, Chongli District",
                "hours": "雪季 08:30-22:00 / 非雪季 10:00-18:00",
                "english_hours": "Snow season 08:30-22:00 / Off season 10:00-18:00",
                "phone": "0313-660-2026",
                "services": "雪场提货、快速换码、租赁套装、团队采购",
                "english_services": "Resort pickup, size exchange, rental sets, team purchasing",
            },
            {
                "name": "思安奇上海体验店",
                "english_name": "SUN Shanghai Experience Store",
                "city": "上海",
                "english_city": "Shanghai",
                "address": "静安区运动生活广场 3 层 305",
                "english_address": "Unit 305, 3F, Sports Life Plaza, Jing'an District",
                "hours": "周二至周日 11:00-20:00",
                "english_hours": "Tue-Sun 11:00-20:00",
                "phone": "021-5100-7788",
                "services": "装备咨询、尺码测量、线上订单自提、维修登记",
                "english_services": "Gear advice, size measurement, online pickup, repair registration",
            },
        ]
        is_english = request.lang and request.lang.code == "en_US"
        return request.render(
            "stock_subwarehouse_hierarchy.stores_page",
            {
                "stores": stores,
                "is_english": is_english,
                "additional_title": "Stores" if is_english else "门店(stores)",
            },
        )
