"""Talking to a model.

One module per wire format. Both expose the same `stream_once` event shape, so the
agent loop never learns which provider is answering."""
