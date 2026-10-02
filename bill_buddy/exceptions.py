from rest_framework.views import exception_handler
from rest_framework import status


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return None

    payload = response.data
    if isinstance(payload, dict) and 'detail' in payload:
        message = str(payload['detail'])
        errors = {}
    else:
        message = "Validation error" if response.status_code == status.HTTP_400_BAD_REQUEST else "Request failed"
        errors = payload

    response.data = {
        "success": False,
        "message": message,
        "data": {},
        "errors": errors,
    }
    return response
