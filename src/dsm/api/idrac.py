"""Shared API helpers for one-off iDRAC requests."""

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import AsyncIterator, TypeVar

from fastapi import HTTPException

from dsm.crypto import decrypt_ciphertext
from dsm.idrac_connector import IdracConnectionError, IdracConnector, IdracError
from dsm.models import Server

Result = TypeVar("Result")


@asynccontextmanager
async def open_idrac_connector(
    server: Server,
    *,
    credential_error_detail: str,
) -> AsyncIterator[IdracConnector]:
    """Create and always close a connector for a single API request."""
    password = decrypt_ciphertext(server.ipmi_password_enc)
    if not password:
        raise HTTPException(status_code=500, detail=credential_error_detail)

    connector = IdracConnector(
        ip=server.ipmi_ip,
        username=server.ipmi_user,
        password=password,
        drac_version=server.drac_version,
    )
    try:
        yield connector
    finally:
        await connector.close()


async def execute_idrac_request(
    server: Server,
    operation: Callable[[IdracConnector], Awaitable[Result]],
    *,
    credential_error_detail: str,
    connection_error_detail: Callable[[IdracConnectionError], str] = str,
    idrac_error_detail: Callable[[IdracError], str] | None = None,
) -> Result:
    """Run an iDRAC operation with route-specific HTTP error wording."""
    async with open_idrac_connector(
        server,
        credential_error_detail=credential_error_detail,
    ) as connector:
        try:
            return await operation(connector)
        except IdracConnectionError as error:
            raise HTTPException(
                status_code=502,
                detail=connection_error_detail(error),
            ) from error
        except IdracError as error:
            if idrac_error_detail is None:
                raise
            raise HTTPException(
                status_code=502,
                detail=idrac_error_detail(error),
            ) from error
