# Input examples

Request bodies for `POST /run` (see the main README). Send one with:

```bash
curl -s -X POST localhost:8000/run -H 'content-type: application/json' -d @input_examples/example_1.json
```

| File | Request | What it shows |
|---|---|---|
| example_1.json | Great Langdale, Cumbria (54.445, -3.105) | terrain blocks every operator's nearest site |
| example_2.json | Near Rhayader, Powys (52.30, -3.51) | the critic asks for the speed trend: a second round |
| example_3.json | Central London, WC2N 5DU | service measures well: no strong cause |
| example_4.json | Sutton St Edmund, Lincolnshire (flat Fens) | flat land, no speed tests here: distance to the mast leads, not enough data close behind |
| example_5.json | Remote cell near Bettws, Powys | no tests and no site within 10 km |
| example_6.json | Gap zone Z00072 (Ousby, Westmorland and Furness) | zone diagnosis: too few measurements, so the fix plan is to gather data (reviewed by the critic) |
| example_7.json | Improvement opportunity O00332 (Windermere) | an opportunity, not a gap |
| example_8.json | A point 1.4 km from Near Rhayader, council officer | not a recorded example: in sample mode the agents' replies come from fixed rules |
| example_9.json | Oxford (51.75, -1.25) | outside the sample extracts: sample mode refuses it and lists the areas it covers |

Examples 1-7 are the recorded examples in [examples/](../examples/) (full traces there). The answers
these requests produced are in [output_examples/](../output_examples/).
