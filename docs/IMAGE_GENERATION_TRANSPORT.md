# Actual image candidates

`research.figures.images.generate_image_candidates(client, output_dir, prompt,
variants=None)` posts to the saved provider's `/images/generations` endpoint using
the existing validated `ModelClient`. The default is three separate, actual
conceptual illustration requests. It returns the `register_candidates` bundle
for independent design, scientific-content and print-layout review. The worker
must run those reviews and manuscript placement after generation.

The saved provider must explicitly configure `config.image_generation.model`
and a positive finite `config.image_generation.max_request_usd`, the upper bound
for **one** request. The image model is independent of the saved text model.
Optional fields are `size`, `quality`, `background`, `style`, `moderation`,
`output_format` (`png` only), `response_format` (`b64_json` only) and `timeout`.
Only supplied model-specific options are sent. Configure `response_format` when
the chosen compatible endpoint requires it. There is no model alias selection,
automatic retry or fallback image.

Every request reserves the saved upper bound in the same locked provider,
project and run ledger as text requests. Image token counts do not establish a
USD charge: received responses remain `uncertain` with the upper bound retained.
An explicit pre-generation rejection or connection refusal releases that
attempt's reservation; ambiguous errors preserve it. Budget availability must
also cover the later text-model reviews. A partially completed job retains the
actual files and safe failure metadata.

Only bounded base64 PNG bytes are accepted. Returned URLs are never downloaded.
Each generated candidate retains its actual prompt, safe response metadata and
image dimensions. Conceptual illustrations cannot supply fabricated scientific
measurements, plots, tables or experimental observations. Data plots and tables
must continue to come from the executed experiment and analysis artifacts.

Verification: `tests/test_image_transport.py` exercises configuration validation,
the real SQLite ledger, concurrent reservations and real loopback HTTP failures.
It substitutes no successful image response and makes no paid request. Successful
generation still requires an actual authorized request to the explicitly chosen
image provider and independent review of its returned PNG.
