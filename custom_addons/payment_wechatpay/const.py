DEFAULT_PAYMENT_METHOD_CODES = {"wechatpay"}

API_BASE_URL = "https://api.mch.weixin.qq.com"
NATIVE_TRANSACTION_ENDPOINT = "/v3/pay/transactions/native"
NOTIFY_ROUTE = "/payment/wechatpay/notify"
PROCESS_ROUTE = "/payment/wechatpay/process"
SIMULATE_SUCCESS_ROUTE = "/payment/wechatpay/simulate_success"
