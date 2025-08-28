import json
import logging
import os
import hmac
import hashlib
import base64
from typing import Optional

import requests
import azure.functions as func

# ===== Azure Functions App =====
app = func.FunctionApp()

# ===== LINE API endpoints =====
LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"

# ===== Env Vars =====
# ・LINE_CHANNEL_ACCESS_TOKEN: Messaging API のチャネルアクセストークン（長期）
# ・LINE_CHANNEL_SECRET:       Messaging API のチャネルシークレット（署名検証に使用）
# ・LINE_DEVELOPER_ID:         デバッグ用に Push する先（U... の userId 推奨）
CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
# 任意。なければ Alexa → LINE Push はスキップ
DEVELOPER_ID = os.environ.get("LINE_DEVELOPER_ID")

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
}

# ===== Utilities =====


def verify_line_signature(req: func.HttpRequest, raw_body: bytes) -> bool:
    """LINE 署名検証（X-Line-Signature）"""
    signature = req.headers.get("x-line-signature")
    if not signature:
        logging.warning("Missing X-Line-Signature")
        return False
    mac = hmac.new(CHANNEL_SECRET.encode("utf-8"),
                   raw_body, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode("utf-8")
    if not hmac.compare_digest(signature, expected):
        logging.warning("Invalid signature")
        return False
    return True


def line_reply(reply_token: str, messages: list[dict]) -> requests.Response:
    payload = {"replyToken": reply_token, "messages": messages}
    r = requests.post(LINE_REPLY_URL, headers=HEADERS,
                      json=payload, timeout=10)
    logging.info(f"[LINE reply] {r.status_code} {r.text}")
    return r


def line_push(to_id: str, messages: list[dict]) -> requests.Response:
    # to_id は U... / R... / C... の ID である必要あり（LINEの “LINE ID” は不可）
    payload = {"to": to_id, "messages": messages}
    r = requests.post(LINE_PUSH_URL, headers=HEADERS, json=payload, timeout=10)
    logging.info(f"[LINE push] to={to_id[:6]}.. {r.status_code} {r.text}")
    return r


def build_text(text: str) -> dict:
    return {"type": "text", "text": text}

# ===== Alexa endpoint (→ LINE に転送も可能) =====


@app.function_name(name="AlexaEndpoint")
@app.route(route="alexa", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def alexa(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse("Invalid JSON", status_code=400)

    request = body.get("request") or {}
    req_type = request.get("type")

    if req_type == "LaunchRequest":
        text = "こんにちは。Azure Functions です。"
        should_end = False

    elif req_type == "IntentRequest":
        intent = (request.get("intent") or {}).get("name", "UnknownIntent")
        text = f"インテント {intent} を受け取りました。"
        should_end = True

        # --- 任意: Alexa → LINE へ Push（DEVELOPER_ID が U... のときのみ）
        if DEVELOPER_ID and DEVELOPER_ID.startswith(("U", "R", "C")):
            try:
                line_push(DEVELOPER_ID, [
                          build_text(f"Alexa intent: {intent}")])
            except Exception as e:
                logging.exception(f"LINE push error: {e}")
        else:
            logging.info("Skip LINE push (DEVELOPER_ID 未設定 or 形式不正)")

    else:
        text = "さようなら。"
        should_end = True

    alexa_response = {
        "version": "1.0",
        "response": {
            "shouldEndSession": should_end,
            "outputSpeech": {"type": "PlainText", "text": text},
        },
    }
    return func.HttpResponse(
        body=json.dumps(alexa_response, ensure_ascii=False),
        status_code=200,
        mimetype="application/json",
    )

# ===== LINE Webhook endpoint =====


@app.function_name(name="LineWebhook")
@app.route(route="line", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def line_webhook(req: func.HttpRequest) -> func.HttpResponse:
    # 生ボディを先に読む（署名検証に必須）
    raw_body = req.get_body()
    if not verify_line_signature(req, raw_body):
        return func.HttpResponse("Signature verification failed", status_code=401)

    try:
        body = json.loads(raw_body.decode("utf-8"))
    except Exception:
        return func.HttpResponse("Bad Request", status_code=400)

    events = body.get("events", [])
    for ev in events:
        ev_type = ev.get("type")
        src = ev.get("source", {})
        user_id: Optional[str] = src.get("userId")  # U...
        reply_token = ev.get("replyToken")

        logging.info(f"[LINE event] type={ev_type} user={user_id}")

        # 1) テキストメッセージにエコーで応答（reply）
        if ev_type == "message" and ev.get("message", {}).get("type") == "text" and reply_token:
            incoming = ev["message"].get("text", "")
            # 署名検証通ったので安全に reply
            line_reply(reply_token, [build_text(f"受信: {incoming}")])

            # 2) デバッグ用: userId が取れたら Push も即時テスト可能
            #    ※ 実運用は KeyVault / DB へ保存して後続ロジックで利用
            if user_id:
                # 過剰送信防止のため例外時も握りつぶさずログ
                try:
                    line_push(user_id, [build_text(
                        "Pushテスト：これはreplyではなくpushです。")])
                except Exception as e:
                    logging.exception(f"Push to user failed: {e}")

        # フォロー（友だち追加）時の挨拶
        elif ev_type == "follow" and reply_token:
            line_reply(reply_token, [build_text("友だち追加ありがとうございます！")])

        # グループ参加時など
        elif ev_type == "join" and reply_token:
            line_reply(reply_token, [build_text("グループに参加しました。よろしくお願いします。")])

    return func.HttpResponse("OK", status_code=200)

# =====（任意）Alexa から明示的に Push を打つための簡易エンドポイント =====
# 例: POST /api/push  { "to": "Uxxx...", "text": "hello" }


@app.function_name(name="ManualPush")
@app.route(route="push", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def manual_push(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse("Invalid JSON", status_code=400)

    to = body.get("to") or DEVELOPER_ID
    text = body.get("text") or "hello from Azure Functions"
    if not to or not to.startswith(("U", "R", "C")):
        return func.HttpResponse("Invalid 'to' (must start with U/R/C)", status_code=400)

    r = line_push(to, [build_text(text)])
    return func.HttpResponse(f"{r.status_code} {r.text}", status_code=r.status_code)
