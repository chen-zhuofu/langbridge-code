# Eval data

```
eval/data/
  data-pipeline/      # collect → env → reference → curate
  langbridge-bench/   # curated internal specs + docker-images
  public/             # verified / pro (original SWE data only)
```

Curate writes `data-pipeline/curate/out/`, then **copies** into
`langbridge-bench/specs/`. Human drops live under `langbridge-bench/drop/`.
