DEFAULT_PAYMENT_METHOD_CODES = {"wechatpay"}

API_BASE_URL = "https://api.mch.weixin.qq.com"
NATIVE_TRANSACTION_ENDPOINT = "/v3/pay/transactions/native"
REFUND_ENDPOINT = "/v3/refund/domestic/refunds"
NOTIFY_ROUTE = "/payment/wechatpay/notify"
REFUND_NOTIFY_ROUTE = "/payment/wechatpay/refund/notify"
PROCESS_ROUTE = "/payment/wechatpay/process"
SIMULATE_SUCCESS_ROUTE = "/payment/wechatpay/simulate_success"
