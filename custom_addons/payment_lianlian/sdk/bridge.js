"use strict";

const fs = require("fs");
const LLPaySdk = require("ga-payment-sdk");

function readStdin() {
  return new Promise((resolve, reject) => {
    let raw = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", chunk => { raw += chunk; });
    process.stdin.on("end", () => {
      try {
        resolve(JSON.parse(raw));
      } catch (error) {
        reject(new Error("Invalid SDK bridge input."));
      }
    });
    process.stdin.on("error", reject);
  });
}

function loadKey(filePath, label) {
  if (!filePath) {
    throw new Error(`${label} path is missing.`);
  }
  return fs.readFileSync(filePath, "utf8").trim();
}

function callAsync(client, method, params) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("LianLian SDK request timed out.")), 35000);
    client[method]({
      params,
      successcb: result => { clearTimeout(timer); resolve(result); },
      failcb: result => {
        clearTimeout(timer);
        let details = result;
        if (typeof result === "string") {
          try {
            details = JSON.parse(result);
          } catch (_error) {
            details = result;
          }
        }
        const message = details && typeof details === "object"
          ? (details.message || details.return_message || details.error || details.body)
          : details;
        reject(new Error(
          typeof message === "string" && message.trim()
            ? message.trim().slice(0, 1000)
            : "LianLian SDK request failed."
        ));
      },
    });
  });
}

async function main() {
  const input = await readStdin();
  const config = input.config || {};
  const client = new LLPaySdk({
    env: config.env,
    sign_type: "RSA",
    merchant_sign_key: loadKey(config.merchant_private_key_path, "Merchant private key"),
    ll_sign_key: loadKey(config.lianlian_public_key_path, "LianLian public key"),
    merchant_id: config.merchant_id,
    sub_merchant_id: config.sub_merchant_id || "",
    is_print_log: false,
  });

  if (input.operation === "notice") {
    const result = client.llNotice(input.body || "", input.headers || {});
    if (!result || result.verifySignResult !== true) {
      throw new Error("LianLian notice signature verification failed.");
    }
    return result;
  }
  const allowed = new Set([
    "pay", "payResultQuery", "payCancel", "refund", "refundResultQuery", "shipmentsUpload",
  ]);
  if (!allowed.has(input.operation)) {
    throw new Error("Unsupported LianLian SDK operation.");
  }
  const result = await callAsync(client, input.operation, input.params || {});
  if (!result || result.verifySignResult !== true) {
    throw new Error("LianLian API response signature verification failed.");
  }
  return result;
}

main()
  .then(result => process.stdout.write(JSON.stringify({ok: true, result})))
  .catch(error => {
    process.stdout.write(JSON.stringify({ok: false, error: String(error && error.message || error)}));
    process.exitCode = 1;
  });
