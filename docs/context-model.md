# v9 context embedding runtime

NomadCompass v9 uses a compact derivative of `sentence-transformers/static-similarity-mrl-multilingual-v1` for semantic retrieval.

Runtime profile:

- 128 dimensions
- int8 static token embeddings
- multilingual, including Italian and English
- source model license: Apache-2.0
- no user data is used to construct the runtime model

The source model is trained with Matryoshka dimensions, so v9 keeps the trained 128-dimensional prefix and applies Model2Vec's int8 quantization. The model is prepared once and stored in the persistent `/data/models` volume. A future release asset can replace the one-time source-model preparation without changing the semantic-engine interface.
