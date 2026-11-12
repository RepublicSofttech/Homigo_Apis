"""Single error format for the whole API: {code, message, field_errors}."""
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.views import exception_handler


class BusinessError(APIException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "BUSINESS_RULE"

    def __init__(self, code, message, status_code=None):
        self.code = code
        if status_code:
            self.status_code = status_code
        super().__init__(detail=message, code=code)


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return None
    if isinstance(exc, BusinessError):
        response.data = {"code": exc.code, "message": str(exc.detail), "field_errors": {}}
    elif isinstance(exc, ValidationError):
        data = exc.detail
        field_errors = data if isinstance(data, dict) else {"non_field_errors": data}
        first = next(iter(field_errors.values()), ["Invalid data"])
        message = first[0] if isinstance(first, list) and first else str(first)
        response.data = {"code": "VALIDATION_ERROR", "message": str(message), "field_errors": field_errors}
    else:
        detail = response.data.get("detail", "Error") if isinstance(response.data, dict) else response.data
        code = getattr(detail, "code", "ERROR")
        response.data = {"code": str(code).upper(), "message": str(detail), "field_errors": {}}
    return response
