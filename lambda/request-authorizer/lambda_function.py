"""Retired credential authorizer. Retained temporarily for safe deployment ordering."""


def lambda_handler(event, context):
    return {"isAuthorized": False, "context": {}}
