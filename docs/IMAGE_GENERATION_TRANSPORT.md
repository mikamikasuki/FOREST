# Actual image candidates

Architecture and conceptual figures use the [editable production workflow](SCIENTIFIC_VISUAL_NARRATIVE.md#editable-production-workflow).
`research.figures.images.generate_image_asset` makes one actual request for an
isolated scientific illustration component using the same transport and spending
reservations described below. Native text, formulae and arrows are composed
separately. Components are embedded in the selected scene and editable SVG.
Temporary production prompts, replies and unsuccessful candidates are removed.
The selected figure retains necessary source and quality metadata.

When native composition exhausts its configured passes, the final image layer
uses the complete scientific context, actual draft images and review defects to
design one batch of five full-image candidates. The same three reviewers inspect
the results; source-faithful selection publishes one noneditable PNG and a PDF
containing that bitmap. The final prompt explicitly targets a top-tier journal
or conference illustration. See the [production workflow](SCIENTIFIC_VISUAL_NARRATIVE.md#editable-production-workflow).

The lower-level complete-image candidate interface is:

`research.figures.images.generate_image_candidates(client, output_dir, prompt,
variants=None, reference_paths=None)` posts to the saved provider's
`/images/generations` endpoint using the existing validated `ModelClient`.
Supplying up to four explicit PNG references instead uses multipart
`/images/edits`; no local paths are included in uploaded metadata.
The lower-level default is three separate, actual
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
