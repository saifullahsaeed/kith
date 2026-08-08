"""Reading and changing his chat settings."""

from __future__ import annotations

from kith.api.blueprint import api
from kith.config import CONFIG_DB_PATH, default_config, to_wire
from kith.infra.db import config_store
from kith.schemas import (
    ConfigSchema,
)

_SETTING_KEYS = {
    "model": "model",
    "numCtx": "num_ctx",
    "numPredict": "num_predict",
    "think": "think",
    "effort": "effort",
    "baseUrl": "base_url",
    "apiKey": "api_key",
}


@api.get("/config")
@api.output(ConfigSchema)
@api.doc(
    summary="Effective defaults",
    description="The default model, context/output sizes, and the persona.",
)
def get_config():
    return to_wire(default_config())


@api.patch("/config")
@api.input(ConfigSchema(partial=True), arg_name="payload")
@api.output(ConfigSchema)
@api.doc(
    summary="Persist config changes",
    description=(
        "Saves the given fields to the config database (they outlive restarts). "
        "`system` is ignored here — the persona is managed via the persona/ "
        "folder, not this store. Returns the new effective config."
    ),
)
def patch_config(payload):
    updates = {_SETTING_KEYS[field]: payload[field] for field in _SETTING_KEYS if field in payload}
    if updates:
        config_store.update_settings(CONFIG_DB_PATH, updates)
    return to_wire(default_config())
