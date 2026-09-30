# Actual prediction inputs

The three CSVs are copies of real saved softmax-training predictions from FOREST runtime qualification on scikit-learn's bundled handwritten-digits dataset. They contain held-out observation IDs, actual labels, and computed class probabilities. `provenance.json` records portable fixture aliases, CSV filenames, run settings, measured metrics and comparison limits. Original local project and run identities are omitted.

These are qualification inputs for fresh analysis and writing, not prewritten Agent solutions or fabricated model responses. The external oracle recalculates each requested metric from the rows. The test suite checks labels against the bundled dataset and recomputes accuracy. The long run and short runs use unequal compute; short runs also differ in seed and regularization. None establishes a controlled causal advantage or novel scientific result.

The public handwritten-digits data retains its original dataset terms and attribution. Consult scikit-learn's `load_digits` dataset documentation for the underlying UCI Optical Recognition of Handwritten Digits provenance. No image pixels or private participant records are included here.
