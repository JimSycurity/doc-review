# Security

## What leaves your machine

`docreview facts` and `docreview catalog` run locally and never use the
network.

`docreview review` is a dry-run by default and sends nothing. With both
`--live` and `--key-file`, it sends the selected document text, context and
candidate sections to OpenRouter and bills the key's account. Only run a live
review on text you have authority to share with that provider. The `review`
command reads the key only from the file you name; it has no environment
variable fallback.

The older trial module (`python -m docreview.documentation_trial`) is kept for
comparison. With `--live` it falls back to the `OPENROUTER_API_KEY` environment
variable when no key file is given.

The engine reads only files inside the `--root` repository and rejects paths
or symbolic links that leave it. In `owned` mode it writes only `--out`
reports, generated catalogs and caches under `.tmp/`.

## Reporting a vulnerability

Do not put details of a security problem in a public issue. Open an issue that
asks the maintainer for a private contact, and share the details there.
