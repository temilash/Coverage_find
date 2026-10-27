# Output examples

The answers `POST /run` gave to each file in [input_examples/](../input_examples/), saved by
`scripts/save_output_examples.py` against `python run.py --sample` on 27 Oct 2026. Each
`example_N.json` holds the request, the HTTP status and the response exactly as returned;
`example_N.trace.jsonl` is that run's agent trace (copied from `logs/`). The app never reads
these files.

How they were produced (sample mode, no Zetaris, no live calls, no model key):

- **Examples 1-10** re-run the real pipeline (coordinator, tools on the DuckDB extracts, guard,
  Meterless) with the model replies recorded for those examples in `examples/<name>/llm_replies.json`
  (mode "recorded replies"). Those replies were recorded on 27 Oct 2026 from NVIDIA Nemotron called
  through NVIDIA's hosted API directly (Meterless Nightshift does not run on our Linux x86_64 machine);
  the `models` field shows the Nemotron model each agent got its answer from.
- **Example 11** is not a recorded example: the same pipeline ran with fixed rules in place of the
  model (mode "rules only, no model").
- **Example 12** is outside the sample extracts, so sample mode refused it with the list of areas it covers.

Full runs (evidence with Zetaris SQL, World Model, round cards) are in [examples/](../examples/).
