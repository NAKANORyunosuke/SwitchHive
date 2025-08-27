import json
import azure.functions as func

app = func.FunctionApp()


@app.function_name(name="AlexaEndpoint")
@app.route(
    route="alexa",
    methods=["POST"],
    auth_level=func.AuthLevel.ANONYMOUS
)
def alexa(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse("Invalid JSON", status_code=400)

    req_type = (body.get("request") or {}).get("type")
    if req_type == "LaunchRequest":
        text = "こんにちは。Azure Functions です。"
        should_end = False
    elif req_type == "IntentRequest":
        intent = (body["request"].get("intent") or {}).get(
            "name", "UnknownIntent")
        text = f"インテント {intent} を受け取りました。"
        should_end = True
    else:
        text = "さようなら。"
        should_end = True

    alexa_response = {
        "version": "1.0",
        "response": {
            "shouldEndSession": should_end,
            "outputSpeech": {"type": "PlainText", "text": text}
        }
    }
    return func.HttpResponse(
        body=json.dumps(alexa_response, ensure_ascii=False),
        status_code=200,
        mimetype="application/json",
    )
