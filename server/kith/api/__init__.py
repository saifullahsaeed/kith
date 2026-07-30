"""HTTP layer: the REST API blueprint and optional SPA serving.

Thin by design — routes validate, delegate, and serialise. Anything that thinks
belongs in the services or domain layers, not here.
"""
