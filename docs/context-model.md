# v9 context embedding runtime

NomadCompass v9 uses a compact derivative of `sentence-transformers/static-similarity-mrl-multilingual-v1` for semantic retrieval.

Runtime profile:

- 128 dimensions
- int8 static token embeddings
- multilingual, including Italian and English
- source model license: Apache-2.0
- no user data is used to construct the runtime model
- installed model size: about 14.6 MiB
- release archive size: about 9.5 MiB

The source model is trained with Matryoshka dimensions, so v9 keeps the trained 128-dimensional prefix and applies Model2Vec's int8 quantization.

## Provisioning

The default Portainer bootstrap downloads the prebuilt compact artifact from the repository release `context-model-v1` and verifies its SHA-256 before extracting it. The expected digest is:

```text
33aebe14cc1cc8e506bca5f2d08fe243f94d4a716875f172f96229bb33bff632
```

The model is then validated with the same Model2Vec runtime loader used by the server and installed atomically under `/data/models`. Subsequent container restarts reuse the persistent model.

If the compact release cannot be downloaded, `CONTEXT_MODEL_SOURCE_FALLBACK=true` allows v9 to reconstruct the same 128d/int8 profile from the pinned upstream source revision. This is deliberately only a fallback because it requires a much larger temporary source-model download.

Semantic search remains optional: if provisioning fails at startup, the server logs a warning and continues with SQLite FTS lexical retrieval.
